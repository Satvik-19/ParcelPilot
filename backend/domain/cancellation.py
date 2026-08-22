"""Cancellation fee resolution (SOP v4 §1, agreement overrides — rank 1).

Pure function, no LLM, no I/O. Implements the decision table in
docs/02_DOMAIN_SPEC.md §3 `resolve_cancellation_fee`, including explicit,
traceable agreement waivers. Unknown order status raises rather than guessing.
"""

import re
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional, Tuple

from .policy_data import CANCELLATION
from .timebase import SNAPSHOT_TS, parse_ts


@dataclass(frozen=True)
class CancellationDecision:
    cancellable: bool
    fee_inr: Optional[int]          # None when cancellation is not possible
    rule: str                       # machine-readable rule id
    overrides: Optional[str] = None  # SOP clause overridden by an agreement
    suggested_action: Optional[str] = None
    evidence: Tuple[str, ...] = field(default_factory=tuple)  # source citations


def _account_rule_prefix(account):
    """NORTHSTAR / LUMENWORKS / ... — first word of the account name."""
    first_word = str(account["account_name"]).split()[0]
    return re.sub(r"[^A-Za-z0-9]", "", first_word).upper()


def resolve_cancellation_fee(order, account, agreement=None, as_of=SNAPSHOT_TS):
    """Resolve the cancellation outcome for an order.

    `order`/`account` are row dicts (typed datetimes). `agreement` is the
    account's active agreement record (backend.domain.policy_data) or None.
    `as_of` is used only when no cancellation request timestamp exists yet.
    """
    status = order["status"]
    sop = CANCELLATION["source"]

    if status == "DRAFT":
        return CancellationDecision(True, 0, "SOP_S1_DRAFT", evidence=(sop,))

    if status == "PICKED_UP":
        evidence = [sop]
        if agreement:
            evidence.append(agreement["cancellation"]["source"])
        return CancellationDecision(
            False, None, "SOP_S1_PICKED_UP",
            suggested_action="return_to_origin", evidence=tuple(evidence),
        )

    if status == "DELIVERED":
        return CancellationDecision(False, None, "SOP_S1_DELIVERED", evidence=(sop,))

    if status != "BOOKED":
        raise ValueError(f"unknown order status: {status!r}")

    # BOOKED, not yet picked up.
    if agreement and agreement["cancellation"]["waives_fee"]:
        return CancellationDecision(
            True, 0, f"{_account_rule_prefix(account)}_AGREEMENT_WAIVER",
            overrides="SOP_S1",
            evidence=(agreement["cancellation"]["source"], sop),
        )

    requested_at = parse_ts(order.get("cancellation_requested_at")) or parse_ts(as_of)
    elapsed = requested_at - parse_ts(order["booked_at"])
    if elapsed <= timedelta(minutes=CANCELLATION["free_window_minutes"]):
        return CancellationDecision(True, 0, "SOP_S1_WITHIN_30MIN", evidence=(sop,))
    return CancellationDecision(
        True, CANCELLATION["late_fee_inr"], "SOP_S1_AFTER_30MIN", evidence=(sop,)
    )
