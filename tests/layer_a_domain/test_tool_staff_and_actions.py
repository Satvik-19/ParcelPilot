"""Phase 4 — analyze_support_activity (staff-only) + prepare_support_action
(draft-only) + golden-answer hygiene (Layer A).

ADR-004: prepare_support_action must only persist a pending draft bound to
the session; nothing executes, no state changes, and re-preparing the same
action is deterministic. The hygiene scan guards 05_CODING_AGENT_RULES §6:
no golden-case answers may ever live in the tool/security/trust layers.
"""

import re
from datetime import timedelta
from pathlib import Path

import pytest

from backend.db.database import open_database
from backend.db.seed import seed_database
from backend.domain.timebase import SNAPSHOT_TS, parse_ts
from backend.tools.analyze_support_activity import analyze_support_activity
from backend.tools.prepare_support_action import prepare_support_action

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_PACK = PROJECT_ROOT / "assessment_docs"


@pytest.fixture(scope="module")
def writable_db(tmp_path_factory):
    """A private seeded DB the action tests may write to."""
    db_path = tmp_path_factory.mktemp("actions_db") / "parcel_pilot.db"
    seed_database(db_path, DATA_PACK)
    conn = open_database(db_path)
    yield conn
    conn.close()


# --- analyze_support_activity: staff-only --------------------------------------

def test_analyze_rejected_for_customer_sessions(seeded_db, customer_sessions):
    conn, _ = seeded_db
    env = analyze_support_activity(conn, customer_sessions["ACCT-001"])
    assert env.status == "rejected"
    assert env.result["rejection_code"] == "STAFF_ONLY"


def test_analyze_rejected_for_malformed_session(seeded_db):
    conn, _ = seeded_db
    env = analyze_support_activity(conn, {"role": "customer"})
    assert env.status == "rejected"
    assert env.result["rejection_code"] == "INVALID_SESSION"


def test_analyze_staff_sees_all_accounts_and_consistent_summary(seeded_db, staff_session):
    conn, _ = seeded_db
    env = analyze_support_activity(conn, staff_session)
    assert env.status == "ok"
    result = env.result
    all_accounts = {r["account_id"] for r in conn.execute("SELECT account_id FROM accounts")}
    assert set(result["visible_accounts"]) == all_accounts
    assert result["summary"]["tickets_in_scope"] == 7
    assert result["summary"]["breached_count"] == sum(
        1 for item in result["sla_status"] if item["breached"]
    )
    assert result["summary"]["escalations_required"] == sum(
        1 for item in result["sla_status"] if item["escalation_required"]
    )


def test_analyze_clusters_are_deterministic(seeded_db, staff_session):
    conn, _ = seeded_db
    first = analyze_support_activity(conn, staff_session).result["clusters"]
    second = analyze_support_activity(conn, staff_session).result["clusters"]
    assert first == second
    assert "TKT-502" in first["bulk upload"]


def test_analyze_respects_explicit_as_of(seeded_db, tickets_by_id, staff_session):
    conn, _ = seeded_db
    early = parse_ts(tickets_by_id["TKT-501"]["created_at"]) + timedelta(minutes=1)
    env = analyze_support_activity(conn, staff_session, as_of=early)
    tkt501 = next(i for i in env.result["sla_status"] if i["ticket_id"] == "TKT-501")
    assert tkt501["breached"] is False  # at creation+1min nothing can breach


# --- prepare_support_action: draft-only ------------------------------------------

def test_prepare_persists_pending_draft_only(writable_db, customer_sessions):
    before = {
        table: writable_db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
        for table in ("accounts", "orders", "tickets")
    }
    env = prepare_support_action(
        writable_db, customer_sessions["ACCT-001"], "request_cancellation",
        {"account_id": "ACCT-001", "order_id": "ORD-1001"},
    )
    assert env.status == "ok"
    result = env.result
    assert result["status"] == "pending"
    assert result["session_id"] == "sess-acct-001"
    assert len(result["payload_hash"]) == 64
    assert len(result["token"]) == 64
    assert result["expires_at"] == format(parse_ts(result["created_at"]) + timedelta(minutes=5), "%Y-%m-%d %H:%M")
    assert "Draft only" in result["rendered_card"]

    # The draft row exists, and NOTHING else changed.
    rows = writable_db.execute("SELECT * FROM actions").fetchall()
    assert len(rows) == 1 and rows[0]["status"] == "pending"
    after = {
        table: writable_db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
        for table in ("accounts", "orders", "tickets")
    }
    assert before == after  # no execution, no state change
    status = writable_db.execute(
        "SELECT status FROM orders WHERE order_id = 'ORD-1001'"
    ).fetchone()[0]
    assert status == "BOOKED"  # the order was NOT cancelled


