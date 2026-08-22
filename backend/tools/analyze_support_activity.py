"""analyze_support_activity — staff-only internal analytics (AGENT_SPEC §3).

Read-only aggregation over tickets: SLA breach states, known-issue
attribution and account activity. Rejected outright for customer sessions;
no prompt text can unlock it (deterministic staff check).
"""

import re

from backend.domain.known_issues import match_known_issue
from backend.domain.policy_data import get_agreement
from backend.domain.sla import check_sla_breach
from backend.security import authorization
from backend.security.authorization import visible_account_ids

from ._accounts import canonical_account_id
from ._envelope import envelope_ok, envelope_rejected

_KEYWORDS = (
    ("bulk upload", r"bulk upload|csv"),
    ("pickup status", r"pickup|webhook"),
    ("account access", r"access|login|credential|reset"),
)


def analyze_support_activity(conn, session, as_of=None, account_scope=None):
    try:
        sess = authorization.validate_session(session)
    except authorization.AuthorizationError as exc:
        return envelope_rejected(exc.code, exc.message)
    try:
        authorization.require_staff(sess)
    except authorization.AuthorizationError as exc:
        return envelope_rejected(exc.code, exc.message)

    account_ids = conn.execute("SELECT account_id FROM accounts").fetchall()
    visible = visible_account_ids(sess, [r["account_id"] for r in account_ids])
    if account_scope is not None:
        # Canonicalize display-name scopes; the optional scope can only
        # NARROW the staff view, never widen it.
        account_scope = canonical_account_id(conn, account_scope) or account_scope
        if account_scope not in visible:
            return envelope_rejected(
                "ACCESS_DENIED",
                "This session is not authorized to access that account's data.",
            )
        visible = [account_scope]

    account_lookup = {
        row["account_id"]: row
        for row in conn.execute("SELECT * FROM accounts").fetchall()
    }
    tickets = conn.execute(
        "SELECT * FROM tickets ORDER BY ticket_id"
    ).fetchall()
    in_scope = [dict(t) for t in tickets if t["account_id"] in visible]

    kwargs = {"as_of": as_of} if as_of is not None else {}
    sla_items = []
    known_issue_items = []
    for ticket in in_scope:
        account = account_lookup[ticket["account_id"]]
        breach = check_sla_breach(
            ticket, account, get_agreement(ticket["account_id"]), **kwargs
        )
        sla_items.append({
            "ticket_id": ticket["ticket_id"],
            "account_id": ticket["account_id"],
            "severity": breach.severity.severity,
            "target_display": breach.target.display,
            "breached": breach.breached,
            "minutes_over_or_remaining": breach.minutes_over_or_remaining,
            "escalation_required": breach.escalation_required,
        })
        match = match_known_issue(ticket)
        known_issue_items.append({
            "ticket_id": ticket["ticket_id"],
            "account_id": ticket["account_id"],
            "matched_ki": match.matched_ki,
            "confidence": match.confidence,
        })

    clusters = {label: [] for label, _ in _KEYWORDS}
    for ticket in in_scope:
        text = f"{ticket['subject']} {ticket['description']}".lower()
        for label, pattern in _KEYWORDS:
            if re.search(pattern, text):
                clusters[label].append(ticket["ticket_id"])
                break  # one cluster per ticket keeps counts deterministic

    summary = {
        "tickets_in_scope": len(in_scope),
        "breached_count": sum(1 for item in sla_items if item["breached"]),
        "escalations_required": sum(
            1 for item in sla_items if item["escalation_required"]
        ),
    }
    return envelope_ok(result={
        "visible_accounts": visible,
        "sla_status": sla_items,
        "known_issues": known_issue_items,
        "clusters": {label: ids for label, ids in clusters.items()},
        "summary": summary,
    })
