"""Deterministic seeder: assessment data pack -> SQLite + FTS5.

Reads ONLY from assessment_docs/ (source of truth). Seeds the workbook rows
(accounts/orders/tickets) and the document chunk index (six PDFs + historical
ticket resolutions, each with status/effective/scope/authority metadata), then
rebuilds the FTS5 index. Acceptance counts are asserted, not assumed
(docs/handoffs/session-00.md): 4 accounts / 6 orders / 7 tickets.

Nothing here invents data; anything absent from the workbook stays NULL.
"""

import sys
from pathlib import Path

from . import documents
from .database import open_database
from .workbook import load_dataset
from backend.domain.timebase import format_ts

DEFAULT_DB_PATH = Path("data") / "parcel_pilot.db"
DEFAULT_DATA_PACK = Path("assessment_docs")

EXPECTED_COUNTS = {"accounts": 4, "orders": 6, "tickets": 7}

_ACCOUNT_COLUMNS = (
    "account_id", "account_name", "plan", "status",
    "csm", "contract_file", "premium_support", "notes",
)
_ORDER_COLUMNS = (
    "order_id", "account_id", "carrier", "status",
    "booked_at", "pickup_window_start", "pickup_window_end", "pickup_actual_at",
    "shipment_fee_inr", "carrier_fault", "customer_fault",
    "cancellation_requested_at", "notes",
)
_TICKET_COLUMNS = (
    "ticket_id", "account_id", "created_at", "status", "subject", "description",
    "channel", "assigned_to", "last_customer_message_at", "historical_resolution",
)


def _bool_int(value):
    """SQLite stores booleans as INTEGER; None stays None (unknown fault)."""
    if value is None:
        return None
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "yes", "1"):
            return 1
        if lowered in ("false", "no", "0", ""):
            return 0
        raise ValueError(f"unrecognised boolean value: {value!r}")
    return int(bool(value))


def _row_values(row, columns):
    return tuple(row.get(col) for col in columns)


def _insert_rows(conn, table, columns, rows):
    placeholders = ", ".join("?" for _ in columns)
    conn.executemany(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
        [_row_values(row, columns) for row in rows],
    )


def _seed_workbook(conn, dataset):
    for table, key, columns in (
        ("accounts", "accounts", _ACCOUNT_COLUMNS),
        ("orders", "orders", _ORDER_COLUMNS),
        ("tickets", "tickets", _TICKET_COLUMNS),
    ):
        rows = dataset[key]
        if len(rows) != EXPECTED_COUNTS[key]:
            raise AssertionError(
                f"workbook holds {len(rows)} {key}, expected {EXPECTED_COUNTS[key]} "
                f"(docs/handoffs/session-00.md)"
            )
        prepared = []
        for row in rows:
            out = dict(row)
            for col in columns:
                value = out.get(col)
                if col in ("premium_support", "carrier_fault", "customer_fault"):
                    out[col] = _bool_int(value)
                elif col.endswith("_at"):
                    out[col] = format_ts(value)
            prepared.append(out)
        _insert_rows(conn, table, columns, prepared)


def _seed_documents(conn, data_pack_dir, tickets):
    chunk_rows = []
    for doc_key in documents.DOCUMENT_CATALOG:
        chunk_rows.extend(documents.document_chunks(doc_key, data_pack_dir))
    chunk_rows.extend(documents.historical_resolution_chunks(tickets))
    _insert_rows(
        conn,
        "document_chunks",
        ("source_doc", "section", "status", "effective", "scope", "authority_rank", "text"),
        chunk_rows,
    )
    # External-content FTS5 index: bulk load, then rebuild from document_chunks.
    conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
    return len(chunk_rows)


def seed_database(db_path, data_pack_dir=DEFAULT_DATA_PACK):
    """Create db_path and seed it from the assessment data pack. Returns counts."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()  # seeding is always from scratch — no stale state

    dataset = load_dataset(data_pack_dir)
    conn = open_database(db_path)
    try:
        with conn:
            _seed_workbook(conn, dataset)
            chunk_count = _seed_documents(conn, data_pack_dir, dataset["tickets"])
    finally:
        conn.close()
    return {
        "accounts": EXPECTED_COUNTS["accounts"],
        "orders": EXPECTED_COUNTS["orders"],
        "tickets": EXPECTED_COUNTS["tickets"],
        "document_chunks": chunk_count,
    }


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    db_path = Path(argv[0]) if argv else DEFAULT_DB_PATH
    data_pack = Path(argv[1]) if len(argv) > 1 else DEFAULT_DATA_PACK
    counts = seed_database(db_path, data_pack)
    print(f"Seeded {db_path}: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
