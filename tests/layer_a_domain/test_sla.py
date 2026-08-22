"""SLA resolution, breach calculation, and deterministic severity.

Severity is classified by classify_severity() from Policy v3 §2 definitions —
never by an LLM (docs/handoffs/session-00.md known issue). The snapshot day
(2026-08-16) is a Sunday, which these tests exploit for business-time cases.
"""

from datetime import datetime

import pytest

from backend.domain.businesstime import business_minutes_between
from backend.domain.severity import classify_severity
from backend.domain.sla import check_sla_breach, resolve_sla
from backend.domain.timebase import SNAPSHOT_TS

from .conftest import agreement_for


def _ticket(ticket_id, account_id, created_at, subject, description=""):
    return {
        "ticket_id": ticket_id,
        "account_id": account_id,
        "created_at": created_at,
        "status": "OPEN",
        "subject": subject,
        "description": description,
        "channel": "email",
        "assigned_to": None,
        "last_customer_message_at": created_at,
        "historical_resolution": None,
    }


# --- Severity classification (v3 §2) -------------------------------------------

def test_real_tickets_classify_per_policy(tickets_by_id):
    assert classify_severity(tickets_by_id["TKT-501"]).severity == "P1"
    assert classify_severity(tickets_by_id["TKT-505"]).severity == "P1"
    assert classify_severity(tickets_by_id["TKT-503"]).severity == "P3"


def test_credential_exposure_rationale(tickets_by_id):
    result = classify_severity(tickets_by_id["TKT-505"])
    assert "credential" in result.rationale
    assert result.source.endswith("§2")


def test_p2_workaround_clause():
    ticket = _ticket("T", "ACCT-003", SNAPSHOT_TS,
                     "Dashboard degraded",
                     "Major feature is degraded but a workaround exists.")
    assert classify_severity(ticket).severity == "P2"


def test_unmatched_text_defaults_to_p3():
    ticket = _ticket("T", "ACCT-003", SNAPSHOT_TS, "Something odd happened once")
    result = classify_severity(ticket)
    assert result.severity == "P3"
    assert "default" in result.rationale


# --- SLA target resolution -------------------------------------------------------

def test_default_targets_match_v3_table():
    expectations = {
        ("Enterprise", "P1"): (30, "minutes", True),
        ("Enterprise", "P2"): (2, "hours", True),
        ("Growth", "P1"): (2, "business_hours", False),
        ("Growth", "P3"): (2, "business_days", False),
        ("Standard", "P2"): (1, "business_days", False),
    }
    for (plan, severity), (value, unit, calendar) in expectations.items():
        target = resolve_sla({"plan": plan}, severity)
        assert (target.value, target.unit, target.calendar) == (value, unit, calendar)


def test_calendar_targets_advertise_24x7():
    target = resolve_sla({"plan": "Enterprise"}, "P1")
    assert "24x7" in target.display


def test_northstar_agreement_overrides_default_table(accounts_by_id):
    target = resolve_sla(
        accounts_by_id["ACCT-001"], "P1", agreement_for("ACCT-001")
    )
    assert target.target_minutes == 15
    assert target.source.startswith("05_")


def test_unknown_severity_and_plan_raise():
    with pytest.raises(ValueError):
        resolve_sla({"plan": "Enterprise"}, "P0")
    with pytest.raises(ValueError):
        resolve_sla({"plan": "Platinum"}, "P1")


# --- Breach calculation ------------------------------------------------------------

def test_no_breach_inside_calendar_target(accounts_by_id):
    ticket = _ticket("T-NB", "ACCT-001", datetime(2026, 8, 16, 10, 55),
                     "All shipment creation is failing with 500 errors")
    report = check_sla_breach(
        ticket, accounts_by_id["ACCT-001"], agreement_for("ACCT-001")
    )
    assert report.breached is False
    assert report.elapsed_minutes == 5
    assert report.minutes_over_or_remaining == -10  # 10 min remaining
    assert report.must_state_breach is False
    assert report.escalation_required is False


def test_business_time_is_zero_on_sunday(accounts_by_id):
    """LumenWorks P2 = 4 business hours; Sunday 10:00 -> 11:00 earns no business time."""
    ticket = _ticket("T-BT", "ACCT-002", datetime(2026, 8, 16, 10, 0),
                     "Major feature unavailable, no workaround found")
    report = check_sla_breach(
        ticket, accounts_by_id["ACCT-002"], agreement_for("ACCT-002"),
        as_of=datetime(2026, 8, 16, 11, 0),
    )
    assert report.severity.severity == "P2"
    assert report.elapsed_minutes == 0
    assert report.breached is False


def test_business_time_accrues_on_monday(accounts_by_id):
    """P1, Monday 12:00: 180 business minutes > 120-minute target -> breach."""
    ticket = _ticket("T-BT2", "ACCT-002", datetime(2026, 8, 16, 10, 0),
                     "All bulk exports are failing with error messages")
    report = check_sla_breach(
        ticket, accounts_by_id["ACCT-002"], agreement_for("ACCT-002"),
        as_of=datetime(2026, 8, 17, 12, 0),
    )
    assert report.severity.severity == "P1"
    assert report.target.unit == "business_hours"
    assert report.target.target_minutes == 120  # LumenWorks P1 = 2 business hours
    assert report.elapsed_minutes == 180
    assert report.breached is True
    assert report.minutes_over_or_remaining == 60
    assert report.must_state_breach is True


def test_default_as_of_is_snapshot_ts(tickets_by_id, accounts_by_id):
    """Fixed-time behavior: omitting as_of must equal passing SNAPSHOT_TS."""
    implicit = check_sla_breach(
        tickets_by_id["TKT-501"], accounts_by_id["ACCT-001"],
        agreement_for("ACCT-001"),
    )
    explicit = check_sla_breach(
        tickets_by_id["TKT-501"], accounts_by_id["ACCT-001"],
        agreement_for("ACCT-001"), as_of=SNAPSHOT_TS,
    )
    assert implicit == explicit
    assert implicit.as_of == SNAPSHOT_TS


def test_breach_state_flips_with_as_of(tickets_by_id, accounts_by_id):
    """The same ticket is unbreached early and breached later — pure function of as_of."""
    early = check_sla_breach(
        tickets_by_id["TKT-501"], accounts_by_id["ACCT-001"],
        agreement_for("ACCT-001"), as_of=datetime(2026, 8, 16, 10, 40),
    )
    late = check_sla_breach(
        tickets_by_id["TKT-501"], accounts_by_id["ACCT-001"],
        agreement_for("ACCT-001"), as_of=datetime(2026, 8, 16, 11, 0),
    )
    assert early.breached is False
    assert late.breached is True


# --- Business-time primitive -------------------------------------------------------

def test_business_minutes_convention():
    monday = datetime(2026, 8, 17, 9, 0)
    assert business_minutes_between(monday, datetime(2026, 8, 17, 18, 0)) == 540
    sunday = datetime(2026, 8, 16, 9, 0)
    assert business_minutes_between(sunday, datetime(2026, 8, 16, 18, 0)) == 0
    assert business_minutes_between(monday, monday) == 0
