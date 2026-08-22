# Session 04 — 2026-08-21

## Goal of this session
Close Phase 6 (formal Layer B evaluation of the chosen model) per
IMPLEMENTATION_PLAN: a targeted pre-Phase-6 hardening pass, the recorded
Layer B suite per 04_EVAL_SPEC.md, and a root-cause investigation of the
draft-chaining gap found in the Phase 5 benchmark.

## Current phase
**Phase 6 (Layer B agent evaluation) — COMPLETE.** Every completion gate
met: Layer A green; Layer B suite implemented and recorded; all
security/tool-boundary assertions pass; deprecated/historical traps pass;
cross-account rejected; no confirmation surface reachable by the model;
action-preparation demonstrated live; draft-chaining measured at 3/3
(100%) after a root-cause fix, up from 0/1 at benchmark. No Phase 7 work
started.

## What was done

### Targeted hardening (only genuine gaps)
- `backend/tools/query_operations.py` — order and ticket results now carry
  the caller's own `account_id` (access already session-authorized). This
  was the load-bearing gap: §8 forbids account data in the system prompt,
  so without this field the model could NEVER form a valid
  `prepare_support_action` payload, and every draft attempt failed with
  INVALID_INPUT before chaining could even be evaluated.
- `backend/tools/prepare_support_action.py` — INVALID_INPUT error text now
  tells the model where to obtain `account_id` (query_operations result).
- `backend/agent/prompts.py` — rule 8 (draft-chaining rule): when a
  trusted result establishes that an action is supported/required, the
  model must call `prepare_support_action`; it may never claim an action
  was executed. Generic rule, no case IDs.
- `backend/agent/tools_schema.py` — `query_operations` description notes
  results include `supported_actions` / `account_id` so the model knows
  the chaining signal exists.

### Layer B suite (`tests/layer_b_tool_use/`)
- `cases.py` — 11 semantic case contracts (LB-01..LB-11) covering:
  cancellation via agreement waiver, cancellation via default SOP,
  service-credit eligibility, SLA lookup + breach, known-issue
  investigation, historical-ticket-then-"what is correct today?" (two
  turns), deprecated-policy trap, cross-account access attempt,
  insufficient evidence (seeded ORD-9901 via `db_setup`), escalation
  (P1 security incident), and the draft-chaining case (3 live samples).
  Contracts assert acceptable tool sets / required tools, legal answer
  states, required trusted facts in feedback, leak guards, action
  preparation — deliberately NOT exact tool sequences.
- `harness.py` — runs cases through the REAL `run_turn` runtime on a
  private DB copy; `RecordingLiveClient` captures every Groq
  request/response; each run saved under `recordings/layer_b/` WITH a
  snapshot of the contracts used (replay judgments never drift as
  contracts evolve); pure `evaluate_turn`/`evaluate_case` functions judge
  recordings offline, plus global checks: no hallucinated tool names, no
  identity-parameter attempts.
- `test_boundaries.py` — 7 deterministic zero-network tests through the
  full loop: `confirm_support_action` unreachable end-to-end; identity
  params cannot override session; unknown params rejected; cross-account
  denied even when well-formed; 8-iteration cap terminates a tool-only
  loop with ESCALATE; provider failure → ESCALATE.
- `test_layer_b_live.py` — `pytest -m live` suite; provider failures are
  SKIPPED, never scored as model behaviour; LB-11 repeated 3× for the
  draft-chaining measurement.
- `test_recording_replay.py` — replays the latest recording offline and
  asserts identical verdicts + security invariants.
- `pytest.ini` — `addopts = -q -m "not live"` + `live` marker: default
  runs are fully deterministic; live Groq runs are explicit.

### Docs
- `01_ARCHITECTURE.md` — repo structure updated for layer_b +
  recordings; ADR-006 gap paragraph rewritten to "Resolved in Phase 6"
  with root cause and measurement.
- This handoff.

## Decisions made
- Draft-chaining gap was a MISSING STRUCTURED SIGNAL, not model weakness.
  Fixed in the smallest-change order the owner specified: (1) prompt rule,
  (2) tool-result signals (`supported_actions`, `account_id`), (3) planner
  rules untouched. No case-ID hardcoding, no vendor special-casing, no
  forced chaining, confirmation surface never exposed.
- Evaluation brittleness: contracts judge semantics (required tools fired,
  trusted facts present, states legal, no forbidden params) so multiple
  valid tool orderings pass; exact-sequence matching rejected.
- Infrastructure vs model behaviour: any turn with a provider failure
  skips the whole case in live scoring; recordings keep the failure for
  the audit trail. Three subsequent full-suite runs (runs 3–5) hit Groq's
  free-tier 200k tokens/day ceiling; every affected case was skipped,
  never counted. Offline replay picks the newest JUDGABLE recording
  (currently run 2) so it always re-exercises real judgement logic.
- Replay judgments use the contract snapshot stored inside each recording
  (pre-snapshot recordings skip rather than re-judge under new contracts).

## Test status
- Layer A + runtime + Layer B deterministic: **296 passed, 0 failed,
  12 deselected** (live tests) — baseline 288 preserved; +7 boundary
  tests, +1 replay test.
- Layer B live (canonical recording `run_20260821_232302`, model
  `qwen/qwen3.6-27b`): **9 passed, 3 skipped** (LB-02/LB-06/LB-08 hit
  transient free-tier 429s — recorded, not scored). Draft-chaining
  **3/3 (100%)**: all three LB-11 samples called
  `prepare_support_action` with a valid payload and produced a pending
  draft card; zero hallucinated tools; zero identity parameters; no
  confirmation surface attempted.
- Draft-chaining before/after: 0/1 (Phase 5 benchmark) and 0/3 (live run
  1, pre-fix payloads) → 3/3 (run 2, post-fix).
- Layer C: not run (Phase 10).

## Known issues / gotchas
- **Groq free-tier TPD ceiling:** 200,000 tokens/day. Two full live
  suites in one day exhausts it (each suite ≈ 100k tokens including
  retries). The 429 body names the reset countdown (~15–20 min); a plain
  no-tools probe can succeed while tool requests still trip TPD because
  they request far more tokens. Run at most ONE `-m live` suite per quota
  window.
- Replay tests require the recording to contain a contract snapshot;
  recordings predating the snapshot feature are skipped, not failed.
- LB-10 (escalation) asserts the derived ESCALATE state + trusted flag;
  the model's visible prose for that case was sparse — acceptable per
  spec (state derives from envelopes, not prose) but worth a Layer C
  presentation pass.
- FTS porter quirks and the read-only shared-fixture rule from earlier
  sessions still apply.
- `.venv-audit/`, `.venv-p34/`, `.venv-p5/` remain on disk; gitignored,
  safe to delete.

## Blockers & open questions
- None — Phase 6 CLOSED.

## Next steps (in order)
1. Phase 7: FastAPI routes + Next.js chat UI surfacing the §7 trace.
   Confirmation flow lives ONLY in the backend endpoint consumed by the
   UI (ADR-004) — keep `confirm_support_action` out of any LLM-visible
   surface.
2. Keep the runtime contract exactly as built: planner validates before
   dispatch, executor injects the session, answer states derive from
   envelopes only, one §7 trace per turn, `account_id`/`supported_actions`
   stay in query_operations results (draft-chaining depends on them).
3. Schedule live Layer B re-runs with the TPD quota in mind (one suite
   per window) if Phase 7 changes touch prompts/tools.
