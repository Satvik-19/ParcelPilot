"""Failed-pickup service-credit resolution (SOP v4 §2–3, agreement overrides).

Pure function, no LLM, no I/O. Implements docs/02_DOMAIN_SPEC.md §3
`resolve_service_credit(order, account, agreement, as_of=SNAPSHOT_TS)`.

Check order (deterministic):
  1. pickup already happened -> not a failed pickup, not eligible;
  2. fault attribution unknown -> INSUFFICIENT_EVIDENCE (SOP §3: never
     promise a credit when fault is unknown);
  3. threshold / fault tests decide eligibility; amount comes from the
     agreement override when one exists, else the SOP default.
"""

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional, Tuple

from .policy_data import SERVICE_CREDIT_DEFAULT
from .timebase import SNAPSHOT_TS, parse_ts

ELIGIBLE = "ELIGIBLE"
NOT_ELIGIBLE = "NOT_ELIGIBLE"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


@dataclass(frozen=True)
class CreditDecision:
    result: str                     # ELIGIBLE | NOT_ELIGIBLE | INSUFFICIENT_EVIDENCE
    credit_inr: Optional[int] = None
    rule: Optional[str] = None
    overrides: Optional[str] = None
    requires_manager_approval: bool = False
    warnings: Tuple[str, ...] = field(default_factory=tuple)
    evidence: Tuple[str, ...] = field(default_factory=tuple)
    reason: Optional[str] = None


def _credit_terms(agreement):
    """Return (threshold_hours, amount_fn, rule_suffix, source, overrides)."""
    sop = SERVICE_CREDIT_DEFAULT
    if agreement and "fixed_amount_inr" in agreement.get("service_credit", {}):
        terms = agreement["service_credit"]
        return (
            terms["threshold_hours"],
            lambda fee: terms["fixed_amount_inr"],
            "AGREEMENT_CREDIT",
            terms["source"],
            "SOP_S2",
        )
    return (
        sop["threshold_hours"],
        lambda fee: min(sop["cap_inr"], fee * sop["percent_of_fee"] // 100),
        "SOP_S2_DEFAULT",
        sop["source"],
        None,
    )


def resolve_service_credit(order, account, agreement=None, as_of=SNAPSHOT_TS):
    """Resolve failed-pickup service-credit eligibility and amount."""
    as_of = parse_ts(as_of)
    sop_source = SERVICE_CREDIT_DEFAULT["source"]

    # 1. A completed pickup is not a failed pickup.
    if order.get("pickup_actual_at") is not None:
        return CreditDecision(
            NOT_ELIGIBLE, rule="SOP_S2_PICKUP_COMPLETED",
            reason="Pickup was completed; service credits apply to failed pickups.",
            evidence=(sop_source,),
        )

    # 2. Unknown fault attribution -> never promise a credit (SOP §3).
    carrier_fault = order.get("carrier_fault")
    customer_fault = order.get("customer_fault")
    if carrier_fault is None or customer_fault is None:
        return CreditDecision(
            INSUFFICIENT_EVIDENCE, rule="SOP_S3_FAULT_UNKNOWN",
            reason="Carrier/customer fault attribution is unknown; the SOP forbids "
                   "promising a credit until it is established.",
            evidence=(sop_source,),
        )

    threshold_hours, amount_fn, rule_suffix, source, overrides = _credit_terms(agreement)

    # 3. Threshold: strictly MORE than N hours past the pickup-window end.
    window_end = parse_ts(order["pickup_window_end"])
    if as_of - window_end <= timedelta(hours=threshold_hours):
        return CreditDecision(
            NOT_ELIGIBLE, rule="SOP_S2_WITHIN_THRESHOLD",
            reason=f"Not yet more than {threshold_hours}h past the pickup window end.",
            evidence=(source,),
        )
    if not carrier_fault:
        return CreditDecision(
            NOT_ELIGIBLE, rule="SOP_S2_NO_CARRIER_FAULT",
            reason="Carrier is not at fault for the missed pickup.",
            evidence=(source,),
        )
    if customer_fault:
        return CreditDecision(
            NOT_ELIGIBLE, rule="SOP_S2_CUSTOMER_FAULT",
            reason="Customer-caused issue excludes the credit.",
            evidence=(source,),
        )

    fee = int(order["shipment_fee_inr"])
    amount = int(amount_fn(fee))
    rule = rule_suffix if rule_suffix == "SOP_S2_DEFAULT" else (
        f"{account['account_name'].split()[0].upper().replace('-', '')}_{rule_suffix}"
    )
    warnings = []
    if agreement and "monthly_cap_inr" in agreement.get("service_credit", {}):
        cap = agreement["service_credit"]["monthly_cap_inr"]
        warnings.append(
            f"Monthly aggregate credits are capped at INR {cap} for this account "
            f"({agreement['service_credit']['source']})."
        )
    return CreditDecision(
        ELIGIBLE,
        credit_inr=amount,
        rule=rule,
        overrides=overrides,
        requires_manager_approval=amount
        > SERVICE_CREDIT_DEFAULT["manager_approval_above_inr"],
        warnings=tuple(warnings),
        evidence=(source, sop_source),
        reason=f"Pickup failed on carrier fault, more than {threshold_hours}h past window end.",
    )
