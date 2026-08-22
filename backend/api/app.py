"""Application core — trusted state + agent wiring behind the HTTP surface.

This module is deliberately framework-free (ADR-002/007): the stdlib server
in ``server.py`` translates HTTP requests into these calls and nothing
else. Everything security-relevant happens here:

- the session is resolved from the mocked registry by KEY; identity fields
  in request payloads are never read;
- conversation history lives server-side, keyed by session;
- confirmation goes through ``backend.actions.confirm_support_action`` —
  the LLM never sees it (ADR-004);
- the wall clock is read ONLY here (via an injectable ``clock``), never in
  ``domain/``; business facts still come from SNAPSHOT_TS-pinned functions.
"""

import json
import threading
from datetime import datetime
from pathlib import Path

from backend.actions.confirm import confirm_support_action
from backend.agent.config import PROJECT_ROOT
from backend.agent.executor import run_turn
from backend.db.database import open_database
from backend.tools.analyze_support_activity import analyze_support_activity

from . import sessions as session_registry

DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "parcel_pilot.db"
DEFAULT_TRACE_PATH = PROJECT_ROOT / "data" / "traces.jsonl"


class ParcelPilotApp:
    """One app instance = one SQLite connection + per-session chat state."""

    def __init__(self, db_path=None, client=None, trace_path=None, clock=None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        # One connection shared by handler threads; every access is
        # serialised through self._lock below (ThreadingHTTPServer).
        self.conn = open_database(self.db_path, check_same_thread=False)
        self.client = client          # None → lazy GroqClient on first chat
        self.trace_path = Path(trace_path) if trace_path else DEFAULT_TRACE_PATH
        self.clock = clock or datetime.now
        self._lock = threading.Lock()
        self.history = {}             # session_key → [{role, content}]
        self.turn_counts = {}         # session_key → turns so far

    # ------------------------------------------------------------------ util
    def _resolve(self, session_key):
        return session_registry.resolve(session_key)

    def _client(self):
        if self.client is None:
            from backend.agent.groq_client import GroqClient
            from backend.agent.provider import make_provider
            self.client = make_provider(GroqClient())
        return self.client

    def _pending_action(self, action_id):
        """The UI card for a still-pending draft (nothing else is exposed)."""
        if not action_id:
            return None
        row = self.conn.execute(
            "SELECT action_id, type, status, payload_json, created_at,"
            " expires_at, token FROM actions WHERE action_id = ?",
            (action_id,),
        ).fetchone()
        if row is None or row["status"] != "pending":
            return None
        from backend.tools.prepare_support_action import _ACTION_DESCRIPTIONS
        payload = json.loads(row["payload_json"])
        description = (
            f"{_ACTION_DESCRIPTIONS.get(row['type'], row['type'])}: "
            + ", ".join(f"{key}={payload[key]}" for key in sorted(payload))
            + ". Pending — nothing happens until you explicitly confirm."
        )
        expires = datetime.strptime(row["expires_at"], "%Y-%m-%d %H:%M")
        now = self.clock()
        return {
            "action_id": row["action_id"],
            "action_type": row["type"],
            "status": row["status"],
            "token": row["token"],
            "payload": payload,
            "description": description,
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "seconds_until_expiry": max(0, int((expires - now).total_seconds())),
        }

    # ------------------------------------------------------------- endpoints
    def sessions(self):
        return session_registry.public_list()

    def chat(self, session_key, message):
        session, error = self._resolve(session_key)
        if error:
            return 401, {"error": error}
        if not isinstance(message, str) or not message.strip():
            return 400, {"error": "Message text is required."}
        message = message.strip()[:4000]

        with self._lock:
            history = self.history.setdefault(session_key, [])
            turn_id = self.turn_counts.get(session_key, 0) + 1
            self.turn_counts[session_key] = turn_id
            result = run_turn(
                self.conn, session, message, self._client(),
                history=list(history), trace_path=self.trace_path,
                turn_id=turn_id, as_of=self.clock(),
            )
            history.append({"role": "user", "content": message})
            history.append({"role": "assistant", "content": result.answer})

        pending = self._pending_action(result.trace.get("action_id"))
        return 200, {
            "answer": result.answer,
            "answer_state": result.answer_state,
            "state_reason": result.state_reason,
            "provider_failure": result.state_reason.startswith(
                "model provider failure"),
            "tools": list(result.tools),
            "evidence": list(result.evidence),
            "trace": result.trace,
            "pending_action": pending,
            "turn_id": turn_id,
        }

    def confirm_action(self, session_key, action_id, token):
        session, error = self._resolve(session_key)
        if error:
            return 401, {"error": error}
        if not isinstance(action_id, str) or not action_id:
            return 400, {"error": "action_id is required."}
        with self._lock:
            outcome = confirm_support_action(
                self.conn, session, action_id, token, as_of=self.clock())
        status_code = 200 if outcome["status"] == "executed" else 409
        return status_code, outcome

    def insights(self, session_key, account_scope=None):
        session, error = self._resolve(session_key)
        if error:
            return 401, {"error": error}
        with self._lock:
            envelope = analyze_support_activity(
                self.conn, session, account_scope=account_scope)
        if envelope.status == "rejected":
            code = envelope.result.get("rejection_code")
            status_code = 403 if code in ("STAFF_ONLY", "ACCESS_DENIED") else 400
            return status_code, envelope.result
        return 200, envelope.result

    def close(self):
        self.conn.close()
