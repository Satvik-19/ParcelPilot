"""Cancellation edge cases — timing boundaries, status transitions, overrides.

Golden-case inputs stay untouched; these synthesize mutations of the real
rows to probe the decision boundaries of SOP v4 §1.
"""

from datetime import datetime, timedelta

import pytest

from backend.domain.cancellation import resolve_cancellation_fee
from backend.domain.timebase import SNAPSHOT_TS

from .conftest import agreement_for


def _booked(account_row, booked_at, requested_at):
    return {
        "order_id": "ORD-EDGE",
        "account_id": account_row["account_id"],
        "carrier": "SwiftShip",
        "status": "BOOKED",
        "booked_at": booked_at,
        "pickup_window_start": booked_at + timedelta(hours=1),
        "pickup_window_end": booked_at + timedelta(hours=2),
        "pickup_actual_at": None,
        "shipment_fee_inr": 1000,
        "carrier_fault": None,
        "customer_fault": None,
        "cancellation_requested_at": requested_at,
        "notes": None,
    }


# --- 30-minute free window boundary ------------------------------------------

def test_exactly_30_minutes_is_still_free(accounts_by_id):
    account = accounts_by_id["ACCT-003"]
    booked = datetime(2026, 8, 16, 9, 0)
    decision = resolve_cancellation_fee(
        _booked(account, booked, booked + timedelta(minutes=30)), account
    )
    assert decision.fee_inr == 0
    assert decision.rule == "SOP_S1_WITHIN_30MIN"


def test_one_minute_past_window_costs_250(accounts_by_id):
    account = accounts_by_id["ACCT-003"]
    booked = datetime(2026, 8, 16, 9, 0)
    decision = resolve_cancellation_fee(
        _booked(account, booked, booked + timedelta(minutes=31)), account
    )
    assert decision.fee_inr == 250
    assert decision.rule == "SOP_S1_AFTER_30MIN"


def test_no_request_timestamp_falls_back_to_as_of(accounts_by_id):
    account = accounts_by_id["ACCT-003"]
    booked = datetime(2026, 8, 16, 9, 0)
    order = _booked(account, booked, None)
    inside = resolve_cancellation_fee(
        order, account, as_of=datetime(2026, 8, 16, 9, 20)
    )
    assert inside.rule == "SOP_S1_WITHIN_30MIN"
    outside = resolve_cancellation_fee(
        order, account, as_of=datetime(2026, 8, 16, 10, 0)
    )
    assert outside.rule == "SOP_S1_AFTER_30MIN"
    assert outside.fee_inr == 250


# --- Status transitions -------------------------------------------------------

def test_draft_cancels_free(accounts_by_id):
    account = accounts_by_id["ACCT-003"]
    order = _booked(account, SNAPSHOT_TS, None)
    order["status"] = "DRAFT"
    decision = resolve_cancellation_fee(order, account)
    assert decision.cancellable is True
    assert decision.fee_inr == 0
    assert decision.rule == "SOP_S1_DRAFT"


def test_delivered_cannot_cancel(accounts_by_id):
    account = accounts_by_id["ACCT-004"]
    order = _booked(account, SNAPSHOT_TS - timedelta(days=1), None)
    order["status"] = "DELIVERED"
    decision = resolve_cancellation_fee(order, account)
    assert decision.cancellable is False
    assert decision.fee_inr is None
    assert decision.rule == "SOP_S1_DELIVERED"


def test_unknown_status_raises(accounts_by_id):
    account = accounts_by_id["ACCT-003"]
    order = _booked(account, SNAPSHOT_TS, None)
    order["status"] = "LOST"
    with pytest.raises(ValueError):
        resolve_cancellation_fee(order, account)


# --- Agreement override semantics ----------------------------------------------

def test_waiver_applies_regardless_of_elapsed_time(accounts_by_id):
    """Northstar §2 waives the fee for any BOOKED cancellation before pickup."""
    account = accounts_by_id["ACCT-001"]
    booked = datetime(2026, 8, 16, 6, 0)
    decision = resolve_cancellation_fee(
        _booked(account, booked, booked + timedelta(hours=4)),
        account,
        agreement_for("ACCT-001"),
    )
    assert decision.fee_inr == 0
    assert decision.rule == "NORTHSTAR_AGREEMENT_WAIVER"
    assert decision.overrides == "SOP_S1"


def test_waiver_does_not_override_post_pickup_rule(accounts_by_id):
    """Even with a waiver agreement, PICKED_UP stays non-cancellable."""
    account = accounts_by_id["ACCT-001"]
    order = _booked(account, SNAPSHOT_TS - timedelta(hours=2), None)
    order["status"] = "PICKED_UP"
    decision = resolve_cancellation_fee(
        order, account, agreement_for("ACCT-001")
    )
    assert decision.cancellable is False
    assert decision.rule == "SOP_S1_PICKED_UP"
    # Evidence cites BOTH the SOP and the account agreement (case 4 wording).
    assert len(decision.evidence) == 2


def test_non_waiving_agreement_falls_through_to_sop(accounts_by_id):
    """LumenWorks' agreement explicitly declines the waiver (§2)."""
    account = accounts_by_id["ACCT-002"]
    booked = datetime(2026, 8, 16, 9, 0)
    decision = resolve_cancellation_fee(
        _booked(account, booked, booked + timedelta(minutes=75)),
        account,
        agreement_for("ACCT-002"),
    )
    assert decision.fee_inr == 250
    assert decision.rule == "SOP_S1_AFTER_30MIN"
    assert decision.overrides is None
