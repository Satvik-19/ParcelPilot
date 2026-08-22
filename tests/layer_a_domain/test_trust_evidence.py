"""Phase 3 — trust/evidence + conflicts (Layer A).

Evidence records must carry the AGENT_SPEC §5 fields; the account's active
agreement is force-included; deprecated/historical sources never decide
(GI-1) and conflicts are resolved purely by authority rank.
"""

import pytest

from backend.trust.conflicts import resolve_conflicts, topic_of
from backend.trust.evidence import (
    EvidenceRecord,
    evidence_from_chunk,
    gather_evidence,
)

SECTION_FIELDS = (
    "evidence_id", "source_doc", "section", "status", "authority_rank",
    "applicable_to", "overridden_by", "excluded_reason", "text",
)


# --- §5 record shape -----------------------------------------------------------

def test_evidence_records_carry_all_spec_fields(seeded_db):
    conn, _ = seeded_db
    records = gather_evidence(conn, "ACCT-001", query="cancellation fee")
    assert records
    for rec in records:
        assert isinstance(rec, EvidenceRecord)
        for field in SECTION_FIELDS:
            assert hasattr(rec, field)
        assert rec.evidence_id == f"{rec.source_doc}#{rec.section}"
        assert rec.text


def test_applicable_to_states_scope_and_validity(seeded_db):
    conn, _ = seeded_db
    records = gather_evidence(conn, "ACCT-001", query="cancellation fee")
    agreement_rec = next(r for r in records if r.source_doc.startswith("05_"))
    assert "account ACCT-001" in agreement_rec.applicable_to
    sop_rec = next(r for r in records if r.source_doc.startswith("03_"))
    assert "all accounts" in sop_rec.applicable_to
    assert "effective" in sop_rec.applicable_to


def test_historical_records_carry_none_rank_and_excluded_reason(seeded_db):
    conn, _ = seeded_db
    records = gather_evidence(conn, "ACCT-001", query="cancellation fee",
                              include_historical=True)
    historical = [r for r in records if r.status == "HISTORICAL_RESOLUTION"]
    assert historical
    for rec in historical:
        assert rec.authority_rank is None          # NONE — never decides
        assert rec.excluded_reason                  # flagged, not silent
        assert "context only" in rec.excluded_reason.lower()


# --- Force-inclusion + GI-1 ------------------------------------------------------

@pytest.mark.parametrize("query", ["billing contact", "webhook delay", "nothing matches xyz"])
def test_active_agreement_force_included_even_for_unrelated_queries(seeded_db, query):
    conn, _ = seeded_db
    records = gather_evidence(conn, "ACCT-001", query=query)
    assert any(r.source_doc.startswith("05_") for r in records)


def test_gi1_deprecated_never_in_evidence(seeded_db):
    conn, _ = seeded_db
    for query in ("response time", "sla", "resolution time", "severity P1"):
        records = gather_evidence(conn, "ACCT-001", query=query)
        assert all(r.status != "DEPRECATED" for r in records), query
        assert all(r.authority_rank != 4 for r in records), query


def test_evidence_ranks_only_1_to_3_or_none(seeded_db):
    conn, _ = seeded_db
    records = gather_evidence(conn, "ACCT-002", query="credit threshold",
                              include_historical=True)
    for rec in records:
        assert rec.authority_rank is None or rec.authority_rank in (1, 2, 3)


# --- Conflict resolution ----------------------------------------------------------

def test_agreement_overrides_sop_with_traceable_marker(seeded_db):
    conn, _ = seeded_db
    records = gather_evidence(conn, "ACCT-001", query="cancellation fee")
    sop = [r for r in records if r.source_doc.startswith("03_")]
    agreement = [r for r in records if r.source_doc.startswith("05_")]
    assert sop and agreement
    winner = min(agreement, key=lambda r: r.evidence_id)
    for rec in sop:
        assert rec.overridden_by == winner.evidence_id  # explicit, traceable
    assert all(rec.overridden_by is None for rec in agreement)


def test_no_conflict_markers_without_an_agreement(seeded_db):
    conn, _ = seeded_db
    # An account with no seeded agreement: the SOP stands alone.
    records = gather_evidence(conn, "ACCT-003", query="cancellation fee")
    assert records
    assert all(r.authority_rank != 1 for r in records)
    sop = [r for r in records if r.source_doc.startswith("03_")]
    assert sop and all(r.overridden_by is None for r in sop)


def test_historical_sources_never_win_or_lose_conflicts():
    fake_historical = EvidenceRecord(
        evidence_id="tickets#TKT-450", source_doc="tickets", section="TKT-450",
        status="HISTORICAL_RESOLUTION", authority_rank=None,
        applicable_to="context only", text="agent waived the fee",
    )
    fake_sop = EvidenceRecord(
        evidence_id="sop#s1", source_doc="03_Cancellation_and_Service_Credit_SOP_v4",
        section="s1", status="CURRENT", authority_rank=2,
        applicable_to="all accounts", text="fee rule",
    )
    resolved = resolve_conflicts([fake_historical, fake_sop])
    assert all(r.overridden_by is None for r in resolved)


def test_higher_rank_wins_and_equal_rank_does_not_conflict():
    sop_a = EvidenceRecord(
        evidence_id="03_sop#a", source_doc="03_Cancellation_and_Service_Credit_SOP_v4",
        section="a", status="CURRENT", authority_rank=2, applicable_to="x", text="t",
    )
    sop_b = EvidenceRecord(
        evidence_id="03_sop#b", source_doc="03_Cancellation_and_Service_Credit_SOP_v4",
        section="b", status="CURRENT", authority_rank=2, applicable_to="x", text="t",
    )
    agreement = EvidenceRecord(
        evidence_id="05_agreement#a", source_doc="05_Northstar_Enterprise_Agreement",
        section="a", status="ACTIVE", authority_rank=1, applicable_to="x", text="t",
    )
    resolved = resolve_conflicts([sop_a, sop_b, agreement])
    by_id = {r.evidence_id: r for r in resolved}
    assert by_id["03_sop#a"].overridden_by == "05_agreement#a"
    assert by_id["03_sop#b"].overridden_by == "05_agreement#a"
    assert by_id["05_agreement#a"].overridden_by is None  # winner unmarked


def test_topic_mapping_covers_the_six_documents():
    assert topic_of("01_Support_Policy_v3") == "support_policy"
    assert topic_of("02_Support_Policy_v2_DEPRECATED") == "support_policy"
    assert topic_of("03_Cancellation_and_Service_Credit_SOP_v4") == "cancellation_credit"
    assert topic_of("04_Product_Operations_Guide") == "operations_known_issues"
    assert topic_of("05_Northstar_Enterprise_Agreement") == "cancellation_credit"
    assert topic_of("06_LumenWorks_Enterprise_Agreement") == "cancellation_credit"
    assert topic_of("tickets") is None


def test_evidence_from_chunk_promotes_metadata():
    chunk = {
        "chunk_id": 1, "source_doc": "01_Support_Policy_v3", "section": "§2",
        "status": "CURRENT", "effective": "2026-07-01", "scope": "GENERAL",
        "authority_rank": 2, "text": "...",
    }
    rec = evidence_from_chunk(chunk)
    assert rec.authority_rank == 2
    assert rec.excluded_reason is None
    assert "2026-07-01" in rec.applicable_to
