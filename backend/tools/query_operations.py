"""query_operations — the single read tool for accounts/orders/tickets.

Never dumps raw rows: returns only the computed, policy-grounded state
(cancellation outcome, service-credit outcome, SLA status, known-issue
attribution). Entity access is scope-checked through the authorization
chokepoint; a cross-account lookup is rejected with the same neutral message
whether or not the entity exists.

Every computed outcome's source citations are resolved back to the actual
document chunks and attached as structured §5 evidence — the UI's evidence
panel therefore always reflects the sources behind a computed answer.
"""

import re
from dataclasses import asdict

from backend.domain.cancellation import resolve_cancellation_fee
from backend.domain.credits import resolve_service_credit
from backend.domain.known_issues import match_known_issue
from backend.domain.policy_data import get_agreement
from backend.domain.severity import classify_severity
from backend.domain.sla import check_sla_breach
from backend.domain.timebase import SNAPSHOT_TS
from backend.security import authorization
from backend.trust.conflicts import resolve_conflicts
from backend.trust.evidence import evidence_from_chunk

from ._accounts import canonical_account_id
from ._envelope import envelope_error, envelope_ok, envelope_rejected

_ENTITIES = ("account", "order", "ticket")

# Domain citations look like "03_Cancellation_and_Service_Credit_SOP_v4 §2–3".
_CITATION_RE = re.compile(r"^(?P<doc>\S+)\s+§\s*(?P<spec>\d+(?:\s*[-–]\s*\d+)?)$")


def _fetch(conn, table, pk, value):
    row = conn.execute(
        f"SELECT * FROM {table} WHERE {pk} = ?", (value,)
    ).fetchone()
    return dict(row) if row else None


def _citation_chunks(conn, citation):
    """Expand one citation string into its matching document chunk rows."""
    match = _CITATION_RE.match(str(citation).strip())
    if not match:
        return []
    doc = match.group("doc")
    bounds = re.split(r"[-–]", match.group("spec"))
    start = int(bounds[0])
    end = int(bounds[1]) if len(bounds) > 1 else start
    rows = []
    for number in range(start, end + 1):
        rows.extend(conn.execute(
            "SELECT * FROM document_chunks"
            " WHERE source_doc = ? AND section LIKE ?",
            (doc, f"Section {number}: %"),
        ).fetchall())
    return [dict(row) for row in rows]


def _citation_evidence(conn, citations):
    """Resolve domain-result citations to structured §5 evidence records.

    Deterministic: only chunks the domain layer actually cited appear, and
    conflict resolution marks any source overridden by a higher authority —
    the same trust semantics as retrieval-backed evidence.
    """
    chunks, seen = [], set()
    for citation in citations:
        for chunk in _citation_chunks(conn, citation):
            if chunk["chunk_id"] in seen:
                continue
            seen.add(chunk["chunk_id"])
            chunks.append(chunk)
    if not chunks:
        return ()
    records = [evidence_from_chunk(chunk) for chunk in chunks]
    return tuple(resolve_conflicts(records))


def _ticket_result(conn, ticket, account_id, as_of):
    account = _fetch(conn, "accounts", "account_id", account_id)
    agreement = get_agreement(account_id)
    severity = classify_severity(ticket)
    breach = check_sla_breach(ticket, account, agreement, as_of=as_of)
    known_issue = match_known_issue(ticket)
    result = {
        "entity": "ticket",
        "ticket_id": ticket["ticket_id"],
        # The caller's own account id (access already authorized above):
        # needed to address any follow-up prepare_support_action payload.
        "account_id": account_id,
        "status": ticket["status"],
        "subject": ticket["subject"],
        "severity": {
            "severity": severity.severity,
            "rationale": severity.rationale,
            "source": severity.source,
        },
        "sla": {
            "target_display": breach.target.display,
            "target_source": breach.target.source,
            "breached": breach.breached,
            "elapsed_minutes": breach.elapsed_minutes,
            "minutes_over_or_remaining": breach.minutes_over_or_remaining,
            "security_incident": breach.security_incident,
            "escalation_required": breach.escalation_required,
            "must_state_breach": breach.must_state_breach,
        },
        "known_issue": {
            "matched_ki": known_issue.matched_ki,
            "confidence": known_issue.confidence,
            "guidance": known_issue.guidance,
            "evidence": list(known_issue.evidence),
            "excluded": list(known_issue.excluded),
        },
        "historical_resolution": ticket["historical_resolution"],
    }
    if breach.must_state_breach:
        result["warning"] = ("SLA breach confirmed — state it explicitly; "
                             "never soften or hide it (Support Policy v3 §4).")
    return result


