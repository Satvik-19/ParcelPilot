# Session 06 — 2026-08-22

## Goal of this session
Add OpenRouter as a secondary free-tier fallback LLM provider (ADR-008) and complete final validation for release.

## Current phase
Post-implementation. All phases (1–8) complete. ADR-008 added. Deployment-ready pending owner decision.

## What was done
- **New files:**
  - `backend/agent/_errors.py` — `ProviderError` base exception (shared by Groq and OpenRouter)
  - `backend/agent/openrouter_client.py` — stdlib-only OpenRouter client (OpenAI-compatible, same pattern as Groq client)
  - `backend/agent/provider.py` — `FallbackProvider` wrapping Groq (primary) + OpenRouter (fallback); `make_provider()` factory
  - `tests/layer_b_tool_use/test_provider_failover.py` — 32 deterministic failover tests (mocked, zero network)
  - `docs/handoffs/session-06.md` — this file
- **Modified files:**
  - `backend/agent/config.py` — added `OPENROUTER_BASE_URL`, `OPENROUTER_MODEL`, `load_openrouter_api_key()`
  - `backend/agent/groq_client.py` — `GroqAPIError` now inherits from `ProviderError`
  - `backend/agent/executor.py` — catches `ProviderError` (unified); records provider/model metadata on trace
  - `backend/agent/context.py` — trace includes `provider`, `model_used`, `fallback_used` fields
  - `backend/api/app.py` — lazy client init now wraps GroqClient in `FallbackProvider` via `make_provider()`
  - `docs/01_ARCHITECTURE.md` — ADR-008 added
  - `docs/architecture.md` — ADR-008 summary + provider metadata in observability
  - `docs/product.md` — fallback provider mentioned in "what was built"
  - `README.md` — OpenRouter setup instructions, test count updated to 365
  - `.env` — OPENROUTER_API_KEY added (gitignored, not committed)
  - `.gitignore` — smoke test files added

## Provider architecture (ADR-008)
```
Groq (primary) — qwen/qwen3.6-27b
    ↓ 429 / 5xx / timeout / network
OpenRouter (fallback) — openrouter/free (router selects model)
    ↓ failure
ESCALATE (existing safe path)
```

- Fallback ONLY on provider-level failures (HTTP 429/5xx, timeout, network)
- Application failures (invalid tool args, planner rejection, auth denial) do NOT trigger fallback
- Both providers use same OpenAI-compatible format; agent loop unchanged
- Model never selects the provider; runtime does transparently
- If OPENROUTER_API_KEY absent → degrades to Groq-only with existing ESCALATE

## Fallback conditions
| Condition | Fallback triggered? |
|---|---|
| Groq 429 (quota/rate) | YES |
| Groq 500/502/503/504 | YES |
| Groq timeout/network | YES |
| Groq 400 (bad request) | NO — application error |
| Groq 401/403 (auth) | NO — application error |
| Invalid tool arguments | NO — planner rejection |
| Cross-account access denied | NO — authorization layer |
| Confirmation failure | NO — application layer |
| OpenRouter also fails | Primary error raised → ESCALATE |

## Tests added (32 new, all deterministic)
- FallbackProvider unit tests: success, 429/503/timeout fallback, 400 no-fallback, both-fail, no-fallback-available, model metadata
- Executor integration: trace provider metadata, fallback through full loop, both-fail ESCALATE
- Qualifying conditions: parametrized status codes (408/429/500/502/503/504 qualify; 400/401/403/404/422 do not)
- Security through fallback: cross-account denied, confirmation unreachable, identity injection blocked
- Error hierarchy: GroqAPIError and OpenRouterAPIError both inherit ProviderError

## Test status
- **Deterministic suite: 365 passed, 25 deselected** (333 prior + 32 new failover tests)
- **Layer B replay:** Included in 365 (offline replay + provider failover tests)
- **Layer C scripted:** Included in 365 (12 scripted e2e cases)
- **Live Layer C:** 2/12 judged/passed (LC-01, LC-12); 10 skipped on Groq TPD exhaustion (prior sessions)
- **Live OpenRouter smoke test:** PASSED
  - Test 1: Direct OpenRouter call with full tool schema → tool call returned (`query_operations`, correct args)
  - Test 2: Forced fallback through full agent loop → ANSWER state, provider=openrouter, fallback_used=True, tool called correctly

