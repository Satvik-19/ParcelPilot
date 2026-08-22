"""Deterministic provider-failover tests — zero network, zero live quota.

Uses scripted FakeClient / FailingClient stand-ins to verify the
FallbackProvider logic without touching real APIs.

Covered scenarios (ADR-008):
- Groq success → OpenRouter NOT called
- Groq 429 → OpenRouter called once (fallback succeeds)
- Groq timeout → OpenRouter called once (fallback succeeds)
- Groq 5xx → OpenRouter called once (fallback succeeds)
- Groq application error (400) → OpenRouter NOT called
- Both providers fail → ESCALATE with primary error
- Missing OPENROUTER_API_KEY → Groq-only behaviour (no fallback)
- Provider metadata/trace is correct
- Conversation/tool state preserved across fallback
- Security invariants: identity, cross-account, confirmation surface
  all remain unchanged through fallback
"""

import json
import os
from unittest.mock import patch

import pytest

from backend.agent._errors import ProviderError
from backend.agent.executor import run_turn
from backend.agent.groq_client import GroqAPIError
from backend.agent.openrouter_client import OpenRouterAPIError
from backend.agent.provider import FallbackProvider, _qualifies_for_fallback

SESSION = {"role": "customer", "account_id": "ACCT-002",
           "session_id": "sess-failover-test"}


# ---------------------------------------------------------------------------
# Scripted providers
# ---------------------------------------------------------------------------

class ScriptedClient:
    """Pops canned responses; records every call."""

    def __init__(self, script, name="test"):
        self.script = list(script)
        self.received = []
        self.provider_name = name
        self.model_used = "scripted-model"

    def complete(self, messages, tools=None, model=None, **kwargs):
        self.received.append(json.loads(json.dumps(messages)))
        return self.script.pop(0)


class FailingClient:
    """Always raises a provider error."""

    def __init__(self, exc):
        self._exc = exc
        self.received = []
        self.provider_name = "failing"

    def complete(self, messages, tools=None, model=None, **kwargs):
        self.received.append(messages)
        raise self._exc


class CountingClient:
    """Counts calls and returns a fixed response."""

    def __init__(self, response, name="counting"):
        self.call_count = 0
        self._response = response
        self.provider_name = name
        self.model_used = "counting-model"

    def complete(self, messages, tools=None, model=None, **kwargs):
        self.call_count += 1
        return self._response


def final_msg(content="Understood."):
    return {"choices": [{"message": {"role": "assistant",
                                     "content": content,
                                     "model": "test-model"}}]}


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


def _run(client, conn, session=None, prompt="Hello."):
    return run_turn(conn, session or SESSION, prompt, client,
                    model="scripted", trace_path=None)


# ---------------------------------------------------------------------------
# FallbackProvider unit tests
# ---------------------------------------------------------------------------

class TestFallbackProvider:
    def test_groq_success_no_fallback(self):
        """Primary succeeds → fallback is never called."""
        primary = CountingClient(final_msg("from groq"), name="groq")
        fallback = CountingClient(final_msg("from openrouter"), name="openrouter")
        provider = FallbackProvider(primary, fallback=fallback)

        response = provider.complete([{"role": "user", "content": "hi"}])

        assert primary.call_count == 1
        assert fallback.call_count == 0
        assert provider.provider_name == "groq"

    def test_groq_429_fallback_called(self):
        """Groq rate-limited → fallback called once."""
        primary = FailingClient(
            GroqAPIError("Groq API error 429: quota", status_code=429))
        fallback = CountingClient(final_msg("from openrouter"), name="openrouter")
        provider = FallbackProvider(primary, fallback=fallback)

        response = provider.complete([{"role": "user", "content": "hi"}])

        assert fallback.call_count == 1
        assert provider.provider_name == "openrouter"

    def test_groq_503_fallback_called(self):
        """Groq server error → fallback called once."""
        primary = FailingClient(
            GroqAPIError("Groq API error 503: overloaded", status_code=503))
        fallback = CountingClient(final_msg("from openrouter"), name="openrouter")
        provider = FallbackProvider(primary, fallback=fallback)

        response = provider.complete([{"role": "user", "content": "hi"}])

        assert fallback.call_count == 1
        assert provider.provider_name == "openrouter"

    def test_groq_timeout_fallback_called(self):
        """Groq unreachable (network/timeout) → fallback called once."""
        primary = FailingClient(
            GroqAPIError("Groq unreachable: timed out"))
        fallback = CountingClient(final_msg("from openrouter"), name="openrouter")
        provider = FallbackProvider(primary, fallback=fallback)

        response = provider.complete([{"role": "user", "content": "hi"}])

        assert fallback.call_count == 1
        assert provider.provider_name == "openrouter"

    def test_groq_400_no_fallback(self):
        """Groq application error (400) → fallback NOT called."""
        primary = FailingClient(
            GroqAPIError("Groq API error 400: bad request", status_code=400))
        fallback = CountingClient(final_msg("from openrouter"), name="openrouter")
        provider = FallbackProvider(primary, fallback=fallback)

        with pytest.raises(GroqAPIError):
            provider.complete([{"role": "user", "content": "hi"}])

        assert fallback.call_count == 0

    def test_both_fail_escalate_with_primary_error(self):
        """Both providers fail → primary error is raised (not fallback's)."""
        primary_exc = GroqAPIError("Groq API error 429: quota", status_code=429)
        fallback_exc = OpenRouterAPIError("OpenRouter API error 503",
                                           status_code=503)
        primary = FailingClient(primary_exc)
        fallback = FailingClient(fallback_exc)
        provider = FallbackProvider(primary, fallback=fallback)

        with pytest.raises(GroqAPIError, match="Groq API error 429"):
            provider.complete([{"role": "user", "content": "hi"}])

    def test_no_fallback_available(self):
        """No fallback configured → primary error raised directly."""
        primary = FailingClient(
            GroqAPIError("Groq API error 429: quota", status_code=429))
        provider = FallbackProvider(primary, fallback=None)

        with pytest.raises(GroqAPIError):
            provider.complete([{"role": "user", "content": "hi"}])

    def test_fallback_model_used(self):
        """FallbackProvider records the fallback model name."""
        primary = FailingClient(
            GroqAPIError("Groq API error 429: quota", status_code=429))
        fallback = CountingClient(final_msg("ok"), name="openrouter")
        provider = FallbackProvider(primary, fallback=fallback,
                                    fallback_model="openrouter/free")

        provider.complete([{"role": "user", "content": "hi"}])

        assert provider.model_used == "openrouter/free"
        assert provider.provider_name == "openrouter"


