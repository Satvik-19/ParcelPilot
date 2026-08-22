# Session 00 — 2026-08-21

## Goal of this session
Phase 0: validate the supplied assessment data pack (`assessment_docs/`) against the
locked documentation, re-derive all golden cases from the real data, correct docs where
the data required it, and confirm readiness for Phase 1.

## Current phase
IMPLEMENTATION_PLAN Phase 0 — Documentation/context validation. **COMPLETE.**

## What was done
- Extracted and inspected every sheet of `assessment_docs/ParcelPilot_Assessment_Data.xlsx`
  (README, accounts, orders, tickets) — all cells verified.
- Extracted and read all six supplied PDFs; verified status/version, effective dates,
  scope, and every business rule against the document catalog in `02_DOMAIN_SPEC.md` §2.
- Programmatically re-derived all 12 golden cases + GI-1 from the actual workbook cells.
- Corrected the row-count discrepancy (see Decisions): the workbook holds **6 orders and
  7 tickets**, not 7/8 — all IDs were already enumerated correctly in the spec.
- Updated `00_PRD.md` (~19 → ~17 data rows), `01_ARCHITECTURE.md` (tech-stack table and
  ADR-001 rationale 4/7/8 → 4/6/7), `02_DOMAIN_SPEC.md` §1 (counts; removed the
  now-resolved reconcile notes; added a data note on Northstar's ₹5,000 monthly credit
  cap), `IMPLEMENTATION_PLAN.md` (counts, Phase 0 deliverables, and Phase 0 blocker
  cleared — data pack is present).
- Re-grep'd all docs for stale numeric claims — none remain.

## Decisions made
- **Actual data is the source of truth; counts corrected, no IDs added.** DOMAIN_SPEC's
  ID lists were complete; only the row counts were wrong (orders 7→6, tickets 8→7). No
  records were invented. No ADR required — this is a factual correction to the answer
  key's enumeration, exactly what Phase 0 mandates ("update DOMAIN_SPEC first and
  document the correction").
- **Northstar monthly credit cap noted.** Agreement 05 caps *monthly aggregate* service
  credits at ₹5,000 (default per-credit SOP rules otherwise apply). No golden case
  exercises it; recorded as a data note in DOMAIN_SPEC §3 for Phase 2/9 awareness.
- **Data pack kept at `assessment_docs/`** (unmodified); Phase 1 ingestion reads it from
  there (or copies into `data/raw/` per ARCHITECTURE §3 — either is fine, files stay
  unmodified).

## Test status
- No application code exists yet; Layer A/B/C suites not applicable.
- Validation performed instead: workbook cell dump (all 4 sheets), PDF text extraction
  (all 6 docs), and a scripted re-derivation of every golden-case figure. All 12 cases +
  GI-1 re-derived cleanly from raw cells.

## Known issues / gotchas
- **Ticket severity is not a column in the workbook.** Golden cases 6/7 rely on P1
  classification (TKT-501 "all shipment creation failing", TKT-505 "API key exposure").
  v3 policy §2 severity definitions cover both. Phase 2/4 must decide a deterministic
  severity assignment (policy-definition-based classification or explicit seed column) —
  unresolved implementation decision, documented here rather than in the locked docs.
- **PDF extraction tooling:** the dev machine (Windows) has no `pdftotext`; validation
  used pypdf (plain + layout modes — plain mode truncated a line in SOP v4 §2, layout
  mode was complete). Phase 1 should prefer `pdftotext -layout` where available or a
  Python equivalent with layout preservation, and assert full-text extraction in tests.
- Workbook datetime cells are strings (`YYYY-MM-DD HH:MM`); parser must handle that.
- TKT-502 description ("fails at roughly 70%" of a 4,200-row CSV ≈ 2,940 rows) aligns
  with KI-208's ~3,000-row failure threshold — a useful deterministic-match anchor.

## Blockers & open questions
- **None.** Data pack present and fully validated. Groq API key remains a Phase 5 item
  (not a Phase 1 blocker).

## Next steps (in order)
1. Phase 1: `backend/db/schema.sql` + `backend/db/seed.py` (xlsx → SQLite + FTS5),
   reading from `assessment_docs/`; row-count acceptance = 4 accounts / 6 orders /
   7 tickets; chunk metadata (status/scope/authority) per the verified catalog.
2. Resolve ticket-severity assignment (see Known issues) before Phase 2 SLA tests.
