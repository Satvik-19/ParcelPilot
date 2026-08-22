"""Mocked session registry — the application's login substitute (PRD §2).

No real authentication by design: the reviewer picks a context, and the
server resolves it to a TRUSTED session dict. Identity never comes from the
request body — only the session key is client-supplied, and unknown keys are
rejected. Every session_id is unique per context so confirmation binding
(03_AGENT_SPEC.md §4 check 3) is meaningful even between two customer
contexts of the same account role.
"""

# staff_id / account_id come from the real data pack (02_DOMAIN_SPEC.md §1).
SESSIONS = {
    "customer-northstar": {
        "role": "customer", "account_id": "ACCT-001",
        "session_id": "sess-cust-northstar",
        "label": "Customer — Northstar Logistics (ACCT-001, Enterprise)",
    },
    "customer-lumenworks": {
        "role": "customer", "account_id": "ACCT-002",
        "session_id": "sess-cust-lumenworks",
        "label": "Customer — LumenWorks (ACCT-002, Growth)",
    },
    "customer-beacon": {
        "role": "customer", "account_id": "ACCT-003",
        "session_id": "sess-cust-beacon",
        "label": "Customer — Beacon Retail (ACCT-003, Standard)",
    },
    "customer-axis": {
        "role": "customer", "account_id": "ACCT-004",
        "session_id": "sess-cust-axis",
        "label": "Customer — Axis Labs (ACCT-004, Enterprise)",
    },
    "staff": {
        "role": "staff", "staff_id": "STF-001",
        "permissions": ("support", "insights"),
        "session_id": "sess-staff-001",
        "label": "Internal staff — support operations",
    },
}


def resolve(session_key):
    """Return (session_dict, None) or (None, error_message). The returned
    dict is trusted server-side state — request payloads cannot alter it."""
    if not isinstance(session_key, str) or session_key not in SESSIONS:
        return None, "Unknown session. Pick one of the mocked sessions."
    return dict(SESSIONS[session_key]), None


def public_list():
    """Session picker entries — labels + role only, no internal fields."""
    return [
        {"key": key, "label": entry["label"], "role": entry["role"]}
        for key, entry in SESSIONS.items()
    ]
