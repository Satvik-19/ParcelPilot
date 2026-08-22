"""Minimal Groq client — stdlib only (OpenAI-compatible chat completions).

A hand-rolled client keeps the runtime framework-free (ADR-002) and adds no
dependency beyond the locked requirements.txt. Retries transient failures
(rate limits / server errors) with a small deterministic backoff.
"""

import json
import time
import urllib.error
import urllib.request

from .config import CHOSEN_MODEL, GROQ_BASE_URL, load_groq_api_key
from ._errors import ProviderError

# Groq's edge rejects the default urllib UA (Cloudflare error 1010); the
# client must always identify itself.
_USER_AGENT = "ParcelPilot/1.0 (support-agent runtime)"

_RETRYABLE = (408, 429, 500, 502, 503, 504)
_MAX_ATTEMPTS = 4
_NETWORK_MAX_ATTEMPTS = 2  # timeouts/network — retry once, then fail fast to fallback
_BACKOFF_SECONDS = 3.0


class GroqAPIError(ProviderError):
    """Groq-specific provider failure — inherits ProviderError for uniform catch."""

    def __init__(self, message, status_code=None):
        super().__init__(message, status_code=status_code,
                         provider_name="groq")


class GroqClient:
    def __init__(self, api_key=None, base_url=GROQ_BASE_URL):
        self._api_key = api_key or load_groq_api_key()
        self._base_url = base_url.rstrip("/")

    def complete(self, messages, tools=None, model=None, temperature=0.0,
                 max_tokens=1024):
        """One chat completion. Returns the raw response dict (choices[0]...)."""
        payload = {
            "model": model or CHOSEN_MODEL,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        body = json.dumps(payload).encode("utf-8")

        last_error = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            request = urllib.request.Request(
                f"{self._base_url}/chat/completions",
                data=body,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": _USER_AGENT,
                    "Accept": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=90) as response:
                    return json.load(response)
            except urllib.error.HTTPError as err:
                detail = err.read().decode("utf-8", "replace")[:300]
                last_error = GroqAPIError(
                    f"Groq API error {err.code}: {detail}", status_code=err.code
                )
                if err.code in _RETRYABLE and attempt < _MAX_ATTEMPTS:
                    time.sleep(_BACKOFF_SECONDS * attempt)
                    continue
                raise last_error from err
            except urllib.error.URLError as err:
                last_error = GroqAPIError(f"Groq unreachable: {err.reason}")
                if attempt < _NETWORK_MAX_ATTEMPTS:
                    time.sleep(_BACKOFF_SECONDS * attempt)
                    continue
                raise last_error from err
        raise last_error  # pragma: no cover — loop always returns or raises
