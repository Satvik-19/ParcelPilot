"""OpenRouter client — stdlib only (OpenAI-compatible chat completions).

OpenRouter provides a free-tier router (`openrouter/free`) that dynamically
selects available free models. This client uses the same request/response
format as Groq (OpenAI-compatible), so the executor's tool-calling loop
works unchanged. Retries transient failures with backoff, same as Groq.
"""

import json
import time
import urllib.error
import urllib.request

from .config import OPENROUTER_BASE_URL, load_openrouter_api_key
from ._errors import ProviderError

_USER_AGENT = "ParcelPilot/1.0 (support-agent runtime)"

_RETRYABLE = (408, 429, 500, 502, 503, 504)
_MAX_ATTEMPTS = 3
_BACKOFF_SECONDS = 2.0


class OpenRouterAPIError(ProviderError):
    """OpenRouter-specific provider failure — inherits ProviderError."""

    def __init__(self, message, status_code=None):
        super().__init__(message, status_code=status_code,
                         provider_name="openrouter")


class OpenRouterClient:
    def __init__(self, api_key=None, base_url=OPENROUTER_BASE_URL):
        self._api_key = api_key or load_openrouter_api_key()
        self._base_url = base_url.rstrip("/")

    def complete(self, messages, tools=None, model=None, temperature=0.0,
                 max_tokens=1024):
        """One chat completion. Returns the raw response dict (choices[0]...)."""
        payload = {
            "model": model or "openrouter/free",
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
                with urllib.request.urlopen(request, timeout=120) as response:
                    return json.load(response)
            except urllib.error.HTTPError as err:
                detail = err.read().decode("utf-8", "replace")[:300]
                last_error = OpenRouterAPIError(
                    f"OpenRouter API error {err.code}: {detail}", status_code=err.code
                )
                if err.code in _RETRYABLE and attempt < _MAX_ATTEMPTS:
                    time.sleep(_BACKOFF_SECONDS * attempt)
                    continue
                raise last_error from err
            except urllib.error.URLError as err:
                last_error = OpenRouterAPIError(f"OpenRouter unreachable: {err.reason}")
                if attempt < _MAX_ATTEMPTS:
                    time.sleep(_BACKOFF_SECONDS * attempt)
                    continue
                raise last_error from err
        raise last_error  # pragma: no cover
