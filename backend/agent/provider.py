"""Provider abstraction — Groq (primary) + OpenRouter (fallback) failover.

The executor calls `client.complete(...)` as before; the FallbackProvider
wraps both providers and tries the primary first. On qualifying provider-
level failures (429/5xx/timeout/network), it falls back to OpenRouter.
Application-level failures (invalid tool args, planner rejection, etc.)
are NOT retried — those retain their existing behaviour.

Both providers use the same OpenAI-compatible request/response format, so
the tool-calling loop in executor.py works unchanged. The model never
selects the provider; the runtime does, transparently.
"""

from .config import OPENROUTER_MODEL, load_openrouter_api_key
from ._errors import ProviderError
from .groq_client import GroqAPIError
from .openrouter_client import OpenRouterAPIError, OpenRouterClient

__all__ = ["ProviderError", "FallbackProvider", "make_provider"]


# Provider-level failures that qualify for fallback. Application errors
# (400 Bad Request from invalid tool args, 401/403 from auth, etc.) do NOT
# qualify — those are client-side issues the fallback can't fix.
_FALLBACK_QUALIFYING = (408, 429, 500, 502, 503, 504)


def _qualifies_for_fallback(exc):
    """Return True if the exception is a provider-level failure worth retrying."""
    if isinstance(exc, (GroqAPIError, OpenRouterAPIError)):
        # Network errors (no status_code) always qualify.
        if exc.status_code is None:
            return True
        return exc.status_code in _FALLBACK_QUALIFYING
    return False


class FallbackProvider:
    """Wraps primary + fallback providers with transparent failover.

    The primary provider is always tried first. If it fails with a qualifying
    provider-level error (429/5xx/timeout/network), the fallback is tried once.
    If the fallback also fails, the original primary error is re-raised so
    the executor sees the first failure (not the fallback's).

    If the fallback provider is None (e.g. OPENROUTER_API_KEY not set), the
    primary is used directly with no fallback — the system degrades to Groq-only.
    """

    def __init__(self, primary, fallback=None, fallback_model=None):
        self._primary = primary
        self._fallback = fallback
        self._fallback_model = fallback_model or OPENROUTER_MODEL
        # Metadata for tracing: which provider/model actually answered.
        self.provider_name = "groq"
        self.model_used = None

    def complete(self, messages, tools=None, model=None, temperature=0.0,
                 max_tokens=1024):
        """Try primary; on qualifying failure, try fallback once."""
        try:
            response = self._primary.complete(
                messages, tools=tools, model=model,
                temperature=temperature, max_tokens=max_tokens)
            self.provider_name = "groq"
            self.model_used = model or response.get("model", "unknown")
            return response
        except (GroqAPIError, OpenRouterAPIError) as primary_exc:
            if self._fallback is None or not _qualifies_for_fallback(primary_exc):
                # No fallback available, or this is an application error —
                # re-raise the original exception unchanged.
                raise
            # Qualifying provider failure — try fallback once.
            try:
                response = self._fallback.complete(
                    messages, tools=tools, model=self._fallback_model,
                    temperature=temperature, max_tokens=max_tokens)
                self.provider_name = "openrouter"
                self.model_used = self._fallback_model
                return response
            except (GroqAPIError, OpenRouterAPIError) as fallback_exc:
                # Both providers failed. Re-raise the PRIMARY error so the
                # executor sees the first failure, not the fallback's.
                # Attach fallback detail for debugging.
                raise primary_exc from fallback_exc


def make_provider(groq_client):
    """Construct a FallbackProvider wrapping the given GroqClient.

    If OPENROUTER_API_KEY is configured, the fallback is an OpenRouterClient;
    otherwise fallback is None and the system degrades to Groq-only.
    """
    or_key = load_openrouter_api_key()
    if or_key:
        fallback = OpenRouterClient(api_key=or_key)
    else:
        fallback = None
    return FallbackProvider(groq_client, fallback=fallback)
