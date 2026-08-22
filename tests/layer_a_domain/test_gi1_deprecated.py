"""GI-1 — Deprecated-policy exclusion (docs/02_DOMAIN_SPEC.md §5).

Support Policy v2 (DEPRECATED) numbers must never surface as authoritative,
even when a naive lexical search would rank v2 highly. Enforced at two levels:
(1) retrieval — the authoritative filter excludes DEPRECATED chunks for every
account and every query shape; (2) domain — the policy constants carry no v2
numbers and every SLA target cites v3 or an agreement, never v2.
"""

import pytest

from backend.domain import policy_data
from backend.domain.policy_data import (
    AGREEMENTS,
    DEFAULT_SLA_TABLE,
    POLICY_V2_DOC,
    get_agreement,
)
from backend.domain.sla import resolve_sla

from .conftest import fts_search

_PLANS = ("Enterprise", "Growth", "Standard")
_SEVERITIES = ("P1", "P2", "P3")

# Query shapes deliberately tuned to rank the v2 policy highly (it describes
# the same topics with older numbers) — exactly the GI-1 probe.
_GI1_QUERIES = (
    "response time",
    "SLA priority first response",
    "support policy response target",
    "P1 P2 priority hours",
    "cancellation fee",
    "service credit",
)


@pytest.mark.parametrize("query", _GI1_QUERIES)
@pytest.mark.parametrize("account_id", ("ACCT-001", "ACCT-002", "ACCT-003", "ACCT-004"))
def test_gi1_deprecated_never_authoritative(seeded_db, query, account_id):
    conn, _ = seeded_db
    hits = fts_search(conn, query, account_id=account_id, authoritative_only=True)
    assert all(c["status"] != "DEPRECATED" for c in hits), (
        f"deprecated v2 surfaced as authoritative for query {query!r}"
    )
    assert all(POLICY_V2_DOC != c["source_doc"] for c in hits)


def test_gi1_deprecated_present_but_rank_4(all_chunks):
    """v2 is indexed (explicit-request retrieval) yet marked non-authoritative."""
    v2 = [c for c in all_chunks if c["source_doc"] == POLICY_V2_DOC]
    assert v2, "v2 must exist in the index for explicitly-requested retrieval"
    assert all(c["status"] == "DEPRECATED" for c in v2)
    assert all(c["authority_rank"] == 4 for c in v2)


@pytest.mark.parametrize("plan", _PLANS)
@pytest.mark.parametrize("severity", _SEVERITIES)
def test_gi1_resolve_sla_never_cites_v2(plan, severity):
    account = {"plan": plan}
    target = resolve_sla(account, severity, agreement=None)
    assert POLICY_V2_DOC not in target.source
    assert target.source.startswith("01_")  # v3 only


@pytest.mark.parametrize("account_id", ("ACCT-001", "ACCT-002"))
@pytest.mark.parametrize("severity", _SEVERITIES)
def test_gi1_agreement_sla_never_cites_v2(account_id, severity):
    agreement = get_agreement(account_id)
    account = {"plan": "Enterprise"}
    target = resolve_sla(account, severity, agreement=agreement)
    assert POLICY_V2_DOC not in target.source


def test_gi1_domain_constants_carry_no_v2_numbers():
    """The deprecated v2 figures are absent from the domain layer by construction."""
    assert DEFAULT_SLA_TABLE["Enterprise"]["P1"] == (30, "minutes", True)
    assert DEFAULT_SLA_TABLE["Growth"]["P1"] == (2, "business_hours", False)
    assert DEFAULT_SLA_TABLE["Standard"]["P1"] == (4, "business_hours", False)
    assert policy_data.CANCELLATION["late_fee_inr"] == 250
    assert policy_data.SERVICE_CREDIT_DEFAULT["threshold_hours"] == 2
    for agreement in AGREEMENTS.values():
        assert POLICY_V2_DOC not in agreement["cancellation"]["source"]
        assert POLICY_V2_DOC not in agreement["sla"]["source"]
        assert POLICY_V2_DOC not in agreement["service_credit"]["source"]


def test_gi1_historical_resolutions_never_authoritative(seeded_db):
    """Rank-5 historical resolutions share v2's exclusion from default retrieval."""
    conn, _ = seeded_db
    for query in ("cancellation fee", "growth plan rows"):
        hits = fts_search(conn, query, account_id="ACCT-001", authoritative_only=True)
        assert all(c["authority_rank"] <= 3 for c in hits)
