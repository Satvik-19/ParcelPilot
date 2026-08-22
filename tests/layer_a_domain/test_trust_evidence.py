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
    sop = [r for r in records
           if r.source_doc.startswith("03_") and r.section != "Header"]
    agreement = [r for r in records
                 if r.source_doc.startswith("05_") and r.section != "Header"]
    assert sop and agreement
    # Each SOP section is overridden by the agreement section that covers
    # the SAME subtopic — not by the agreement Header (metadata).
    for rec in sop:
        assert rec.overridden_by is not None, (
            f"SOP {rec.section} should be overridden by a matching agreement"
            " section"
        )
        assert "#Header" not in rec.overridden_by, (
            "agreement Header must never override a substantive SOP section"
        )
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
    """Subtopic-aware conflicts: same subtopic → higher rank wins;
    different subtopics → no conflict even at the same rank."""
    sop_cancel = EvidenceRecord(
        evidence_id="03_sop#Section 1: Order cancellation",
        source_doc="03_Cancellation_and_Service_Credit_SOP_v4",
        section="Section 1: Order cancellation", status="CURRENT",
        authority_rank=2, applicable_to="all accounts", text="t",
    )
    sop_credits = EvidenceRecord(
        evidence_id="03_sop#Section 2: Failed-pickup service credits",
        source_doc="03_Cancellation_and_Service_Credit_SOP_v4",
        section="Section 2: Failed-pickup service credits", status="CURRENT",
        authority_rank=2, applicable_to="all accounts", text="t",
    )
    agreement_cancel = EvidenceRecord(
        evidence_id="05_agree#Section 2: Shipment cancellation",
        source_doc="05_Northstar_Logistics_Enterprise_Agreement",
        section="Section 2: Shipment cancellation", status="ACTIVE",
        authority_rank=1, applicable_to="account ACCT-001", text="t",
    )
    resolved = resolve_conflicts([sop_cancel, sop_credits, agreement_cancel])
    by_id = {r.evidence_id: r for r in resolved}
    # SOP cancellation overridden by agreement cancellation (same subtopic):
    assert by_id["03_sop#Section 1: Order cancellation"].overridden_by == \
        "05_agree#Section 2: Shipment cancellation"
    # SOP credits NOT overridden — no matching agreement credit section:
    assert by_id["03_sop#Section 2: Failed-pickup service credits"].overridden_by is None
    # Agreement winner stays unmarked:
    assert by_id["05_agree#Section 2: Shipment cancellation"].overridden_by is None


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


# --- Subtopic-aware conflict resolution ----------------------------------------

def test_header_never_wins_or_overrides_substantive_section():
    """Document Headers are metadata — they never participate as winners
    in conflict resolution and never mark a substantive section as
    overridden."""
    header = EvidenceRecord(
        evidence_id="05_agree#Header",
        source_doc="05_Northstar_Logistics_Enterprise_Agreement",
        section="Header", status="ACTIVE", authority_rank=1,
        applicable_to="account ACCT-001", text="agreement metadata",
    )
    sop_cancel = EvidenceRecord(
        evidence_id="03_sop#Section 1: Order cancellation",
        source_doc="03_Cancellation_and_Service_Credit_SOP_v4",
        section="Section 1: Order cancellation", status="CURRENT",
        authority_rank=2, applicable_to="all accounts", text="t",
    )
    resolved = resolve_conflicts([header, sop_cancel])
    by_id = {r.evidence_id: r for r in resolved}
    # Header stays in the trace but never marks anything as overridden:
    assert by_id["05_agree#Header"].overridden_by is None
    # SOP section is NOT overridden because the Header has no subtopic:
    assert by_id["03_sop#Section 1: Order cancellation"].overridden_by is None


def test_unrelated_agreement_section_does_not_override_unrelated_sop():
    """An agreement's cancellation section must not override the SOP's
    credit section — they govern different policy subjects."""
    agree_cancel = EvidenceRecord(
        evidence_id="05_agree#Section 2: Shipment cancellation",
        source_doc="05_Northstar_Logistics_Enterprise_Agreement",
        section="Section 2: Shipment cancellation", status="ACTIVE",
        authority_rank=1, applicable_to="account ACCT-001", text="t",
    )
    sop_credits = EvidenceRecord(
        evidence_id="03_sop#Section 2: Failed-pickup service credits",
        source_doc="03_Cancellation_and_Service_Credit_SOP_v4",
        section="Section 2: Failed-pickup service credits", status="CURRENT",
        authority_rank=2, applicable_to="all accounts", text="t",
    )
    resolved = resolve_conflicts([agree_cancel, sop_credits])
    by_id = {r.evidence_id: r for r in resolved}
    assert by_id["03_sop#Section 2: Failed-pickup service credits"].overridden_by is None
    assert by_id["05_agree#Section 2: Shipment cancellation"].overridden_by is None


