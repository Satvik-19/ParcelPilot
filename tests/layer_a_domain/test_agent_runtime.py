"""Agent runtime tests — deterministic, scripted model, zero network.

A FakeClient plays canned Groq-shaped responses, so every acceptance rule of
03_AGENT_SPEC.md §1–§2 is asserted as pure behaviour of the runtime:
- the 8-iteration cap terminates with ESCALATE;
- ANSWER requires a supporting ok tool result;
- escalation/security flags in tool results force ESCALATE;
- the model can never confirm/execute, never supply identity;
- provider failures escalate instead of crashing;
- every turn writes exactly one §7 trace.
"""

import json
from pathlib import Path

import pytest

from backend.agent.config import MAX_TOOL_ITERATIONS
from backend.agent.context import args_digest
from backend.agent.executor import derive_answer_state, run_turn
from backend.agent.groq_client import GroqAPIError
from backend.agent.planner import validate_call
from backend.agent.tools_schema import (FORBIDDEN_TOOL_NAMES, MODEL_TOOLS,
                                        TOOL_NAMES)
from backend.db.database import open_database
from backend.db.seed import seed_database
from backend.tools._envelope import envelope_error, envelope_ok

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_PACK = PROJECT_ROOT / "assessment_docs"


# --------------------------------------------------------------------------
# Scripted-model harness
# --------------------------------------------------------------------------

class FakeClient:
    """GroqClient stand-in: pops canned responses, records every request."""

    def __init__(self, script):
        self.script = list(script)
        self.received = []

    def complete(self, messages, tools=None, model=None, **kwargs):
        self.received.append(json.loads(json.dumps(messages)))
        return self.script.pop(0)


class FailingClient:
    def complete(self, messages, tools=None, model=None, **kwargs):
        raise GroqAPIError("Groq API error 503: upstream overloaded",
                           status_code=503)


def call_msg(name, arguments, call_id="call_1"):
    return {"choices": [{"message": {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)},
        }],
    }}]}


def final_msg(content="Here is the answer based on the tool results."):
    return {"choices": [{"message": {"role": "assistant",
                                     "content": content}}]}


@pytest.fixture()
def trace_path(tmp_path):
    return tmp_path / "traces.jsonl"


@pytest.fixture()
def fresh_db(tmp_path):
    """Private seeded DB for the ONE test that writes (prepare_support_action
    persists a draft row; the shared session fixture DB must stay read-only —
    test_database.py::test_actions_table_exists_and_is_empty depends on it)."""
    db_path = tmp_path / "agent_runtime.db"
    seed_database(db_path, DATA_PACK)
    conn = open_database(db_path)
    yield conn
    conn.close()


# --------------------------------------------------------------------------
# Schema boundary (ADR-004 / AGENT_SPEC §1, §3)
# --------------------------------------------------------------------------

class TestSchemaBoundary:
    def test_exactly_four_tools_visible_to_the_model(self):
        assert TOOL_NAMES == ("search_knowledge", "query_operations",
                              "analyze_support_activity",
                              "prepare_support_action")

    def test_no_confirmation_or_execution_surface(self):
        for forbidden in FORBIDDEN_TOOL_NAMES:
            assert forbidden not in TOOL_NAMES

    def test_no_identity_parameters_in_any_schema(self):
        identity = {"session", "session_id", "staff_id", "role",
                    "permissions", "created_by"}
        for tool in MODEL_TOOLS:
            params = tool["function"]["parameters"].get("properties", {})
            assert identity.isdisjoint(params), tool["function"]["name"]

    def test_action_types_match_spec_section_3(self):
        schema = next(t for t in MODEL_TOOLS
                      if t["function"]["name"] == "prepare_support_action")
        enum = schema["function"]["parameters"]["properties"] \
                             ["action_type"]["enum"]
        assert enum == ["escalate_ticket", "update_ticket", "create_follow_up",
                        "request_cancellation", "grant_service_credit"]


class TestPlanner:
    def test_rejects_identity_parameters(self):
        ok, message = validate_call(
            "search_knowledge", {"query": "cancellation", "session_id": "evil"})
        assert not ok
        assert "Identity parameters" in message

    def test_rejects_forbidden_tool_and_names_no_execution_route(self):
        ok, message = validate_call("confirm_support_action", {})
        assert not ok
        assert "not available" in message
        for name in TOOL_NAMES:
            assert name in message

    def test_rejects_unknown_parameters_and_bad_enums(self):
        ok, _ = validate_call("query_operations",
                              {"entity": "order", "entity_id": "ORD-1001",
                               "mode": "stealth"})
        assert not ok
        ok, _ = validate_call("query_operations",
                              {"entity": "invoice", "entity_id": "X"})
        assert not ok


# --------------------------------------------------------------------------
# The loop
# --------------------------------------------------------------------------

