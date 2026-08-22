"""Known-issue matching — KI-208/KI-211 hits and the KI-176 exclusion."""

from backend.domain.known_issues import match_known_issue
from backend.domain.timebase import SNAPSHOT_TS


def _ticket(subject, description=""):
    return {
        "ticket_id": "T-KI",
        "account_id": "ACCT-003",
        "created_at": SNAPSHOT_TS,
        "status": "OPEN",
        "subject": subject,
        "description": description,
        "channel": "email",
        "assigned_to": None,
        "last_customer_message_at": SNAPSHOT_TS,
        "historical_resolution": None,
    }


def test_small_csv_does_not_match_ki208():
    """Under the ~3,000-row failure threshold there is no deterministic match."""
    match = match_known_issue(_ticket("CSV upload fails", "My 200-row CSV fails to upload."))
    assert match.matched_ki is None
    assert match.confidence == "none"


def test_row_count_above_threshold_matches_ki208():
    match = match_known_issue(_ticket(
        "Bulk upload failing", "A 4,200-row CSV fails at roughly 70%."
    ))
    assert match.matched_ki == "KI-208"
    assert "3,000" in match.guidance


def test_large_keyword_matches_without_row_count():
    match = match_known_issue(_ticket("Bulk upload errors", "Large CSV files fail intermittently."))
    assert match.matched_ki == "KI-208"


def test_ki211_requires_swiftship_pickup_and_stale_status():
    match = match_known_issue(_ticket(
        "Pickup status stale",
        "SwiftShip driver collected the parcel but the order still shows BOOKED.",
    ))
    assert match.matched_ki == "KI-211"
    assert "20 minutes" in match.guidance


def test_non_swiftship_pickup_issue_does_not_match_ki211():
    match = match_known_issue(_ticket(
        "Pickup status stale",
        "BlueDart driver collected the parcel but the order still shows BOOKED.",
    ))
    assert match.matched_ki is None


def test_ki176_resolved_issue_is_excluded_not_matched():
    """The guide itself says: do not attribute new issues to resolved KI-176."""
    match = match_known_issue(_ticket(
        "Address validation error",
        "Shipments are rejected with an address validation error.",
    ))
    assert match.matched_ki is None
    assert len(match.excluded) == 1
    assert "KI-176" in match.excluded[0]
    assert "2026-07-18" in match.excluded[0]


def test_unrelated_ticket_matches_nothing():
    match = match_known_issue(_ticket("How do I change my billing contact?"))
    assert match.matched_ki is None
    assert match.confidence == "none"
