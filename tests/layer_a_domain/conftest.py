"""Layer A shared fixtures (04_EVAL_SPEC.md §2 — no LLM anywhere).

Inputs come from the real assessment data pack via the same loaders the
seeder uses, so golden cases run on the actual workbook rows. Expected
outcomes are asserted from docs/02_DOMAIN_SPEC.md §5 (the locked answer key).
"""

import re
from pathlib import Path

import pytest

from backend.db import documents
from backend.db.database import open_database
from backend.db.seed import seed_database
from backend.db.workbook import load_dataset
from backend.domain.policy_data import get_agreement

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_PACK = PROJECT_ROOT / "assessment_docs"


@pytest.fixture(scope="session")
def dataset():
    return load_dataset(DATA_PACK)


@pytest.fixture(scope="session")
def accounts_by_id(dataset):
    return {row["account_id"]: row for row in dataset["accounts"]}


@pytest.fixture(scope="session")
def orders_by_id(dataset):
    return {row["order_id"]: row for row in dataset["orders"]}


@pytest.fixture(scope="session")
def tickets_by_id(dataset):
    return {row["ticket_id"]: row for row in dataset["tickets"]}


@pytest.fixture(scope="session")
def seeded_db(tmp_path_factory, dataset):
    """A seeded SQLite database (session-scoped, read-only for tests)."""
    db_path = tmp_path_factory.mktemp("db") / "parcel_pilot.db"
    counts = seed_database(db_path, DATA_PACK)
    conn = open_database(db_path)
    yield conn, counts
    conn.close()


@pytest.fixture(scope="session")
def all_chunks(seeded_db, dataset):
    """Every chunk row as a plain dict (metadata + text)."""
    conn, _ = seeded_db
    rows = conn.execute(
        "SELECT source_doc, section, status, effective, scope, authority_rank, text"
        " FROM document_chunks ORDER BY chunk_id"
    ).fetchall()
    return [dict(row) for row in rows]


@pytest.fixture(scope="session")
def customer_sessions():
    """Trusted customer sessions, one per agreement-holding account.

    These stand in for the identity the runtime injects server-side —
    the model never supplies them (03_AGENT_SPEC.md §1).
    """
    return {
        "ACCT-001": {"role": "customer", "account_id": "ACCT-001",
                     "session_id": "sess-acct-001"},
        "ACCT-002": {"role": "customer", "account_id": "ACCT-002",
                     "session_id": "sess-acct-002"},
    }


@pytest.fixture(scope="session")
def staff_session():
    return {"role": "staff", "staff_id": "STF-001", "session_id": "sess-staff",
            "permissions": ("support",)}


def agreement_for(account_id):
    """Helper: the account's active agreement record, or None."""
    return get_agreement(account_id)


def _to_fts_query(query):
    """Quote every word token so arbitrary user text is safe FTS5 syntax."""
    tokens = re.findall(r"\w+", query)
    if not tokens:
        raise ValueError(f"empty FTS query: {query!r}")
    return " ".join(f'"{token}"' for token in tokens)


def fts_search(conn, query, account_id=None, authoritative_only=False):
    """FTS5 query returning chunk dicts, with optional authority filtering.

    Mirrors how the Phase 3 evidence resolver will query: lexical match first,
    then the deterministic authority/scope filter — never the other way round.
    """
    rows = conn.execute(
        "SELECT c.source_doc, c.section, c.status, c.effective, c.scope,"
        "       c.authority_rank, c.text"
        " FROM chunks_fts f JOIN document_chunks c ON c.chunk_id = f.rowid"
        " WHERE chunks_fts MATCH ? ORDER BY rank",
        (_to_fts_query(query),),
    ).fetchall()
    chunks = [dict(row) for row in rows]
    if authoritative_only:
        chunks = documents.authoritative_chunks(chunks, account_id)
    elif account_id is not None:
        chunks = [c for c in chunks if documents.visible_to_account(c, account_id)]
    return chunks
