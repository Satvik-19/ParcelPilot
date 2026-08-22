"""Scope-constrained retrieval — the only read path tools may use.

Every query here is constrained by the trusted session account_id *before*
results are returned (PRD FR-2, 03_AGENT_SPEC.md §3). Arbitrary user text is
neutralised by to_fts_query() and bound as a parameter, so it can never
become a raw SQL/FTS expression capable of bypassing authorization.

Authority filtering (DEPRECATED/historical exclusion) is applied on top of
scope by backend/trust/evidence.py — retrieval authorization and evidence
applicability are distinct concepts (03_AGENT_SPEC.md §5).
"""

from backend.db.documents import to_fts_query, visible_to_account

_CHUNK_FIELDS = (
    "c.chunk_id, c.source_doc, c.section, c.status, c.effective, c.scope,"
    " c.authority_rank, c.text"
)


def _rows_to_dicts(rows):
    return [dict(row) for row in rows]


def search_scoped_chunks(conn, account_id, query, include_historical=False):
    """FTS search whose results can never leave the account's scope.

    `account_id` must already be the scope the session is authorized for —
    the caller (tool layer) checks that via security.authorization before
    getting here. Each chunk keeps its bm25 ``fts_rank`` so the evidence
    layer can prefer better lexical matches when trimming to the cap.
    """
    rows = conn.execute(
        f"SELECT {_CHUNK_FIELDS}, rank AS fts_rank"
        " FROM chunks_fts f JOIN document_chunks c ON c.chunk_id = f.rowid"
        " WHERE chunks_fts MATCH ? ORDER BY rank, c.chunk_id",
        (to_fts_query(query),),
    ).fetchall()
    chunks = [
        row for row in _rows_to_dicts(rows) if visible_to_account(row, account_id)
    ]
    if not include_historical:
        chunks = [
            c for c in chunks if c["status"] not in ("DEPRECATED", "HISTORICAL_RESOLUTION")
        ]
    return chunks


def all_scoped_chunks(conn, account_id, include_historical=False):
    """Scope-constrained scan without a lexical query (force-inclusion paths)."""
    rows = conn.execute(
        f"SELECT {_CHUNK_FIELDS}, 0 AS fts_rank FROM document_chunks c"
        " ORDER BY c.chunk_id"
    ).fetchall()
    chunks = [
        row for row in _rows_to_dicts(rows) if visible_to_account(row, account_id)
    ]
    if not include_historical:
        chunks = [
            c for c in chunks if c["status"] not in ("DEPRECATED", "HISTORICAL_RESOLUTION")
        ]
    return chunks


def agreement_chunks(conn, account_id):
    """Force-included active agreement for the account (03_AGENT_SPEC.md §3).

    Agreements are seeded with scope=<account_id>, status=ACTIVE, rank=1, so
    the scope constraint alone selects exactly the right document.
    """
    rows = conn.execute(
        f"SELECT {_CHUNK_FIELDS}, 0 AS fts_rank FROM document_chunks c"
        " WHERE c.scope = ? AND c.authority_rank = 1 ORDER BY c.chunk_id",
        (account_id,),
    ).fetchall()
    return _rows_to_dicts(rows)
