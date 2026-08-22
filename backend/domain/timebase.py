"""Fixed snapshot time for all business logic (docs/02_DOMAIN_SPEC.md).

SNAPSHOT_TS comes from the assessment workbook README sheet ("Dataset
snapshot"). Every time in the dataset is a local Asia/Kolkata wall-clock
value; this module keeps them as naive datetimes so all arithmetic is
deterministic and environment-independent.

Business logic must NEVER read the wall clock (05_CODING_AGENT_RULES.md §4).
Time-dependent functions take an explicit ``as_of`` defaulting to SNAPSHOT_TS.
"""

from datetime import datetime

# 2026-08-16 11:00 Asia/Kolkata (a Sunday — relevant for business-time math)
SNAPSHOT_TS = datetime(2026, 8, 16, 11, 0)

_TS_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M")


def parse_ts(value):
    """Parse a dataset timestamp (datetime passthrough or ISO-like string)."""
    if isinstance(value, datetime):
        return value
    if value is None:
        return None
    text = str(value).strip()
    for fmt in _TS_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"unrecognised timestamp: {value!r}")


def format_ts(value):
    """Canonical string form used for SQLite TEXT columns."""
    if value is None:
        return None
    return parse_ts(value).strftime("%Y-%m-%d %H:%M")
