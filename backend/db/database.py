"""SQLite connection handling and schema initialisation.

The whole persistence layer is one SQLite file (ADR-001): relational tables
plus an FTS5 virtual table over document chunks. No ORM, no migrations —
schema.sql is applied idempotently (CREATE ... IF NOT EXISTS).
"""

import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def connect(db_path, check_same_thread=True):
    """Open a connection with row access by name and FK enforcement.

    ``check_same_thread=False`` is for the HTTP app only: one connection is
    shared across handler threads, serialised by the app-level lock.
    """
    conn = sqlite3.connect(str(db_path), check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn):
    """Apply schema.sql (idempotent)."""
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
    return conn


def open_database(db_path, check_same_thread=True):
    """Connect and ensure the schema exists."""
    return init_db(connect(db_path, check_same_thread=check_same_thread))
