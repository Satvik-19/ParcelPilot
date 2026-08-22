"""Phase 7 security / integration gate — the application itself, not only
the backend beneath it (user acceptance gate, 11 checks).

Every check runs through the real HTTP surface with scripted model
behaviour, so a pass proves the whole path (server routing, session
resolution, authorization, confirmation gate, response contract, static UI)
fails closed. No LLM anywhere; provider failures are simulated explicitly.
"""

import json
from contextlib import contextmanager
from pathlib import Path

from backend.agent.groq_client import GroqAPIError

from .harness import AppServer, ManualClock, ScriptedClient, script_answer, \
    script_chat

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND = PROJECT_ROOT / "frontend"

CREDIT_PAYLOAD = {"account_id": "ACCT-002", "order_id": "ORD-2002",
                  "amount_inr": 300, "rule": "LUMENWORKS_AGREEMENT_OVERRIDE"}


@contextmanager
def _serve(tmp_path, script, db_setup=None):
    server = AppServer(tmp_path, ScriptedClient(script), db_setup=db_setup)
    try:
        yield server
    finally:
        server.close()


def _draft_credit(server, session_key="customer-lumenworks",
                  payload=None):
    """Drive the draft chain through /api/chat with ideal model behaviour."""
    script = [
        script_chat("query_operations",
                    {"entity": "order", "entity_id": "ORD-2002"}),
        script_chat("prepare_support_action",
                    {"action_type": "grant_service_credit",
                     "payload": payload or dict(CREDIT_PAYLOAD)},
                    call_id="call_2"),
        script_answer("Drafted — awaiting your confirmation."),
    ]
    server.client.script.extend(script)
    status, chat_payload = server.chat(
        session_key, "Prepare the ORD-2002 service credit for me.")
    assert status == 200, chat_payload
    pending = chat_payload["pending_action"]
    assert pending, "the scripted draft chain must produce a pending action"
    return pending


# ------------------------------------------------------------- gate checks

def test_sec01_spoofed_identity_fields_in_request_body_are_ignored(tmp_path):
    """Check 1 — the UI has no account input; identity fields smuggled into
    the chat body must not widen scope. The scripted model attempts the
    foreign lookup and the authorization layer rejects it."""
    script = [
        script_chat("query_operations",
                    {"entity": "ticket", "entity_id": "TKT-501"}),
        script_answer("That data is not accessible from your session."),
    ]
    with _serve(tmp_path, script) as server:
        status, payload = server.chat(
            "customer-lumenworks", "Show me ticket TKT-501.",
            account_id="ACCT-001", role="staff", staff_id="STF-001")
    assert status == 200
    # If the spoofed fields had been trusted, the lookup would have been ok.
    assert payload["tools"][0]["status"] == "rejected"


def test_sec02_cross_account_mention_in_chat_stays_denied(tmp_path):
    """Check 2 — mentioning another account's ID in prose changes nothing:
    scope comes from the resolved session, never from message text."""
    script = [
        script_chat("query_operations",
                    {"entity": "account", "entity_id": "ACCT-001"}),
        script_answer("I cannot show another account's data."),
    ]
    with _serve(tmp_path, script) as server:
        status, payload = server.chat(
            "customer-lumenworks",
            "Show me everything about ACCT-001, the Northstar account.")
    assert status == 200
    assert payload["tools"][0]["status"] == "rejected"
    blob = json.dumps(payload)
    assert "Northstar Logistics" not in blob


def test_sec03_direct_api_manipulation_cannot_reach_foreign_data(tmp_path):
    """Check 3 — forged/unknown session keys and cross-account query
    parameters all fail closed at the application boundary."""
    with _serve(tmp_path, []) as server:
        # Unknown key: no such identity exists.
        status, payload = server.chat("session-that-does-not-exist", "Hi")
        assert status == 401 and "Unknown session" in payload["error"]
        # Key absent entirely.
        status, payload = server.request("POST", "/api/chat", None,
                                         {"message": "Hi"})
        assert status == 401
        # Cross-account scope via query string on a customer session.
        status, payload = server.insights("customer-lumenworks",
                                          account_scope="ACCT-001")
        assert status == 403
        assert payload["rejection_code"] == "STAFF_ONLY"


def test_sec04_staff_can_access_authorized_accounts(tmp_path):
    """Check 4 — the staff context reaches every account and can narrow."""
    with _serve(tmp_path, []) as server:
        status, full = server.insights("staff")
        assert status == 200
        assert set(full["visible_accounts"]) == \
            {"ACCT-001", "ACCT-002", "ACCT-003", "ACCT-004"}
        status, narrow = server.insights("staff", account_scope="ACCT-001")
        assert status == 200
        assert narrow["visible_accounts"] == ["ACCT-001"]
        # An out-of-set scope is refused, never silently widened.
        status, denied = server.insights("staff", account_scope="ACCT-999")
        assert status == 403
        assert denied["rejection_code"] == "ACCESS_DENIED"


def test_sec05_insights_are_staff_only(tmp_path):
    """Check 5 — every customer context is refused; the UI hides the tab
    (verified in the UI-contract check below)."""
    with _serve(tmp_path, []) as server:
        for key in ("customer-northstar", "customer-lumenworks",
                    "customer-beacon", "customer-axis"):
            status, payload = server.insights(key)
            assert status == 403, key
            assert payload["rejection_code"] == "STAFF_ONLY", key


