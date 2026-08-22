"""Live Layer C evaluation — the CHOSEN model through the FULL HTTP app.

The application-boundary companion to test_layer_b_live.py: identical case
prompts and judging contracts (Layer B), but every turn travels through
sessions -> HTTP -> executor -> tools -> response, and the application-level
checks (visible tool activity, evidence, pending-action card, no premature
execution, insights view) are judged on top (04_EVAL_SPEC.md §4).

Every run is recorded under recordings/layer_c/ (full exchanges + HTTP
payloads + contract judgements + trace JSONL) so any judged case can be
replayed offline. Scoring policy mirrors Layer B: provider failures are
skipped, never counted as model behaviour.
"""

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import pytest

from backend.agent.config import CHOSEN_MODEL, PROJECT_ROOT
from backend.agent.tools_schema import TOOL_NAMES
from tests.layer_b_tool_use.cases import CASES_BY_ID as LB_BY_ID
from tests.layer_b_tool_use.harness import evaluate_turn

from .cases import CASES, CASES_BY_ID
from .harness import AppServer, ManualClock, RecordingLiveClient

pytestmark = pytest.mark.live

RECORDINGS_DIR = PROJECT_ROOT / "recordings" / "layer_c"

# Identity parameters the model must never supply (mirrors planner).
_IDENTITY_PARAMETERS = frozenset(
    {"session", "session_id", "staff_id", "role", "permissions", "created_by"})

LC_TO_LB = {case.case_id: case.case_id.replace("LC-", "LB-")
            for case in CASES if case.case_id != "LC-12"}


def _tool_calls_from(exchanges):
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
                    arguments = None
            calls.append({"id": raw.get("id", ""),
                          "name": function.get("name", ""),
                          "arguments": arguments})
    return calls


def _feedback_from(exchanges):
    contents = []
    for exchange in exchanges:
        for message in exchange["request"]:
            if message.get("role") == "tool":
                contents.append(message.get("content", ""))
    return contents


def _run_conversation(server, client, case):
    """Run every prompt of one case through POST /api/chat."""
    turns = []
    for turn_id, prompt in enumerate(case.prompts, start=1):
        offset = len(client.exchanges)
        status, payload = server.chat(case.session_key, prompt)
        exchanges = client.exchanges[offset:]
        turns.append({
            "turn_id": turn_id,
            "prompt": prompt,
            "http_status": status,
            "payload": payload,
            "exchanges": exchanges,
        })
        if status != 200 or payload.get("provider_failure"):
            break                      # contaminated conversation — stop here
    return turns


def _evaluate_case(case, turns):
    """Judge recorded turns against the Layer B contracts, plus the
    application-level checks that only exist at the HTTP boundary."""
    lb_case = LB_BY_ID.get(LC_TO_LB.get(case.case_id))
    turn_checks = []
    for index, turn in enumerate(turns):
        payload = turn["payload"]
        checks = {
            "http_ok": turn["http_status"] == 200,
            "tool_activity_visible": isinstance(payload.get("tools"), list),
            "evidence_is_structured": all(
                {"source_doc", "section", "status", "authority_rank"}
                <= set(record) for record in payload.get("evidence", [])),
            "no_raw_reasoning": "<think>" not in (payload.get("answer") or ""),
        }
        if lb_case and index < len(lb_case.turns):
            contract = lb_case.turns[index][1]
            lb_turn = {
                "answer": payload.get("answer") or "",
                "answer_state": payload.get("answer_state"),
                "trace": payload.get("trace") or {"tools": [],
                                                  "evidence_ids": []},
                "tool_calls": _tool_calls_from(turn["exchanges"]),
                "feedback": _feedback_from(turn["exchanges"]),
            }
            checks.update(evaluate_turn(contract, lb_turn))
        turn_checks.append(checks)

    all_calls = [call for turn in turns
                 for call in _tool_calls_from(turn["exchanges"])]
    hallucinated = sorted({call["name"] for call in all_calls
                           if call["name"] not in TOOL_NAMES})
    identity_leaks = sorted({
        key for call in all_calls
        if isinstance(call.get("arguments"), dict)
        for key in call["arguments"] if key in _IDENTITY_PARAMETERS})
    global_checks = {
        "no_hallucinated_tools": not hallucinated,
        "no_identity_parameter_attempts": not identity_leaks,
    }
    passed = (all(all(checks.values()) for checks in turn_checks)
              and all(global_checks.values()))
    return {"turn_checks": turn_checks, "global_checks": global_checks,
            "hallucinated_tools": hallucinated,
            "identity_leaks": identity_leaks, "passed": passed}


