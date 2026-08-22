"""Stdlib HTTP surface — thin transport over ``ParcelPilotApp`` (ADR-007).

Routing, JSON framing and static-file serving only; every decision lives in
``app.py`` and the trusted layers beneath it. Deliberately framework-free:
the project's zero-dependency convention applies to the web surface too.
"""

import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from backend.agent.config import PROJECT_ROOT

FRONTEND_DIR = PROJECT_ROOT / "frontend"
_API_PREFIX = "/api/"
_MAX_BODY = 64 * 1024


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ParcelPilot/1.0"
        # Keep request noise out of the test/run output; errors still raise.
        def log_message(self, fmt, *args):  # noqa: N802 (stdlib signature)
            pass

        # ---------------------------------------------------------- plumbing
        def _send_json(self, status_code, payload):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            if length > _MAX_BODY:
                raise ValueError("Request body too large.")
            raw = self.rfile.read(length)
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise ValueError("Request body must be valid JSON.")
            return parsed if isinstance(parsed, dict) else {}

        def _session_key(self, body):
            """Header wins; a body-supplied key is a convenience fallback.
            Identity FIELDS (account_id, role, staff_id) are never read."""
            return self.headers.get("X-Session-Key") or body.get("session_key")

        def _api(self, method, path):
            try:
                if method == "GET" and path == "/api/sessions":
                    return self._send_json(200, {"sessions": app.sessions()})

                body = self._read_json() if method == "POST" else {}
                session_key = self._session_key(body)

                if method == "POST" and path == "/api/chat":
                    status, payload = app.chat(session_key, body.get("message"))
                    return self._send_json(status, payload)
                if method == "POST" and path == "/api/actions/confirm":
                    status, payload = app.confirm_action(
                        session_key, body.get("action_id"), body.get("token"))
                    return self._send_json(status, payload)
                if method == "GET" and path.startswith("/api/insights"):
                    scope = None
                    if "?" in path:
                        query = path.split("?", 1)[1]
                        for pair in query.split("&"):
                            if pair.startswith("account_scope="):
                                scope = pair.split("=", 1)[1] or None
                    status, payload = app.insights(session_key, account_scope=scope)
                    return self._send_json(status, payload)
                return self._send_json(404, {"error": "Not found."})
            except ValueError as exc:
                return self._send_json(400, {"error": str(exc)})
            except Exception:
                # Structured failure, never a stack trace to the client.
                return self._send_json(
                    500, {"error": "Internal error — the request was not processed."})

        # ------------------------------------------------------------ routing
        def do_GET(self):  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path.startswith(_API_PREFIX):
                return self._api("GET", self.path)
            return self._static(path)

        def do_POST(self):  # noqa: N802
            if self.path.startswith(_API_PREFIX):
                return self._api("POST", self.path)
            return self._send_json(404, {"error": "Not found."})

        def _static(self, path):
            relative = path.lstrip("/") or "index.html"
            target = (FRONTEND_DIR / relative).resolve()
            if not str(target).startswith(str(FRONTEND_DIR.resolve())):
                return self._send_json(404, {"error": "Not found."})
            if not target.is_file():
                target = FRONTEND_DIR / "index.html"  # SPA-style fallback
            if not target.is_file():
                return self._send_json(404, {"error": "Not found."})
            content_type = mimetypes.guess_type(str(target))[0] or "text/plain"
            body = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8"
                             if content_type.startswith("text/")
                             or content_type == "application/javascript"
                             else content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def serve(app, host="127.0.0.1", port=8000):
    """Create the HTTP server (not started)."""
    return ThreadingHTTPServer((host, port), make_handler(app))
