"""Layer C shared harness — the application tested through its real HTTP surface.

Spins up the exact production composition (ParcelPilotApp behind the stdlib
ThreadingHTTPServer, seeded database, mocked session registry) on an
ephemeral localhost port. Deterministic suites inject a scripted client and a
manual clock; the live suite injects a recording wrapper around the real
GroqClient. Wall-clock reads happen only inside backend/api (the sanctioned
location — 05_CODING_AGENT_RULES.md §4); tests substitute ManualClock so no
test depends on real time.
"""

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

from backend.api.app import ParcelPilotApp
from backend.api.server import serve
from backend.db.database import open_database
from backend.db.seed import seed_database
from backend.domain.timebase import SNAPSHOT_TS

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_PACK = PROJECT_ROOT / "assessment_docs"


class ScriptedClient:
    """Pops canned Groq-shaped responses; records every request.

    Stands in for the model so application behaviour can be judged over the
    real HTTP path with zero network and zero nondeterminism.
    """

    def __init__(self, script):
        self.script = list(script)
        self.received = []

    def complete(self, messages, tools=None, model=None, **kwargs):
        self.received.append(json.loads(json.dumps(messages)))
        if not self.script:
            raise AssertionError("ScriptedClient ran out of canned responses.")
        return self.script.pop(0)


class RecordingLiveClient:
    """Real provider client (Groq + OpenRouter fallback) that records every
    request/response pair for Layer C live evaluation."""

    def __init__(self):
        from backend.agent.groq_client import GroqClient
        from backend.agent.provider import make_provider
        self._client = make_provider(GroqClient())
        self.exchanges = []

    def complete(self, messages, tools=None, model=None, **kwargs):
        response = self._client.complete(messages, tools=tools, model=model,
                                         **kwargs)
        self.exchanges.append({
            "request": json.loads(json.dumps(messages)),
            "response": json.loads(json.dumps(response)),
        })
        return response


class ManualClock:
    """Deterministic stand-in for the API layer's wall clock."""

    def __init__(self, start=None):
        from datetime import timedelta
        self.now = start or SNAPSHOT_TS
        self._timedelta = timedelta

    def __call__(self):
        return self.now

    def advance(self, **kwargs):
        self.now += self._timedelta(**kwargs)


class AppServer:
    """One fully wired application instance served on an ephemeral port."""

    def __init__(self, tmp_path, client, clock=None, db_setup=None,
                 trace_path=None):
        tmp = Path(tmp_path)
        db_path = tmp / "app.db"
        seed_database(db_path, DATA_PACK)
        if db_setup is not None:
            conn = open_database(db_path)
            try:
                db_setup(conn)
            finally:
                conn.close()
        self.client = client
        self.clock = clock or ManualClock()
        self.app = ParcelPilotApp(
            db_path=db_path, client=client,
            trace_path=trace_path or (tmp / "traces.jsonl"),
            clock=self.clock)
        self.server = serve(self.app, host="127.0.0.1", port=0)
        self.port = self.server.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self.server.serve_forever,
                                        daemon=True)
        self._thread.start()

    def request(self, method, path, session_key=None, body=None):
        """Return (status, payload-dict) — never raises on HTTP error codes."""
        url = self.base_url + path
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if session_key is not None:
            req.add_header("X-Session-Key", session_key)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = {"error": raw.decode("utf-8", "replace")}
            return exc.code, payload

    def chat(self, session_key, message, **body_extra):
        return self.request("POST", "/api/chat", session_key,
                            {"message": message, **body_extra})

    def confirm(self, session_key, action_id, token):
        return self.request("POST", "/api/actions/confirm", session_key,
                            {"action_id": action_id, "token": token})

    def insights(self, session_key, account_scope=None):
        path = "/api/insights"
        if account_scope:
            path += f"?account_scope={account_scope}"
        return self.request("GET", path, session_key)

    def action_row(self, action_id):
        row = self.app.conn.execute(
            "SELECT * FROM actions WHERE action_id = ?",
            (action_id,)).fetchone()
        return dict(row) if row else None

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.app.close()


# --------------------------------------------------------------------------
# Groq-shaped canned responses for the scripted suites.
# --------------------------------------------------------------------------

def script_chat(name, arguments, call_id="call_1"):
    return {"choices": [{"message": {
        "role": "assistant", "content": "",
        "tool_calls": [{
            "id": call_id, "type": "function",
            "function": {"name": name,
                         "arguments": json.dumps(arguments)},
        }],
    }}]}


def script_answer(text):
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


def tool_names(payload):
    return [record["name"] for record in payload.get("tools", [])]