def _supported_actions(cancellation, credit):
    """Draftable next steps implied by the domain decisions.

    A signal for the agent only — preparing still goes through
    prepare_support_action (draft-only) and executing still requires the
    user's UI confirmation. Never derived from the model's phrasing.
    """
    supported = []
    if cancellation.cancellable:
        supported.append("request_cancellation")
    if credit.result == "ELIGIBLE":
        supported.append("grant_service_credit")
    return supported


def _account_result(conn, row):
    """Account facts plus bounded entity listings for the account.

    order_ids/ticket_ids let the agent answer account-level questions
    ("show me my orders") by following up with the individual entity
    lookups; access was already authorized for this account, so listing
    its own ids reveals nothing out of scope, and the dataset keeps the
    lists bounded (AGENT_SPEC §3.2 `id|filters`).
    """
    account_id = row["account_id"]
    order_ids = [
        r["order_id"] for r in conn.execute(
            "SELECT order_id FROM orders WHERE account_id = ?"
            " ORDER BY order_id", (account_id,))
    ]
    ticket_ids = [
        r["ticket_id"] for r in conn.execute(
            "SELECT ticket_id FROM tickets WHERE account_id = ?"
            " ORDER BY ticket_id", (account_id,))
    ]
    return {
        "entity": "account",
        "account_id": account_id,
        "account_name": row["account_name"],
        "plan": row["plan"],
        "status": row["status"],
        "csm": row["csm"],
        "premium_support": bool(row["premium_support"]),
        "order_ids": order_ids,
        "ticket_ids": ticket_ids,
    }


def _order_result(conn, order, account_id, as_of):
    account = _fetch(conn, "accounts", "account_id", account_id)
    agreement = get_agreement(account_id)
    cancellation = resolve_cancellation_fee(order, account, agreement, as_of=as_of)
    credit = resolve_service_credit(order, account, agreement, as_of=as_of)
    return {
        "entity": "order",
        "order_id": order["order_id"],
        # The caller's own account id (access already authorized above):
        # needed to address any follow-up prepare_support_action payload.
        "account_id": account_id,
        "carrier": order["carrier"],
        "status": order["status"],
        "cancellation": asdict(cancellation),
        "service_credit": asdict(credit),
        "supported_actions": _supported_actions(cancellation, credit),
    }


def query_operations(conn, session, entity, entity_id, as_of=None):
    try:
        sess = authorization.validate_session(session)
    except authorization.AuthorizationError as exc:
        return envelope_rejected(exc.code, exc.message)

    if entity not in _ENTITIES:
        return envelope_error(
            "INVALID_INPUT", f"entity must be one of {sorted(_ENTITIES)}."
        )
    if not entity_id or not isinstance(entity_id, str):
        return envelope_error("INVALID_INPUT", "entity_id is required.")

    table, pk = {
        "account": ("accounts", "account_id"),
        "order": ("orders", "order_id"),
        "ticket": ("tickets", "ticket_id"),
    }[entity]

    if entity == "account":
        # Canonicalize display-name references ("LumenWorks") to the trusted
        # id BEFORE the scope check: a customer asking about their own
        # account by name must not be mis-denied, while any other account's
        # name still resolves and fails the unchanged authorization check.
        entity_id = canonical_account_id(conn, entity_id) or entity_id
        if not authorization.can_access_account(sess, entity_id):
            return envelope_rejected(
                "ACCESS_DENIED",
                "This session is not authorized to access that account's data.",
            )
        row = _fetch(conn, table, pk, entity_id)
        if row is None:
            return envelope_error("NOT_FOUND", "Account not found.")
        return envelope_ok(result=_account_result(conn, row))

    row = _fetch(conn, table, pk, entity_id)
    if row is None:
        # Neutral on purpose: the message does not depend on whether the
        # entity exists — a scope denial reveals nothing.
        if sess.role == "customer":
            return envelope_rejected(
                "ACCESS_DENIED",
                "This session is not authorized to access that account's data.",
            )
        return envelope_error("NOT_FOUND", f"{entity.capitalize()} not found.")

    account_id = row["account_id"]
    if not authorization.can_access_account(sess, account_id):
        return envelope_rejected(
            "ACCESS_DENIED",
            "This session is not authorized to access that account's data.",
        )

    resolved_as_of = as_of if as_of is not None else SNAPSHOT_TS
    if entity == "order":
        result = _order_result(conn, row, account_id, resolved_as_of)
        citations = (
            list(result["cancellation"]["evidence"])
            + list(result["service_credit"]["evidence"])
        )
        return envelope_ok(result=result,
                           evidence=_citation_evidence(conn, citations))
    result = _ticket_result(conn, row, account_id, resolved_as_of)
    citations = [
        result["severity"]["source"],
        result["sla"]["target_source"],
        *result["known_issue"]["evidence"],
    ]
    return envelope_ok(result=result,
                       evidence=_citation_evidence(conn, citations))
