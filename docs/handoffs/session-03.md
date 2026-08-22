# Session 03 — 2026-08-21

## Goal of this session
Close Phase 5 (agent runtime + Groq model/tool-calling benchmark) per
IMPLEMENTATION_PLAN, with deterministic runtime tests plus live end-to-end
verification, in the same build→test→handoff flow as Phases 1–4.

## Current phase
**Phase 5 (agent runtime + benchmark) — COMPLETE.** Every acceptance
criterion met: the loop terminates at the 8-iteration cap with ESCALATE;
ANSWER is never derived without a supporting ok tool result; the benchmark
table is recorded and the winner logged as an ADR-006 update. No blockers.
Next locked phase is Phase 6 (Layer B agent evaluation) — NOT started.

## What was done
- `backend/agent/config.py` — PROJECT_ROOT, `GROQ_BASE_URL`,
  `MAX_TOOL_ITERATIONS=8` (runtime-enforced, not prompt-enforced),
  `CHOSEN_MODEL` (set to the benchmark winner), `load_groq_api_key()`
  reading the process env or the gitignored project-root `.env`. The
  credential lives only in `.env` (gitignored via `.env` / `.env.*`) and
  never appears in code, prompts, traces, or docs.
- `backend/agent/groq_client.py` — stdlib-only OpenAI-compatible client
  (zero new dependencies, ADR-002). Custom User-Agent is MANDATORY (Groq's
  edge rejects the default urllib UA with Cloudflare error 1010). Retries
  408/429/5xx up to 4 attempts with linear backoff; raises `GroqAPIError`.
- `backend/agent/tools_schema.py` — the model-visible surface: exactly the 4
  tools; NO session/identity parameters anywhere; `confirm_support_action`
  and any execution surface absent by construction (ADR-004).
- `backend/agent/prompts.py` — §8-compliant system prompt: role, tone, tool
  guidance, confirmation rule only; no policy numbers, account data or
  access-control instructions.
- `backend/agent/planner.py` — validates every model-proposed call BEFORE
  dispatch: known name, JSON-parseable args, required params, types, enums,
  unknown params, and hard rejection of identity parameters. Invalid calls
  become structured feedback, never crashes.
- `backend/agent/executor.py` — the capped loop: validate → dispatch with
  server-side session injection → envelope feedback → repeat ≤ 8 times.
  `derive_answer_state` is pure code over envelope results with priority
  ESCALATE (cap hit / ≥2 consecutive non-ok / escalation, security or
  manager-approval flags anywhere in results) > INSUFFICIENT_EVIDENCE (no ok
  result, or an INSUFFICIENT_EVIDENCE marker) > ANSWER. Provider failures
  and cap exhaustion return deterministic ESCALATE text; every turn appends
  exactly one §7 trace.
- `backend/agent/context.py` — TurnContext + §7 trace
  (`request_id, session_id, turn_id, tools[{name, args_digest, latency_ms,
  status}], answer_state, evidence_ids[], action_id?, total_latency_ms`)
  written as JSONL (default `data/traces.jsonl`).
- `backend/agent/benchmark.py` — `python -m backend.agent.benchmark`: 8
  Layer B prompts (golden cases 1, 3, 6, 8, 12, policy retrieval, staff
  analytics, action draft) × candidate models through the REAL runtime on
  private DB copies; scores expected-tool firing, argument correctness, no
  hallucinated tools, no identity params, §2 state correctness and
  case-specific outcomes. Raw detail in `data/benchmark_results.json`.
- Spec-alignment fixes (locked spec wins): `prepare_support_action`
  ACTION_TYPES are exactly the 5 AGENT_SPEC §3 values; tests updated;
  `analyze_support_activity` gained an `account_scope` param that can only
  NARROW the staff view.
