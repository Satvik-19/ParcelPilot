# Session 05 — 2026-08-22

## Goal of this session
Phase 7 (application interface) + Phase 8 (Layer C evaluation) in one
controlled pass: the two-context chat application, the non-LLM confirmation
flow at the HTTP boundary, the staff insights view, the application security
gate, and the Layer C suite (deterministic scripted + live) per
04_EVAL_SPEC.md §4.

## Current phase
**Phase 7 — COMPLETE.** **Phase 8 — deterministic part COMPLETE; live
judging partially quota-blocked** (2/12 cases judged live and passed; the
rest skipped under the locked provider-failure policy — see Test status and
Next steps; a watcher process re-runs the suite automatically when quota
returns).

## What was done

### Backend application layer (`backend/api/`, `backend/actions/`)
- `backend/api/app.py` — `ParcelPilotApp`: routes `/api/sessions`,
  `/api/chat`, `/api/actions/confirm`, `/api/insights` + static frontend.
  Identity is resolved ONLY from the session key via the mocked-session
  registry; identity fields in request bodies are never read. Responses
  carry `answer_state`, structured `evidence`, `tools` (name/status/latency,
  no arguments or reasoning), `pending_action`, `provider_failure`.
  Provider failures become a safe, user-visible ESCALATE state (never a
  crash, never hidden). Pending-card description ends with explicit
  "nothing happens until you confirm" wording.
- `backend/api/sessions.py` — mocked session registry: 4 customer sessions
  (one per account) + 1 staff session; role/account scope resolved
  server-side.
- `backend/api/server.py`, `__main__.py` — stdlib `ThreadingHTTPServer`
  wrapper; whole product runs with `python -m backend.api`.
- `backend/actions/confirm.py` — confirmation gate hardened further: the
  Northstar monthly-cap aggregate now excludes the action being confirmed
  (the one-shot claim flips the row to `executed` before the effect runs,
  which previously double-counted it) and the cap is sourced from
  `get_agreement()["service_credit"]["monthly_cap_inr"]` instead of a
  literal. Six-check gate + one-shot atomic claim unchanged (ADR-004).

### Executor / runtime
- `TurnResult` exposes per-turn `tools` + `evidence` for the UI; `<think>`
  reasoning is stripped before any answer leaves the runtime; prompt rule 5
  requires escalation answers to say what happened, why escalation is
  required, and what happens next (presentation only — answer states still
  derive solely from tool envelopes, fixing the LB-10 sparse-prose note
  without touching trusted logic).

### Frontend (`frontend/`)
- Single static page (vanilla JS + CSS, no build step — ADR-007): session
  switcher for the mocked sessions, conversation view, answer-state badge
  (ANSWER / ESCALATE / INSUFFICIENT_EVIDENCE), evidence citations
  (source/section/authority/override status), tool activity list with
  status, provider-failure banner, pending-action card with explicit
  confirm button calling the non-LLM endpoint, staff-only insights panel
  (SLA breached/at-risk, KI-208/KI-211 matches, activity clusters). No
  chain-of-thought surface exists in the DOM.

### Tests (`tests/layer_c_e2e/`, `tests/`)
- `harness.py` — `AppServer` boots the real production composition on an
  ephemeral port; `ScriptedClient` (canned Groq responses) for
  deterministic e2e; `RecordingLiveClient` for live runs; `ManualClock`.
- `cases.py` — LC-01..LC-12 definitions mirroring the 12 required cases,
  reusing Layer B judging contracts.
- `test_layer_c_scripted.py` — 12 deterministic e2e cases through real
  HTTP (including full draft→pending→confirm→execute→replay-refused chain
  in LC-11, and the insights payload in LC-12).
- `test_application_security.py` — the 11-check Phase 7 security gate
  (spoofed identity fields, cross-account via chat/API, staff scope,
  insights staff-only, confirmation invalid/replay/cross-session, confirm
  not reachable as a tool, provider-failure safe state, CoT never exposed).
