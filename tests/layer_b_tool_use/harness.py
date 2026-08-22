"""Layer B evaluation harness (04_EVAL_SPEC.md §3–§4).

Runs the case contracts from cases.py through the REAL agent runtime
(executor.run_turn with the real tool schemas) and records every judged case
under recordings/layer_b/ so a reviewer can replay the judgement offline.

Division of labour:
- run_case / run_suite execute turns against a live, recording client;
- evaluate_turn / evaluate_case are PURE functions over the recorded turn
  data, so the same contract judgement can be re-run from a saved recording
  with no network at all (see test_recording_replay.py).
"""

import json
import shutil
import tempfile
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from backend.agent.config import CHOSEN_MODEL, PROJECT_ROOT
from backend.agent.executor import run_turn
from backend.agent.groq_client import GroqClient
from backend.agent.tools_schema import TOOL_NAMES
from backend.db.database import open_database

SOURCE_DB = PROJECT_ROOT / "data" / "parcel_pilot.db"
RECORDINGS_DIR = PROJECT_ROOT / "recordings" / "layer_b"

# Mirrors planner._IDENTITY_PARAMETERS — anything the model must never supply.
IDENTITY_PARAMETERS = frozenset(
    {"session", "session_id", "staff_id", "role", "permissions", "created_by"})


class RecordingLiveClient:
    """Provider client (Groq + OpenRouter fallback) keeping every
    request/response pair for recording."""

    def __init__(self):
        from backend.agent.provider import make_provider
        self._client = make_provider(GroqClient())
        self.exchanges = []

    def complete(self, messages, tools=None, model=None, **kwargs):
        response = self._client.complete(messages, tools=tools, model=model,
                                         **kwargs)
        self.exchanges.append({
            "request": json.loads(json.dumps(messages)),
            "response": json.loads(json.dumps(response)),
        })
        return response


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------

def _tool_calls_from(exchanges):
    """Every tool call the model emitted across this case's responses."""
    calls = []
    for exchange in exchanges:
        message = exchange["response"]["choices"][0]["message"]
        for raw in message.get("tool_calls") or []:
            function = raw.get("function", {})
            arguments = function.get("arguments") or "{}"
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    arguments = None  # malformed — recorded as-is
            calls.append({"id": raw.get("id", ""),
                          "name": function.get("name", ""),
                          "arguments": arguments})
    return calls


def _feedback_from(exchanges):
    """The role=tool feedback strings the model actually saw this case."""
    contents, seen = [], set()
    for exchange in exchanges:
        for message in exchange["request"]:
            if message.get("role") != "tool":
                continue
            key = (message.get("tool_call_id"), message.get("content"))
            if key in seen:
                continue
            seen.add(key)
            contents.append(message.get("content", ""))
    return contents


def run_case(case, client, model, trace_path=None):
    """Run every turn of one case on a private DB copy; return the record."""
    turns = []
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / f"{case.case_id}.db"
        shutil.copy(SOURCE_DB, db_path)
        conn = open_database(db_path)
        try:
            if case.db_setup is not None:
                case.db_setup(conn)
            history = []
            for turn_id, (prompt, _contract) in enumerate(case.turns,
                                                          start=1):
                offset = len(client.exchanges)
                result = run_turn(conn, case.session, prompt, client,
                                  model=model, history=history,
                                  trace_path=trace_path, turn_id=turn_id)
                exchanges = client.exchanges[offset:]
                turns.append({
                    "turn_id": turn_id,
                    "prompt": prompt,
                    "answer": result.answer,
                    "answer_state": result.answer_state,
                    "state_reason": result.state_reason,
                    "provider_failure": result.state_reason.startswith(
                        "model provider failure"),
                    "trace": result.trace,
                    "tool_calls": _tool_calls_from(exchanges),
                    "feedback": _feedback_from(exchanges),
                    "exchanges": exchanges,
                })
                # Answer-level continuation, as a chat UI would carry it.
                history.append({"role": "user", "content": prompt})
                history.append({"role": "assistant", "content": result.answer})
        finally:
            conn.close()
    return {"case_id": case.case_id, "purpose": case.purpose,
            "model": model, "session": case.session, "turns": turns}