def test_prepare_is_deterministic_and_idempotent(writable_db, customer_sessions):
    payload = {"account_id": "ACCT-001", "order_id": "ORD-1001", "reason": "duplicate"}
    first = prepare_support_action(writable_db, customer_sessions["ACCT-001"],
                                   "request_cancellation", payload)
    second = prepare_support_action(writable_db, customer_sessions["ACCT-001"],
                                    "request_cancellation", payload)
    assert first.status == second.status == "ok"
    assert first.result["action_id"] == second.result["action_id"]
    assert first.result["token"] == second.result["token"]
    assert writable_db.execute("SELECT COUNT(*) FROM actions").fetchone()[0] == 2


def test_prepare_expiry_follows_explicit_as_of(writable_db, staff_session):
    as_of = SNAPSHOT_TS + timedelta(hours=1)
    env = prepare_support_action(
        writable_db, staff_session, "grant_service_credit",
        {"account_id": "ACCT-002", "order_id": "ORD-2002", "amount_inr": 300},
        as_of=as_of,
    )
    assert env.status == "ok"
    assert env.result["created_at"] == format(as_of, "%Y-%m-%d %H:%M")
    assert env.result["expires_at"] == format(as_of + timedelta(minutes=5), "%Y-%m-%d %H:%M")


def test_prepare_cross_account_rejected(writable_db, customer_sessions):
    env = prepare_support_action(
        writable_db, customer_sessions["ACCT-002"], "request_cancellation",
        {"account_id": "ACCT-001", "order_id": "ORD-1001"},
    )
    assert env.status == "rejected"
    assert env.result["rejection_code"] == "ACCESS_DENIED"
    assert writable_db.execute(
        "SELECT COUNT(*) FROM actions WHERE payload_json LIKE '%ORD-1002%' OR created_by = 'ACCT-002'"
    ).fetchone()[0] == 0  # no draft was persisted


@pytest.mark.parametrize("action_type,payload", [
    ("delete_account", {"account_id": "ACCT-001"}),        # unknown action type
    ("CANCEL_ORDER", {"account_id": "ACCT-001"}),          # old uppercase name is NOT valid
    ("request_cancellation", {}),                          # empty payload
    ("request_cancellation", "not a dict"),                # wrong shape
    ("request_cancellation", {"order_id": "ORD-1001"}),    # missing account_id
])
def test_prepare_invalid_inputs_are_structured_errors(writable_db, customer_sessions,
                                                      action_type, payload):
    env = prepare_support_action(writable_db, customer_sessions["ACCT-001"],
                                 action_type, payload)
    assert env.status == "error"
    assert env.result["error_code"] == "INVALID_INPUT"


def test_prepare_rejects_malformed_session(writable_db):
    env = prepare_support_action(writable_db, {"role": "customer"},
                                 "request_cancellation", {"account_id": "ACCT-001"})
    assert env.status == "rejected"
    assert env.result["rejection_code"] == "INVALID_SESSION"


def test_analyze_scope_can_only_narrow_the_staff_view(seeded_db, staff_session):
    conn, _ = seeded_db
    narrowed = analyze_support_activity(conn, staff_session, account_scope="ACCT-002")
    assert narrowed.status == "ok"
    assert narrowed.result["visible_accounts"] == ["ACCT-002"]
    assert all(item["account_id"] == "ACCT-002" for item in narrowed.result["sla_status"])


def test_analyze_scope_outside_visible_accounts_rejected(seeded_db, customer_sessions):
    conn, _ = seeded_db
    # Customers are staff-gated first; even a well-formed scope changes nothing.
    env = analyze_support_activity(conn, customer_sessions["ACCT-001"],
                                   account_scope="ACCT-001")
    assert env.status == "rejected"
    assert env.result["rejection_code"] == "STAFF_ONLY"


# --- Golden-answer hygiene (05_CODING_AGENT_RULES §6) -----------------------------

FORBIDDEN_ANSWER_LITERALS = (
    # golden case values / entity ids must never appear in the tool layer
    "NORTHSTAR", "LUMENWORKS",
    "ORD-1001", "ORD-1002", "ORD-2001", "ORD-2002",
    "TKT-450", "TKT-451", "TKT-501", "TKT-502", "TKT-503", "TKT-504", "TKT-505",
    "NORTHSTAR_AGREEMENT_WAIVER", "LUMENWORKS_AGREEMENT_CREDIT",
    "KI-176", "KI-208", "KI-211",
)
FORBIDDEN_AMOUNTS = (r"\b250\b", r"\b300\b", r"\b360\b", r"\b5000\b")
SCANNED_PACKAGES = ("tools", "security", "trust")


def test_no_golden_answers_hardcoded_in_tool_or_security_layers():
    offenders = []
    for package in SCANNED_PACKAGES:
        for path in (PROJECT_ROOT / "backend" / package).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for literal in FORBIDDEN_ANSWER_LITERALS:
                if literal in text:
                    offenders.append(f"{path.name}: {literal}")
            for pattern in FORBIDDEN_AMOUNTS:
                if re.search(pattern, text):
                    offenders.append(f"{path.name}: amount {pattern}")
    assert not offenders, f"golden answers leaked into trusted layers: {offenders}"
