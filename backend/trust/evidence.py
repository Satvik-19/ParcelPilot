"""Structured evidence records (03_AGENT_SPEC.md §5) + force-inclusion.

Every evidence record carries evidence_id, source_doc, section, status,
authority_rank, applicable_to and text, plus overridden_by / excluded_reason
when a source lost a conflict or was excluded.

Retrieval authorization != evidence applicability: DEPRECATED documents and
historical resolutions are *displayable* on explicit request
(include_historical=True, golden cases 10/11) but always carry
authority_rank=NONE — they can never determine a policy outcome.
"""

from dataclasses import dataclass
from typing import Optional, Union

from backend.domain.timebase import SNAPSHOT_TS, format_ts
from backend.security.retrieval import (
    agreement_chunks,
    all_scoped_chunks,
    search_scoped_chunks,
)

from .conflicts import resolve_conflicts

NON_AUTHORITATIVE_STATUSES = ("DEPRECATED", "HISTORICAL_RESOLUTION")
_MAX_RESULTS = 8


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    source_doc: str
    section: str
    status: str                     # CURRENT | ACTIVE | DEPRECATED | HISTORICAL_RESOLUTION
    authority_rank: Union[int, None]  # 1..3 authoritative; None == NONE (never decides)
    applicable_to: str              # account scope + date validity at SNAPSHOT_TS
    overridden_by: Optional[str] = None
    excluded_reason: Optional[str] = None
    text: str = ""


def _applicable_to(chunk, as_of):
    scope = chunk["scope"]
    scope_label = "all accounts" if scope == "GENERAL" else f"account {scope}"
    if chunk["status"] == "HISTORICAL_RESOLUTION":
        validity = "context only — no policy validity"
    elif chunk["effective"]:
        validity = f"effective {chunk['effective']} (valid at {format_ts(as_of)})"
    else:
        validity = f"valid at {format_ts(as_of)}"
    return f"{scope_label}; {validity}"


def _excluded_reason(status):
    if status == "DEPRECATED":
        return ("Deprecated document — displayable on explicit request only; "
                "never an authority source (GI-1).")
    if status == "HISTORICAL_RESOLUTION":
        return ("Historical ticket resolution — context only, never a policy "
                "source (workbook README).")
    return None


def evidence_from_chunk(chunk, as_of=SNAPSHOT_TS):
    """Build the §5 record for one chunk row (dict)."""
    status = chunk["status"]
    authoritative = status not in NON_AUTHORITATIVE_STATUSES
    return EvidenceRecord(
        evidence_id=f"{chunk['source_doc']}#{chunk['section']}",
        source_doc=chunk["source_doc"],
        section=chunk["section"],
        status=status,
        authority_rank=int(chunk["authority_rank"]) if authoritative else None,
        applicable_to=_applicable_to(chunk, as_of),
        excluded_reason=_excluded_reason(status),
        text=chunk["text"],
    )


def gather_evidence(conn, account_id, query=None, include_historical=False,
                    as_of=SNAPSHOT_TS):
    """Evidence for the account in scope, fully deterministic.

    - Force-includes the account's active agreement chunks (AGENT_SPEC §3);
    - Default retrieval returns only CURRENT/ACTIVE sources; DEPRECATED and
      historical chunks appear ONLY when include_historical=True, flagged
      authority_rank=NONE;
    - Higher authority_rank wins conflicts; losers keep overridden_by.
    """
    selected = {}

    for chunk in agreement_chunks(conn, account_id):
        selected[chunk["chunk_id"]] = chunk

    if query:
        hits = search_scoped_chunks(conn, account_id, query, include_historical)
    else:
        hits = all_scoped_chunks(conn, account_id, include_historical)
    for chunk in hits:
        selected.setdefault(chunk["chunk_id"], chunk)

    # Deterministic order: authoritative ranks first, then chunk_id.
    ordered = sorted(selected.values(), key=lambda c: (c["authority_rank"], c["chunk_id"]))
    if not include_historical:
        ordered = [c for c in ordered if c["status"] not in NON_AUTHORITATIVE_STATUSES]
    ordered = ordered[:_MAX_RESULTS]

    records = [evidence_from_chunk(chunk, as_of) for chunk in ordered]
    return resolve_conflicts(records)