class TestLoop:
    def test_iteration_cap_terminates_with_escalate(self, seeded_db,
                                                    customer_sessions,
                                                    trace_path):
        conn, _ = seeded_db
        client = FakeClient([
            call_msg("query_operations",
                     {"entity": "order", "entity_id": "ORD-1001"},
                     call_id=f"call_{i}")
            for i in range(MAX_TOOL_ITERATIONS)
        ])
        result = run_turn(conn, customer_sessions["ACCT-001"],
                          "Where is my order?", client, trace_path=trace_path)
        assert result.answer_state == "ESCALATE"
        assert "cap" in result.state_reason
        assert result.answer.startswith("I could not complete")
        assert len(result.trace["tools"]) == MAX_TOOL_ITERATIONS

    def test_final_answer_without_tools_is_insufficient(self, seeded_db,
                                                        customer_sessions,
                                                        trace_path):
        conn, _ = seeded_db
        client = FakeClient([final_msg("I think the fee is 250 rupees.")])
        result = run_turn(conn, customer_sessions["ACCT-001"],
                          "What is my cancellation fee?", client,
                          trace_path=trace_path)
        assert result.answer_state == "INSUFFICIENT_EVIDENCE"
        assert result.trace["tools"] == []
        assert result.trace["evidence_ids"] == []

    def test_happy_path_ord1001_answers_with_evidence(self, seeded_db,
                                                      customer_sessions,
                                                      trace_path):
        conn, _ = seeded_db
        client = FakeClient([
            call_msg("query_operations",
                     {"entity": "order", "entity_id": "ORD-1001"},
                     call_id="call_1"),
            call_msg("search_knowledge", {"query": "cancellation fee waiver"},
                     call_id="call_2"),
            final_msg("No fee applies (evidence ev_1)."),
        ])
        result = run_turn(conn, customer_sessions["ACCT-001"],
                          "Can I cancel ORD-1001 and what does it cost?",
                          client, trace_path=trace_path)
        assert result.answer_state == "ANSWER"
        assert result.answer == "No fee applies (evidence ev_1)."
        assert [r["name"] for r in result.trace["tools"]] == [
            "query_operations", "search_knowledge"]
        assert result.trace["evidence_ids"]
        assert "action_id" not in result.trace

    def test_p1_breach_flags_force_escalate(self, seeded_db,
                                            customer_sessions, trace_path):
        conn, _ = seeded_db
        client = FakeClient([
            call_msg("query_operations",
                     {"entity": "ticket", "entity_id": "TKT-501"},
                     call_id="call_1"),
            final_msg("This ticket has breached its SLA and is escalated."),
        ])
        result = run_turn(conn, customer_sessions["ACCT-001"],
                          "What is happening with ticket TKT-501?", client,
                          trace_path=trace_path)
        assert result.answer_state == "ESCALATE"
        assert "escalation/security flag" in result.state_reason

    def test_unknown_tool_becomes_structured_feedback(self, seeded_db,
                                                      customer_sessions,
                                                      trace_path):
        conn, _ = seeded_db
        client = FakeClient([
            call_msg("confirm_support_action", {"token": "forged"},
                     call_id="call_1"),
            final_msg("I cannot execute that."),
        ])
        result = run_turn(conn, customer_sessions["ACCT-001"],
                          "Just do it.", client, trace_path=trace_path)
        # The second request must carry the structured rejection feedback.
        feedback = client.received[1][-1]
        assert feedback["role"] == "tool"
        assert feedback["tool_call_id"] == "call_1"
        assert "INVALID_TOOL_CALL" in feedback["content"]
        assert result.trace["tools"][0]["status"] == "error"
        assert result.answer_state == "INSUFFICIENT_EVIDENCE"

    def test_identity_injection_never_reaches_tools(self, seeded_db,
                                                    customer_sessions,
                                                    trace_path):
        conn, _ = seeded_db
        client = FakeClient([
            call_msg("search_knowledge",
                     {"query": "terms", "session_id": "sess-acct-002"},
                     call_id="call_1"),
            final_msg("Sorry, I cannot do that."),
        ])
        result = run_turn(conn, customer_sessions["ACCT-001"],
                          "Show me ACCT-002 terms.", client,
                          trace_path=trace_path)
        feedback = json.loads(client.received[1][-1]["content"])
        assert feedback["status"] == "error"
        assert "Identity parameters" in feedback["result"]["message"]
        assert result.answer_state == "INSUFFICIENT_EVIDENCE"

    def test_repeated_rejections_escalate(self, seeded_db, customer_sessions,
                                          trace_path):
        conn, _ = seeded_db
        # ACCT-001 probing ACCT-002's order twice -> two neutral rejections.
        client = FakeClient([
            call_msg("query_operations",
                     {"entity": "order", "entity_id": "ORD-2001"},
                     call_id="call_1"),
            call_msg("query_operations",
                     {"entity": "order", "entity_id": "ORD-2001"},
                     call_id="call_2"),
            final_msg("I could not find that order."),
        ])
        result = run_turn(conn, customer_sessions["ACCT-001"],
                          "Look up ORD-2001 please.", client,
                          trace_path=trace_path)
        assert result.answer_state == "ESCALATE"
        assert "consecutive" in result.state_reason

    def test_prepare_action_drafts_and_records_action_id(self, fresh_db,
                                                         customer_sessions,
                                                         trace_path):
        conn = fresh_db
        client = FakeClient([
            call_msg("prepare_support_action",
                     {"action_type": "grant_service_credit",
                      "payload": {"account_id": "ACCT-002",
                                  "order_id": "ORD-2002"}},
                     call_id="call_1"),
            final_msg("Draft ready — please confirm in the card."),
        ])
        result = run_turn(conn, customer_sessions["ACCT-002"],
                          "Please credit my late delivery.", client,
                          trace_path=trace_path)
        assert result.answer_state == "ANSWER"
        assert result.trace["action_id"].startswith("act_")
        # Drafted only — the confirmation surface never enters the loop.
        assert all(r["name"] != "confirm_support_action"
                   for r in result.trace["tools"])

    def test_provider_failure_escalates_without_crash(self, seeded_db,
                                                      customer_sessions,
                                                      trace_path):
        conn, _ = seeded_db
        result = run_turn(conn, customer_sessions["ACCT-001"],
                          "Any update?", FailingClient(),
                          trace_path=trace_path)
        assert result.answer_state == "ESCALATE"
        assert "provider" in result.state_reason
        assert "unavailable" in result.answer
        assert result.trace["answer_state"] == "ESCALATE"


