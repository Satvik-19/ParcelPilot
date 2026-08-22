"""Deterministic business-time arithmetic.

The supplied policies use "business hours" / "business days" (Support Policy
v3 §3, both agreements) but never define them. Fixed, documented convention:
a business day is Monday–Friday, 09:00–18:00 local time; one business day
equals one such working day (9 hours). No public holidays are modelled (none
fall in the dataset window). This convention never affects the golden cases,
which all use 24x7 calendar-minute targets.
"""

from datetime import datetime, time, timedelta

BUSINESS_START = time(9, 0)
BUSINESS_END = time(18, 0)
BUSINESS_MINUTES_PER_DAY = 9 * 60
_MAX_DAY_SPAN = 400  # safety bound for iteration


def _day_overlap_minutes(day, start, end):
    """Overlap of [start, end] with business hours on `day` (a date)."""
    if day.weekday() >= 5:  # Saturday/Sunday
        return 0
    window_start = datetime.combine(day, BUSINESS_START)
    window_end = datetime.combine(day, BUSINESS_END)
    overlap = min(end, window_end) - max(start, window_start)
    seconds = overlap.total_seconds()
    return int(seconds // 60) if seconds > 0 else 0


def business_minutes_between(start, end):
    """Whole business minutes elapsed between two datetimes (>= 0)."""
    if end <= start:
        return 0
    total = 0
    day = start.date()
    for _ in range(_MAX_DAY_SPAN):
        total += _day_overlap_minutes(day, start, end)
        if datetime.combine(day, BUSINESS_END) >= end:
            break
        day = day + timedelta(days=1)
    return total
