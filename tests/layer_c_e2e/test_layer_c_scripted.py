"""Layer C scripted suite — the twelve product scenarios judged through the
REAL HTTP application with scripted (ideal) model behaviour.

These tests evaluate the application contract, not the model: answer states
are derived by the trusted runtime, tool activity and evidence come from the
envelope layer, pending-action cards are produced by the backend, and
confirmation executes through the non-LLM endpoint. Prose is scripted, so no
assertion depends on model wording (04_EVAL_SPEC.md §4).
"""

from contextlib import contextmanager

from backend.tools.prepare_support_action import ACTION_TYPES

from .cases import CASES_BY_ID
from .harness import AppServer, ScriptedClient, script_answer, script_chat

PENDING_CARD_KEYS = {"action_id", "action_type", "status", "token", "payload",
                     "description", "created_at", "expires_at",
                     "seconds_until_expiry"}


@contextmanager
def _serve(tmp_path, script, db_setup=None):
    server = AppServer(tmp_path, ScriptedClient(script), db_setup=db_setup)
    try:
        yield server
    finally:
        server.close()


def test_lc01_northstar_cancellation_override(tmp_path):
    """Golden case 1 — the agreement waiver flows to the UI contract."""
    script = [
        script_chat("query_operations",
                    {"entity": "order", "entity_id": "ORD-1001"}),
        script_answer("Your enterprise agreement waives the cancellation "
                      "fee for ORD-1001 — cancelling costs nothing."),
    ]
    with _serve(tmp_path, script) as server:
        status, payload = server.chat("customer-northstar",
                                      CASES_BY_ID["LC-01"].prompts[0])
    assert status == 200
    assert payload["answer_state"] == "ANSWER"
    assert [(t["name"], t["status"]) for t in payload["tools"]] == \
        [("query_operations", "ok")]
    assert not payload["pending_action"]
    assert payload["provider_failure"] is False


def test_lc02_lumenworks_cancellation_fee(tmp_path):
    script = [
        script_chat("query_operations",
                    {"entity": "order", "entity_id": "ORD-2001"}),
        script_answer("Cancelling ORD-2001 now incurs a fee of INR 250 "
                      "under the standard SOP terms."),
    ]
    with _serve(tmp_path, script) as server:
        status, payload = server.chat("customer-lumenworks",
                                      CASES_BY_ID["LC-02"].prompts[0])
    assert status == 200
    assert payload["answer_state"] == "ANSWER"
    assert payload["tools"][0]["status"] == "ok"


def test_lc03_lumenworks_service_credit(tmp_path):
    script = [
        script_chat("query_operations",
                    {"entity": "order", "entity_id": "ORD-2002"}),
        script_answer("Yes — your service agreement entitles you to a flat "
                      "INR 300 service credit for the missed pickup."),
    ]
    with _serve(tmp_path, script) as server:
        status, payload = server.chat("customer-lumenworks",
                                      CASES_BY_ID["LC-03"].prompts[0])
    assert status == 200
    assert payload["answer_state"] == "ANSWER"


def test_lc04_sla_breach_escalates(tmp_path):
    """Golden case 6 — the breach flag derives ESCALATE regardless of prose."""
    script = [
        script_chat("query_operations",
                    {"entity": "ticket", "entity_id": "TKT-501"}),
        script_answer("TKT-501 has breached the 15-minute P1 response "
                      "target in your agreement; it has been escalated."),
    ]
    with _serve(tmp_path, script) as server:
        status, payload = server.chat("customer-northstar",
                                      CASES_BY_ID["LC-04"].prompts[0])
    assert status == 200
    assert payload["answer_state"] == "ESCALATE"
    assert payload["state_reason"] == \
        "tool result carries an escalation/security flag"


def test_lc05_known_issue_ki211(tmp_path):
    script = [
        script_chat("query_operations",
                    {"entity": "ticket", "entity_id": "TKT-504"}),
        script_answer("This matches known issue KI-211: tracking updates "
                      "lag up to 20 minutes after pickup."),
    ]
    with _serve(tmp_path, script) as server:
        status, payload = server.chat("customer-northstar",
                                      CASES_BY_ID["LC-05"].prompts[0])
    assert status == 200
    assert payload["answer_state"] == "ANSWER"


