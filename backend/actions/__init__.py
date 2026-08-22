"""Trusted action lifecycle: drafting lives in tools/prepare_support_action,
confirmation/execution here — both outside the LLM's tool surface (ADR-004)."""

from .confirm import confirm_support_action, rejection

__all__ = ["confirm_support_action", "rejection"]
