"""Confirmation gate — Layer A suite for 03_AGENT_SPEC.md §4 (04_EVAL_SPEC §2).

Each of the six validation checks fails CLOSED individually, with no state
mutation; replay/double-confirm can never execute twice; the mocked effects
only ever apply inside the atomic claim transaction. No LLM anywhere — the
gate is a plain backend function the UI endpoint calls (ADR-004).

Synthetic ``as_of`` values stand in for the confirmation request time
(§4 check 6); the API layer supplies its own clock there, never here.
"""

import json
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from backend.actions.confirm import confirm_support_action
from backend.db.database import open_database
from backend.db.seed import seed_database
from backend.domain.timebase import SNAPSHOT_TS, format_ts
from backend.tools.prepare_support_action import prepare_support_action

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_PACK = PROJECT_ROOT / "assessment_docs"

DRAFT_TIME = SNAPSHOT_TS
WITHIN_WINDOW = SNAPSHOT_TS + timedelta(minutes=4)
AFTER_WINDOW = SNAPSHOT_TS + timedelta(minutes=6)

CUSTOMER = {"role": "customer", "account_id": "ACCT-002",
            "session_id": "sess-gate-cust"}
OTHER_CUSTOMER = {"role": "customer", "account_id": "ACCT-001",
                  "session_id": "sess-gate-other"}
STAFF = {"role": "staff", "staff_id": "STF-001", "session_id": "sess-gate-staff",
         "permissions": ("support",)}

CREDIT_PAYLOAD = {"account_id": "ACCT-002", "order_id": "ORD-2002",
                  "amount_inr": 300, "rule": "LUMENWORKS_AGREEMENT_OVERRIDE"}


@pytest.fixture()
def gate_db(tmp_path):
    """Private seeded DB per test — confirmations mutate state."""
    db_path = tmp_path / "gate.db"
    seed_database(db_path, DATA_PACK)
    conn = open_database(db_path)
    yield conn
    conn.close()


def _draft(conn, session=CUSTOMER, action_type="grant_service_credit",
           payload=None):
    envelope = prepare_support_action(conn, session, action_type,
                                      payload or dict(CREDIT_PAYLOAD),
                                      as_of=DRAFT_TIME)
    assert envelope.status == "ok", envelope.result
    return envelope.result


def _action_row(conn, action_id):
    return conn.execute("SELECT * FROM actions WHERE action_id = ?",
                        (action_id,)).fetchone()


# ---------------------------------------------------------------- six checks

def test_happy_path_executes_exactly_once_with_effect(gate_db):
    draft = _draft(gate_db)
    outcome = confirm_support_action(gate_db, CUSTOMER, draft["action_id"],
                                     draft["token"], as_of=WITHIN_WINDOW)
    assert outcome["status"] == "executed"
    assert "INR 300" in outcome["effect"]
    row = _action_row(gate_db, draft["action_id"])
    assert row["status"] == "executed"
    assert row["confirmed_at"] == format_ts(WITHIN_WINDOW)


def test_check1_nonexistent_action_rejected(gate_db):
    outcome = confirm_support_action(gate_db, CUSTOMER, "act_does_not_exist",
                                     "any-token", as_of=WITHIN_WINDOW)
    assert outcome["rejection_code"] == "NOT_FOUND"


def test_check2_not_pending_rejected(gate_db):
    draft = _draft(gate_db)
    gate_db.execute("UPDATE actions SET status = 'rejected' WHERE action_id = ?",
                    (draft["action_id"],))
    gate_db.commit()
    outcome = confirm_support_action(gate_db, CUSTOMER, draft["action_id"],
                                     draft["token"], as_of=WITHIN_WINDOW)
    assert outcome["rejection_code"] == "NOT_PENDING"


def test_check3_wrong_session_rejected(gate_db):
    draft = _draft(gate_db)
    # A different customer context — even with the right token in hand.
    outcome = confirm_support_action(gate_db, OTHER_CUSTOMER, draft["action_id"],
                                     draft["token"], as_of=WITHIN_WINDOW)
    assert outcome["rejection_code"] == "WRONG_SESSION"
    assert _action_row(gate_db, draft["action_id"])["status"] == "pending"
    # Staff did not draft it either.
    outcome = confirm_support_action(gate_db, STAFF, draft["action_id"],
                                     draft["token"], as_of=WITHIN_WINDOW)
    assert outcome["rejection_code"] == "WRONG_SESSION"


def test_check4_wrong_token_rejected(gate_db):
    draft = _draft(gate_db)
    outcome = confirm_support_action(gate_db, CUSTOMER, draft["action_id"],
                                     draft["token"][:-8] + "deadbeef",
                                     as_of=WITHIN_WINDOW)
    assert outcome["rejection_code"] == "WRONG_TOKEN"
    assert _action_row(gate_db, draft["action_id"])["status"] == "pending"


