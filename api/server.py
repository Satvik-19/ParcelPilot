"""WSGI entrypoint for Vercel serverless deployment.

Adapts the existing ParcelPilotApp (backend/api/app.py) to the WSGI
interface that Vercel's Python runtime requires.  No business logic is
duplicated — every request routes through the same ParcelPilotApp methods
used by the stdlib server and the Layer C test harness.

Database lifecycle:
  Vercel serverless functions reuse the process on warm invocations but
  start fresh on cold starts.  An in-memory SQLite database is seeded
  from the assessment data pack on first request and reused for the
  lifetime of the instance.  Conversation history is held in memory
  (per-session); there is no cross-instance state.

Static files:
  The frontend is served by Vercel's edge CDN (vercel.json rewrites
  /assets/* → /frontend/assets/*).  The WSGI handler only handles API
  routes and falls back to a minimal SPA index.html for non-API paths.
"""

import io
import json
import mimetypes
import os
import tempfile
import threading
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure the project root is importable regardless of where Vercel
# mounts the function directory.
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(PROJECT_ROOT))

from backend.actions.confirm import confirm_support_action
from backend.agent.config import PROJECT_ROOT as _CFG_ROOT
from backend.agent.executor import run_turn
from backend.db.database import open_database
from backend.db.seed import seed_database
from backend.domain.timebase import SNAPSHOT_TS
from backend.tools.analyze_support_activity import analyze_support_activity

from backend.api import sessions as session_registry

FRONTEND_DIR = _CFG_ROOT / "frontend"
_DEFAULT_DB_PATH = _CFG_ROOT / "data" / "parcel_pilot.db"
_DATA_PACK = _CFG_ROOT / "assessment_docs"