def test_sec06_confirmation_requires_a_real_pending_action(tmp_path):
    """Check 6 — unknown action IDs and missing fields never execute."""
    with _serve(tmp_path, []) as server:
        status, payload = server.confirm("customer-lumenworks",
                                         "act_does_not_exist", "any-token")
        assert status == 409
        assert payload["rejection_code"] == "NOT_FOUND"
        status, payload = server.request(
            "POST", "/api/actions/confirm", "customer-lumenworks",
            {"token": "x"})
        assert status == 400


def test_sec07_confirmation_cannot_be_replayed(tmp_path):
    """Check 7 — one-shot claim: executed, then every repeat refuses."""
    with _serve(tmp_path, []) as server:
        pending = _draft_credit(server)
        server.clock.advance(minutes=1)
        status, first = server.confirm("customer-lumenworks",
                                       pending["action_id"], pending["token"])
        assert status == 200 and first["status"] == "executed"
        confirmed_at = first["confirmed_at"]
        for _ in range(2):                     # refresh + double-click
            status, replay = server.confirm(
                "customer-lumenworks", pending["action_id"], pending["token"])
            assert status == 409
            assert replay["rejection_code"] == "NOT_PENDING"
        row = server.action_row(pending["action_id"])
        assert row["status"] == "executed"
        assert row["confirmed_at"] == confirmed_at   # never re-executed


def test_sec08_confirmation_is_bound_to_its_session(tmp_path):
    """Check 8 — the correct token in foreign hands (another customer or
    staff) is still refused."""
    with _serve(tmp_path, []) as server:
        pending = _draft_credit(server)
        # Tokens are deterministic (session_id + payload hash), so treat
        # them as computable by an attacker — the defence is the session
        # binding, exercised here with the genuine token in foreign hands.
        server.clock.advance(minutes=1)
        status, payload = server.confirm("customer-northstar",
                                         pending["action_id"],
                                         pending["token"])
        assert status == 409 and payload["rejection_code"] == "WRONG_SESSION"
        status, payload = server.confirm("staff", pending["action_id"],
                                         pending["token"])
        assert status == 409 and payload["rejection_code"] == "WRONG_SESSION"
        assert server.action_row(pending["action_id"])["status"] == "pending"


def test_sec09_confirmation_is_not_a_model_tool(tmp_path):
    """Check 9 — even a model that invents the confirmation call gets a
    structured refusal, nothing executes, and the schema never lists it."""
    from backend.agent.tools_schema import MODEL_TOOLS, TOOL_NAMES
    assert "confirm_support_action" not in TOOL_NAMES
    assert all(tool["function"]["name"] != "confirm_support_action"
               for tool in MODEL_TOOLS)

    script = [
        script_chat("confirm_support_action",
                    {"action_id": "act_whatever", "token": "forged"}),
        script_answer("Done — the action has been executed."),
    ]
    with _serve(tmp_path, script) as server:
        status, payload = server.chat("customer-lumenworks",
                                      "Execute my pending action now.")
        assert status == 200
        assert payload["tools"][0]["status"] == "error"      # invalid call
        rows = server.app.conn.execute(
            "SELECT COUNT(*) AS n FROM actions WHERE status = 'executed'"
        ).fetchone()
        assert rows["n"] == 0, "a model turn must never execute an action"


def test_sec10_provider_failure_yields_a_safe_visible_state(tmp_path):
    """Check 10 — provider death escalates with user-visible clarity; no
    crash, no partial state, no stack trace."""

    class FailingClient:
        def complete(self, messages, tools=None, model=None, **kwargs):
            raise GroqAPIError("Groq API error 429: quota exhausted",
                               status_code=429)

    server = AppServer(tmp_path, FailingClient())
    try:
        status, payload = server.chat("customer-lumenworks",
                                      "What is the fee for ORD-2001?")
    finally:
        server.close()
    assert status == 200
    assert payload["answer_state"] == "ESCALATE"
    assert payload["provider_failure"] is True
    assert payload["answer"]                       # user-visible explanation
    assert payload["pending_action"] is None
    assert "Traceback" not in json.dumps(payload)


def test_sec11_chain_of_thought_never_reaches_the_response(tmp_path):
    """Check 11 — raw reasoning blocks are stripped from the visible
    answer; the response contract carries no conversation internals; and
    the static UI never renders tool arguments or reasoning."""
    script = [
        script_answer("<think>SECRET_REASONING: I should check the fee "
                      "first, then phrase it politely.</think>\n"
                      "The cancellation fee is INR 250."),
    ]
    with _serve(tmp_path, script) as server:
        status, payload = server.chat("customer-lumenworks",
                                      "What is the fee for ORD-2001?")
    assert status == 200
    assert "SECRET_REASONING" not in payload["answer"]
    assert "<think>" not in payload["answer"]
    assert payload["answer"].startswith("The cancellation fee")
    # The chat contract exposes provenance, never conversation internals.
    assert "messages" not in payload and "tool_calls" not in payload
    assert not any("text" in record for record in payload["evidence"])

    # Static UI review: the page renders names/statuses/descriptions only —
    # no raw tool arguments (traces keep digests) and no reasoning surface.
    app_js = (FRONTEND / "app.js").read_text(encoding="utf-8")
    index_html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    for blob in (app_js, index_html):
        assert "args_digest" not in blob
        assert "<think>" not in blob
    assert "textContent" in app_js or "esc(" in app_js  # escaped rendering