def test_check5_tampered_payload_rejected(gate_db):
    draft = _draft(gate_db)
    # Simulate tampering with the stored payload behind the gate's back.
    tampered = dict(CREDIT_PAYLOAD, amount_inr=30000)
    gate_db.execute(
        "UPDATE actions SET payload_json = ? WHERE action_id = ?",
        (json.dumps(tampered, sort_keys=True, separators=(",", ":")),
         draft["action_id"]),
    )
    gate_db.commit()
    outcome = confirm_support_action(gate_db, CUSTOMER, draft["action_id"],
                                     draft["token"], as_of=WITHIN_WINDOW)
    assert outcome["rejection_code"] == "PAYLOAD_TAMPERED"
    assert _action_row(gate_db, draft["action_id"])["status"] == "pending"


def test_check6_expired_draft_rejected_and_marked(gate_db):
    draft = _draft(gate_db)
    outcome = confirm_support_action(gate_db, CUSTOMER, draft["action_id"],
                                     draft["token"], as_of=AFTER_WINDOW)
    assert outcome["rejection_code"] == "EXPIRED"
    assert _action_row(gate_db, draft["action_id"])["status"] == "expired"
    # And the expiry boundary is the stored five-minute window, exactly.
    draft2 = _draft(gate_db, payload=dict(CREDIT_PAYLOAD, order_id="ORD-2001"))
    edge = DRAFT_TIME + timedelta(minutes=5)
    outcome = confirm_support_action(gate_db, CUSTOMER, draft2["action_id"],
                                     draft2["token"], as_of=edge)
    assert outcome["status"] == "executed"


# ------------------------------------------------- replay / double execution

def test_replay_after_execution_is_refused_with_no_second_effect(gate_db):
    draft = _draft(gate_db, action_type="request_cancellation",
                   payload={"account_id": "ACCT-002", "order_id": "ORD-2001",
                            "fee_inr": 250})
    first = confirm_support_action(gate_db, CUSTOMER, draft["action_id"],
                                   draft["token"], as_of=WITHIN_WINDOW)
    assert first["status"] == "executed"
    order = gate_db.execute(
        "SELECT status FROM orders WHERE order_id = 'ORD-2001'").fetchone()
    assert order["status"] == "CANCELLED"

    # Refresh / double-click / replay with the identical token:
    replay = confirm_support_action(gate_db, CUSTOMER, draft["action_id"],
                                    draft["token"], as_of=WITHIN_WINDOW)
    assert replay["status"] == "rejected"
    assert replay["rejection_code"] == "NOT_PENDING"
    row = _action_row(gate_db, draft["action_id"])
    assert row["status"] == "executed"          # never re-executed
    assert row["confirmed_at"] == format_ts(WITHIN_WINDOW)


# ------------------------------------------------------- fail-closed effects

def test_credit_cap_blocks_execution_fail_closed(gate_db):
    """Northstar monthly aggregate cap (02_DOMAIN_SPEC §3 data note)."""
    northstar = {"role": "customer", "account_id": "ACCT-001",
                 "session_id": "sess-gate-northstar"}
    big = {"account_id": "ACCT-001", "order_id": "ORD-1001",
           "amount_inr": 4800, "rule": "TEST"}
    small = {"account_id": "ACCT-001", "order_id": "ORD-1002",
             "amount_inr": 300, "rule": "TEST"}

    first = _draft(gate_db, session=northstar, payload=big)
    assert confirm_support_action(gate_db, northstar, first["action_id"],
                                  first["token"],
                                  as_of=WITHIN_WINDOW)["status"] == "executed"

    second = _draft(gate_db, session=northstar, payload=small)
    outcome = confirm_support_action(gate_db, northstar, second["action_id"],
                                     second["token"], as_of=WITHIN_WINDOW)
    assert outcome["status"] == "rejected"
    assert outcome["rejection_code"] == "EXECUTION_REFUSED"
    assert "5,000" in outcome["message"]
    # The refusal mutated nothing: still pending, no ledger effect.
    assert _action_row(gate_db, second["action_id"])["status"] == "pending"


def test_invalid_session_shape_rejected(gate_db):
    draft = _draft(gate_db)
    outcome = confirm_support_action(gate_db, {"role": "tourist"},
                                     draft["action_id"], draft["token"],
                                     as_of=WITHIN_WINDOW)
    assert outcome["rejection_code"] == "INVALID_SESSION"
    assert _action_row(gate_db, draft["action_id"])["status"] == "pending"
