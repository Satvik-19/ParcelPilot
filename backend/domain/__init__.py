"""Pure, deterministic PolicyResolver (ADR-003). No LLM, no I/O, no framework."""

from .timebase import SNAPSHOT_TS, parse_ts
from .severity import SeverityResult, classify_severity
from .cancellation import CancellationDecision, resolve_cancellation_fee
from .credits import (
    ELIGIBLE,
    INSUFFICIENT_EVIDENCE,
    NOT_ELIGIBLE,
    CreditDecision,
    resolve_service_credit,
)
from .sla import BreachReport, SlaTarget, check_sla_breach, resolve_sla
from .known_issues import KnownIssueMatch, match_known_issue

__all__ = [
    "SNAPSHOT_TS",
    "parse_ts",
    "SeverityResult",
    "classify_severity",
    "CancellationDecision",
    "resolve_cancellation_fee",
    "CreditDecision",
    "resolve_service_credit",
    "ELIGIBLE",
    "NOT_ELIGIBLE",
    "INSUFFICIENT_EVIDENCE",
    "SlaTarget",
    "BreachReport",
    "resolve_sla",
    "check_sla_breach",
    "KnownIssueMatch",
    "match_known_issue",
]
