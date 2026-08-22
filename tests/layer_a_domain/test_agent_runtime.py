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
from backend.agent.executor import derive_answer_state, run_turn, serialize_envelope
from backend.agent.groq_client import GroqAPIError
from backend.agent.planner import validate_call
from backend.agent.tools_schema import (FORBIDDEN_TOOL_NAMES, MODEL_TOOLS,
                                        TOOL_NAMES)
from backend.db.database import open_database
from backend.db.seed import seed_database
from backend.tools._envelope import envelope_error, envelope_ok, envelope_rejected
from backend.trust.evidence import EvidenceRecord

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

    def test_repeated_denials_are_definitive_not_escalation(self, seeded_db,
                                                            customer_sessions,
                                                            trace_path):
        conn, _ = seeded_db
        # ACCT-001 probing ACCT-002's order — a neutral rejection is a
        # trusted deterministic decision (ISSUE 3). The runtime now
        # terminates early when every envelope is a rejection (the model
        # has hit an access wall), so only ONE tool call fires before the
        # turn ends. The contract: definitive ANSWER, never ESCALATE,
        # never INSUFFICIENT_EVIDENCE, with a safe user-facing message.
        client = FakeClient([
            call_msg("query_operations",
                     {"entity": "order", "entity_id": "ORD-2001"},
                     call_id="call_1"),
            final_msg("I could not find that order."),
        ])
        result = run_turn(conn, customer_sessions["ACCT-001"],
                          "Look up ORD-2001 please.", client,
                          trace_path=trace_path)
        assert result.answer_state == "ANSWER"
        statuses = [r["status"] for r in result.trace["tools"]]
        assert statuses == ["rejected"]
        assert "unable to access" in result.answer.lower()

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
                              "total_latency_ms", "model_calls"}
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


# --------------------------------------------------------------------------
# Compact tool feedback — serialize_envelope strips per-record text
# --------------------------------------------------------------------------

class TestCompactFeedback:
    def test_serialize_envelope_strips_text_from_evidence(self):
        """The full text of evidence records must not leak into the model's
        tool feedback — it inflates the prompt without adding value since
        the model already saw the text when search_knowledge returned it."""
        record = EvidenceRecord(
            evidence_id="doc#section", source_doc="doc", section="section",
            status="CURRENT", authority_rank=2, applicable_to="all accounts",
            text="A very long policy paragraph that should not be in feedback.",
        )
        env = envelope_ok({"fee": 0}, evidence=[record])
        serialized = serialize_envelope(env)
        assert serialized["status"] == "ok"
        assert serialized["evidence"], "evidence records must be present"
        for ev in serialized["evidence"]:
            assert "text" not in ev, "per-record text must be stripped"
            assert ev["evidence_id"] == "doc#section"

    def test_serialize_envelope_preserves_text_in_result(self):
        """The model needs per-record text from tool results (e.g.
        search_knowledge) to formulate accurate, source-grounded answers.
        Evidence record text is stripped (duplicate of result text), but
        the result itself must keep its text fields."""
        env = envelope_ok({
            "cancellation": {
                "cancellable": True,
                "text": "Full cancellation policy text here...",
                "evidence": ("doc#sec1", "doc#sec2"),
            },
        })
        serialized = serialize_envelope(env)
        # Result text is preserved — the model needs it:
        assert serialized["result"]["cancellation"]["text"] == (
            "Full cancellation policy text here..."
        )
        # Citation tuples survive (the model needs them to cite):
        assert list(serialized["result"]["cancellation"]["evidence"]) == [
            "doc#sec1", "doc#sec2"
        ]

    def test_serialize_envelope_preserves_status_and_structure(self):
        env = envelope_rejected("ACCESS_DENIED", "Not authorized.")
        serialized = serialize_envelope(env)
        assert serialized["status"] == "rejected"
        assert serialized["result"]["rejection_code"] == "ACCESS_DENIED"


# --------------------------------------------------------------------------
# Early deterministic termination — all-rejection turns skip synthesis
# --------------------------------------------------------------------------

