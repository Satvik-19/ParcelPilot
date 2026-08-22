"""Service-credit edge cases — fault evidence, thresholds, amounts, overrides.

SOP v4 §3 is strict: when fault attribution is unknown the ONLY acceptable
outcome is INSUFFICIENT_EVIDENCE — never a guess, never a goodwill credit.
"""

from datetime import datetime, timedelta

from backend.domain.credits import (
    ELIGIBLE,
    INSUFFICIENT_EVIDENCE,
    NOT_ELIGIBLE,
    resolve_service_credit,
)
from backend.domain.timebase import SNAPSHOT_TS

from .conftest import agreement_for

WINDOW_END = datetime(2026, 8, 16, 6, 30)


def _failed_pickup(account_row, fee=3600, carrier_fault=True, customer_fault=False):
    return {
        "order_id": "ORD-EDGE",
        "account_id": account_row["account_id"],
        "carrier": "RoadRunner",
        "status": "BOOKED",
        "booked_at": WINDOW_END - timedelta(hours=2),
        "pickup_window_start": WINDOW_END - timedelta(minutes=30),
        "pickup_window_end": WINDOW_END,
        "pickup_actual_at": None,
        "shipment_fee_inr": fee,
        "carrier_fault": carrier_fault,
        "customer_fault": customer_fault,
        "cancellation_requested_at": None,
        "notes": None,
    }


# --- INSUFFICIENT_EVIDENCE paths (unknown fault) ------------------------------

def test_unknown_carrier_fault_is_insufficient_evidence(accounts_by_id):
    order = _failed_pickup(accounts_by_id["ACCT-003"], carrier_fault=None)
    result = resolve_service_credit(order, accounts_by_id["ACCT-003"])
    assert result.result == INSUFFICIENT_EVIDENCE
    assert result.rule == "SOP_S3_FAULT_UNKNOWN"
    assert result.credit_inr is None


def test_unknown_customer_fault_is_insufficient_evidence(accounts_by_id):
    order = _failed_pickup(accounts_by_id["ACCT-003"], customer_fault=None)
    result = resolve_service_credit(order, accounts_by_id["ACCT-003"])
    assert result.result == INSUFFICIENT_EVIDENCE


def test_unknown_fault_wins_even_when_well_past_threshold(accounts_by_id):
    """Being late never substitutes for fault evidence."""
    order = _failed_pickup(accounts_by_id["ACCT-003"], carrier_fault=None)
    result = resolve_service_credit(
        order, accounts_by_id["ACCT-003"], as_of=SNAPSHOT_TS + timedelta(days=2)
    )
    assert result.result == INSUFFICIENT_EVIDENCE


# --- Pickup-completed short-circuit --------------------------------------------

def test_completed_pickup_is_never_a_failed_pickup(accounts_by_id):
    order = _failed_pickup(accounts_by_id["ACCT-003"])
    order["pickup_actual_at"] = WINDOW_END - timedelta(minutes=5)
    result = resolve_service_credit(order, accounts_by_id["ACCT-003"])
    assert result.result == NOT_ELIGIBLE
    assert result.rule == "SOP_S2_PICKUP_COMPLETED"


# --- Threshold boundary (default SOP: strictly MORE than 2h) -------------------

def test_exactly_at_threshold_is_not_eligible(accounts_by_id):
    order = _failed_pickup(accounts_by_id["ACCT-003"])
    result = resolve_service_credit(
        order, accounts_by_id["ACCT-003"], as_of=WINDOW_END + timedelta(hours=2)
    )
    assert result.result == NOT_ELIGIBLE
    assert result.rule == "SOP_S2_WITHIN_THRESHOLD"