def test_lc06_historical_vs_current_policy(tmp_path):
    """Golden cases 10/11 — historical text is retrievable but flagged;
    the current rule comes from authoritative sources only."""
    case = CASES_BY_ID["LC-06"]
    script = [
        script_chat("search_knowledge",
                    {"query": "cancellation fee",
                     "include_historical": True}),
        script_answer("Historically, in ticket TKT-450, support quoted a "
                      "fee — that is a past resolution, not current policy."),
        script_chat("search_knowledge",
                    {"query": "cancellation fee"}),
        script_answer("Today your enterprise agreement waives the "
                      "cancellation fee entirely."),
    ]
    with _serve(tmp_path, script) as server:
        status1, turn1 = server.chat("customer-northstar", case.prompts[0])
        status2, turn2 = server.chat("customer-northstar", case.prompts[1])
    assert status1 == 200 and status2 == 200
    # Turn 1: the historical material is visible but flagged non-authoritative.
    hist = [ev for ev in turn1["evidence"]
            if ev["status"] == "HISTORICAL_RESOLUTION"]
    assert hist, "historical resolution must be retrievable on explicit request"
    assert all(ev["authority_rank"] is None for ev in hist)
    assert all(ev["excluded_reason"] for ev in hist)
    # Turn 2: current policy only — no historical record may reappear.
    assert turn2["answer_state"] == "ANSWER"
    assert all(ev["status"] != "HISTORICAL_RESOLUTION"
               for ev in turn2["evidence"])


def test_lc07_deprecated_policy_never_surfaces(tmp_path):
    """GI-1 at application level: Policy v2 is absent from default evidence."""
    script = [
        script_chat("search_knowledge",
                    {"query": "response time targets by severity"}),
        script_answer("Your agreement sets P1 at 2 business hours, P2 at 4 "
                      "business hours and P3 at 2 business days."),
    ]
    with _serve(tmp_path, script) as server:
        status, payload = server.chat("customer-lumenworks",
                                      CASES_BY_ID["LC-07"].prompts[0])
    assert status == 200
    assert payload["answer_state"] == "ANSWER"
    assert payload["evidence"], "a policy question must carry evidence"
    assert not any("DEPRECATED" in ev["source_doc"]
                   for ev in payload["evidence"])
    assert all(ev["authority_rank"] is not None
               for ev in payload["evidence"])


def test_lc08_cross_account_attack_fails_closed(tmp_path):
    """Golden case 12 — the well-formed cross-account lookup is denied by
    the authorization layer; the UI shows the refusal, never the data."""
    script = [
        script_chat("query_operations",
                    {"entity": "account", "entity_id": "ACCT-001"}),
        script_answer("I cannot access another account's data from your "
                      "session."),
    ]
    with _serve(tmp_path, script) as server:
        status, payload = server.chat("customer-lumenworks",
                                      CASES_BY_ID["LC-08"].prompts[0])
    assert status == 200
    assert payload["tools"][0]["status"] == "rejected"
    # No trusted facts from ACCT-001 crossed the boundary into the response.
    blob = str(payload["evidence"]) + str(payload["trace"])
    assert "Northstar" not in blob and "ACCT-001" not in blob
    assert payload["answer_state"] in ("ANSWER", "INSUFFICIENT_EVIDENCE",
                                       "ESCALATE")


def test_lc09_insufficient_evidence(tmp_path):
    """Golden case 9 — unknown fault attribution can never become a credit."""
    case = CASES_BY_ID["LC-09"]
    script = [
        script_chat("query_operations",
                    {"entity": "order", "entity_id": "ORD-9901"}),
        script_answer("Fault attribution is still unknown, so no credit can "
                      "be confirmed yet."),
    ]
    with _serve(tmp_path, script, db_setup=case.db_setup) as server:
        status, payload = server.chat("customer-lumenworks", case.prompts[0])
    assert status == 200
    assert payload["answer_state"] == "INSUFFICIENT_EVIDENCE"


