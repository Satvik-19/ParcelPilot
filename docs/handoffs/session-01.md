# Session 01 — 2026-08-21

## Goal of this session
Implement IMPLEMENTATION_PLAN Phase 1 (SQLite + FTS5 data layer) and Phase 2
(pure deterministic domain layer), plus the full Layer A test suite — and stop
before any tool/agent/UI work, pending project-owner approval.

## Current phase
IMPLEMENTATION_PLAN Phase 1 (data layer) — **COMPLETE.**
IMPLEMENTATION_PLAN Phase 2 (domain layer + Layer A tests) — **COMPLETE.**
**Quality gate audit passed** — Phase 1 + Phase 2 cleared for Phase 3.
Phase 3+ deliberately untouched.

## What was done
- **Phase 1 — data layer** (`backend/db/`):
  - `schema.sql` — accounts/orders/tickets/actions/document_chunks + FTS5
    external-content index `chunks_fts` (porter unicode61). Fault booleans are
    nullable INTEGER (NULL = unknown → INSUFFICIENT_EVIDENCE path).
  - `workbook.py` — single deterministic interpretation of the assessment
    workbook (used by both seeder and tests); parses string datetimes, keeps
    None intact.
  - `documents.py` — verified 6-document catalog (status/effective/scope/
    authority rank), layout-mode pypdf extraction, section-level chunking with
    header status sanity-check, `historical_resolution_chunks()` (rank 5),
    `visible_to_account()` (scope filter, golden case 12 substrate),
    `authoritative_chunks()` (rank ≤ 3 default-retrieval filter — the GI-1 /
    cases 10–12 enforcement point).
  - `database.py` + `seed.py` — idempotent schema init; seeder asserts the
    Phase-0 acceptance counts (4/6/7), converts bools→INTEGER and datetimes→
    canonical TEXT, rebuilds the FTS index; seeded DB = 26 chunks.
    Run with `python -m backend.db.seed` (writes `data/parcel_pilot.db`).
- **Phase 2 — domain layer** (`backend/domain/`): `timebase` (SNAPSHOT_TS
  pinned), `policy_data` (all constants source-cited; v2 numbers absent by
  construction), `businesstime` (Mon–Fri 09:00–18:00 convention), `severity`
  (deterministic `classify_severity()` from Policy v3 §2 — resolves the
  session-00 open question), `cancellation`, `credits`, `sla` (targets +
  breach report with escalation/security flags), `known_issues` (KI-208/211
  match, KI-176 exclusion).
- **Layer A tests** (`tests/layer_a_domain/`, 8 files): all 12 golden cases +
  GI-1 (retrieval-level across 6 query shapes × 4 accounts + domain-level),
  cancellation/credit/SLA boundary cases, KI-176 exclusion, SNAPSHOT_TS
  pinning, wall-clock static guard over all backend sources, seeded-DB
  round-trips, chunk metadata, FTS integrity.

## Decisions made
- **Ticket severity via deterministic classifier (no new column).** Session-00
  left this open (classifier vs explicit seed column). Chose
  `classify_severity()` keyed to v3 §2 wording: keeps the workbook untouched
  (source of truth) and the classification is auditable per ticket
  (`rationale` + `source`). No ADR needed — implementation choice within the
  locked spec, recorded here per session-00's own note.
- **Enterprise P2 "2 hours" treated as calendar time** (v3 marks only P1 as
  24x7 and gives Enterprise P2 no business-hours qualifier). Documented in
  `policy_data.py`; no golden case depends on it.
- **FTS query sanitisation lives with the search helper**: user text is
  tokenised and quoted before MATCH (adversarial phrasings like case 12's
  injection string must never break retrieval — they just match fewer docs).
- No locked doc changed; no new dependencies (openpyxl/pypdf/pytest already
  declared in Phase 0/1 scaffolding).

## Test status
- Layer A: **135 passed, 0 failed** (`python -m pytest tests`) — verified both on
  the system interpreter (pytest 7.4.4) and in a fresh venv built from
  `requirements.txt` alone (pytest 9.1.1). Golden cases 1–12 + GI-1 all green
  on the real workbook rows.
- Layer B: not run (no agent/tools yet — Phase 5+).
- Layer C: not run (no runtime yet — Phase 9+).

## Quality-gate audit (post-implementation, same session)
- **Domain vs source PDFs:** every `policy_data` constant re-derived against the
  extracted PDF text (SOP §1–3, v3 §2–3, both agreements §1–3, Ops Guide KIs).
  All match. Note: Northstar §2 says "no cancellation fee" — the word "waiver"
  never appears in the document; `waives_fee=True` models it correctly.
- **Security boundary:** all SQL parameterised; FTS MATCH only ever receives
  quoted `\w+` tokens through a bound parameter, so arbitrary natural language
  (FTS operators, quotes, wildcards, SQL injection strings) can neither error
  nor bypass scope — now regression-tested. Scope checks are pure equality
  (fail-closed for unknown/None account_id) — also tested. Remaining exposure:
  `backend/db` still offers unfiltered low-level access; the mandatory
  chokepoint `security/authorization.py` is the Phase 3 deliverable and must
  wrap every read (filters `visible_to_account`/`authoritative_chunks` exist
  as its substrate).
- **Coverage gaps found & closed (+3 tests):** FTS operator/injection
  neutralisation, unknown/anonymous-account fail-closed scope,
  `authoritative_chunks` direct check. Deliberately NOT added: monthly-cap
  enforcement (needs a credit ledger — Phase 9 per DOMAIN_SPEC data note),
  manager-approval >₹1,000 trigger (unreachable at current capped amounts),
  LumenWorks no-weekend flag semantics (business-time convention already
  yields zero weekend minutes).
- **Clean-environment check:** fresh venv → `pip install -r requirements.txt`
  → seed → FTS retrieval → full suite, all green. No undeclared runtime
  dependencies; no new dependencies added.
- No behavioral fixes were required; no locked doc changed; no ADR needed.

## Known issues / gotchas
- Workbook ticket statuses are **lowercase** (`open`/`closed`); order statuses
  are uppercase (`BOOKED`, …). Domain logic keys on order status only.
- No workbook order has NULL fault fields (all False except ORD-2002
  carrier_fault=True); the INSUFFICIENT_EVIDENCE path is covered by synthetic
  credit tests instead.
- FTS5 MATCH with unquoted user text raises `OperationalError` on apostrophes
  — always route queries through the token-quoting helper.
- Policy v2 has no numbered sections; its chunker emits Header + one body
  chunk. Any new document without "Status:" in its header fails the seeder's
  sanity check by design.
- The seeded `data/parcel_pilot.db` is gitignored; re-seed deterministically
  any time with `python -m backend.db.seed`.
- `.venv-audit/` (created for the clean-environment check) is gitignored via
  `.venv-*/`; delete it freely — it is reproducible from `requirements.txt`.

## Blockers & open questions
- None for Phase 3. Groq API key remains a Phase 5 item.

## Next steps (in order)
1. Phase 3: `backend/security/authorization.py` (session model + chokepoint
  filtering for EVERY data read — the one known exposure from the audit) and
  `backend/trust/evidence.py` + `conflicts.py` per IMPLEMENTATION_PLAN; the
  data-layer filters `visible_to_account()`/`authoritative_chunks()` are the
  substrate to wrap, not replace.
2. Layer A coverage for the tool layer's authorization rejections (extends
   golden case 12 from data-layer scope filter to tool-level structured
   rejection).
3. Then Phase 4 evidence resolver per IMPLEMENTATION_PLAN.