- `test_layer_c_live.py` — `pytest -m live`; judged with Layer B contracts
  + application checks (visible tool activity, structured evidence, no raw
  reasoning, no hallucinated tools, no identity parameters); recordings
  under `recordings/layer_c/`.

### Docs
- `01_ARCHITECTURE.md` — ADR-007 added (locked-stack deviation) + §1/§2/§3
  aligned with the built system.

## Decisions made
- **ADR-007** (locked §2 rows changed, recorded per the doc's own rule):
  stdlib `http.server` + static vanilla-JS frontend instead of FastAPI +
  Next.js — the API contract is pinned by the deterministic application
  tests instead of a framework's OpenAPI.
- Confirmation wording surfaced in the pending-card description itself so
  the confirmation requirement is unmistakable in both UI and API contract.
- Layer C judging reuses the Layer B semantic contracts (no exact-wording
  asserts anywhere); the scripted suite pins plumbing, the live suite
  measures the model.

## Test status
- Deterministic suite: **330 passed, 0 failed, 25 deselected** (live
  tests) — +34 over the Phase 6 baseline of 296 (12 scripted e2e,
  11 application security, 11 confirmation-gate). Includes the Layer B
  offline replay test, which stays green against the canonical recording.
- Confirmation gate: all six checks fail closed; replay, cross-session,
  forged token, expiry, payload mismatch all refused with the pending row
  untouched; credit-cap fail-closed re-verified after the double-count fix.
- Phase 7 security gate: **11/11 pass** (SEC-01..SEC-11, deterministic).
- Layer C scripted (real HTTP, zero LLM): **12/12 pass**, including the
  full LC-11 confirm chain with double-execute refusal and LC-12 insights.
- Layer C live, model `qwen/qwen3.6-27b`:
  - run `20260822_004300`: **LC-01 passed** (agreement waiver, ANSWER,
    contract + application checks green) and **LC-12 passed** (insights);
    LC-02..LC-11 SKIPPED — Groq free-tier TPD exhausted at 199,958/200,000.
  - runs `20260822_004800` and `20260822_005828`: all conversation cases
    skipped for the same reason (LC-12 insights passed both times — no LLM).
  - Per the locked policy these are provider failures: skipped, recorded,
    never scored as model behaviour. All 12 cases remain deterministically
    covered by the scripted suite.

## Known issues / gotchas
- **Quota timing:** the Aug-21 evening Layer B runs (~150k tokens) roll out
  of the 24h TPD window around 23:06–23:47 local on Aug 22. A watcher
  (`_layer_c_watcher.py`, log `layer_c_watcher.log`) polls the TPD counter
  via the 429 body (zero quota consumed) and auto-runs the live suite once
  ≥130k tokens of headroom exist. Manual alternative:
  `.venv-p5\Scripts\python.exe -m pytest tests/layer_c_e2e/test_layer_c_live.py -m live`.
- The dataset genuinely contains NO cross-account incident pattern; LC-12
  asserts cluster labels are all single-account — do not "fix" this.
- Scratch files `_layer_c_watcher.py`, `layer_c_watcher.log`,
  `layer_c_live_output.txt` are session utilities; delete once the live
  run is recorded.
- FTS porter quirks, read-only shared-fixture rule, and the one-live-suite-
  per-quota-window rule from session-04 all still apply.

## Blockers & open questions
- None blocking. Live judging of LC-02..LC-11 is waiting on provider quota
  only; no code change is expected to be needed (every case's plumbing is
  already pinned deterministically).

## Next steps (in order)
1. Check `layer_c_watcher.log` / newest file in `recordings/layer_c/` for
   the automatic live re-run; if absent, run the manual `-m live` command
   above in a fresh quota window and record results in session-06 (never
   edit this handoff).
2. Judge the live recording against the same contracts; expect the same
   skip-policy for any residual 429s.
3. Deployment remains explicitly NOT started — owner decision required.