- Tests: `tests/layer_a_domain/test_agent_runtime.py` — 26 deterministic
  scripted-model tests (FakeClient, zero network): cap→ESCALATE,
  no-tools→INSUFFICIENT_EVIDENCE, ORD-1001 happy path with evidence,
  TKT-501 flag→ESCALATE, forged confirm call→structured error feedback,
  identity injection rejected before dispatch, repeated rejections→ESCALATE,
  draft records `action_id`, provider failure→ESCALATE, `derive_answer_state`
  unit matrix, §7 trace contract (one line per turn, exact key sets, append).
- Live verification (real API): end-to-end turn on the ORD-1001 golden case
  returns ANSWER with the correct waiver outcome and cites the agreement;
  repeated after switching `CHOSEN_MODEL` to the benchmark winner — same
  correct behavior.
- Docs: ADR-006 updated with method, results table and the recorded
  candidate-set deviation; `01_ARCHITECTURE.md` §2 LLM row and §3 repo
  structure refreshed; this handoff.

## Decisions made
- **ADR-006 benchmark result (logged in the ADR, not silent):** winner
  `qwen/qwen3.6-27b` — 47/52 (90.4%) vs `openai/gpt-oss-120b` 46/52 and
  `openai/gpt-oss-20b` 46/52. Recorded deviation: the account's Groq
  catalog exposes neither Llama-3.3-70B nor Kimi/DeepSeek-family models, so
  all three available tool-capable models were benchmarked instead.
- Answer-state derivation rules fixed in code (see executor.py docstring):
  escalation/security/manager-approval flags force ESCALATE even when the
  model's prose looks fine — e.g. the staff analytics view legitimately
  escalates because it contains TKT-501's P1 breach flag.
- Benchmark scoring inspects the tool FEEDBACK the model saw (rule strings
  live there), not the §7 trace (digests only).
- Client retry policy raised to 4 attempts / 3 s linear backoff: free-tier
  rate limits are frequent and transient.

## Test status
- Layer A (+ runtime): **288 passed / 0 failed** (session-02 baseline 250;
  +26 agent-runtime tests, +2 analyze-scope tests, spec-alignment rewrites).
  Re-run green in a fresh venv (`.venv-p5`, gitignored, deletable).
- Layer B: benchmark executed live — 3 models × 8 cases, zero hallucinated
  tool names and zero identity parameters across all 24 runs; winner logged.
  The full Phase 6 recorded-run suite is still ahead.
- Layer C: not run (Phase 10).

## Known issues / gotchas
- Groq edge blocks the default urllib User-Agent (Cloudflare 1010) —
  GroqClient must always send its custom UA.
- Free-tier instability observed during benchmarks: occasional 429s and a
  provider-side "tool call validation" HTTP 400 from the gpt-oss models;
  the runtime converts unrecoverable provider failures to ESCALATE and the
  benchmark records `provider_failure` per row.
- Shared benchmark gap: after `query_operations` reports credit eligibility,
  all three models answer without chaining into `prepare_support_action`.
  Runtime mechanics are proven by Layer A (FakeClient draft test); the
  chaining behavior is Phase 6 prompt/model work.
- FTS porter quirks from session 02 still apply ("waiver" ≠ "waived";
  v2 chunk has "response" but not "time").
- The shared session-fixture DB must stay read-only: the ONE runtime test
  that writes (`..._records_action_id`) uses its own `fresh_db` fixture —
  `test_database.py::test_actions_table_exists_and_is_empty` depends on it.
- `.venv-audit/`, `.venv-p34/`, `.venv-p5/` remain on disk; all gitignored
  and safe to delete. `data/audit_check.db` (session 01) also remains.

## Blockers & open questions
- None — Phase 5 CLOSED.

## Next steps (in order)
1. Phase 6: `tests/layer_b_tool_use/` — recorded-run suite for the chosen
   model (`qwen/qwen3.6-27b`): expected tool sequences per golden case,
   schema-boundary and session-injection assertions; triage the draft-
   chaining gap (prompt tweak vs model limitation) before proceeding.
2. Phase 7: FastAPI routes + Next.js chat UI surfacing the §7 trace.
3. Keep the runtime contract exactly as built: planner validates before
   dispatch, executor injects the session, answer states derive from
   envelopes only, one §7 trace per turn.
