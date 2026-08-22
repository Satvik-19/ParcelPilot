"""Deterministic ticket severity classification (Support Policy v3 §2).

The workbook carries no severity column, so severity is derived from the
ticket's subject + description using the policy's own definitions — never by
an LLM (docs/handoffs/session-00.md, IMPLEMENTATION_PLAN Phase 2).

v3 §2 definitions:
  P1 Critical — complete production outage preventing all shipment creation;
                confirmed security incident or suspected credential exposure;
                or immediate material business risk with no workaround.
  P2 High     — major feature unavailable or materially degraded, but core
                operations remain possible or a workaround exists.
  P3 Normal   — minor defect, how-to question, configuration request, or
                issue with limited operational impact.
"""

import re
from dataclasses import dataclass

from .policy_data import POLICY_V3_DOC

_SOURCE = f"{POLICY_V3_DOC} §2"

# Ordered rules: first match wins. Each entry: (compiled pattern, severity,
# human-readable definition clause matched).
_P1_RULES = [
    (
        re.compile(
            r"(api[ -]?key|credential|secret|token).{0,40}(expos|leak|post|public|compromis)",
            re.IGNORECASE,
        ),
        "suspected credential exposure",
    ),
    (
        re.compile(
            r"(expos|leak|compromis).{0,40}(api[ -]?key|credential|secret|token)",
            re.IGNORECASE,
        ),
        "suspected credential exposure",
    ),
    (re.compile(r"security incident", re.IGNORECASE), "confirmed security incident"),
    (
        re.compile(
            r"\b(all|every)\b.{0,60}\b(failing|fails|failed|failure|500|error|down|outage)\b",
            re.IGNORECASE,
        ),
        "complete production outage preventing all shipment creation",
    ),
    (
        re.compile(r"(complete|total)\s+(production\s+)?outage", re.IGNORECASE),
        "complete production outage preventing all shipment creation",
    ),
]

_P2_RULES = [
    (
        re.compile(r"(major|core).{0,40}(unavailable|degrad)", re.IGNORECASE),
        "major feature unavailable or materially degraded",
    ),
    (
        re.compile(r"(still works|workaround|one-by-one|one by one)", re.IGNORECASE),
        "major degradation, but core operations remain possible or a workaround exists",
    ),
    (
        re.compile(r"(intermittent|some\s.{0,30}fail)", re.IGNORECASE),
        "major feature materially degraded with partial availability",
    ),
]

_P3_RULES = [
    (
        re.compile(r"how (do|to|can)|billing|contact|configur|question", re.IGNORECASE),
        "how-to question or configuration request",
    ),
]


@dataclass(frozen=True)
class SeverityResult:
    severity: str          # "P1" | "P2" | "P3"
    rationale: str         # which definition clause matched (or default)
    source: str = _SOURCE  # policy citation


def classify_severity(ticket):
    """Classify a ticket dict ({subject, description, ...}) deterministically."""
    text = " ".join(
        str(ticket.get(field) or "") for field in ("subject", "description")
    )
    for pattern, clause in _P1_RULES:
        if pattern.search(text):
            return SeverityResult("P1", f"P1 — {clause}")
    for pattern, clause in _P2_RULES:
        if pattern.search(text):
            return SeverityResult("P2", f"P2 — {clause}")
    for pattern, clause in _P3_RULES:
        if pattern.search(text):
            return SeverityResult("P3", f"P3 — {clause}")
    return SeverityResult(
        "P3", "P3 — default: no P1/P2 definition matched; limited operational impact assumed"
    )