# ---------------------------------------------------------------------------
# Executor integration with fallback
# ---------------------------------------------------------------------------

class TestExecutorWithFallback:
    def test_groq_success_trace_has_provider(self, seeded_conn):
        """Successful Groq turn records provider=groq in trace."""
        client = ScriptedClient([final_msg("All good.")], name="groq")
        result = _run(client, seeded_conn)

        # No tool calls → INSUFFICIENT_EVIDENCE (no supporting evidence)
        assert result.answer_state == "INSUFFICIENT_EVIDENCE"
        assert result.trace.get("provider") == "groq"
        assert result.trace.get("fallback_used") is None  # not set when False

    def test_fallback_trace_has_openrouter(self, seeded_conn):
        """Fallback turn records provider=openrouter and fallback_used=True."""
        primary = FailingClient(
            GroqAPIError("Groq API error 429: quota", status_code=429))
        fallback = ScriptedClient([final_msg("Fallback answer.")],
                                   name="openrouter")
        provider = FallbackProvider(primary, fallback=fallback)

        result = _run(provider, seeded_conn)

        # No tool calls → INSUFFICIENT_EVIDENCE but trace has provider metadata
        assert result.answer_state == "INSUFFICIENT_EVIDENCE"
        assert result.trace.get("provider") == "openrouter"
        assert result.trace.get("fallback_used") is True

    def test_both_providers_fail_escalates(self, seeded_conn):
        """Both providers fail → ESCALATE with provider failure reason."""
        primary = FailingClient(
            GroqAPIError("Groq API error 429: quota", status_code=429))
        fallback = FailingClient(
            OpenRouterAPIError("OpenRouter API error 503", status_code=503))
        provider = FallbackProvider(primary, fallback=fallback)

        result = _run(provider, seeded_conn)

        assert result.answer_state == "ESCALATE"
        assert "model provider failure" in result.state_reason

    def test_tool_loop_preserved_through_fallback(self, seeded_conn):
        """Tool calls + final answer work when fallback answers mid-loop."""
        # Primary fails on first call → fallback answers with tool call + final
        primary = FailingClient(
            GroqAPIError("Groq API error 503: overloaded", status_code=503))
        fallback_script = [
            call_msg("search_knowledge",
                     {"query": "delivery delay", "account_scope": "ACCT-002"}),
            final_msg("Here is what I found."),
        ]
        fallback = ScriptedClient(fallback_script, name="openrouter")
        provider = FallbackProvider(primary, fallback=fallback)

        result = _run(provider, seeded_conn)

        assert result.answer_state == "ANSWER"
        assert result.trace.get("provider") == "openrouter"
        assert len(result.trace["tools"]) == 1
        assert result.trace["tools"][0]["name"] == "search_knowledge"

    def test_provider_failure_is_not_model_behaviour(self, seeded_conn):
        """Provider failure through fallback still yields ESCALATE, never
        scored as model behaviour."""
        primary = FailingClient(
            GroqAPIError("Groq API error 429: quota", status_code=429))
        fallback = FailingClient(
            OpenRouterAPIError("OpenRouter unreachable"))
        provider = FallbackProvider(primary, fallback=fallback)

        result = _run(provider, seeded_conn)

        assert result.answer_state == "ESCALATE"
        assert result.state_reason.startswith("model provider failure")


