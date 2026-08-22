"""Layer B shared fixtures (04_EVAL_SPEC.md §3).

Deterministic tests here use a freshly seeded database — never the shared
data/parcel_pilot.db — so drafted actions or seeded probe rows can never leak
into other suites. Live tests (marker `live`) manage their own per-case DB
copies inside the harness.
"""

from pathlib import Path

import pytest

from backend.db.database import open_database
from backend.db.seed import seed_database

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_PACK = PROJECT_ROOT / "assessment_docs"


@pytest.fixture(scope="session")
def seeded_conn(tmp_path_factory):
    """Session-scoped freshly seeded DB connection (read-only for tests)."""
    db_path = tmp_path_factory.mktemp("layerb") / "parcel_pilot.db"
    seed_database(db_path, DATA_PACK)
    conn = open_database(db_path)
    yield conn
    conn.close()
