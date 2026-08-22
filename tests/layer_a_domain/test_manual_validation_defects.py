"""Regression tests for the pre-deployment manual-validation defects.

Each class pins one real defect found during manual testing of the deployed
build; every test runs on the seeded assessment database with zero LLM
involvement (04_EVAL_SPEC §2). The fixes are behaviour-level:

- ISSUE 1  service-credit retrieval gap (FTS recall + relevance-aware trim);
- ISSUE 2  empty final text must never surface as an ANSWER;
- ISSUE 3  ACCESS_DENIED is a trusted definitive outcome, not missing evidence;
- ISSUE 4  own-account display-name references must not be mis-denied;
- ISSUE 5  account-level entity listing (bounded, session-scoped);
- ISSUE 6  computed outcomes carry the §5 evidence behind their citations.
"""

import json

import pytest

from backend.agent.executor import derive_answer_state, run_turn
from backend.tools._accounts import canonical_account_id
from backend.tools._envelope import envelope_error, envelope_rejected
from backend.tools.query_operations import query_operations
from backend.tools.search_knowledge import search_knowledge

SOP_CREDIT_SECTION = (
    "03_Cancellation_and_Service_Credit_SOP_v4#Section 2: Failed-pickup "
    "service credits"
)


# --------------------------------------------------------------------------
# Scripted-model harness (same shape as test_agent_runtime.py)
# --------------------------------------------------------------------------

class FakeClient:
    def __init__(self, script):
        self.script = list(script)
        self.received = []

    def complete(self, messages, tools=None, model=None, **kwargs):
        self.received.append(json.loads(json.dumps(messages)))
        return self.script.pop(0)


def call_msg(name, arguments, call_id="call_1"):
    return {"choices": [{"message": {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)},
        }],
    }}]}


def final_msg(content="Here is the answer based on the tool results."):
    return {"choices": [{"message": {"role": "assistant",
                                     "content": content}}]}


@pytest.fixture()
def trace_path(tmp_path):
    return tmp_path / "traces.jsonl"


# --------------------------------------------------------------------------
# ISSUE 1 — the service-credit SOP must be retrievable when the agreement
# delegates to it, even for natural-language phrasings.
# --------------------------------------------------------------------------

class TestIssue1ServiceCreditRetrieval:
    MANUAL_PHRASING = (
        "A pickup is three hours late because of carrier fault. "
        "Should I get a service credit?"
    )

    def test_manual_phrasing_surfaces_the_sop_credit_section(
            self, seeded_db, customer_sessions):
        conn, _ = seeded_db
        env = search_knowledge(conn, customer_sessions["ACCT-001"],
                               self.MANUAL_PHRASING)
        assert env.status == "ok"
        ids = {r["evidence_id"] for r in env.result["results"]}
        # The delegated-to SOP section the manual run failed to retrieve:
        assert SOP_CREDIT_SECTION in ids
        # ...alongside the delegating agreement section.
        assert ("05_Northstar_Logistics_Enterprise_Agreement"
                "#Section 3: Service credits") in ids

    def test_keyword_phrasing_also_surfaces_sop(self, seeded_db,
                                                 customer_sessions):
        conn, _ = seeded_db
        env = search_knowledge(conn, customer_sessions["ACCT-001"],
                               "service credit pickup late carrier fault")
        ids = {r["evidence_id"] for r in env.result["results"]}
        assert SOP_CREDIT_SECTION in ids

    def test_result_set_stays_bounded_and_authority_ordered(
            self, seeded_db, customer_sessions):
        conn, _ = seeded_db
        env = search_knowledge(conn, customer_sessions["ACCT-001"],
                               self.MANUAL_PHRASING)
        results = env.result["results"]
        assert len(results) <= 8
        ranks = [r["authority_rank"] for r in results]
        assert ranks == sorted(ranks)  # agreement before SOP/Policy


# --------------------------------------------------------------------------
# ISSUE 2 — a blank model output is a generation failure, never an ANSWER.
# --------------------------------------------------------------------------

class TestIssue2BlankAnswerEscalates:
    @pytest.mark.parametrize("blank", ["", "   ", "\n"])
    def test_blank_final_text_escalates_with_safe_text(
            self, seeded_db, customer_sessions, trace_path, blank):
        conn, _ = seeded_db
        client = FakeClient([
            call_msg("query_operations",
                     {"entity": "order", "entity_id": "ORD-1001"}),
            final_msg(blank),
        ])
        result = run_turn(conn, customer_sessions["ACCT-001"],
                          "What cancellation terms apply to my order?",
                          client, trace_path=trace_path)
        assert result.answer_state == "ESCALATE"
        assert "no final answer" in result.state_reason
        assert result.answer.strip()          # UI never renders an empty box
        assert "escalated" in result.answer
        assert result.trace["answer_state"] == "ESCALATE"

    def test_think_only_output_is_also_blank(self, seeded_db,
                                              customer_sessions, trace_path):
        conn, _ = seeded_db
        client = FakeClient([
            call_msg("query_operations",
                     {"entity": "order", "entity_id": "ORD-1001"}),
            final_msg("<think>internal reasoning only</think>"),
        ])
        result = run_turn(conn, customer_sessions["ACCT-001"],
                          "What cancellation terms apply?", client,
                          trace_path=trace_path)
        assert result.answer_state == "ESCALATE"
        assert result.answer.strip()


