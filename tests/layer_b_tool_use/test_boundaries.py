"""Layer B boundary/security tests — deterministic, zero network.

Layer A already unit-tests the planner and schema; this file drives the same
violations through the FULL agent loop (executor.run_turn with the real tool
schemas), because that is the exact surface the Layer B live evaluation
judges. A scripted FakeClient stands in for the model.

Covered (03_AGENT_SPEC.md §1–§2, ADR-004):
- confirmation/execution surface stays unreachable end-to-end;
- identity parameters cannot override the server-side session;
- unknown (smuggled) parameters are rejected before dispatch;
- cross-account entity lookups stay denied even for well-formed calls;
- the 8-iteration cap terminates a tool-only loop with ESCALATE;
- a provider failure escalates instead of being scored as model behaviour.
"""

import json

from backend.agent.config import MAX_TOOL_ITERATIONS
from backend.agent.executor import run_turn
from backend.agent.groq_client import GroqAPIError

SESSION_ACCT_002 = {"role": "customer", "account_id": "ACCT-002",
                    "session_id": "sess-boundary-acct-002"}
TKT_501_SUBJECT = "All shipment creation is failing"


class FakeClient:
    """Pops canned Groq-shaped responses; records every request."""

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


def final_msg(content="Understood — here is what I found."):
    return {"choices": [{"message": {"role": "assistant",
                                     "content": content}}]}


def _feedback_blob(result_messages):
    return "\n".join(
        message.get("content", "")
        for messages in result_messages
        for message in messages
        if message.get("role") == "tool"
    )


def _run(client, conn, session, prompt="Please handle my request."):
    return run_turn(conn, session, prompt, client, model="scripted",
                    trace_path=None)


class TestConfirmationSurface:
    def test_confirm_support_action_is_unreachable_end_to_end(self,
                                                              seeded_conn):
        """Even if the model invents the confirmation call, the runtime must
        refuse it and never produce an executed action (ADR-004)."""
        client = FakeClient([
            call_msg("confirm_support_action", {"action_id": "ACT-0001"}),
            final_msg("All done — your action has been executed."),
        ])
        result = _run(client, seeded_conn, SESSION_ACCT_002)
        feedback = _feedback_blob(client.received[1:])
        assert "Confirmation/execution endpoints are not available" in \
            feedback
        # The invalid call counts as a failure but a single failure does not
        # force ESCALATE; with no ok result the state is INSUFFICIENT_EVIDENCE.
        assert result.answer_state == "INSUFFICIENT_EVIDENCE"
        assert result.trace.get("action_id") is None


class TestIdentityBoundary:
    def test_identity_parameters_cannot_override_the_session(self,
                                                             seeded_conn):
        """Supplying session/role must be rejected outright — the session is
        injected server-side and can never be spoofed through arguments."""
        client = FakeClient([
            call_msg("query_operations", {
                "entity": "ticket", "entity_id": "TKT-501",
                "session_id": "sess-fake-admin", "role": "staff",
            }),
            final_msg(),
        ])
        result = _run(client, seeded_conn, SESSION_ACCT_002)
        feedback = _feedback_blob(client.received[1:])
        assert "Identity parameters" in feedback
        assert TKT_501_SUBJECT not in feedback   # no data crossed the boundary
        assert result.answer_state == "INSUFFICIENT_EVIDENCE"

    def test_unknown_parameters_are_rejected_before_dispatch(self,
                                                             seeded_conn):
        """Smuggled extra parameters (e.g. a rogue account_id) never reach
        the tool layer."""
        client = FakeClient([
            call_msg("query_operations", {
                "entity": "ticket", "entity_id": "TKT-501",
                "account_id": "ACCT-001",
            }),
            final_msg(),
        ])
        result = _run(client, seeded_conn, SESSION_ACCT_002)
        feedback = _feedback_blob(client.received[1:])
        assert "Unknown parameter(s)" in feedback
        assert TKT_501_SUBJECT not in feedback
        assert result.answer_state == "INSUFFICIENT_EVIDENCE"

    def test_cross_account_lookup_denied_even_when_well_formed(self,
                                                               seeded_conn):
        """A perfectly valid call for another account's entity executes but
        the authorization layer denies it — scope comes from the session.
        The runtime terminates early on deterministic rejection, so only
        one model call fires — the denial IS the definitive outcome."""
        client = FakeClient([
            call_msg("query_operations",
                     {"entity": "ticket", "entity_id": "TKT-501"}),
            final_msg(),
        ])
        result = _run(client, seeded_conn, SESSION_ACCT_002)
        # Verify the rejection through the trace (early termination means
        # no second model call, so there is no feedback blob to inspect):
        trace_tools = result.trace["tools"]
        assert len(trace_tools) == 1
        assert trace_tools[0]["status"] == "rejected"
        assert TKT_501_SUBJECT not in str(result.evidence)
        # The denial is a trusted definitive outcome (manual-validation
        # ISSUE 3) — never INSUFFICIENT_EVIDENCE.
        assert result.answer_state == "ANSWER"


class TestLoopAndProviderGuards:
    def test_iteration_cap_terminates_a_tool_only_loop(self, seeded_conn):
        """A model that keeps calling tools and never answers must hit the
        hard cap and escalate — no unbounded loop is possible."""
        script = [
            call_msg("query_operations",
                     {"entity": "ticket", "entity_id": "TKT-502"},
                     call_id=f"call_{index}")
            for index in range(MAX_TOOL_ITERATIONS)
        ]
        client = FakeClient(script)
        result = _run(client, seeded_conn, SESSION_ACCT_002)
        assert result.answer_state == "ESCALATE"
        assert result.state_reason == \
            "iteration cap reached without a final answer"
        assert len(result.trace["tools"]) == MAX_TOOL_ITERATIONS
        assert "escalated" in result.answer

    def test_provider_failure_escalates_and_is_not_model_behaviour(
            self, seeded_conn):
        client = FailingClient()
        result = _run(client, seeded_conn, SESSION_ACCT_002)
        assert result.answer_state == "ESCALATE"
        assert result.state_reason.startswith("model provider failure")
        assert result.trace["answer_state"] == "ESCALATE"
