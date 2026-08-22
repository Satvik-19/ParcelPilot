"""Deterministic policy constants (PolicyResolver data — ADR-003).

Every constant here was extracted from the supplied assessment documents and
verified in Phase 0 (docs/handoffs/session-00.md). Each carries its source
citation. These are the *rules*, not golden-case answers — the answers must
be computed from these rules by the domain functions.

GI-1 enforcement by construction: Support Policy v2 (deprecated) numbers are
intentionally ABSENT from this module. Deprecated material exists only in the
document index for explicitly-requested retrieval and is never referenced by
domain logic.
"""

# --- Document identities (assessment_docs/) ---------------------------------

POLICY_V3_DOC = "01_Support_Policy_v3_CURRENT"
POLICY_V2_DOC = "02_Support_Policy_v2_DEPRECATED"  # retrieval metadata only; never used here
SOP_DOC = "03_Cancellation_and_Service_Credit_SOP_v4"
OPS_GUIDE_DOC = "04_Product_Operations_Guide_and_Known_Issues"
NORTHSTAR_AGREEMENT_DOC = "05_Northstar_Logistics_Enterprise_Agreement"
LUMENWORKS_AGREEMENT_DOC = "06_LumenWorks_Service_Agreement"

# --- Cancellation (SOP v4 §1) ------------------------------------------------

CANCELLATION = {
    "free_window_minutes": 30,   # BOOKED: no fee within 30 minutes of booking
    "late_fee_inr": 250,         # after 30 minutes, unless an agreement waives it
    "source": f"{SOP_DOC} §1",
}

# --- Failed-pickup service credits (SOP v4 §2–3) -----------------------------

SERVICE_CREDIT_DEFAULT = {
    "threshold_hours": 2,                  # more than 2h past pickup-window end
    "cap_inr": 500,                        # credit = lower of cap or percentage
    "percent_of_fee": 10,                  # 10% of the shipment fee
    "manager_approval_above_inr": 1000,    # §3
    "source": f"{SOP_DOC} §2–3",
}

# --- Default first-response SLA targets (Support Policy v3 §3) ---------------
# Tuple shape: (value, unit, calendar). calendar=True -> wall-clock elapsed
# time; calendar=False -> business time (see businesstime.py convention).
# "hours" without a "business" qualifier on the Enterprise row is treated as
# calendar time (documented convention — the doc marks only P1 as 24x7, but
# gives no business-hours qualifier for Enterprise P2).

DEFAULT_SLA_TABLE = {
    "Enterprise": {
        "P1": (30, "minutes", True),        # "30 minutes, 24x7"
        "P2": (2, "hours", True),           # "2 hours"
        "P3": (1, "business_days", False),  # "1 business day"
    },
    "Growth": {
        "P1": (2, "business_hours", False),
        "P2": (4, "business_hours", False),
        "P3": (2, "business_days", False),
    },
    "Standard": {
        "P1": (4, "business_hours", False),
        "P2": (1, "business_days", False),
        "P3": (2, "business_days", False),
    },
}
DEFAULT_SLA_SOURCE = f"{POLICY_V3_DOC} §3"

# --- Signed customer agreement overrides (rank 1 in the authority hierarchy) --

AGREEMENTS = {
    "ACCT-001": {
        "account_id": "ACCT-001",
        "doc": NORTHSTAR_AGREEMENT_DOC,
        "term": "2026-01-01 to 2026-12-31",
        "cancellation": {
            "waives_fee": True,  # any BOOKED shipment before pickup, regardless of elapsed time (§2)
            "source": f"{NORTHSTAR_AGREEMENT_DOC} §2",
        },
        "sla": {
            "P1": (15, "minutes", True),        # "15 minutes, 24x7"
            "P2": (1, "hours", True),           # "1 hour"
            "P3": (8, "business_hours", False), # "8 business hours"
            "source": f"{NORTHSTAR_AGREEMENT_DOC} §1",
        },
        "service_credit": {
            # No custom threshold/amount — default SOP applies (§3) — but the
            # monthly aggregate is capped.
            "monthly_cap_inr": 5000,
            "source": f"{NORTHSTAR_AGREEMENT_DOC} §3",
        },
    },
    "ACCT-002": {
        "account_id": "ACCT-002",
        "doc": LUMENWORKS_AGREEMENT_DOC,
        "term": "2026-03-01 to 2027-02-28",
        "cancellation": {
            "waives_fee": False,  # "No special cancellation-fee waiver applies" (§2)
            "source": f"{LUMENWORKS_AGREEMENT_DOC} §2",
        },
        "sla": {
            "P1": (2, "business_hours", False),
            "P2": (4, "business_hours", False),
            "P3": (2, "business_days", False),
            "no_weekend_or_after_hours": True,
            "source": f"{LUMENWORKS_AGREEMENT_DOC} §1",
        },
        "service_credit": {
            # Replaces the SOP default threshold and amount (§3):
            # more than 4h past window + carrier fault + no customer fault -> fixed INR 300.
            "threshold_hours": 4,
            "fixed_amount_inr": 300,
            "source": f"{LUMENWORKS_AGREEMENT_DOC} §3",
        },
    },
}


def get_agreement(account_id):
    """Return the active agreement record for an account, or None."""
    return AGREEMENTS.get(account_id)


# --- Known issues (Product Operations Guide §2–3) -----------------------------

KNOWN_ISSUES = {
    "KI-208": {
        "title": "Bulk Upload failures on large CSVs",
        "status": "Investigating",
        "opened": "2026-08-10",
        "failure_row_threshold": 3000,  # intermittent failures above ~3,000 rows
        "product_row_limit": 5000,      # supported product limit remains 5,000 rows
        "workaround": (
            "Split the upload into files below 3,000 rows; individual shipment "
            "creation is unaffected."
        ),
        "source": f"{OPS_GUIDE_DOC} §2",
    },
    "KI-211": {
        "title": "SwiftShip pickup webhook delay",
        "status": "Monitoring",
        "opened": "2026-08-12",
        "max_delay_minutes": 20,  # webhooks can arrive up to 20 minutes late
        "workaround": (
            "Verify the carrier status or wait through the known delay window "
            "before telling a customer that a pickup did not occur."
        ),
        "source": f"{OPS_GUIDE_DOC} §2",
    },
}

# Resolved issues must not be used to explain new incidents (Guide §3).
RESOLVED_ISSUES = {
    "KI-176": {
        "title": "Address validation",
        "resolved": "2026-07-18",
        "source": f"{OPS_GUIDE_DOC} §3",
    },
}
