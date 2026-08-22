"""The common tool envelope (03_AGENT_SPEC.md §3).

status: "ok" | "rejected" | "error"
  - ok:       authoritative result computed from trusted data/domain logic;
  - rejected: a deterministic security/scope/staff decision (never a guess);
  - error:    structured, non-crashing failure the agent can react to.
Errors are returned as structured results, never raw stack traces.
"""

from dataclasses import dataclass, field
from typing import Any, Tuple

from backend.security.authorization import AuthorizationError


@dataclass(frozen=True)
class ToolEnvelope:
    status: str
    result: Any = None
    evidence: Tuple = field(default_factory=tuple)
    warnings: Tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self):
        return {
            "status": self.status,
            "result": self.result,
            "evidence": list(self.evidence),
            "warnings": list(self.warnings),
        }


def envelope_ok(result, evidence=(), warnings=()):
    return ToolEnvelope("ok", result=result, evidence=tuple(evidence),
                        warnings=tuple(warnings))


def envelope_rejected(code, message):
    return ToolEnvelope("rejected", result={"rejection_code": code, "message": message})


def envelope_error(code, message):
    return ToolEnvelope("error", result={"error_code": code, "message": message})


def rejected_from(exc):
    """Turn an AuthorizationError into the structured rejection envelope."""
    assert isinstance(exc, AuthorizationError)
    return envelope_rejected(exc.code, exc.message)