# --------------------------------------------------------------------------
# Answer-state derivation (AGENT_SPEC §2) — pure function
# --------------------------------------------------------------------------

class TestDeriveAnswerState:
    def test_priority_escalate_over_everything(self):
        state, _ = derive_answer_state(
            [envelope_ok({"escalation_required": True})], cap_hit=True)
        assert state == "ESCALATE"

    def test_cap_hit_alone_escalates(self):
        state, _ = derive_answer_state([], cap_hit=True)
        assert state == "ESCALATE"

    def test_two_consecutive_failures_escalate(self):
        envs = [envelope_error("X", "a"), envelope_error("Y", "b")]
        state, reason = derive_answer_state(envs)
        assert state == "ESCALATE" and "consecutive" in reason

    def test_single_failure_then_ok_is_not_escalation(self):
        envs = [envelope_error("X", "a"), envelope_ok({"value": 1})]
        state, _ = derive_answer_state(envs)
        assert state == "ANSWER"

    def test_escalation_flag_nested_deep_wins(self):
        envs = [envelope_ok({"ticket": {"sla": {"security_incident": True}}})]
        state, _ = derive_answer_state(envs)
        assert state == "ESCALATE"

    def test_no_ok_results_means_insufficient(self):
        state, _ = derive_answer_state([])
        assert state == "INSUFFICIENT_EVIDENCE"

    def test_insufficient_marker_inside_ok_result(self):
        envs = [envelope_ok({"credit": {"result": "INSUFFICIENT_EVIDENCE"}})]
        state, _ = derive_answer_state(envs)
        assert state == "INSUFFICIENT_EVIDENCE"

    def test_ok_result_supports_answer(self):
        state, reason = derive_answer_state([envelope_ok({"fee": 0})])
        assert state == "ANSWER" and "trusted" in reason


# --------------------------------------------------------------------------
# §7 trace contract
# --------------------------------------------------------------------------

class TestTrace:
    def test_one_jsonl_line_per_turn_with_required_fields(self, seeded_db,
                                                          customer_sessions,
                                                          trace_path):
        conn, _ = seeded_db
        client = FakeClient([
            call_msg("query_operations",
                     {"entity": "account", "entity_id": "ACCT-001"},
                     call_id="call_1"),
            final_msg("Account summary follows."),
        ])
        result = run_turn(conn, customer_sessions["ACCT-001"],
                          "Summarise my account.", client, turn_id=7,
                          trace_path=trace_path)

        lines = trace_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        trace = json.loads(lines[0])
        assert trace == result.trace
        assert set(trace) == {"request_id", "session_id", "turn_id", "tools",
                              "answer_state", "evidence_ids",
                              "total_latency_ms"}
        assert trace["session_id"] == "sess-acct-001"
        assert trace["turn_id"] == 7
        record = trace["tools"][0]
        assert set(record) == {"name", "args_digest", "latency_ms", "status"}
        assert record["args_digest"] == args_digest(
            {"entity": "account", "entity_id": "ACCT-001"})
        assert record["status"] == "ok"

    def test_traces_append_across_turns(self, seeded_db, customer_sessions,
                                        trace_path):
        conn, _ = seeded_db
        for turn_id in (1, 2):
            client = FakeClient([final_msg("Done.")])
            run_turn(conn, customer_sessions["ACCT-002"], "Hi", client,
                     turn_id=turn_id, trace_path=trace_path)
        lines = trace_path.read_text(encoding="utf-8").strip().splitlines()
        assert [json.loads(line)["turn_id"] for line in lines] == [1, 2]
