"""Seeded database validation — counts, round-trips, chunk metadata, FTS5.

Acceptance counts come from docs/handoffs/session-00.md: 4 accounts, 6 orders,
7 tickets. Document metadata must preserve status, effective date, scope and
authority rank exactly as the verified catalog states them.
"""

from backend.db.documents import DOCUMENT_CATALOG
from backend.domain.timebase import SNAPSHOT_TS


# --- Row counts and round-trips -------------------------------------------------

def test_seeded_row_counts(seeded_db):
    conn, counts = seeded_db
    assert counts == {"accounts": 4, "orders": 6, "tickets": 7,
                      "document_chunks": counts["document_chunks"]}
    for table, expected in (("accounts", 4), ("orders", 6), ("tickets", 7)):
        actual = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        assert actual == expected


def test_order_fault_attribution_roundtrips(seeded_db):
    """Fault attribution survives the round-trip; the NULL case (unknown fault)
    is exercised by the synthetic credit tests in test_credits.py."""
    conn, _ = seeded_db
    row = conn.execute(
        "SELECT * FROM orders WHERE order_id = 'ORD-4001'"
    ).fetchone()
    assert row["carrier_fault"] == 0
    assert row["customer_fault"] == 0
    assert row["shipment_fee_inr"] == 3600
    assert row["status"] == "DELIVERED"
    # Exactly one carrier-fault order exists in the dataset (golden case 3).
    faulted = conn.execute(
        "SELECT order_id FROM orders WHERE carrier_fault = 1"
    ).fetchall()
    assert [r["order_id"] for r in faulted] == ["ORD-2002"]


def test_order_timestamps_stored_as_canonical_text(seeded_db):
    conn, _ = seeded_db
    row = conn.execute(
        "SELECT booked_at, cancellation_requested_at FROM orders"
        " WHERE order_id = 'ORD-1001'"
    ).fetchone()
    assert row["booked_at"] == "2026-08-16 09:00"
    assert row["cancellation_requested_at"] == "2026-08-16 11:00"


def test_fault_booleans_stored_as_integers(seeded_db):
    conn, _ = seeded_db
    row = conn.execute(
        "SELECT carrier_fault, customer_fault FROM orders WHERE order_id = 'ORD-2002'"
    ).fetchone()
    assert row["carrier_fault"] == 1
    assert row["customer_fault"] == 0


def test_historical_resolutions_only_on_closed_tickets(seeded_db):
    conn, _ = seeded_db
    rows = conn.execute(
        "SELECT ticket_id, status, historical_resolution FROM tickets"
    ).fetchall()
    historical = {r["ticket_id"] for r in rows if r["historical_resolution"]}
    assert historical == {"TKT-450", "TKT-451"}
    assert all(r["status"] == "closed" for r in rows if r["ticket_id"] in historical)


def test_actions_table_exists_and_is_empty(seeded_db):
    conn, _ = seeded_db
    assert conn.execute("SELECT COUNT(*) FROM actions").fetchone()[0] == 0


# --- Document chunk metadata ------------------------------------------------------

def test_every_catalog_document_is_indexed(all_chunks):
    sources = {c["source_doc"] for c in all_chunks}
    for doc_key, meta in DOCUMENT_CATALOG.items():
        assert doc_key in sources
        doc_chunks = [c for c in all_chunks if c["source_doc"] == doc_key]
        assert all(c["status"] == meta["status"] for c in doc_chunks)
        assert all(c["effective"] == meta["effective"] for c in doc_chunks)
        assert all(c["scope"] == meta["scope"] for c in doc_chunks)
        assert all(c["authority_rank"] == meta["authority_rank"] for c in doc_chunks)


def test_agreements_are_account_scoped_rank_1(all_chunks):
    northstar = [c for c in all_chunks
                 if c["source_doc"] == "05_Northstar_Logistics_Enterprise_Agreement"]
    lumenworks = [c for c in all_chunks
                  if c["source_doc"] == "06_LumenWorks_Service_Agreement"]
    assert northstar and lumenworks
    assert all(c["scope"] == "ACCT-001" and c["authority_rank"] == 1 for c in northstar)
    assert all(c["scope"] == "ACCT-002" and c["authority_rank"] == 1 for c in lumenworks)


def test_historical_chunks_are_rank_5(all_chunks):
    historical = [c for c in all_chunks if c["source_doc"] == "tickets"]
    assert len(historical) == 2
    assert all(c["status"] == "HISTORICAL_RESOLUTION" for c in historical)
    assert all(c["authority_rank"] == 5 for c in historical)
    assert {c["scope"] for c in historical} == {"ACCT-001", "ACCT-002"}


def test_sop_full_text_survived_extraction(all_chunks):
    """Phase 0 gotcha: plain-mode pypdf truncated SOP §2 — assert the real text."""
    sop = [c for c in all_chunks
           if c["source_doc"] == "03_Cancellation_and_Service_Credit_SOP_v4"]
    text = " ".join(c["text"] for c in sop)
    assert "250" in text          # late fee
    assert "500" in text          # credit cap
    assert "omitted" not in text.lower()  # no extraction truncation marker


# --- FTS5 index --------------------------------------------------------------------

def test_fts_matches_join_back_to_chunks(seeded_db):
    conn, _ = seeded_db
    rows = conn.execute(
        "SELECT c.source_doc, c.section FROM chunks_fts f"
        " JOIN document_chunks c ON c.chunk_id = f.rowid"
        " WHERE chunks_fts MATCH 'webhook'"
    ).fetchall()
    assert any(r["source_doc"] == "04_Product_Operations_Guide_and_Known_Issues"
               for r in rows)


def test_fts_rowid_mapping_is_consistent(seeded_db):
    """External-content integrity: every FTS rowid resolves to a real chunk."""
    conn, _ = seeded_db
    orphan = conn.execute(
        "SELECT COUNT(*) FROM chunks_fts f"
        " LEFT JOIN document_chunks c ON c.chunk_id = f.rowid"
        " WHERE c.chunk_id IS NULL"
    ).fetchone()[0]
    assert orphan == 0


def test_fts_finds_deprecated_doc_but_metadata_neutralises_it(seeded_db):
    """v2 is searchable (explicit-request retrieval) yet carries rank 4."""
    conn, _ = seeded_db
    rows = conn.execute(
        "SELECT c.status, c.authority_rank FROM chunks_fts f"
        " JOIN document_chunks c ON c.chunk_id = f.rowid"
        " WHERE chunks_fts MATCH 'policy'"
    ).fetchall()
    deprecated = [r for r in rows if r["status"] == "DEPRECATED"]
    assert deprecated
    assert all(r["authority_rank"] == 4 for r in deprecated)


def test_seeded_snapshot_reference(seeded_db):
    """The dataset README's snapshot timestamp is the one the domain layer pins."""
    conn, _ = seeded_db
    latest = max(
        conn.execute("SELECT created_at FROM tickets").fetchall(),
        key=lambda r: r["created_at"],
    )["created_at"]
    assert latest <= "2026-08-16 11:00"
    assert SNAPSHOT_TS.strftime("%Y-%m-%d %H:%M") == "2026-08-16 11:00"
