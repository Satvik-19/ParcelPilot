"""SLA target resolution and breach calculation.

Sources, in authority order (docs/02_DOMAIN_SPEC.md §2):
  1. active customer agreement SLA table (per account);
  2. Support Policy v3 default table per plan.
Deprecated Policy v2 is NEVER consulted (GI-1) — its numbers do not exist
anywhere in the domain layer.

Business-time targets use the documented convention in businesstime.py.
`minutes_over_or_remaining` is positive when breached (minutes over target)
and negative when not (minutes remaining).
"""

from dataclasses import dataclass
from datetime import datetime

from .businesstime import BUSINESS_MINUTES_PER_DAY, business_minutes_between
from .policy_data import DEFAULT_SLA_SOURCE, DEFAULT_SLA_TABLE
from .severity import SeverityResult, classify_severity
from .timebase import SNAPSHOT_TS, parse_ts

_UNIT_MINUTES = {
    "minutes": 1,
    "hours": 60,
    "business_hours": 60,
    "business_days": BUSINESS_MINUTES_PER_DAY,
}

_UNIT_DISPLAY = {
    "minutes": "minutes",
    "hours": "hours",
    "business_hours": "business hours",
    "business_days": "business days",
}


@dataclass(frozen=True)
class SlaTarget:
    severity: str
    value: int
    unit: str
    calendar: bool          # True -> wall-clock elapsed time applies
    target_minutes: int
    display: str
    source: str             # document + section the target came from


@dataclass(frozen=True)
class BreachReport:
    ticket_id: str
    severity: SeverityResult
    target: SlaTarget
    breached: bool
    elapsed_minutes: int            # calendar or business, per target.calendar
    minutes_over_or_remaining: int  # >0 over (breached), <0 remaining
    must_state_breach: bool         # Support Policy v3 §4: state it explicitly
    security_incident: bool         # P1 classification matched a credential clause
    escalation_required: bool       # P1 breach -> escalation (04_EVAL_SPEC case 7)
    as_of: datetime


def _build_target(severity, raw, source):
    value, unit, calendar = raw
    display = f"{value} {_UNIT_DISPLAY[unit]}"
    if calendar and unit in ("minutes", "hours"):
        display += ", 24x7"
    return SlaTarget(
        severity=severity,
        value=value,
        unit=unit,
        calendar=calendar,
        target_minutes=value * _UNIT_MINUTES[unit],
        display=display,
        source=source,
    )


def resolve_sla(account, ticket_severity, agreement=None):
    """Return the SlaTarget for an account plan/severity. Never uses v2."""
    if ticket_severity not in ("P1", "P2", "P3"):
        raise ValueError(f"unknown severity: {ticket_severity!r}")
    if agreement and ticket_severity in agreement.get("sla", {}):
        sla = agreement["sla"]
        return _build_target(ticket_severity, sla[ticket_severity], sla["source"])
    plan = account["plan"]
    if plan not in DEFAULT_SLA_TABLE:
        raise ValueError(f"unknown plan: {plan!r}")
    return _build_target(
        ticket_severity,
        DEFAULT_SLA_TABLE[plan][ticket_severity],
        f"{DEFAULT_SLA_SOURCE} ({plan} default)",
    )


def check_sla_breach(ticket, account, agreement=None, as_of=SNAPSHOT_TS):
    """Compute the SLA breach state for a ticket at `as_of`."""
    as_of = parse_ts(as_of)
    severity = classify_severity(ticket)
    target = resolve_sla(account, severity.severity, agreement)
    created = parse_ts(ticket["created_at"])

    if target.calendar:
        elapsed = int((as_of - created).total_seconds() // 60)
    else:
        elapsed = business_minutes_between(created, as_of)

    delta = elapsed - target.target_minutes
    breached = delta > 0
    security_incident = "credential" in severity.rationale
    return BreachReport(
        ticket_id=ticket["ticket_id"],
        severity=severity,
        target=target,
        breached=breached,
        elapsed_minutes=elapsed,
        minutes_over_or_remaining=delta,
        must_state_breach=breached,  # v3 §4: never soften or hide a breach
        security_incident=security_incident,
        escalation_required=breached and severity.severity == "P1",
        as_of=as_of,
    )
