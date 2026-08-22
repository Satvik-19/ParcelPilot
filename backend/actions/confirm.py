"""Action confirmation — the trusted prepare -> confirm -> execute gate.

``confirm_support_action`` is a plain backend function called by the API
layer when the user clicks confirm in the UI (ADR-004). It is NEVER part of
the LLM tool surface, and no chat message can reach it: a model turn can
only ever draft via ``prepare_support_action``.

Before any execution it validates all six checks from 03_AGENT_SPEC.md §4 —
existence, pending status, session binding, one-time token, payload
integrity, expiry — and fails CLOSED: any violation rejects with a
structured reason and mutates nothing. Confirmation is one-shot: the claim
is a guarded UPDATE (``WHERE status = 'pending'``) inside a single
transaction with the mocked effect, so refresh/replay/double-click can
never execute an action twice.

``as_of`` is explicit (default SNAPSHOT_TS) so the gate is fully testable
with synthetic times; the API passes its own clock so a live draft keeps a
real five-minute confirmation window. Wall-clock reads live in the API
layer only, never here or in ``domain/`` (05_CODING_AGENT_RULES.md §4).
"""

import hashlib
import json
import sqlite3

from backend.domain.policy_data import get_agreement
from backend.domain.timebase import SNAPSHOT_TS, parse_ts

__all__ = ["confirm_support_action", "rejection"]


def rejection(code, message):
    """Structured, user-displayable refusal. No state ever changed."""
    return {"status": "rejected", "rejection_code": code, "message": message}