class TestEarlyTermination:
    def test_single_rejection_ends_turn_as_answer(self, seeded_db,
                                                   customer_sessions,
                                                   trace_path):
        """A single deterministic rejection terminates the turn as ANSWER
        without a synthesis model call — the rejection IS the definitive
        trusted outcome (ISSUE 3)."""
        conn, _ = seeded_db
        client = FakeClient([
            call_msg("query_operations",
                     {"entity": "order", "entity_id": "ORD-2001"},
                     call_id="call_1"),
            final_msg("This should never be reached."),
        ])
        result = run_turn(conn, customer_sessions["ACCT-001"],
                          "Show me ORD-2001.", client,
                          trace_path=trace_path)
        assert result.answer_state == "ANSWER"
        assert "unable to access" in result.answer.lower()
        # Only ONE model call happened (no synthesis):
        assert len(client.received) == 1

    def test_mixed_results_do_not_trigger_early_termination(self, seeded_db,
                                                            customer_sessions,
                                                            trace_path):
        """When at least one envelope is not a rejection (ok or error),
        early termination does NOT fire — the model must synthesize."""
        conn, _ = seeded_db
        client = FakeClient([
            call_msg("query_operations",
                     {"entity": "account", "entity_id": "ACCT-001"},
                     call_id="call_1"),
            final_msg("Account summary follows."),
        ])
        result = run_turn(conn, customer_sessions["ACCT-001"],
                          "Show my account.", client,
                          trace_path=trace_path)
        # Two model calls: one for the tool call, one for synthesis.
        assert len(client.received) == 2
        assert result.answer_state == "ANSWER"
        assert "account summary" in result.answer.lower()

    def test_model_calls_appear_in_trace(self, seeded_db, customer_sessions,
                                          trace_path):
        """Every model call should be recorded in the trace for latency
        diagnosis — the trace must include model_calls with iteration,
        latency_ms and tool_calls for each call."""
        conn, _ = seeded_db
        client = FakeClient([
            call_msg("query_operations",
                     {"entity": "account", "entity_id": "ACCT-001"},
                     call_id="call_1"),
            final_msg("Done."),
        ])
        result = run_turn(conn, customer_sessions["ACCT-001"],
                          "Show my account.", client,
                          trace_path=trace_path)
        mc = result.trace["model_calls"]
        assert len(mc) == 2
        assert mc[0]["tool_calls"] is True
        assert mc[1]["tool_calls"] is False
        assert all("latency_ms" in entry for entry in mc)


# --------------------------------------------------------------------------
# Knowledge-intent routing guard — safety net for prompt non-compliance
# --------------------------------------------------------------------------

from backend.agent.executor import _is_knowledge_intent, _ROUTING_HINT


class TestKnowledgeIntentGuard:
    def test_guard_fires_when_model_skips_tools_on_knowledge_query(
            self, seeded_db, customer_sessions, trace_path):
        """When the model goes directly to a final answer on its first
        response and the user message looks like a policy/knowledge query,
        the guard injects a routing hint and gives the model another
        chance to call search_knowledge."""
        conn, _ = seeded_db
        # Script: first call returns no tools (model skipped),
        # second call (after guard hint) calls search_knowledge,
        # third call returns final answer.
        client = FakeClient([
            final_msg("I don't have that information."),
            call_msg("search_knowledge",
                     {"query": "known issue pickup processing"},
                     call_id="call_1"),
            final_msg("There is a known issue KI-211 affecting pickup."),
        ])
        result = run_turn(conn, customer_sessions["ACCT-001"],
                          "Is there a known issue affecting pickup processing?",
                          client, trace_path=trace_path)
        # The guard should have fired: 3 model calls total
        assert len(client.received) == 3
        # The routing hint should be in the messages of the second call:
        second_call_messages = client.received[1]
        contents = [m["content"] for m in second_call_messages
                    if m["role"] == "system"]
        assert any(_ROUTING_HINT in c for c in contents)
        assert result.answer_state == "ANSWER"

    def test_guard_does_not_fire_for_action_requests(self, seeded_db,
                                                      customer_sessions,
                                                      trace_path):
        """Action requests (cancel, refund, etc.) should not trigger the
        knowledge-intent guard — they need query_operations, not
        search_knowledge."""
        conn, _ = seeded_db
        client = FakeClient([
            final_msg("Sure, I'll help you cancel."),
        ])
        result = run_turn(conn, customer_sessions["ACCT-001"],
                          "Please cancel my order.", client,
                          trace_path=trace_path)
        # Only one model call — no guard intervention
        assert len(client.received) == 1

    def test_guard_does_not_fire_when_tools_are_called(self, seeded_db,
                                                       customer_sessions,
                                                       trace_path):
        """When the model calls tools on the first response, the guard
        must not fire even if the user message is a knowledge query."""
        conn, _ = seeded_db
        client = FakeClient([
            call_msg("search_knowledge",
                     {"query": "known issue pickup"},
                     call_id="call_1"),
            final_msg("There is a known issue."),
        ])
        result = run_turn(conn, customer_sessions["ACCT-001"],
                          "Is there a known issue affecting pickup?",
                          client, trace_path=trace_path)
        # Two model calls — no guard, just normal tool + synthesis
        assert len(client.received) == 2

    @pytest.mark.parametrize("message,expected", [
        ("Is there a known issue affecting pickup?", True),
        ("What is the cancellation policy?", True),
        ("What are the SLA targets?", True),
        ("What plan capabilities do we have?", True),
        ("Show me my orders", False),
        ("What is the status of TKT-501?", False),
        ("Please cancel my order", False),
        ("Help me with my refund", False),
        ("Look up ORD-1001", False),
        ("", False),
    ])
    def test_intent_classifier(self, message, expected):
        """The intent classifier should detect knowledge queries without
        false-positiving on action requests or entity-specific queries."""
        assert _is_knowledge_intent(message) == expected
