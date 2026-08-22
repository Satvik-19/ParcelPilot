"""Tool layer (IMPLEMENTATION_PLAN Phase 4).

Each tool is a thin wrapper over domain/ + security/ + trust/ (ADR-003):
the model decides WHEN to ask, the trusted layer decides what is true and
what is allowed. Every tool returns the common envelope
{status, result, evidence[], warnings[]} (03_AGENT_SPEC.md §3) and receives
the session injected server-side — tool schemas expose no identity
parameters. No golden-case answers live here (05_CODING_AGENT_RULES.md §6).
"""

from ._envelope import ToolEnvelope, envelope_error, envelope_ok, envelope_rejected

__all__ = ["ToolEnvelope", "envelope_ok", "envelope_rejected", "envelope_error"]
