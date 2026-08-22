"""search_knowledge — scoped, evidence-backed policy/document search.

The only retrieval tool the model may use for policy questions (AGENT_SPEC
§3). Scope is decided by the session (security), applicability by the trust
layer; the model supplies only the query text. Golden case 12 lives here:
no phrasing can make this tool return another account's chunks.
"""

from backend.security import authorization
from backend.trust import evidence

from ._envelope import envelope_error, envelope_ok, envelope_rejected


def search_knowledge(conn, session, query, account_scope=None,
                     include_historical=False, as_of=None):
    try:
        sess = authorization.validate_session(session)
    except authorization.AuthorizationError as exc:
        return envelope_rejected(exc.code, exc.message)

    if not query or not str(query).strip():
        return envelope_error("INVALID_INPUT", "A non-empty query is required.")

    scope = account_scope or sess.account_id
    if not scope:
        return envelope_error("INVALID_INPUT",
                              "This tool requires an account scope.")
    if not authorization.can_access_account(sess, scope):
        return envelope_rejected(
            "ACCESS_DENIED",
            "This session is not authorized to access that account's data.",
        )

    kwargs = {"as_of": as_of} if as_of is not None else {}
    records = evidence.gather_evidence(
        conn, scope, query=query, include_historical=include_historical, **kwargs
    )
    if not records:
        return envelope_ok(
            result={
                "account_scope": scope,
                "results": [],
                "note": "No applicable, in-scope source matched the query.",
            },
            evidence=(),
        )

    results = [
        {
            "evidence_id": rec.evidence_id,
            "source_doc": rec.source_doc,
            "section": rec.section,
            "status": rec.status,
            "authority_rank": rec.authority_rank,
            "applicable_to": rec.applicable_to,
            "overridden_by": rec.overridden_by,
            "excluded_reason": rec.excluded_reason,
            "text": rec.text,
        }
        for rec in records
    ]
    return envelope_ok(
        result={"account_scope": scope, "results": results},
        evidence=records,
    )