def test_same_subtopic_higher_rank_wins():
    """When two records share a topic AND subtopic, the higher-rank
    record wins — the loser's overridden_by points to the winner."""
    sop_credits = EvidenceRecord(
        evidence_id="03_sop#Section 2: Failed-pickup service credits",
        source_doc="03_Cancellation_and_Service_Credit_SOP_v4",
        section="Section 2: Failed-pickup service credits", status="CURRENT",
        authority_rank=2, applicable_to="all accounts", text="t",
    )
    agree_credits = EvidenceRecord(
        evidence_id="05_agree#Section 3: Service credits",
        source_doc="05_Northstar_Logistics_Enterprise_Agreement",
        section="Section 3: Service credits", status="ACTIVE",
        authority_rank=1, applicable_to="account ACCT-001", text="t",
    )
    resolved = resolve_conflicts([sop_credits, agree_credits])
    by_id = {r.evidence_id: r for r in resolved}
    assert by_id["03_sop#Section 2: Failed-pickup service credits"].overridden_by == \
        "05_agree#Section 3: Service credits"
    assert by_id["05_agree#Section 3: Service credits"].overridden_by is None


def test_sop_approval_not_overridden_by_unrelated_agreement_section():
    """SOP §3 (Approval and uncertainty) has no counterpart in the
    agreement — it must not be overridden by an unrelated section."""
    sop_approval = EvidenceRecord(
        evidence_id="03_sop#Section 3: Approval and uncertainty",
        source_doc="03_Cancellation_and_Service_Credit_SOP_v4",
        section="Section 3: Approval and uncertainty", status="CURRENT",
        authority_rank=2, applicable_to="all accounts", text="t",
    )
    agree_cancel = EvidenceRecord(
        evidence_id="05_agree#Section 2: Shipment cancellation",
        source_doc="05_Northstar_Logistics_Enterprise_Agreement",
        section="Section 2: Shipment cancellation", status="ACTIVE",
        authority_rank=1, applicable_to="account ACCT-001", text="t",
    )
    agree_credits = EvidenceRecord(
        evidence_id="05_agree#Section 3: Service credits",
        source_doc="05_Northstar_Logistics_Enterprise_Agreement",
        section="Section 3: Service credits", status="ACTIVE",
        authority_rank=1, applicable_to="account ACCT-001", text="t",
    )
    resolved = resolve_conflicts([sop_approval, agree_cancel, agree_credits])
    by_id = {r.evidence_id: r for r in resolved}
    assert by_id["03_sop#Section 3: Approval and uncertainty"].overridden_by is None


# --- Evidence slot source-diversity allocation ---------------------------------

def test_non_agreement_evidence_gets_minimum_representation(seeded_db):
    """When an agreement's chunks would fill most of the 8-slot cap,
    the diversity allocator reserves slots for non-agreement (GENERAL-scoped)
    evidence so rank-3 operational/known-issue sources are not crowded out."""
    conn, _ = seeded_db
    # ACCT-001 has a Northstar agreement (rank 1, multiple sections).
    # A broad query returns many chunks; verify non-agreement evidence
    # survives the cap.
    records = gather_evidence(conn, "ACCT-001", query="service credit pickup")
    non_agreement = [r for r in records if "all accounts" in r.applicable_to]
    agreement = [r for r in records if "all accounts" not in r.applicable_to]
    # At least some non-agreement evidence must survive:
    assert len(non_agreement) >= 1, (
        f"expected at least 1 non-agreement record, got {len(non_agreement)}")
    assert len(records) <= 8
    # Agreement records should still be present:
    assert len(agreement) >= 1


def test_no_diversity_cap_without_agreement(seeded_db):
    """For accounts without an agreement (ACCT-003), the diversity cap is
    irrelevant — all chunks are GENERAL-scoped and the standard ranking
    applies."""
    conn, _ = seeded_db
    records = gather_evidence(conn, "ACCT-003", query="known issue")
    # All records should be GENERAL-scoped:
    assert all("all accounts" in r.applicable_to for r in records)
    assert len(records) <= 8

