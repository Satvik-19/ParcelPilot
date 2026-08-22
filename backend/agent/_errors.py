"""Shared provider error types — no circular imports.

ProviderError is the unified base for all provider failures. Both
GroqAPIError and OpenRouterAPIError inherit from it. The executor catches
ProviderError to handle any provider failure uniformly.
"""


class ProviderError(RuntimeError):
    """Unified provider exception — the executor converts this to ESCALATE.

    Carries optional status_code and provider_name for tracing.
    """

    def __init__(self, message, status_code=None, provider_name=None):
        super().__init__(message)
        self.status_code = status_code
        self.provider_name = provider_name
