"""Phase 4 — query_operations tool (Layer A).

Golden cases 1–7 re-asserted through the tool envelope, plus the scope
behaviour: computed state only (never raw row dumps), neutral denials that
reveal nothing about existence, and deterministic as_of handling.
"""

from datetime import timedelta

import pytest

from backend.domain.timebase import SNAPSHOT_TS, parse_ts
from backend.tools.query_operations import query_operations


# --- Golden cases through the tool ---------------------------------------------------

def test_case_01_via_tool_northstar_waiver(seeded_db, customer_sessions):
    conn, _ = seeded_db
    env = query_operations(conn, customer_sessions["ACCT-001"], "order", "ORD-1001")
    assert env.status == "ok"
    cancellation = env.result["cancellation"]
    assert cancellation["cancellable"] is True
    assert cancellation["fee_inr"] == 0
    assert cancellation["rule"] == "NORTHSTAR_AGREEMENT_WAIVER"
    assert cancellation["overrides"] == "SOP_S1"


def test_case_02_via_tool_lumenworks_250_fee(seeded_db, customer_sessions):
    conn, _ = seeded_db
    env = query_operations(conn, customer_sessions["ACCT-002"], "order", "ORD-2001")
    assert env.status == "ok"
    cancellation = env.result["cancellation"]
    assert cancellation["fee_inr"] == 250
    assert cancellation["rule"] == "SOP_S1_AFTER_30MIN"
    assert cancellation["overrides"] is None


def test_case_03_via_tool_lumenworks_300_credit(seeded_db, customer_sessions):
    conn, _ = seeded_db
    env = query_operations(conn, customer_sessions["ACCT-002"], "order", "ORD-2002")
    assert env.status == "ok"
    credit = env.result["service_credit"]
    assert credit["result"] == "ELIGIBLE"
    assert credit["credit_inr"] == 300
    assert credit["rule"] == "LUMENWORKS_AGREEMENT_CREDIT"
    assert credit["overrides"] == "SOP_S2"


def test_case_04_via_tool_picked_up_not_cancellable(seeded_db, customer_sessions):
    conn, _ = seeded_db
    env = query_operations(conn, customer_sessions["ACCT-001"], "order", "ORD-1002")
    assert env.status == "ok"
    cancellation = env.result["cancellation"]
    assert cancellation["cancellable"] is False
    assert cancellation["rule"] == "SOP_S1_PICKED_UP"
    assert cancellation["suggested_action"] == "return_to_origin"


def test_case_06_via_tool_p1_breach_escalation(seeded_db, customer_sessions):
    conn, _ = seeded_db
    env = query_operations(conn, customer_sessions["ACCT-001"], "ticket", "TKT-501")
    assert env.status == "ok"
    assert env.result["severity"]["severity"] == "P1"
    sla = env.result["sla"]
    assert sla["breached"] is True
    assert sla["escalation_required"] is True
    assert sla["must_state_breach"] is True
    assert "warning" in env.result  # breach must be stated explicitly


def test_case_07_via_tool_security_incident(seeded_db, staff_session):
    conn, _ = seeded_db
    env = query_operations(conn, staff_session, "ticket", "TKT-505")
    assert env.status == "ok"
    assert env.result["severity"]["severity"] == "P1"
    assert env.result["sla"]["security_incident"] is True
    assert env.result["sla"]["escalation_required"] is True


# --- Scope enforcement ---------------------------------------------------------------

def test_cross_account_order_rejected_for_customer(seeded_db, customer_sessions):
    conn, _ = seeded_db
    env = query_operations(conn, customer_sessions["ACCT-001"], "order", "ORD-2001")
    assert env.status == "rejected"
    assert env.result["rejection_code"] == "ACCESS_DENIED"


def test_cross_account_ticket_rejected_for_customer(seeded_db, customer_sessions):
    conn, _ = seeded_db
    env = query_operations(conn, customer_sessions["ACCT-001"], "ticket", "TKT-505")
    assert env.status == "rejected"
    assert env.result["rejection_code"] == "ACCESS_DENIED"


def test_cross_account_account_lookup_rejected(seeded_db, customer_sessions):
    conn, _ = seeded_db
    env = query_operations(conn, customer_sessions["ACCT-002"], "account", "ACCT-001")
    assert env.status == "rejected"
    assert env.result["rejection_code"] == "ACCESS_DENIED"
    assert "ACCT-001" not in env.result["message"]


def test_missing_entity_is_neutral_for_customers(seeded_db, customer_sessions):
    conn, _ = seeded_db
    # A customer asking about an unknown id gets the SAME denial as a real
    # cross-account id — nothing about existence leaks.
    missing = query_operations(conn, customer_sessions["ACCT-001"], "order", "ORD-9999")
    denied = query_operations(conn, customer_sessions["ACCT-001"], "order", "ORD-2001")
    assert missing.status == denied.status == "rejected"
    assert missing.result == denied.result


def test_missing_entity_is_not_found_for_staff(seeded_db, staff_session):
    conn, _ = seeded_db
    env = query_operations(conn, staff_session, "order", "ORD-9999")
    assert env.status == "error"
    assert env.result["error_code"] == "NOT_FOUND"


def test_staff_can_read_any_account_entity(seeded_db, staff_session):
    conn, _ = seeded_db
    env = query_operations(conn, staff_session, "account", "ACCT-001")
    assert env.status == "ok"
    assert env.result["account_id"] == "ACCT-001"
    assert env.result["plan"]


# --- Input validation ---------------------------------------------------------------

@pytest.mark.parametrize("entity", ["invoice", "", None])
def test_unknown_entity_is_structured_error(seeded_db, staff_session, entity):
    conn, _ = seeded_db
    env = query_operations(conn, staff_session, entity, "X")
    assert env.status == "error"
    assert env.result["error_code"] == "INVALID_INPUT"


def test_malformed_session_rejected(seeded_db):
    conn, _ = seeded_db
    env = query_operations(conn, {"role": "root"}, "order", "ORD-1001")
    assert env.status == "rejected"
    assert env.result["rejection_code"] == "INVALID_SESSION"


# --- Deterministic as_of ---------------------------------------------------------------

def test_breach_state_follows_explicit_as_of(seeded_db, tickets_by_id, staff_session):
    conn, _ = seeded_db
    ticket = tickets_by_id["TKT-501"]
    early = parse_ts(ticket["created_at"]) + timedelta(minutes=5)
    env = query_operations(conn, staff_session, "ticket", "TKT-501", as_of=early)
    assert env.status == "ok"
    assert env.result["sla"]["breached"] is False

    env = query_operations(conn, staff_session, "ticket", "TKT-501", as_of=SNAPSHOT_TS)
    assert env.result["sla"]["breached"] is True


def test_default_as_of_is_snapshot_ts(seeded_db, staff_session):
    conn, _ = seeded_db
    default = query_operations(conn, staff_session, "ticket", "TKT-501")
    explicit = query_operations(conn, staff_session, "ticket", "TKT-501",
                                as_of=SNAPSHOT_TS)
    assert default.result == explicit.result
