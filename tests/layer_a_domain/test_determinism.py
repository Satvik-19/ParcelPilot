"""Time determinism and code hygiene guards.

Business logic must never touch the wall clock (05_CODING_AGENT_RULES.md §4):
SNAPSHOT_TS is the only clock, and it is pinned here as a constant so any
future drift fails the suite.
"""

from datetime import datetime
from pathlib import Path

import pytest

from backend.domain.timebase import SNAPSHOT_TS, format_ts, parse_ts

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND = PROJECT_ROOT / "backend"
# Business-logic packages. backend/api/ is excluded on purpose: it is the
# application boundary where 03_AGENT_SPEC.md §4 check 6 requires the draft
# expiry to be checked against the CONFIRMATION REQUEST time — the wall clock
# lives there (injectable for tests) and nowhere else.
BUSINESS_PACKAGES = ("domain", "tools", "agent", "trust", "security",
                     "actions", "db")
BUSINESS_SOURCES = sorted(
    path for package in BUSINESS_PACKAGES
    for path in (BACKEND / package).rglob("*.py")
)


def test_snapshot_ts_is_pinned():
    assert SNAPSHOT_TS == datetime(2026, 8, 16, 11, 0)


def test_parse_ts_roundtrip():
    assert parse_ts(SNAPSHOT_TS) is SNAPSHOT_TS
    assert parse_ts("2026-08-16 11:00") == SNAPSHOT_TS
    assert parse_ts("2026-08-16T11:00") == SNAPSHOT_TS
    assert parse_ts(None) is None
    assert format_ts(SNAPSHOT_TS) == "2026-08-16 11:00"
    assert format_ts(None) is None


def test_parse_ts_rejects_garbage():
    with pytest.raises(ValueError):
        parse_ts("16/08/2026 11am")


@pytest.mark.parametrize("path", BUSINESS_SOURCES)
def test_no_wall_clock_in_business_logic(path):
    """datetime.now()/utcnow()/today() are forbidden in business logic."""
    source = path.read_text(encoding="utf-8")
    for banned in ("datetime.now", "utcnow(", "date.today", "time.time()"):
        assert banned not in source, f"{path.name} uses wall-clock time ({banned})"