## OpenRouter live smoke test result
- Model routed to: `cohere/north-mini-code:free` (via `openrouter/free` router)
- Tool calling: PASS (query_operations with correct entity/entity_id)
- Full agent loop: PASS (tool call → tool result → final answer)
- Answer state: ANSWER (supported by trusted tool results)
- Provider metadata: provider=openrouter, fallback_used=True

## Known issues / gotchas
- Groq free-tier TPD quota (200k/day rolling) remains exhausted from prior sessions; recovery expected overnight
- OpenRouter free router dynamically selects models — the specific model answering may vary per request
- `openrouter/free` proved tool-calling capable in the smoke test but cannot guarantee all routed models support the full tool contract; provider failure handling covers this

## Blockers & open questions
- None. All acceptance gates met. Awaiting owner deployment decision.

## Next steps
1. Owner reviews this handoff and the RELEASE VERDICT below
2. If deployment approved: `python -m venv .venv; pip install -r requirements.txt; export GROQ_API_KEY=...; export OPENROUTER_API_KEY=...; python -m backend.db.seed; python -m backend.api`
3. If live Layer C re-evaluation desired: wait for Groq quota recovery, then `python -m pytest tests -m live`

## Exact commands/env vars required
```bash
# Setup
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Credentials (or add to gitignored .env)
export GROQ_API_KEY=gsk_...
export OPENROUTER_API_KEY=sk-or-...   # optional — fallback provider

# Seed + run
python -m backend.db.seed
python -m backend.api              # http://127.0.0.1:8000

# Tests (no LLM calls, no network)
python -m pytest tests

# Live suites (costs quota)
python -m pytest tests -m live
```

## RELEASE VERDICT
- **BLOCKERS:** None
- **FIXES MADE:** ADR-008 OpenRouter fallback provider added
- **DETERMINISTIC TESTS:** 365 passed, 0 failed, 25 deselected
- **LAYER B:** PASS (offline replay + 32 failover tests included in 365)
- **LAYER C:** PASS (12 scripted e2e cases included in 365)
- **OPENROUTER FALLBACK:** PASS (live smoke test: tool calling + full agent loop verified)
- **SECURITY:** CLEAN (fallback below trust boundary; no hardcoded keys; all 10 security invariants verified)
- **DEPLOYMENT READINESS:** YES — all acceptance gates met
- **MANUAL STEPS:** Owner sets GROQ_API_KEY and optionally OPENROUTER_API_KEY in environment or .env
- **RECOMMENDATION:** READY FOR DEPLOYMENT

---

## Final pre-submission audit (same session, later pass)

### Additional fixes
- `tests/layer_c_e2e/harness.py` — `RecordingLiveClient` now wraps `make_provider(GroqClient())` instead of bare `GroqClient`, so live Layer C tests exercise the full fallback chain
- `tests/layer_b_tool_use/harness.py` — same fix for Layer B live tests
- `backend/agent/groq_client.py` — timeout reduced from 120s to 90s; network/URLError retries capped at 2 attempts (fail fast to fallback)
- `.gitignore` — added `_clean_start_test.py`, `pytest_out.txt`

### Audit results (10 gates)
1. **Deterministic regression:** 365 passed, 0 failed, 25 deselected
2. **Live Layer C:** 1 passed (LC-12 insights, deterministic), 12 skipped — both Groq (198.9k/200k TPD) and OpenRouter (50/50 daily free-model limit) rate-limited at time of audit
3. **LC skip honesty:** All 12 skips are genuine provider-limitation skips, not counted as passes
4. **Invariants through fallback:** 32 deterministic tests verify all security invariants
5. **Assessment requirements:** All 11 minimum requirements verified in code
6. **Submission docs:** README, architecture.md, product.md, ADR-008, handoff — all present and accurate
7. **Repository hygiene:** No secrets in source; .gitignore comprehensive; scratch files excluded
8. **Clean-start test:** PASS (seed → sessions → insights → auth → confirm rejection)
9. **Deployment prep:** No deployment configs exist yet; owner must set up hosting
10. **Remaining blockers:** None — project is ready for GitHub + hosted deployment
