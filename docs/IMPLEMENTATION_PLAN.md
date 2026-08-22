# Implementation Plan

**Status: ACTIVE.** This plan elaborates the locked build order in `00_PRD.md` §7 at finer
granularity — it changes no locked decision. Phases are sequential; do not start a phase
whose dependencies are not green (`05_CODING_AGENT_RULES.md` §2). Scope is deliberately
small: 4 accounts, 6 order rows, 7 ticket rows, six one-page PDFs.

## Phase 0 — Documentation/context validation
- **Objective:** Confirm the doc set is consistent and the supplied data pack is present
  and matches `02_DOMAIN_SPEC.md` before any code.
- **Deliverables:** verified doc cross-references; data pack placed in `data/raw/`
  unmodified; dataset row counts corrected in `02_DOMAIN_SPEC.md` (6 orders / 7 tickets —
  the earlier 7/8 counts were wrong; all IDs were already enumerated correctly);
  `docs/handoffs/session-00.md` written.
- **Acceptance:** all golden-case numbers re-derivable from workbook + PDFs; no open doc
  contradictions; handoff exists.
- **Dependencies/blockers:** data pack present at `assessment_docs/` (6 PDFs + workbook),
  validated against `02_DOMAIN_SPEC.md` — no remaining Phase 0 blockers. Groq API key
  needed from Phase 5.

## Phase 1 — Data ingestion + SQLite/FTS5
- **Objective:** Single SQLite file holding all relational data and the document index.
- **Deliverables:** `backend/db/schema.sql` (accounts, orders, tickets, actions incl.
  `payload_hash`/`session_id`/`expires_at`), `backend/db/seed.py` (xlsx → SQLite), PDFs
  parsed via `pdftotext -layout` into section-level chunks, FTS5 virtual table over chunks
  with metadata columns (`source_doc, status, scope, section, authority_rank`).
- **Tests/acceptance:** row counts match `02_DOMAIN_SPEC.md` §1; every chunk carries
  correct status/scope metadata (v2 flagged DEPRECATED, TKT-450/451 resolutions flagged
  historical); FTS5 returns expected chunks for sample queries; DB opens read/write on
  Windows and Linux paths.
- **Dependencies:** Phase 0 (data pack).

## Phase 2 — Pure domain/business logic + Layer A tests
- **Objective:** The complete PolicyResolver with no LLM, no framework dependency.
- **Deliverables:** `backend/domain/` — `cancellation.py`, `credits.py`, `sla.py`,
  `known_issues.py`; `SNAPSHOT_TS` constant; every time-dependent function taking explicit
  `as_of` (default `SNAPSHOT_TS`); `tests/layer_a_domain/` covering golden cases 1–9 plus
  time-determinism tests (`04_EVAL_SPEC.md` §2).
- **Tests/acceptance:** all golden computation cases assert exact `rule`/amount outcomes;
  synthetic `as_of` values reproduce spec results; zero `datetime.now()` in `domain/`
  (grep-enforced test).
- **Dependencies:** Phase 1 fixtures for realistic row shapes (pure functions may be
  developed against spec literals in parallel).

## Phase 3 — Security/authorization + trusted evidence/policy layer
- **Objective:** The trust boundary: session-scoped access and authority-based evidence.
- **Deliverables:** `backend/security/authorization.py` (session model `{role, account_id |
  staff_id, permissions}`, chokepoint filtering for every data read); `backend/trust/
  evidence.py` (structured evidence records per `03_AGENT_SPEC.md` §5, force-inclusion of
  the in-scope active agreement, default exclusion of DEPRECATED/historical, explicit-
  request surfacing with `authority_rank=NONE`); `backend/trust/conflicts.py`
  (deterministic higher-rank-wins with `overridden_by` recorded).
- **Tests/acceptance:** golden case 12 rejected at this layer for plain and adversarial
  inputs; GI-1 resolver unit test green; evidence records carry all §5 fields; retrieval
  authorization vs applicability distinction covered by tests.
- **Dependencies:** Phases 1–2.

## Phase 4 — Tool layer
- **Objective:** The four tools as thin wrappers over domain/trust/security.
- **Deliverables:** `backend/tools/` — `search_knowledge.py`, `query_operations.py`,
  `analyze_support_activity.py` (staff-only check), `prepare_support_action.py` (drafts
  only); common `{status, result, evidence[], warnings[]}` envelope; session injected
  server-side, absent from tool schemas.
- **Tests/acceptance:** tool-level Layer A tests incl. adversarial case 12; staff-only tool
  rejects customer sessions; `prepare_support_action` persists a correct pending row and
  executes nothing; no golden-case answers hardcoded in tool code.
