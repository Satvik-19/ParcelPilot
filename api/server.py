"""Minimal WSGI entrypoint for Vercel serverless deployment.

Every heavy import (pypdf, openpyxl, backend.agent.*) is deferred until
first request so a missing dependency returns a clear error message
instead of crashing the function with a silent 500.
"""

import json
import os
import threading
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(PROJECT_ROOT))

# ── Lazy-loaded app singleton ──────────────────────────────────────────
_app = None
_app_lock = threading.Lock()
_boot_error = None


def _boot():
    """Seed DB and wire up the app. Runs once on first request."""
    global _app, _boot_error
    if _app is not None or _boot_error is not None:
        return
    with _app_lock:
        if _app is not None or _boot_error is not None:
            return
        try:
            _lazy_import_and_seed()
        except Exception as exc:
            _boot_error = str(exc)


def _lazy_import_and_seed():
    global _app
    from backend.db.database import open_database
    from backend.db.seed import seed_database

    db_path = PROJECT_ROOT / "data" / "parcel_pilot.db"
    data_pack = PROJECT_ROOT / "assessment_docs"

    if db_path.is_file():
        conn = open_database(db_path, check_same_thread=False)
    else:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        if not (data_pack / "ParcelPilot_Assessment_Data.xlsx").is_file():
            _boot_error = (
                "Assessment data pack not found. "
                "Ensure assessment_docs/ is deployed with the function."
            )
            return
        seed_database(db_path, data_pack)
        conn = open_database(db_path, check_same_thread=False)

    from backend.api.app import ParcelPilotApp
    _app = ParcelPilotApp(db_path=db_path)


# ── WSGI application ───────────────────────────────────────────────────
def application(environ, start_response):
    method = environ.get("REQUEST_METHOD", "GET")
    path = environ.get("PATH_INFO", "/")

    # Read POST body
    body = {}
    if method == "POST":
        length = int(environ.get("CONTENT_LENGTH") or 0)
        if length > 0:
            try:
                body = json.loads(environ["wsgi.input"].read(length))
            except Exception:
                pass

    session_key = environ.get("HTTP_X_SESSION_KEY") or body.get("session_key")

    # Boot app on first request
    _boot()

    # API routes
    if path.startswith("/api/"):
        if _boot_error:
            status, payload = 500, {"error": f"App failed to start: {_boot_error}"}
        else:
            status, payload = _route(method, path, body, session_key)
        body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        start_response(
            f"{status} OK" if status < 400 else f"{status} Error",
            [
                ("Content-Type", "application/json; charset=utf-8"),
                ("Content-Length", str(len(body_bytes))),
                ("Cache-Control", "no-store"),
                ("Access-Control-Allow-Origin", "*"),
            ],
        )
        return [body_bytes]

    # Static files (frontend)
    return _serve_static(path, start_response)


def _route(method, path, body, session_key):
    try:
        if method == "GET" and path == "/api/sessions":
            return 200, {"sessions": _app.sessions()}
        if method == "POST" and path == "/api/chat":
            return _app.chat(session_key, body.get("message"))
        if method == "POST" and path == "/api/actions/confirm":
            return _app.confirm_action(
                session_key, body.get("action_id"), body.get("token"))
        if method == "GET" and path.startswith("/api/insights"):
            scope = None
            if "?" in path:
                for pair in path.split("?", 1)[1].split("&"):
                    if pair.startswith("account_scope="):
                        scope = pair.split("=", 1)[1] or None
            return _app.insights(session_key, account_scope=scope)
        return 404, {"error": "Not found."}
    except ValueError as exc:
        return 400, {"error": str(exc)}
    except Exception as exc:
        return 500, {"error": f"Internal error: {type(exc).__name__}: {exc}"}


def _serve_static(path, start_response):
    import mimetypes
    frontend = PROJECT_ROOT / "frontend"
    relative = path.lstrip("/") or "index.html"
    target = (frontend / relative).resolve()
    if not str(target).startswith(str(frontend.resolve())):
        target = frontend / "index.html"
    if not target.is_file():
        target = frontend / "index.html"
    if not target.is_file():
        start_response("404 Not Found", [("Content-Type", "text/plain")])
        return [b"Not found"]
    ct = mimetypes.guess_type(str(target))[0] or "text/plain"
    data = target.read_bytes()
    start_response("200 OK", [
        ("Content-Type", f"{ct}; charset=utf-8" if ct.startswith("text/") else ct),
        ("Content-Length", str(len(data))),
    ])
    return [data]