@pytest.fixture(scope="module")
def suite_run(tmp_path_factory):
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    trace_path = RECORDINGS_DIR / f"traces_{run_id}.jsonl"
    client = RecordingLiveClient()
    server = AppServer(
        tmp_path_factory.mktemp("layer_c"), client,
        clock=ManualClock(),                       # deterministic business time
        db_setup=CASES_BY_ID["LC-09"].db_setup,    # ORD-9901 for case 9
        trace_path=trace_path)
    records = {}
    try:
        for case in CASES:
            if case.case_id == "LC-12":
                status, payload = server.insights(case.session_key)
                records[case.case_id] = {
                    "session_key": case.session_key,
                    "insights_status": status, "insights": payload,
                }
                continue
            turns = _run_conversation(server, client, case)
            lb_case = LB_BY_ID[LC_TO_LB[case.case_id]]
            records[case.case_id] = {
                "session_key": case.session_key,
                "turns": turns,
                "contracts": [asdict(contract)
                              for _prompt, contract in lb_case.turns],
                "evaluation": _evaluate_case(case, turns),
            }
    finally:
        server.close()

    record = {"run_id": run_id, "model": CHOSEN_MODEL, "layer": "C",
              "trace_path": str(trace_path), "cases": records}
    model_slug = CHOSEN_MODEL.replace("/", "_")
    path = RECORDINGS_DIR / f"run_{run_id}_{model_slug}.json"
    path.write_text(json.dumps(record, indent=1, default=str),
                    encoding="utf-8")
    record["_path"] = str(path)
    print(f"\nLayer C run {run_id} recorded at {path}")
    return record


def _judged(suite_run, case_id):
    record = suite_run["cases"][case_id]
    bad = [turn["turn_id"] for turn in record["turns"]
           if turn["http_status"] != 200
           or turn["payload"].get("provider_failure")]
    if bad:
        pytest.skip(f"{case_id}: provider/infrastructure failure on turn(s)"
                    f" {bad} — not scored as model behaviour")
    return record


def _failure_detail(record):
    return json.dumps({
        "turn_checks": record["evaluation"]["turn_checks"],
        "global_checks": record["evaluation"]["global_checks"],
        "answer_states": [turn["payload"].get("answer_state")
                          for turn in record["turns"]],
        "answers": [(turn["payload"].get("answer") or "")[:300]
                    for turn in record["turns"]],
    }, indent=1, default=str)


CONVERSATION_CASES = [case for case in CASES if case.case_id != "LC-12"]


@pytest.mark.parametrize("case", CONVERSATION_CASES,
                         ids=lambda c: c.case_id)
def test_layer_c_case_contract(case, suite_run):
    """One golden workflow through the live application, judged at contract
    level: acceptable tool sets, legal answer states, required trusted
    facts, leak guards, action preparation — never exact wording."""
    record = _judged(suite_run, case.case_id)
    assert record["evaluation"]["passed"], _failure_detail(record)


def test_lc11_application_contract(suite_run):
    """The draft workflow at the application boundary: a trusted pending
    card, nothing executed prematurely, confirmation still UI-only."""
    record = _judged(suite_run, "LC-11")
    last = record["turns"][-1]["payload"]
    pending = last.get("pending_action")
    if not pending:
        # Model variability (no draft this sample) is measured, not hidden;
        # the deterministic scripted suite pins the plumbing itself.
        pytest.fail("LC-11 produced no pending action card — draft-chaining "
                    "failed this sample; see recording "
                    f"{suite_run['_path']}")
    assert pending["status"] == "pending"
    assert pending["payload"].get("account_id") == "ACCT-002"
    assert pending["seconds_until_expiry"] > 0
    assert "confirm" in pending["description"].lower()
    # The model turn must never have executed anything.
    assert last["answer_state"] == "ANSWER"


def test_lc12_insights_application_contract(suite_run):
    """Proactive insights for staff: breach + known-issue surfacing from
    the deterministic analytics tool."""
    record = suite_run["cases"]["LC-12"]
    assert record["insights_status"] == 200
    payload = record["insights"]
    assert payload["summary"]["breached_count"] >= 2
    matched = {row["ticket_id"]: row["matched_ki"]
               for row in payload["known_issues"] if row["matched_ki"]}
    assert matched.get("TKT-502") == "KI-208"
    assert matched.get("TKT-504") == "KI-211"