def test_one_minute_past_threshold_is_eligible(accounts_by_id):
    order = _failed_pickup(accounts_by_id["ACCT-003"])
    result = resolve_service_credit(
        order, accounts_by_id["ACCT-003"],
        as_of=WINDOW_END + timedelta(hours=2, minutes=1),
    )
    assert result.result == ELIGIBLE
    assert result.rule == "SOP_S2_DEFAULT"
    assert result.credit_inr == 360  # 10% of 3600, below the 500 cap


# --- Fault attribution ----------------------------------------------------------

def test_no_carrier_fault_no_credit(accounts_by_id):
    order = _failed_pickup(accounts_by_id["ACCT-003"], carrier_fault=False)
    result = resolve_service_credit(order, accounts_by_id["ACCT-003"])
    assert result.result == NOT_ELIGIBLE
    assert result.rule == "SOP_S2_NO_CARRIER_FAULT"


def test_customer_fault_excludes_credit(accounts_by_id):
    order = _failed_pickup(accounts_by_id["ACCT-003"], customer_fault=True)
    result = resolve_service_credit(order, accounts_by_id["ACCT-003"])
    assert result.result == NOT_ELIGIBLE
    assert result.rule == "SOP_S2_CUSTOMER_FAULT"


# --- Default amount math: min(500, 10% of fee) ----------------------------------

def test_credit_caps_at_500(accounts_by_id):
    order = _failed_pickup(accounts_by_id["ACCT-003"], fee=8000)
    result = resolve_service_credit(order, accounts_by_id["ACCT-003"])
    assert result.result == ELIGIBLE
    assert result.credit_inr == 500
    assert result.requires_manager_approval is False  # 500 <= 1000


def test_small_fee_takes_10_percent(accounts_by_id):
    order = _failed_pickup(accounts_by_id["ACCT-003"], fee=1200)
    result = resolve_service_credit(order, accounts_by_id["ACCT-003"])
    assert result.credit_inr == 120


# --- Agreement override: LumenWorks 4h threshold + flat 300 ---------------------

def test_lumenworks_threshold_replaces_default(accounts_by_id):
    """3h past window: default SOP would pay, LumenWorks' 4h rule does not."""
    order = _failed_pickup(accounts_by_id["ACCT-002"])
    result = resolve_service_credit(
        order, accounts_by_id["ACCT-002"], agreement_for("ACCT-002"),
        as_of=WINDOW_END + timedelta(hours=3),
    )
    assert result.result == NOT_ELIGIBLE
    assert result.rule == "SOP_S2_WITHIN_THRESHOLD"


def test_lumenworks_flat_amount_ignores_fee(accounts_by_id):
    order = _failed_pickup(accounts_by_id["ACCT-002"], fee=10000)
    result = resolve_service_credit(
        order, accounts_by_id["ACCT-002"], agreement_for("ACCT-002"),
        as_of=WINDOW_END + timedelta(hours=5),
    )
    assert result.result == ELIGIBLE
    assert result.credit_inr == 300  # fixed, not min(500, 10%)
    assert result.overrides == "SOP_S2"


# --- Northstar monthly-cap warning ----------------------------------------------

def test_northstar_credit_carries_monthly_cap_warning(accounts_by_id):
    order = _failed_pickup(accounts_by_id["ACCT-001"])
    result = resolve_service_credit(
        order, accounts_by_id["ACCT-001"], agreement_for("ACCT-001")
    )
    assert result.result == ELIGIBLE            # default SOP terms apply
    assert result.rule == "SOP_S2_DEFAULT"
    assert any("5000" in w for w in result.warnings)


# --- as_of accepts the canonical string form ------------------------------------

def test_as_of_accepts_string_timestamp(accounts_by_id):
    order = _failed_pickup(accounts_by_id["ACCT-003"])
    via_string = resolve_service_credit(
        order, accounts_by_id["ACCT-003"], as_of="2026-08-16 11:00"
    )
    via_datetime = resolve_service_credit(
        order, accounts_by_id["ACCT-003"], as_of=SNAPSHOT_TS
    )
    assert via_string == via_datetime