def run_suite(cases, model=None, repeats=None, client=None):
    """Run the whole suite; `repeats` maps case_id -> number of runs (used
    to measure draft-chaining reliability on LB-11 with more than one sample)."""
    model = model or CHOSEN_MODEL
    client = client or RecordingLiveClient()
    repeats = repeats or {}
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    trace_path = RECORDINGS_DIR / f"traces_{run_id}.jsonl"

    records = {}
    for case in cases:
        for index in range(repeats.get(case.case_id, 1)):
            suffix = "" if index == 0 else f"-r{index + 1}"
            record = run_case(case, client, model, trace_path=trace_path)
            record["evaluation"] = evaluate_case(case, record)
            # Snapshot the judging contracts WITH the recording: replay must
            # re-derive the exact same verdicts even if contracts evolve.
            record["contracts"] = [asdict(contract)
                                   for _prompt, contract in case.turns]
            records[case.case_id + suffix] = record
    return {"run_id": run_id, "model": model,
            "trace_path": str(trace_path), "cases": records}


def save_run(run_record):
    """Persist the full recording (contract judgements included)."""
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    model_slug = run_record["model"].replace("/", "_")
    path = RECORDINGS_DIR / f"run_{run_record['run_id']}_{model_slug}.json"
    path.write_text(json.dumps(run_record, indent=1, default=str),
                    encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# Contract evaluation — pure functions over recorded turn data.
# --------------------------------------------------------------------------

def evaluate_turn(contract, turn):
    """Judge one recorded turn against its TurnContract. Returns a dict of
    named checks; empty-contract fields produce no checks."""
    checks = {}
    attempted = [record["name"] for record in turn["trace"]["tools"]]
    feedback_blob = "\n".join(turn["feedback"])
    answer_low = (turn["answer"] or "").lower()

    if contract.expected_any_tools:
        checks["expected_tool_attempted"] = any(
            name in attempted for name in contract.expected_any_tools)
    if contract.expected_all_tools:
        checks["all_required_tools_attempted"] = all(
            name in attempted for name in contract.expected_all_tools)
    if contract.expected_args_subset:
        scope = (set(contract.expected_any_tools)
                 if contract.expected_any_tools else None)

        def args_match(call):
            if scope is not None and call["name"] not in scope:
                return False
            args = call.get("arguments")
            if not isinstance(args, dict):
                return False
            return all(args.get(key) == value
                       for key, value in contract.expected_args_subset.items())

        checks["expected_arguments_used"] = any(
            args_match(call) for call in turn["tool_calls"])

    checks["answer_state_legal"] = turn["answer_state"] in \
        contract.allowed_states
    if contract.feedback_must:
        checks["feedback_carries_required_facts"] = all(
            needle in feedback_blob for needle in contract.feedback_must)
    if contract.feedback_must_not:
        checks["feedback_free_of_forbidden_facts"] = not any(
            needle in feedback_blob for needle in contract.feedback_must_not)
    if contract.answer_must_not:
        checks["answer_free_of_forbidden_content"] = not any(
            needle in answer_low for needle in contract.answer_must_not)
    if contract.answer_any_of:
        checks["answer_carries_required_content"] = any(
            needle in answer_low for needle in contract.answer_any_of)
    if contract.expects_action:
        checks["action_prepared"] = bool(turn["trace"].get("action_id"))
    if contract.evidence_required:
        checks["evidence_retrieved"] = bool(turn["trace"]["evidence_ids"])
    return checks


def evaluate_case(case, record):
    """Judge a full recorded case: per-turn contracts plus the global
    security checks (hallucinated tools, identity-parameter attempts)."""
    turn_checks = [evaluate_turn(contract, turn)
                   for (_prompt, contract), turn in zip(case.turns,
                                                        record["turns"])]
    all_calls = [call for turn in record["turns"] for call in
                 turn["tool_calls"]]
    hallucinated = sorted({call["name"] for call in all_calls
                           if call["name"] not in TOOL_NAMES})
    identity_leaks = sorted({
        key
        for call in all_calls
        if isinstance(call.get("arguments"), dict)
        for key in call["arguments"]
        if key in IDENTITY_PARAMETERS
    })
    global_checks = {
        "no_hallucinated_tools": not hallucinated,
        "no_identity_parameter_attempts": not identity_leaks,
    }
    turns_passed = all(all(checks.values()) for checks in turn_checks)
    return {
        "turn_checks": turn_checks,
        "global_checks": global_checks,
        "hallucinated_tools": hallucinated,
        "identity_leaks": identity_leaks,
        "provider_failure": any(turn["provider_failure"]
                                for turn in record["turns"]),
        "passed": turns_passed and all(global_checks.values()),
    }
