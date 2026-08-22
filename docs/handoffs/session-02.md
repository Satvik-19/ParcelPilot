# Session 02 — 2026-08-21

## Goal of this session
Close Phase 3 (security/authorization + trusted evidence layer) and Phase 4
(tool layer) per IMPLEMENTATION_PLAN, with full Layer A validation, in the
same build→test→handoff flow as Phases 1–2.

## Current phase
**Phase 3 (security + trust) — COMPLETE. Phase 4 (tool layer) — COMPLETE.**
Both phases met every acceptance criterion; no blockers. Next locked phase is
Phase 5 (agent runtime + Groq benchmark) — NOT started (needs a Groq API key).

## What was done
- `backend/security/authorization.py` — Session model + the access-control
  chokepoint: `validate_session`, `can_access_account` (pure equality on the
  trusted account_id), `require_account_access`, `require_staff`,
  `visible_account_ids`; neutral `AuthorizationError` messages that never
  reveal entity existence (PRD FR-2, golden case 12).
- `backend/security/retrieval.py` — the only read path tools may use:
  `search_scoped_chunks`, `all_scoped_chunks`, `agreement_chunks` (all
  scope-constrained before results return); FTS input neutralised via the new
  `backend/db/documents.to_fts_query()` (word tokens, individually quoted,
  bound as a parameter — promoted from the Phase 1 conftest helper).
- `backend/trust/evidence.py` — `EvidenceRecord` with all 03_AGENT_SPEC §5
  fields (evidence_id, source_doc, section, status, authority_rank,
  applicable_to, overridden_by, excluded_reason, text); `gather_evidence`
  force-includes the account's active agreement, caps at 8 results
  deterministically ordered, and renders deprecated/historical sources with
  `authority_rank=None` + `excluded_reason` (displayable, never deciding).
- `backend/trust/conflicts.py` — deterministic conflict resolution: strictly
  lower authority_rank loses within a policy topic (doc-prefix topic map);
  losers keep `overridden_by`; equal-rank chunks never conflict;
  deprecated/historical records never participate.
- `backend/tools/_envelope.py` — common `{status, result, evidence[],
  warnings[]}` envelope; statuses `ok | rejected | error`; errors are
  structured results, never stack traces.
- `backend/tools/search_knowledge.py` — scoped policy search; account_scope
  defaults to the session account; explicit cross-account scope requests are
  rejected; evidence returned as §5 records.
- `backend/tools/query_operations.py` — the single read tool for
  account/order/ticket; returns computed state only (cancellation, credit,
  severity+SLA+known-issue), never raw row dumps; missing-id and
  cross-account lookups return the SAME neutral denial for customers.
- `backend/tools/analyze_support_activity.py` — staff-only analytics
  (breach states, KI attribution, deterministic keyword clusters); rejected
  for every non-staff session.
- `backend/tools/prepare_support_action.py` — DRAFT-only per ADR-004:
  persists a pending row with canonical payload_hash, session-bound token and
  5-minute expiry from explicit as_of; idempotent (same session+payload
  returns the existing draft); executes NOTHING.
- Tests: 5 new files in `tests/layer_a_domain/`
  (`test_security_authorization.py` 32, `test_trust_evidence.py` 14,
  `test_tool_search_knowledge.py` 24, `test_tool_query_operations.py` 18,
  `test_tool_staff_and_actions.py` 15) + session fixtures in conftest.
- Verified in a clean venv (`.venv-p34`, gitignored, deletable):
  install → seed (4/6/7 + 26 chunks) → full suite green.

## Decisions made
- Conflict "same matter" = policy topic derived from the source-document
  prefix (01/02 → support_policy, 03/05/06 → cancellation_credit,
  04 → operations_known_issues). Deterministic, matches the corpus shape;
  avoids spurious agreement-vs-Ops-Guide conflicts.
- `include_historical=True` surfaces BOTH deprecated v2 and historical
  resolutions (AGENT_SPEC: "displayable on explicit request only"), always
  flagged `authority_rank=None`; default retrieval excludes both.
- Re-preparing an identical action is idempotent (returns the existing
  pending draft) instead of erroring on the deterministic action_id.
- Customer missing-id lookups return ACCESS_DENIED, not NOT_FOUND, so
  responses never reveal whether another account's entity exists.
- All of the above stays inside the locked specs — no locked-doc change, so
  no ADR was required.

## Test status
- Layer A: **250 passed / 0 failed** (was 135; +115 new) on pytest 7.4.4 and
  again in a fresh venv (pytest 9.1.1). Coverage highlights: golden cases
  1–7 re-asserted through tool envelopes; case 12 at data, retrieval and
  tool level (8 adversarial phrasings + 6 FTS/SQL injection probes +
  explicit scope-parameter rejection); GI-1 through the evidence resolver;
  §5 field completeness; force-inclusion; staff-only rejection; draft-only
  action semantics incl. byte-for-byte table-unchanged assertions;
  golden-answer hygiene scan over tools/security/trust (§6); no-wall-clock
  static guard now covers the new modules automatically.
- Layer B: not run (agent runtime is Phase 5; needs Groq key).
- Layer C: not run (Phase 6+).

## Known issues / gotchas
- FTS porter stemming quirks discovered while writing tests: "waiver" does
  NOT stem to match "waived", and v2's chunk contains "response" but not
  "time" — tests for historical/deprecated displayability use
  "cancellation fee" and "severity response targets" accordingly.
- `gather_evidence` caps at 8 records ordered by (authority_rank, chunk_id);
  the force-included 5-chunk agreement consumes most of the cap for
  agreement-holding accounts. Fine for the dataset; revisit when the agent
  complains about missing hits (Phase 5).
- `prepare_support_action` is the only tool that writes; it uses its own
  module-scoped test DB so the shared session fixture DB stays read-only
  (`test_database.py::test_actions_table_exists_and_is_empty` relies on that).
- `.venv-audit/` (session 01) and `.venv-p34/` remain on disk; both
  gitignored and safe to delete.

## Blockers & open questions
- None for Phases 3–4 — both CLOSED.
- Phase 5 requires a Groq API key from the project owner (PRD/PLAN).

## Next steps (in order)
1. Phase 5: agent runtime (`backend/agent/`) — hand-rolled loop per ADR-002,
   system prompt per 03_AGENT_SPEC §2, tool schema WITHOUT session params,
   `confirm_support_action` as a backend endpoint outside the model's tool
   surface (ADR-004), and the Groq model benchmark per 04_EVAL_SPEC.
2. Layer B tests (tool-coverage assertions) once the runtime exists.
3. Keep the envelope/session contract exactly as built here: tools receive
   `(conn, session, ...)`; authorization errors become structured rejections.