def _payload_hash(action_type, canonical):
    return hashlib.sha256(f"{action_type}:{canonical}".encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Mocked effects (PRD §6: no real carrier/payment integrations). Each effect
# is applied inside the SAME transaction as the status flip — either the
# action executes atomically with its effect, or nothing changes.
# ---------------------------------------------------------------------------

def _current_month_prefix(as_of):
    return as_of.strftime("%Y-%m")


def _execute_effect(conn, action_type, payload, as_of, action_id=None):
    """Apply the mocked effect; return a short human-readable description.

    Raises ValueError for effects that fail closed (e.g. the Northstar
    monthly credit cap from 02_DOMAIN_SPEC.md §3).
    """
    if action_type == "escalate_ticket":
        ticket_id = payload["ticket_id"]
        conn.execute("UPDATE tickets SET status = 'ESCALATED' WHERE ticket_id = ?",
                     (ticket_id,))
        return f"Ticket {ticket_id} escalated to a human support specialist."

    if action_type == "update_ticket":
        ticket_id = payload["ticket_id"]
        new_status = payload.get("status")
        if new_status:
            conn.execute("UPDATE tickets SET status = ? WHERE ticket_id = ?",
                         (new_status, ticket_id))
        return (f"Ticket {ticket_id} updated"
                + (f" to status {new_status}." if new_status else "."))

    if action_type == "create_follow_up":
        target = payload.get("ticket_id") or payload.get("order_id")
        return (f"Follow-up task created for the support team"
                + (f" on {target}." if target else "."))

    if action_type == "request_cancellation":
        order_id = payload["order_id"]
        conn.execute(
            "UPDATE orders SET status = 'CANCELLED',"
            " cancellation_requested_at = COALESCE(cancellation_requested_at, ?)"
            " WHERE order_id = ?",
            (as_of.strftime("%Y-%m-%d %H:%M"), order_id),
        )
        fee = payload.get("fee_inr")
        fee_note = (f" Cancellation fee: INR {fee}."
                    if isinstance(fee, (int, float)) else "")
        return f"Order {order_id} cancelled (mocked).{fee_note}"

    if action_type == "grant_service_credit":
        account_id = payload["account_id"]
        amount = int(payload["amount_inr"])
        # The account's agreement may cap the monthly AGGREGATE service
        # credit (Northstar: INR 5,000 — 02_DOMAIN_SPEC.md §3 data note).
        # Deterministic check over already-executed grants in the same
        # calendar month; the row being confirmed right now is already
        # flipped to 'executed' by the claim above, so it must be excluded
        # to avoid double counting.
        agreement = get_agreement(account_id) or {}
        cap = agreement.get("service_credit", {}).get("monthly_cap_inr")
        if cap is not None:
            rows = conn.execute(
                "SELECT payload_json FROM actions"
                " WHERE type = 'grant_service_credit' AND status = 'executed'"
                " AND confirmed_at LIKE ? AND action_id != ?",
                (_current_month_prefix(as_of) + "%", action_id or ""),
            ).fetchall()
            granted_this_month = 0
            for row in rows:
                stored = json.loads(row["payload_json"])
                if stored.get("account_id") == account_id:
                    granted_this_month += int(stored.get("amount_inr", 0))
            if granted_this_month + amount > cap:
                raise ValueError(
                    "Granting this credit would exceed the account's monthly "
                    f"aggregate cap of INR {cap:,}; it requires manager approval."
                )
        return (f"Service credit of INR {amount} granted to {account_id} "
                f"(mocked ledger entry).")

    raise ValueError(f"No mocked effect defined for action type {action_type!r}.")


def confirm_support_action(conn, session, action_id, token, as_of=SNAPSHOT_TS):
    """Validate the six §4 checks, then execute exactly once.

    ``session`` is the trusted caller session dict resolved by the API layer
    (never client-supplied identity fields). Returns a structured dict:
    ``{"status": "executed", ...}`` on success, ``rejection(...)`` otherwise.
    """
    from backend.security import authorization  # local: avoid import cycle risk

    try:
        sess = authorization.validate_session(session)
    except (authorization.AuthorizationError, ValueError):
        return rejection("INVALID_SESSION", "Unrecognised session format.")

    row = conn.execute(
        "SELECT * FROM actions WHERE action_id = ?", (action_id,)
    ).fetchone()
    if row is None:                                     # check 1 — exists
        return rejection("NOT_FOUND", "No such action exists.")
    if row["status"] != "pending":                     # check 2 — pending
        # A replayed confirmation lands here: the action already moved on.
        return rejection("NOT_PENDING",
                         f"This action is not pending (status: {row['status']}).")
    if row["session_id"] != sess.session_id:            # check 3 — session binding
        return rejection("WRONG_SESSION",
                         "This action was drafted by a different session.")
    if row["created_by"] != (sess.staff_id or sess.account_id):
        return rejection("WRONG_SESSION",
                         "This action was drafted by a different user.")
    if token != row["token"]:                          # check 4 — one-time token
        return rejection("WRONG_TOKEN", "The confirmation token is invalid.")
    recomputed = _payload_hash(row["type"], row["payload_json"])
    if recomputed != row["payload_hash"]:              # check 5 — payload integrity
        return rejection("PAYLOAD_TAMPERED",
                         "The stored action payload failed its integrity check.")
    if parse_ts(as_of) > parse_ts(row["expires_at"]):  # check 6 — expiry
        conn.execute("UPDATE actions SET status = 'expired' WHERE action_id = ?",
                     (action_id,))
        conn.commit()
        return rejection("EXPIRED", "This draft expired before confirmation.")

    confirmed_at = as_of.strftime("%Y-%m-%d %H:%M")
    try:
        # One-shot claim: only a pending row can flip. A concurrent or
        # repeated confirmation finds 0 rows and refuses — nothing executes
        # twice, ever.
        claimed = conn.execute(
            "UPDATE actions SET status = 'executed', confirmed_at = ?"
            " WHERE action_id = ? AND status = 'pending'",
            (confirmed_at, action_id),
        ).rowcount
        if not claimed:
            conn.rollback()
            return rejection("NOT_PENDING", "This action is no longer pending.")
        effect = _execute_effect(conn, row["type"], json.loads(row["payload_json"]),
                                 parse_ts(as_of), action_id=action_id)
        conn.commit()
    except (sqlite3.Error, ValueError, KeyError) as exc:
        conn.rollback()  # fail closed: the effect and the flip are atomic
        return rejection("EXECUTION_REFUSED", str(exc))

    return {
        "status": "executed",
        "action_id": action_id,
        "action_type": row["type"],
        "confirmed_at": confirmed_at,
        "effect": effect,
    }