def test_lc10_security_escalation(tmp_path):
    """Golden case 7 — security-incident flag forces ESCALATE server-side."""
    script = [
        script_chat("query_operations",
                    {"entity": "ticket", "entity_id": "TKT-505"}),
        script_answer("TKT-505 is flagged as a security incident and has "
                      "been escalated to the security team."),
    ]
    with _serve(tmp_path, script) as server:
        status, payload = server.chat("customer-axis",
                                      CASES_BY_ID["LC-10"].prompts[0])
    assert status == 200
    assert payload["answer_state"] == "ESCALATE"


def test_lc11_action_preparation_and_confirmation(tmp_path):
    """The full trusted workflow: draft via chat -> explicit confirmation
    endpoint -> exactly-once execution -> replay refused."""
    prompt = CASES_BY_ID["LC-11"].prompts[0]
    payload = {"account_id": "ACCT-002", "order_id": "ORD-2002",
               "amount_inr": 300, "rule": "LUMENWORKS_AGREEMENT_OVERRIDE"}
    script = [
        script_chat("query_operations",
                    {"entity": "order", "entity_id": "ORD-2002"}),
        script_chat("prepare_support_action",
                    {"action_type": "grant_service_credit",
                     "payload": payload}, call_id="call_2"),
        script_answer("Eligible for a INR 300 credit — the action is "
                      "drafted and awaiting your confirmation."),
    ]
    with _serve(tmp_path, script) as server:
        status, chat_payload = server.chat("customer-lumenworks", prompt)
        assert status == 200
        assert chat_payload["answer_state"] == "ANSWER"

        # Nothing executed prematurely: the draft is pending, nothing more.
        pending = chat_payload["pending_action"]
        assert pending and PENDING_CARD_KEYS <= set(pending)
        assert pending["status"] == "pending"
        assert pending["payload"] == payload
        assert pending["action_type"] in ACTION_TYPES
        assert 0 < pending["seconds_until_expiry"] <= 300
        assert "confirm" in pending["description"].lower()
        action_id = pending["action_id"]
        assert server.action_row(action_id)["status"] == "pending"

        # Explicit user confirmation through the non-LLM endpoint.
        server.clock.advance(minutes=2)
        status, confirmed = server.confirm(
            "customer-lumenworks", action_id, pending["token"])
        assert status == 200
        assert confirmed["status"] == "executed"
        assert "INR 300" in confirmed["effect"]
        row = server.action_row(action_id)
        assert row["status"] == "executed"

        # Refresh / double-click / replay: refused, no second effect.
        status, replayed = server.confirm(
            "customer-lumenworks", action_id, pending["token"])
        assert status == 409
        assert replayed["rejection_code"] == "NOT_PENDING"
        assert server.action_row(action_id)["status"] == "executed"


def test_lc12_staff_proactive_insights(tmp_path):
    """The insights view: staff-only, deterministic analytics, honest about
    the patterns the dataset does (and does not) contain."""
    with _serve(tmp_path, []) as server:
        status, payload = server.insights("staff")
        assert status == 200
        summary = payload["summary"]
        sla = payload["sla_status"]
        assert summary["tickets_in_scope"] == len(sla)

        # SLA breaches the domain layer pins (golden cases 6/7).
        by_ticket = {row["ticket_id"]: row for row in sla}
        assert by_ticket["TKT-501"]["breached"] is True
        assert by_ticket["TKT-505"]["breached"] is True
        assert summary["breached_count"] >= 2
        assert summary["escalations_required"] >= 1

        # Known-issue matches the domain layer pins (golden cases 8/9).
        matched = {row["ticket_id"]: row["matched_ki"]
                   for row in payload["known_issues"] if row["matched_ki"]}
        assert matched.get("TKT-502") == "KI-208"
        assert matched.get("TKT-504") == "KI-211"

        # Cross-account grouping is deterministic keyword clustering — the
        # dataset's keyword hits are all single-account, so the honest
        # result carries no cross-account cluster (the UI says exactly that).
        clusters = payload["clusters"]
        assert set(clusters) == {"bulk upload", "pickup status",
                                 "account access"}
        for label, ticket_ids in clusters.items():
            accounts = {by_ticket[tid]["account_id"]
                        for tid in ticket_ids if tid in by_ticket}
            assert len(accounts) <= 1, f"{label} unexpectedly spans {accounts}"