class ParcelPilotApp:
    """Lightweight app instance wired to an in-memory seeded database."""

    def __init__(self, db_path=None, client=None, clock=None):
        self.conn = self._open_or_seed(db_path)
        self.client = client
        self.clock = clock
        self._lock = threading.Lock()
        self.history = {}
        self.turn_counts = {}

    @staticmethod
    def _open_or_seed(db_path=None):
        """Open an existing DB file or create a seeded copy."""
        import shutil, sqlite3

        if db_path:
            return open_database(db_path, check_same_thread=False)

        # Warm invocation: the production DB file persists on Vercel.
        if _DEFAULT_DB_PATH.is_file():
            return open_database(_DEFAULT_DB_PATH, check_same_thread=False)

        # Cold start: seed from the assessment data pack into a
        # persistent file so warm invocations skip re-seeding.
        _DEFAULT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        if _DATA_PACK.is_file():
            seed_database(_DEFAULT_DB_PATH, _DATA_PACK)
            return open_database(_DEFAULT_DB_PATH, check_same_thread=False)

        # Data pack not found — fall back to in-memory read-only DB.
        # Use sqlite3.backup() for a reliable binary copy (executescript
        # cannot handle raw SQLite bytes).
        tmp_src = Path(tempfile.mktemp(suffix=".db"))
        try:
            seed_database(tmp_src, _DATA_PACK)
            src = sqlite3.connect(str(tmp_src))
            mem = sqlite3.connect(":memory:", check_same_thread=False)
            src.backup(mem)
            src.close()
            mem.row_factory = sqlite3.Row
            return mem
        finally:
            tmp_src.unlink(missing_ok=True)

    # ---- internal helpers ------------------------------------------------

    def _resolve(self, session_key):
        return session_registry.resolve(session_key)

    def _client(self):
        if self.client is None:
            from backend.agent.groq_client import GroqClient
            from backend.agent.provider import make_provider
            self.client = make_provider(GroqClient())
        return self.client

    def _pending_action(self, action_id):
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
        from datetime import datetime
        expires = datetime.strptime(row["expires_at"], "%Y-%m-%d %H:%M")
        now = self.clock() if self.clock else SNAPSHOT_TS
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

    # ---- endpoint methods (same as backend/api/app.py) -------------------

    def sessions(self):
        return session_registry.public_list()

    def chat(self, session_key, message):
        session, error = self._resolve(session_key)
        if error:
            return 401, {"error": error}
        if not isinstance(message, str) or not message.strip():
            return 400, {"error": "Message text is required."}
        message = message.strip()[:4000]
        from datetime import datetime
        now = self.clock() if self.clock else datetime.now

        with self._lock:
            history = self.history.setdefault(session_key, [])
            turn_id = self.turn_counts.get(session_key, 0) + 1
            self.turn_counts[session_key] = turn_id
            result = run_turn(
                self.conn, session, message, self._client(),
                history=list(history), trace_path=None,
                turn_id=turn_id, as_of=now(),
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
        from datetime import datetime
        now = self.clock() if self.clock else datetime.now
        with self._lock:
            outcome = confirm_support_action(
                self.conn, session, action_id, token, as_of=now())
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


# ---------------------------------------------------------------------------
# Module-level singleton — reused across warm invocations.
# ---------------------------------------------------------------------------
_app = None
_lock = threading.Lock()


def _get_app():
    global _app
    if _app is None:
        with _lock:
            if _app is None:
                _app = ParcelPilotApp()
    return _app


# ---------------------------------------------------------------------------
# WSGI application — the entrypoint Vercel looks for.
# ---------------------------------------------------------------------------

_API_PREFIX = "/api/"


def application(environ, start_response):
    """Standard WSGI callable.  Vercel maps this as the function handler."""
    method = environ.get("REQUEST_METHOD", "GET")
    path = environ.get("PATH_INFO", "/")

    # ---- Read request body (POST only) ------------------------------------
    body = {}
    if method == "POST":
        length = int(environ.get("CONTENT_LENGTH") or 0)
        if length > 0:
            raw = environ["wsgi.input"].read(length)
            try:
                body = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                body = {}

    session_key = (
        environ.get("HTTP_X_SESSION_KEY")
        or environ.get("HTTP_X_SESSIONKEY")
        or body.get("session_key")
    )

    app = _get_app()

    # ---- API routes -------------------------------------------------------
    if path.startswith(_API_PREFIX):
        status_code, payload = _route_api(app, method, path, body, session_key)
        body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        start_response(
            f"{status_code} {'OK' if status_code == 200 else 'Error'}",
            [
                ("Content-Type", "application/json; charset=utf-8"),
                ("Content-Length", str(len(body_bytes))),
                ("Cache-Control", "no-store"),
                ("Access-Control-Allow-Origin", "*"),
            ],
        )
        return [body_bytes]

    # ---- Static files (SPA fallback) --------------------------------------
    return _serve_static(path, start_response)


def _route_api(app, method, path, body, session_key):
    """Dispatch an API request to ParcelPilotApp.  Returns (status, dict)."""
    try:
        if method == "GET" and path == "/api/sessions":
            return 200, {"sessions": app.sessions()}

        if method == "POST" and path == "/api/chat":
            return app.chat(session_key, body.get("message"))

        if method == "POST" and path == "/api/actions/confirm":
            return app.confirm_action(
                session_key, body.get("action_id"), body.get("token"))

        if method == "GET" and path.startswith("/api/insights"):
            scope = None
            if "?" in path:
                for pair in path.split("?", 1)[1].split("&"):
                    if pair.startswith("account_scope="):
                        scope = pair.split("=", 1)[1] or None
            return app.insights(session_key, account_scope=scope)

        return 404, {"error": "Not found."}
    except ValueError as exc:
        return 400, {"error": str(exc)}
    except Exception:
        return 500, {"error": "Internal error."}


def _serve_static(path, start_response):
    """Serve frontend static files with SPA fallback."""
    relative = path.lstrip("/") or "index.html"
    target = (FRONTEND_DIR / relative).resolve()

    # Path-traversal guard.
    if not str(target).startswith(str(FRONTEND_DIR.resolve())):
        target = FRONTEND_DIR / "index.html"

    if not target.is_file():
        target = FRONTEND_DIR / "index.html"

    if not target.is_file():
        start_response("404 Not Found", [("Content-Type", "text/plain")])
        return [b"Not found"]

    content_type = mimetypes.guess_type(str(target))[0] or "text/plain"
    body = target.read_bytes()
    start_response("200 OK", [
        ("Content-Type", f"{content_type}; charset=utf-8"
         if content_type.startswith("text/")
         or content_type == "application/javascript"
         else content_type),
        ("Content-Length", str(len(body))),
    ])
    return [body]