# --------------------------------------------------------------------------
# ISSUE 3 — ACCESS_DENIED must map to a definitive outcome, never
# INSUFFICIENT_EVIDENCE, and rejections never count as tool failures.
# --------------------------------------------------------------------------

class TestIssue3DenialIsDefinitive:
    def test_single_rejection_supports_answer(self):
        state, reason = derive_answer_state(
            [envelope_rejected("ACCESS_DENIED", "denied")])
        assert state == "ANSWER"
        assert "trusted" in reason

    def test_repeated_denials_do_not_escalate(self):
        envs = [envelope_rejected("ACCESS_DENIED", "denied") for _ in range(5)]
        state, _ = derive_answer_state(envs)
        assert state == "ANSWER"

    def test_rejection_breaks_the_error_streak(self):
        # error, rejected, error — no two CONSECUTIVE errors, so no
        # escalation: a denial between errors is a trusted decision.
        envs = [envelope_error("X", "a"),
                envelope_rejected("ACCESS_DENIED", "denied"),
                envelope_error("Y", "b")]
        state, _ = derive_answer_state(envs)
        assert state == "ANSWER"

    def test_cross_account_denial_ends_turn_as_answer(
            self, seeded_db, customer_sessions, trace_path):
        conn, _ = seeded_db
        client = FakeClient([
            call_msg("query_operations",
                     {"entity": "order", "entity_id": "ORD-2001"},
                     call_id="call_1"),
            final_msg("I cannot access that order from this account."),
        ])
        result = run_turn(conn, customer_sessions["ACCT-001"],
                          "Look up ORD-2001 please.", client,
                          trace_path=trace_path)
        assert result.answer_state == "ANSWER"
        # The runtime terminates early on deterministic rejection — the
        # denial is a trusted outcome (ISSUE 3), not something the model
        # needs to rephrase. Verify via the tool trace:
        trace_tools = result.trace["tools"]
        assert len(trace_tools) == 1
        assert trace_tools[0]["status"] == "rejected"

    def test_spoofed_name_denial_is_neutral(self, seeded_db,
                                            customer_sessions):
        conn, _ = seeded_db
        env = query_operations(conn, customer_sessions["ACCT-002"],
                               "account", "Northstar Logistics")
        assert env.status == "rejected"
        assert env.result["rejection_code"] == "ACCESS_DENIED"
        assert "ACCT-001" not in env.result["message"]


# --------------------------------------------------------------------------
# ISSUE 4 — own-account display-name references resolve before the scope
# check; cross-account references still fail the unchanged check.
# --------------------------------------------------------------------------

class TestIssue4DisplayNameCanonicalization:
    @pytest.mark.parametrize("reference", [
        "LumenWorks", "lumenworks", "LUMENWORKS", " LumenWorks ", "ACCT-002",
    ])
    def test_own_account_by_name_or_id_is_allowed(
            self, seeded_db, customer_sessions, reference):
        conn, _ = seeded_db
        env = query_operations(conn, customer_sessions["ACCT-002"],
                               "account", reference)
        assert env.status == "ok"
        assert env.result["account_id"] == "ACCT-002"
        assert env.result["account_name"] == "LumenWorks"

    @pytest.mark.parametrize("reference", [
        "Northstar Logistics", "northstar logistics", "NORTHSTAR LOGISTICS",
    ])
    def test_other_account_by_name_still_denied(
            self, seeded_db, customer_sessions, reference):
        conn, _ = seeded_db
        env = query_operations(conn, customer_sessions["ACCT-002"],
                               "account", reference)
        assert env.status == "rejected"
        assert env.result["rejection_code"] == "ACCESS_DENIED"
        assert "ACCT-001" not in env.result["message"]

    def test_unknown_name_is_denied_for_customer_not_leaking(
            self, seeded_db, customer_sessions):
        conn, _ = seeded_db
        env = query_operations(conn, customer_sessions["ACCT-002"],
                               "account", "NoSuchAccount")
        assert env.status == "rejected"
        assert env.result["rejection_code"] == "ACCESS_DENIED"

    def test_staff_can_resolve_any_account_by_name(self, seeded_db,
                                                   staff_session):
        conn, _ = seeded_db
        env = query_operations(conn, staff_session, "account",
                               "northstar logistics")
        assert env.status == "ok"
        assert env.result["account_id"] == "ACCT-001"

    def test_canonicalizer_is_authorization_free_and_exact(self, seeded_db):
        conn, _ = seeded_db
        assert canonical_account_id(conn, "LumenWorks") == "ACCT-002"
        assert canonical_account_id(conn, "ACCT-002") == "ACCT-002"
        assert canonical_account_id(conn, "Lumen") is None   # no fuzzy match
        assert canonical_account_id(conn, "") is None
        assert canonical_account_id(conn, None) is None

    def test_search_scope_by_display_name(self, seeded_db, customer_sessions):
        conn, _ = seeded_db
        # Own scope by name resolves and succeeds:
        env = search_knowledge(conn, customer_sessions["ACCT-002"],
                               "cancellation", account_scope="LumenWorks")
        assert env.status == "ok"
        assert env.result["account_scope"] == "ACCT-002"
        # Someone else's scope by name resolves and is still denied:
        env = search_knowledge(conn, customer_sessions["ACCT-001"],
                               "cancellation", account_scope="LumenWorks")
        assert env.status == "rejected"
        assert env.result["rejection_code"] == "ACCESS_DENIED"