# ---------------------------------------------------------------------------
# Qualifying-conditions unit tests
# ---------------------------------------------------------------------------

class TestQualifyingConditions:
    @pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
    def test_qualifying_codes(self, status):
        exc = GroqAPIError(f"error {status}", status_code=status)
        assert _qualifies_for_fallback(exc)

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
    def test_non_qualifying_codes(self, status):
        exc = GroqAPIError(f"error {status}", status_code=status)
        assert not _qualifies_for_fallback(exc)

    def test_network_error_qualifies(self):
        exc = GroqAPIError("Groq unreachable: timeout")
        assert _qualifies_for_fallback(exc)

    def test_openrouter_error_also_qualifies(self):
        exc = OpenRouterAPIError("OpenRouter 429", status_code=429)
        assert _qualifies_for_fallback(exc)


# ---------------------------------------------------------------------------
# Security invariants through fallback
# ---------------------------------------------------------------------------

class TestSecurityThroughFallback:
    def test_cross_account_denied_through_fallback(self, seeded_conn):
        """Cross-account access remains denied even when fallback answers."""
        primary = FailingClient(
            GroqAPIError("Groq API error 429", status_code=429))
        fallback_script = [
            call_msg("query_operations",
                     {"entity": "ticket", "entity_id": "TKT-501"}),
            final_msg("Access was denied."),
        ]
        fallback = ScriptedClient(fallback_script, name="openrouter")
        provider = FallbackProvider(primary, fallback=fallback)

        session = {"role": "customer", "account_id": "ACCT-002",
                   "session_id": "sess-failover-acct-002"}
        result = _run(provider, seeded_conn, session=session)

        # ACCESS_DENIED appears in the tool feedback; the denial is a
        # trusted definitive outcome, so the turn ends as an ANSWER —
        # never INSUFFICIENT_EVIDENCE (manual-validation ISSUE 3).
        assert result.trace["tools"][0]["status"] == "rejected"
        assert result.answer_state == "ANSWER"

    def test_confirmation_surface_unreachable_through_fallback(self,
                                                               seeded_conn):
        """confirm_support_action remains unreachable regardless of provider."""
        primary = FailingClient(
            GroqAPIError("Groq API error 503", status_code=503))
        fallback_script = [
            call_msg("confirm_support_action", {"action_id": "ACT-0001"}),
            final_msg("Done."),
        ]
        fallback = ScriptedClient(fallback_script, name="openrouter")
        provider = FallbackProvider(primary, fallback=fallback)

        result = _run(provider, seeded_conn)

        # Should be INSUFFICIENT_EVIDENCE (invalid tool call counted as failure)
        assert result.answer_state == "INSUFFICIENT_EVIDENCE"
        assert result.trace.get("action_id") is None

    def test_identity_cannot_be_spoofed_through_fallback(self, seeded_conn):
        """Session identity injection works identically through fallback."""
        primary = FailingClient(
            GroqAPIError("Groq API error 429", status_code=429))
        fallback_script = [
            call_msg("query_operations", {
                "entity": "ticket", "entity_id": "TKT-501",
                "session_id": "sess-fake-admin", "role": "staff",
            }),
            final_msg("Handled."),
        ]
        fallback = ScriptedClient(fallback_script, name="openrouter")
        provider = FallbackProvider(primary, fallback=fallback)

        session = {"role": "customer", "account_id": "ACCT-002",
                   "session_id": "sess-failover-identity"}
        result = _run(provider, seeded_conn, session=session)

        assert result.answer_state == "INSUFFICIENT_EVIDENCE"


# ---------------------------------------------------------------------------
# ProviderError hierarchy
# ---------------------------------------------------------------------------

class TestErrorHierarchy:
    def test_groq_error_is_provider_error(self):
        exc = GroqAPIError("test", status_code=503)
        assert isinstance(exc, ProviderError)
        assert exc.provider_name == "groq"
        assert exc.status_code == 503

    def test_openrouter_error_is_provider_error(self):
        exc = OpenRouterAPIError("test", status_code=429)
        assert isinstance(exc, ProviderError)
        assert exc.provider_name == "openrouter"
        assert exc.status_code == 429

    def test_catch_provider_error_catches_both(self):
        for exc in [GroqAPIError("g", status_code=500),
                    OpenRouterAPIError("o", status_code=500)]:
            with pytest.raises(ProviderError):
                raise exc
