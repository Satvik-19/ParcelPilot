"""Live Layer B evaluation — CHOSEN model through the real runtime.

The ONLY tests that require live Groq inference (marker `live`, deselected by
default in pytest.ini — run them with `pytest -m live`). Every run is
recorded under recordings/layer_b/ (full exchanges + contract judgements +
trace JSONL) so any judged case can be replayed offline (04_EVAL_SPEC.md §4).

Scoring policy: infrastructure/provider failures are skipped, never counted
as model successes or failures.
"""

import json

import pytest

from backend.agent.config import CHOSEN_MODEL

from . import harness
from .cases import CASES

pytestmark = pytest.mark.live

# The Phase-5 gap under investigation: models stopped at credit eligibility
# instead of chaining into prepare_support_action. LB-11 is therefore judged
# on three independent samples so a rate — not a single coin flip — is measured.
DRAFT_CHAINING_REPEATS = {"LB-11": 3}


@pytest.fixture(scope="module")
def suite_run():
    record = harness.run_suite(CASES, model=CHOSEN_MODEL,
                               repeats=DRAFT_CHAINING_REPEATS)
    path = harness.save_run(record)
    print(f"\nLayer B run {record['run_id']} recorded at {path}")
    return record


def _judged_record(suite_run, case_id):
    record = suite_run["cases"][case_id]
    # A provider failure on ANY turn contaminates the whole conversation
    # (later turns inherit a broken history), so the case is not judged in
    # either direction — infrastructure failures are never model behaviour.
    failed_turns = [turn["turn_id"] for turn in record["turns"]
                    if turn["provider_failure"]]
    if failed_turns:
        pytest.skip(f"{case_id}: provider failure on turn(s) {failed_turns}"
                    " — not scored as model behaviour")
    return record


def _failure_detail(evaluation, record):
    return json.dumps({
        "turn_checks": evaluation["turn_checks"],
        "global_checks": evaluation["global_checks"],
        "answer_states": [turn["answer_state"] for turn in record["turns"]],
        "tools_used": [[call["name"] for call in turn["tool_calls"]]
                       for turn in record["turns"]],
        "answers": [turn["answer"][:300] for turn in record["turns"]],
    }, indent=1, default=str)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.case_id)
def test_layer_b_case_contract(case, suite_run):
    """One golden workflow judged at contract level: acceptable tool sets,
    legal answer states, required trusted facts, leak guards, action prep."""
    record = _judged_record(suite_run, case.case_id)
    evaluation = record["evaluation"]
    assert evaluation["passed"], _failure_detail(evaluation, record)


def test_draft_chaining_reliability(suite_run):
    """Phase-6 investigation (Section C): trusted eligibility result →
    prepare_support_action. Measured over the LB-11 sample set; the rate is
    recorded in the run recording and the session handoff, and at least one
    sample must demonstrate the full workflow."""
    judged, prepared = 0, 0
    details = []
    for key in sorted(suite_run["cases"]):
        if not key.startswith("LB-11"):
            continue
        record = suite_run["cases"][key]
        if any(turn["provider_failure"] for turn in record["turns"]):
            details.append({"sample": key, "skipped": "provider failure"})
            continue
        evaluation = record["evaluation"]
        judged += 1
        chained = (evaluation["turn_checks"][0]
                   .get("all_required_tools_attempted", False)
                   and evaluation["turn_checks"][0].get("action_prepared",
                                                        False))
        prepared += chained
        details.append({"sample": key, "passed": evaluation["passed"],
                        "chained": chained})
    rate = prepared / judged if judged else 0.0
    suite_run["draft_chaining"] = {
        "judged_samples": judged, "chained": prepared,
        "success_rate": round(rate, 3), "details": details,
    }
    harness.save_run(suite_run)  # persist the measured rate into the recording
    print(f"\nDraft-chaining success rate: {prepared}/{judged} "
          f"({rate:.0%}) — details: {details}")
    assert judged >= 1, "no LB-11 sample was judgable (provider failures)"
    assert prepared >= 1, (
        "the action-preparation workflow was never demonstrated — see "
        f"{details}")
    assert not any(item.get("passed") is False for item in details), \
        f"draft-chaining sample failed its full contract: {details}"