# --------------------------------------------------------------------------
# ISSUE 5 — account results carry bounded entity listings so the agent can
# answer account-level questions without asking the user for ids.
# --------------------------------------------------------------------------

class TestIssue5AccountListing:
    def test_account_result_lists_own_orders_and_tickets(
            self, seeded_db, customer_sessions):
        conn, _ = seeded_db
        env = query_operations(conn, customer_sessions["ACCT-001"],
                               "account", "ACCT-001")
        assert env.status == "ok"
        # Exactly the DB rows for this account — nothing more, sorted:
        order_ids = [r["order_id"] for r in conn.execute(
            "SELECT order_id FROM orders WHERE account_id = 'ACCT-001'"
            " ORDER BY order_id")]
        ticket_ids = [r["ticket_id"] for r in conn.execute(
            "SELECT ticket_id FROM tickets WHERE account_id = 'ACCT-001'"
            " ORDER BY ticket_id")]
        assert env.result["order_ids"] == order_ids
        assert env.result["ticket_ids"] == ticket_ids
        assert env.result["order_ids"]  # dataset guarantees non-empty

    def test_denied_account_lookup_leaks_no_listing(
            self, seeded_db, customer_sessions):
        conn, _ = seeded_db
        env = query_operations(conn, customer_sessions["ACCT-002"],
                               "account", "Northstar Logistics")
        assert env.status == "rejected"
        assert "order_ids" not in env.result
        assert "ticket_ids" not in env.result

    def test_listing_via_display_name(self, seeded_db, customer_sessions):
        conn, _ = seeded_db
        env = query_operations(conn, customer_sessions["ACCT-002"],
                               "account", "LumenWorks")
        assert env.status == "ok"
        assert env.result["order_ids"] == ["ORD-2001", "ORD-2002"]


# --------------------------------------------------------------------------
# ISSUE 6 — computed outcomes must carry the §5 evidence behind their
# citations, so the UI evidence panel matches the answer.
# --------------------------------------------------------------------------

class TestIssue6CitationEvidence:
    def test_order_envelope_carries_cited_evidence(self, seeded_db,
                                                   customer_sessions):
        conn, _ = seeded_db
        env = query_operations(conn, customer_sessions["ACCT-001"],
                               "order", "ORD-1001")
        assert env.status == "ok"
        assert env.evidence, "computed outcome must expose §5 evidence"
        ids = {rec.evidence_id for rec in env.evidence}
        # Every cited source resolves to a real evidence record:
        assert ("05_Northstar_Logistics_Enterprise_Agreement"
                "#Section 2: Shipment cancellation") in ids
        assert ("03_Cancellation_and_Service_Credit_SOP_v4"
                "#Section 1: Order cancellation") in ids
        # Conflict semantics survive: the SOP loses to the agreement.
        sop = next(r for r in env.evidence
                   if r.source_doc.startswith("03_"))
        assert sop.overridden_by

    def test_range_citation_expands_to_each_section(self, seeded_db,
                                                    customer_sessions):
        conn, _ = seeded_db
        # ORD-2002's credit cites SOP "§2–3": both sections must appear.
        env = query_operations(conn, customer_sessions["ACCT-002"],
                               "order", "ORD-2002")
        assert env.status == "ok"
        sections = {rec.section for rec in env.evidence
                    if rec.source_doc.startswith("03_")}
        assert {"Section 2: Failed-pickup service credits",
                "Section 3: Approval and uncertainty"} <= sections

    def test_ticket_envelope_carries_cited_evidence(self, seeded_db,
                                                    customer_sessions):
        conn, _ = seeded_db
        env = query_operations(conn, customer_sessions["ACCT-001"],
                               "ticket", "TKT-501")
        assert env.status == "ok"
        assert env.evidence
        docs = {rec.source_doc for rec in env.evidence}
        assert any(d.startswith("01_") for d in docs)  # severity/SLA source

    def test_ui_evidence_reaches_the_turn_result(self, seeded_db,
                                                 customer_sessions,
                                                 trace_path):
        conn, _ = seeded_db
        client = FakeClient([
            call_msg("query_operations",
                     {"entity": "order", "entity_id": "ORD-1001"},
                     call_id="call_1"),
            final_msg("No cancellation fee applies under your agreement."),
        ])
        result = run_turn(conn, customer_sessions["ACCT-001"],
                          "Can I cancel ORD-1001?", client,
                          trace_path=trace_path)
        assert result.answer_state == "ANSWER"
        assert result.evidence, "turn result must expose UI evidence"
        assert result.trace["evidence_ids"], "trace must record evidence ids"
