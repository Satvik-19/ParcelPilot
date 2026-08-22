"""Offline replay of recorded Layer B runs — deterministic, zero network.

04_EVAL_SPEC.md §4: runs are recorded so a reviewer can replay any judged
case. This module re-judges the latest recording with the same pure contract
functions the live run used and requires the verdicts to match exactly —
proof that the judgement lives in the record, not in the live moment.

Skips until a live run has produced a recording (run `pytest -m live` first).
"""

import json

import pytest

from . import harness
from .cases import CASES_BY_ID, LayerBCase, TurnContract


def _latest_run():
    runs = sorted(harness.RECORDINGS_DIR.glob("run_*.json"))
    if not runs:
        pytest.skip("no recorded Layer B run yet — run `pytest -m live` first")
    return json.loads(runs[-1].read_text(encoding="utf-8"))


def _latest_judgable_run():
    """Newest recording containing at least one judged (non-provider-failure)
    turn — a run where every request hit a rate limit carries no model
    behaviour to re-judge, so fall back past it."""
    runs = sorted(harness.RECORDINGS_DIR.glob("run_*.json"), reverse=True)
    if not runs:
        pytest.skip("no recorded Layer B run yet — run `pytest -m live` first")
    for path in runs:
        record = json.loads(path.read_text(encoding="utf-8"))
        if any(not turn.get("provider_failure")
               for case in record["cases"].values()
               for turn in case["turns"]):
            return record
    pytest.skip("every recording is a full provider-failure run")


def _case_for(case_key, record):
    """Rebuild the judging case — contracts come FROM the recording when
    snapshotted there, so evolving contracts never rewrite history."""
    base_id = case_key.split("-r")[0]  # strip repeat suffixes (-r2, ...)
    template = CASES_BY_ID[base_id]
    contracts = record.get("contracts")
    if not contracts:
        return template
    turns = tuple(
        (prompt, TurnContract(**{key: (tuple(value)
                                       if isinstance(value, list)
                                       and key != "expected_args_subset"
                                       else value)
                                 for key, value in saved.items()}))
        for (prompt, _saved_contract), saved in zip(template.turns, contracts)
    )
    return LayerBCase(case_id=template.case_id, purpose=template.purpose,
                      session=template.session, turns=turns,
                      db_setup=template.db_setup)


def test_latest_recording_replays_with_identical_verdicts():
    run_record = _latest_judgable_run()
    replayable = {key: record for key, record in run_record["cases"].items()
                  if record.get("contracts")}
    if not replayable:
        pytest.skip("recording predates contract snapshotting — its "
                    "verdicts were judged by unversioned contracts")
    mismatches = {}
    for case_key, record in replayable.items():
        case = _case_for(case_key, record)
        replayed = harness.evaluate_case(case, record)
        saved = record["evaluation"]
        if replayed != saved:
            mismatches[case_key] = {"replayed": replayed, "saved": saved}
    assert not mismatches, (
        f"replay verdicts diverge from the recorded run: {mismatches}")


def test_recording_preserves_security_invariants():
    """Re-derive the critical security facts straight from the recorded tool
    calls — independent of whatever verdicts were saved."""
    run_record = _latest_run()
    for case_key, record in run_record["cases"].items():
        for turn in record["turns"]:
            for call in turn["tool_calls"]:
                assert call["name"] in harness.TOOL_NAMES, (
                    f"{case_key}: hallucinated tool {call['name']!r}")
                args = call.get("arguments")
                if isinstance(args, dict):
                    leaked = harness.IDENTITY_PARAMETERS & set(args)
                    assert not leaked, (
                        f"{case_key}: identity parameters attempted: "
                        f"{sorted(leaked)}")