- **Dependencies:** Phases 2–3.

## Phase 5 — Agent runtime + model/tool-calling benchmark
- **Objective:** Hand-rolled loop + chosen model.
- **Deliverables:** `backend/agent/` — planner/executor/context/prompts per
  `03_AGENT_SPEC.md` §1 (8-iteration cap, answer state derived from tool/domain results per
  §2); Groq client; model benchmark per ADR-006 (2–3 candidates against the Layer B prompt
  set), winner logged as an ADR-006 update.
- **Tests/acceptance:** loop terminates at cap with ESCALATE; answer state never ANSWER
  without a supporting tool/domain result; benchmark table recorded.
- **Dependencies:** Phase 4; Groq API key. **Blocker risk:** weak tool-calling model →
  fall back per ADR-006/PRD §8 change control.

## Phase 6 — Layer B agent evaluation
- **Objective:** Prove tool-selection behavior across the golden set.
- **Deliverables:** `tests/layer_b_tool_use/` — expected tool sequences per case,
  well-formed-argument assertions, schema-boundary assertion (`confirm_support_action`
  absent from the model-visible schema), session-injection assertion; recorded runs.
- **Tests/acceptance:** tool-coverage assertions pass for the chosen model on all runnable
  cases; failures triaged (prompt vs model) before proceeding.
- **Dependencies:** Phase 5.

## Phase 7 — Next.js chat UI + tool/evidence trace
- **Objective:** Working chat for both contexts with visible provenance.
- **Deliverables:** `frontend/` — session selector (4 mock accounts + staff), chat view,
  per-turn trace display (tools fired, latencies, answer state, evidence list per
  `03_AGENT_SPEC.md` §7), FastAPI routes in `backend/api/` returning traces.
- **Tests/acceptance:** FR-6 satisfied: every turn visibly shows which tools fired and the
  evidence used; trace JSONL written locally.
- **Dependencies:** Phases 5–6.

## Phase 8 — Internal insights/proactive issue view
- **Objective:** FR-8 surface for staff sessions.
- **Deliverables:** staff-only insights view rendering `analyze_support_activity` output:
  SLA breach/at-risk list, KI-208/KI-211 matches, deterministic cross-account groupings.
- **Tests/acceptance:** view renders TKT-501/505 breaches and TKT-502/504 KI matches from
  real seeded data; hidden/unreachable for customer sessions.
- **Dependencies:** Phases 4, 7.

## Phase 9 — Confirmation/action workflow + security tests
- **Objective:** The full prepare → confirm → mocked-execute flow, fail-closed.
- **Deliverables:** pending-action creation per `03_AGENT_SPEC.md` §4 (payload hash,
  session binding, 5-min expiry, one-time token); `confirm_support_action` backend endpoint
  with the six-check validation; UI confirm card wired to that endpoint (not the chat
  stream); mocked effects for escalate/update/follow-up/cancellation/credit.
- **Tests/acceptance:** each validation check fails closed individually with no state
  mutation (`04_EVAL_SPEC.md` §2 confirmation-gate suite); expiry tested with synthetic
  time; chat-based confirmation attempts execute nothing.
- **Dependencies:** Phases 4, 7. **Security gate: this suite must be green before demo.**

## Phase 10 — Layer C end-to-end evaluation
- **Objective:** Prove the live system against the answer key.
- **Deliverables:** `tests/layer_c_e2e/` — ≥6 of 12 golden cases passing through the live
  agent (trace-based judging); full adversarial suite (case 12 injection, confirmation
  bypass, GI-1 probe, TKT-450/451 historical traps); retained trace JSONL per run.
- **Tests/acceptance:** coverage and adversarial criteria of `04_EVAL_SPEC.md` §4 all met.
- **Dependencies:** Phases 7–9.

## Phase 11 — Deployment, README, final docs, demo
- **Objective:** Hosted, reviewable submission.
- **Deliverables:** frontend on Vercel + backend on Render/Railway free tier (SQLite file
  persistence verified); `README.md` with setup/run; trimmed `docs/architecture.md` +
  `docs/product.md` (incl. explicit out-of-scope trade-offs per PRD §6); 5-minute demo
  video per `01_ARCHITECTURE.md` §5 script; final handoff.
- **Tests/acceptance:** PRD §5 definition of done fully checked; demo script beats
  covered live (ORD-1001 vs ORD-2001, TKT-450 trap, cross-account rejection, TKT-501
  confirm flow, insights view).
- **Dependencies:** Phases 1–10. **Blocker risk:** free-tier ephemeral filesystem for
  SQLite — verify persistence or pin the instance; decide in this phase, log in handoff.
