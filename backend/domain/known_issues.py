"""Deterministic known-issue matching (Product Operations Guide §2–3).

Keyword/category matching against KI-208 and KI-211 only. KI-176 is resolved
and is explicitly EXCLUDED from attribution — if a ticket looks like it, the
result says so instead of matching (per the guide's own instruction).
"""

import re
from dataclasses import dataclass, field
from typing import Optional, Tuple

from .policy_data import KNOWN_ISSUES, RESOLVED_ISSUES


@dataclass(frozen=True)
class KnownIssueMatch:
    matched_ki: Optional[str]
    confidence: str                     # "high" | "none"
    guidance: str = ""
    evidence: Tuple[str, ...] = field(default_factory=tuple)
    excluded: Tuple[str, ...] = field(default_factory=tuple)  # e.g. KI-176 note


_ROW_COUNT_RE = re.compile(r"([\d][\d,]*)\s*-?\s*row", re.IGNORECASE)


def _ticket_text(ticket):
    return " ".join(
        str(ticket.get(f) or "") for f in ("subject", "description")
    ).lower()


def _row_count(text):
    match = _ROW_COUNT_RE.search(text)
    if not match:
        return None
    return int(match.group(1).replace(",", ""))


def match_known_issue(ticket):
    """Match a ticket dict against the current known-issue register."""
    text = _ticket_text(ticket)

    # KI-176 (resolved) must never explain new incidents — surface the
    # exclusion deliberately instead of matching.
    excluded = ()
    if "address validation" in text:
        ki176 = RESOLVED_ISSUES["KI-176"]
        excluded = (
            f"KI-176 ({ki176['title']}) resolved {ki176['resolved']}; not used to "
            f"explain new incidents ({ki176['source']}).",
        )

    ki208 = KNOWN_ISSUES["KI-208"]
    if ("bulk upload" in text or "csv" in text):
        rows = _row_count(text)
        if (rows is not None and rows > ki208["failure_row_threshold"]) or "large" in text:
            return KnownIssueMatch(
                matched_ki="KI-208",
                confidence="high",
                guidance=(
                    f"{ki208['title']} ({ki208['status']}): intermittent failures occur "
                    f"above ~{ki208['failure_row_threshold']:,} rows even though the "
                    f"supported product limit is {ki208['product_row_limit']:,} rows. "
                    f"Workaround: {ki208['workaround']}"
                ),
                evidence=(ki208["source"],),
                excluded=excluded,
            )

    ki211 = KNOWN_ISSUES["KI-211"]
    if "swiftship" in text and "pickup" in text and (
        "booked" in text or "webhook" in text or "not updated" in text
    ):
        return KnownIssueMatch(
            matched_ki="KI-211",
            confidence="high",
            guidance=(
                f"{ki211['title']} ({ki211['status']}): pickup webhooks can arrive up to "
                f"{ki211['max_delay_minutes']} minutes late; a parcel may be collected "
                f"while still shown as BOOKED. {ki211['workaround']}"
            ),
            evidence=(ki211["source"],),
            excluded=excluded,
        )

    return KnownIssueMatch(matched_ki=None, confidence="none", excluded=excluded)
