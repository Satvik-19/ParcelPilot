"""prepare_support_action — DRAFT-only state-changing preparation (ADR-004).

This tool writes a *pending* action row and nothing else: it never applies
cancellations, credits, or any state change, and it never produces an
executable confirmation — confirmation is a backend endpoint called only by
the UI, deliberately absent from the LLM's tool schema (Phase 9).

The draft is bound to the session (session_id), carries a canonical
payload_hash and a deterministic token, and expires five minutes after the
explicit as_of time. No wall clock anywhere (05_CODING_AGENT_RULES.md §4).
"""

import hashlib
import json
import sqlite3
from datetime import timedelta

from backend.domain.timebase import SNAPSHOT_TS, format_ts, parse_ts
from backend.security import authorization

from ._envelope import envelope_error, envelope_ok, envelope_rejected

ACTION_TYPES = (
    "escalate_ticket", "update_ticket", "create_follow_up",
    "request_cancellation", "grant_service_credit",
)  # exact set from 03_AGENT_SPEC.md §3
_EXPIRY = timedelta(minutes=5)

_ACTION_DESCRIPTIONS = {
    "escalate_ticket": "Escalate the ticket to a human support specialist",
    "update_ticket": "Update the ticket's status/notes",
    "create_follow_up": "Create a follow-up task for the support team",
    "request_cancellation": "Cancel the shipment identified by order_id",
    "grant_service_credit": "Grant a service credit to the account's balance",
}


def _canonical_json(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _payload_hash(action_type, canonical):
    return hashlib.sha256(f"{action_type}:{canonical}".encode("utf-8")).hexdigest()


def _token(session_id, payload_hash):
    return hashlib.sha256(f"{session_id}:{payload_hash}".encode("utf-8")).hexdigest()


def prepare_support_action(conn, session, action_type, payload, as_of=SNAPSHOT_TS):
    try:
        sess = authorization.validate_session(session)
    except authorization.AuthorizationError as exc:
        return envelope_rejected(exc.code, exc.message)

    if action_type not in ACTION_TYPES:
        return envelope_error(
            "INVALID_INPUT", f"action_type must be one of {sorted(ACTION_TYPES)}."
        )
    if not isinstance(payload, dict) or not payload:
        return envelope_error("INVALID_INPUT", "payload must be a non-empty object.")

    account_id = payload.get("account_id")
    if not account_id:
        return envelope_error(
            "INVALID_INPUT", "payload must name the account_id the action "
            "targets; query_operations returns it on the target entity."
        )
    if not authorization.can_access_account(sess, account_id):
        return envelope_rejected(
            "ACCESS_DENIED",
            "This session is not authorized to prepare actions for that account.",
        )

    created_at = parse_ts(as_of)
    canonical = _canonical_json(payload)
    payload_hash = _payload_hash(action_type, canonical)
    token = _token(sess.session_id, payload_hash)
    action_id = f"act_{payload_hash[:12]}"

    try:
        conn.execute(
            "INSERT INTO actions (action_id, type, payload_json, payload_hash, status,"
            " token, created_by, session_id, created_at, expires_at)"
            " VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)",
            (
                action_id, action_type, canonical, payload_hash, token,
                sess.staff_id or sess.account_id, sess.session_id,
                format_ts(created_at), format_ts(created_at + _EXPIRY),
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        # Deterministic drafting: the same session + payload always yields the
        # same action_id, so re-preparing simply returns the existing draft.
        pass

    row = conn.execute(
        "SELECT * FROM actions WHERE action_id = ?", (action_id,)
    ).fetchone()

    description = (
        f"{_ACTION_DESCRIPTIONS[action_type]}: "
        + ", ".join(f"{key}={payload[key]}" for key in sorted(payload))
        + ". Draft only — a UI confirmation is still required before anything happens."
    )
    return envelope_ok(result={
        "action_id": action_id,
        "action_type": action_type,
        "status": row["status"],
        "token": row["token"],
        "payload_hash": row["payload_hash"],
        "session_id": row["session_id"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "rendered_card": description,
        "note": "Drafted only; nothing was executed. Confirmation happens outside "
                "the model's tool surface.",
    })
