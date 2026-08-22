"""Session-scoped authorization — the access-control chokepoint (PRD FR-2).

Every account-scoped read in the system goes through this module; access
control is enforced here in code, never by prompt instruction
(05_CODING_AGENT_RULES.md §5). A customer session is *structurally*
incapable of retrieving another account's data: scope decisions are pure
equality checks on the session's trusted account_id, so no phrasing,
persistence, or injected instruction can change the outcome (golden case 12).

A session is the authenticated identity injected server-side by the runtime
(03_AGENT_SPEC.md §1) — the model never supplies it.
"""

from dataclasses import dataclass
from typing import Optional


class AuthorizationError(Exception):
    """Structured, machine-readable rejection.

    Messages are deliberately neutral: they never reveal whether the denied
    entity exists or which account owns it.
    """

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Session:
    role: str                                # "customer" | "staff"
    account_id: Optional[str] = None         # customer sessions only
    staff_id: Optional[str] = None           # staff sessions only
    permissions: tuple = ()                  # staff permissions (mocked)
    session_id: str = "session"              # binds drafted actions (AGENT_SPEC §4)


def validate_session(session):
    """Validate and normalise a session dict into a trusted Session object.

    Raises AuthorizationError (neutral message) for anything malformed — the
    caller turns it into a structured rejection; nothing here guesses.
    """
    if session is None:
        raise ValueError("session is required — the runtime injects it server-side")
    if not isinstance(session, dict):
        raise AuthorizationError("INVALID_SESSION", "Unrecognised session format.")
    role = session.get("role")
    if role == "customer":
        account_id = session.get("account_id")
        if not account_id or not isinstance(account_id, str):
            raise AuthorizationError(
                "INVALID_SESSION", "Customer sessions carry a trusted account_id."
            )
        return Session("customer", account_id=account_id,
                       session_id=session.get("session_id", "session"))
    if role == "staff":
        staff_id = session.get("staff_id")
        if not staff_id:
            raise AuthorizationError(
                "INVALID_SESSION", "Staff sessions carry a staff_id."
            )
        permissions = session.get("permissions") or ()
        return Session("staff", staff_id=staff_id, permissions=tuple(permissions),
                       session_id=session.get("session_id", "session"))
    raise AuthorizationError("INVALID_SESSION", "Unrecognised session role.")


def can_access_account(session, account_id):
    """The single scope decision: staff may access any account, a customer
    only their own. Pure equality — nothing else influences it."""
    if session.role == "staff":
        return True
    return account_id == session.account_id


def require_account_access(session, account_id):
    """Chokepoint guard: raise the structured rejection when scope fails."""
    if not can_access_account(session, account_id):
        raise AuthorizationError(
            "ACCESS_DENIED",
            "This session is not authorized to access that account's data.",
        )


def require_staff(session):
    """Staff-only operations (AGENT_SPEC §3: analyze_support_activity)."""
    if session.role != "staff":
        raise AuthorizationError(
            "STAFF_ONLY",
            "This operation is available to internal staff sessions only.",
        )


def visible_account_ids(session, all_account_ids):
    """Deterministic scope projection over any account-id collection."""
    return sorted(
        a for a in all_account_ids if can_access_account(session, a)
    )
