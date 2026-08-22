# Architecture

**Status: LOCKED.** Changes require a new ADR entry in §4, not a silent edit.

## 1. Four-layer system

```
┌────────────────────────────────────────────────────┐
│  EXPERIENCE                                         │
│  Static vanilla-JS chat UI (customer + staff        │
│  contexts) + Insights view (staff only) — ADR-007   │
└───────────────────────┬──────────────────────────────┘
                        │
┌───────────────────────▼──────────────────────────────┐
│  AGENT RUNTIME                                       │
│  Planning / tool selection / response composition    │
│  Owns: which tool to call next, how to phrase the    │
│  final answer. Owns NOTHING about truth or access.   │
└───────────────────────┬──────────────────────────────┘
                        │
┌───────────────────────▼──────────────────────────────┐
│  TRUSTED APPLICATION LAYER                           │
│  Authorization · PolicyResolver · Evidence Resolver  │
│  · Confirmation Gate                                 │
│  This layer is where every fact and every permission │
│  decision actually gets made. The LLM never bypasses │
│  it — it can only call into it and report the result.│
└───────────────────────┬──────────────────────────────┘
                        │
┌───────────────────────▼──────────────────────────────┐
│  DATA LAYER                                          │
│  SQLite: accounts / orders / tickets / actions        │
│  + FTS5 virtual table over document chunks            │
└────────────────────────────────────────────────────┘
```

**Core thesis (use this exact framing in the demo and the architecture note):**
> The model may request evidence, but it does not determine evidence authority or
> applicability. The model may propose an action, but cannot execute it without
> application-level authorization and explicit user confirmation.

## 2. Tech stack

| Layer | Choice | Rationale |
|---|---|---|
| Backend | Python stdlib `http.server` (hand-rolled thin routes, ADR-007) | Four endpoints + static files; the locked zero-dependency convention (ADR-002) applies to the app surface too — FastAPI would be the first non-test dependency for ~150 lines of routing |
| Data + doc index | Single SQLite file (tables + FTS5) | Dataset is ~17 rows + 6 one-page docs — a second DB engine or a vector store is unjustified complexity (see ADR-001) |
| Doc parsing | `pdftotext -layout` (already verified clean — real text layer, no OCR needed) | — |
| Agent runtime | Hand-rolled loop, no framework | 4 tools doesn't justify LangChain-scale abstraction (see ADR-002) |
| Frontend | Single static page, vanilla JS + CSS (ADR-007) | One chat view, one insights panel, one action card — no build step, nothing to compile for a reviewer to run |
| LLM | Groq free tier, `qwen/qwen3.6-27b` (chosen by benchmark) | See ADR-006 |
| Hosting | Vercel (frontend) + Render/Railway free tier (backend) | $0 |

## 3. Repository structure

```
parcelpilot-ai/
├── frontend/                     static index.html + app.js + styles.css —
│                                 chat UI, evidence/citations, tool activity,
│                                 pending-action card, staff insights panel
│                                 (vanilla JS, no build step — ADR-007)
├── backend/
│   ├── api/                      stdlib HTTP app (app.py routes, server.py,
│   │                             sessions.py mocked-session registry) — thin,
│   │                             delegates to agent/, tools/, actions/
│   ├── actions/                  confirm.py — the non-LLM confirmation gate
│   │                             (ADR-004; six checks, one-shot claim)
│   ├── agent/                    planner.py (validates model tool selections),
│   │                             executor.py (the capped loop + answer-state derivation),
│   │                             context.py (per-turn state + §7 trace), prompts.py,
│   │                             tools_schema.py (model-visible surface, no identity
│   │                             params, no confirm/execute), groq_client.py,
│   │                             config.py, benchmark.py (ADR-006)
│   ├── tools/                    search_knowledge.py, query_operations.py,
│   │                             analyze_support_activity.py, prepare_support_action.py
│   │                             (each tool is a thin interface onto domain/ — see ADR-003)
│   ├── domain/                   cancellation.py, credits.py, sla.py, known_issues.py
│   │                             — pure, testable business-rule functions, no LLM,
│   │                             no framework dependency. This is PolicyResolver.
│   ├── trust/                    evidence.py (force-inclusion + authority ranking),
│   │                             conflicts.py (conflict detection)
│   ├── security/                 authorization.py (session-scoped filtering — the
│   │                             actual access-control chokepoint)
│   └── db/                       schema.sql, seed.py (xlsx → SQLite + FTS5 build)
├── data/raw/                     the 6 supplied PDFs + xlsx, unmodified
├── tests/
│   ├── layer_a_domain/           no LLM
│   ├── layer_b_tool_use/         recorded LLM evaluation of the chosen model:
│   │                             case contracts (cases.py), harness, deterministic
│   │                             boundary tests, live runs (`pytest -m live`),
│   │                             offline replay of recordings
│   └── layer_c_e2e/              application boundary: deterministic scripted
│                                 e2e through real HTTP, 11-check application
│                                 security gate, live runs (`pytest -m live`)
├── recordings/layer_b/           recorded Layer B runs (exchanges, contracts,
│                                 verdicts) + per-run trace JSONL — replayable
│                                 offline (04_EVAL_SPEC.md §4)
├── recordings/layer_c/           recorded live Layer C runs (HTTP payloads,
│                                 exchanges, contract judgements) + trace JSONL
├── docs/
│   ├── handoffs/                 session-NN.md, one per coding session — see
│   │                             06_SESSION_HANDOFF_TEMPLATE.md
│   ├── architecture.md           trimmed version of this file for submission
│   └── product.md                product note for submission
└── README.md
```

## 4. Architecture Decision Records

### ADR-001: SQLite + FTS5, not DuckDB, not a vector DB
**Context:** Two independent planning passes initially reached for heavier retrieval
(vector embeddings, or DuckDB for analytics) before the actual data pack was inspected.
**Decision:** Single SQLite file for both relational tables and an FTS5 virtual table over
document chunks. No embeddings, no vector store, no second DB engine.
**Rationale:** The real pack is six one-page PDFs and 4/6/7 rows across
accounts/orders/tickets. DuckDB's analytical advantage and a vector store's semantic-recall
advantage are both irrelevant at this scale — introducing them reads as not right-sizing
the solution, which is explicitly something the assessment penalizes ("sensible solutions,"
"product judgment").
**Consequences:** Retrieval quality depends more on deterministic metadata rules (force-
include the account's agreement, exclude deprecated docs by default) than on search
ranking. This is handled explicitly in the Evidence Resolver (`trust/evidence.py`), not
left to FTS5 relevance scoring alone.
**Alternatives considered:** ChromaDB + sentence-transformer embeddings (rejected — no
benefit at 6 documents, adds a dependency); DuckDB (rejected — analytical query performance
irrelevant at ~20 rows, and running two DB engines for a demo-scale app adds needless
complexity).

### ADR-002: Hand-rolled agent loop, no agent framework
**Decision:** Custom `agent/planner.py` + `agent/executor.py`, no LangChain/CrewAI/etc.
**Rationale:** With exactly 4 tools, a framework's abstraction overhead exceeds its value.
A reviewer should be able to open `agent/executor.py` and understand the entire tool-calling
loop in minutes, not trace through framework internals.
**Consequences:** More code to write by hand for tool-schema definitions and the
call/execute/feed-back loop, but far more legible and demoable.

### ADR-003: PolicyResolver as a separate deterministic layer (`domain/`)
**Context:** Business rules (does an agreement override the SOP? what's the cancellation
fee? is a ticket SLA-breached?) are exactly the facts that must never be left to LLM
judgment or LLM arithmetic, even though the LLM is the one deciding *when* to ask for them.
**Decision:** Every business rule lives as a pure Python function in `domain/`, independent
of both the tool layer and the LLM. Tools are thin wrappers that call into `domain/` and
return structured results with the evidence used.
**Rationale:** This is the single most important reliability decision in the system — it
converts "the LLM reasoned that Northstar's agreement overrides the SOP" (a claim you have
to trust) into "the LLM called `resolve_cancellation_fee()`, which returned
`{fee: 0, rule: "NORTHSTAR_AGREEMENT_WAIVER", overrides: "SOP_v4_S1"}`, and reported that
result" (a claim you can unit-test independent of the model).
**Consequences:** Every domain function needs a Layer A unit test before any agent work
begins (see `04_EVAL_SPEC.md`). This is enforced by the build order in the PRD §7.

### ADR-004: `confirm_support_action` is not an LLM tool
**Context:** If the confirmation step is just another tool the LLM can call, then a
sufficiently confused or adversarially-prompted model could call it back-to-back with
`prepare_support_action`, defeating the entire point of requiring human confirmation.
**Decision:** `prepare_support_action` is LLM-selectable and returns a token bound to the
exact drafted payload, rendered to the user as a confirm card. `confirm_support_action` is
a plain backend endpoint the **frontend** calls when the user clicks confirm — it is never
in the LLM's tool schema and the model has no path to invoke it.
**Consequences:** Slightly more frontend wiring (the confirm button hits a different
endpoint than the chat message stream), but a structurally stronger guarantee than "the
system prompt tells the model to wait for confirmation."

### ADR-005: One platform, two contexts — not two separate apps
**Decision:** Single backend and mostly-shared frontend, with session role/account_id
determining scope, rather than a fully separate internal application.
**Rationale:** Problem 1 (proactive detection) needs broader data access and one extra
tool, not a parallel product. Building two apps would roughly double surface area for
modest incremental signal.

### ADR-006: LLM provider — Groq free tier, exact model TBD by benchmark
**Decision:** Use Groq's free tier for the hosted demo. Do not lock the exact model before
running a small tool-calling benchmark.
**Rationale:** This system lives or dies on structured tool-call accuracy (argument
correctness, not calling `confirm_support_action`-equivalents unprompted, multi-step
chaining). A model that writes fluent prose but occasionally passes malformed arguments is
worse than a less articulate one that calls tools reliably.
**Action required before backend work depends on this:** benchmark 2–3 Groq-hosted models
(Llama-3.3-70B as the safe default; check whichever Kimi/DeepSeek-family model Groq exposes)
against the Layer B test prompts in `04_EVAL_SPEC.md` before finalizing. Log the result as
an update to this ADR, not a silent choice.

**Update 2026-08-21 — benchmark run, model chosen: `qwen/qwen3.6-27b`.**
Candidate-set deviation (recorded, not silent): the account's Groq catalog exposes neither
Llama-3.3-70B nor any Kimi/DeepSeek-family model. The benchmark therefore ran against all
three tool-capable models available: `openai/gpt-oss-120b`, `openai/gpt-oss-20b`,
`qwen/qwen3.6-27b`.

Method: `python -m backend.agent.benchmark` — eight Layer B prompts (golden cases 1, 3, 6,
8, 12, a policy-retrieval question, the staff analytics tool, and an action draft) run
through the real agent runtime per model, scored on expected-tool firing, argument
correctness, no hallucinated tool names, no identity parameters, §2 answer-state
correctness and case-specific outcomes. Raw per-check detail: `data/benchmark_results.json`.

| Model | Score | Notes |
|---|---|---|
| `qwen/qwen3.6-27b` | **47/52 (90.4%)** | Only model to fire the staff analytics tool cleanly; one transient provider error |
| `openai/gpt-oss-120b` | 46/52 (88.5%) | One provider-side "tool call validation" 400 on the staff case |
| `openai/gpt-oss-20b` | 46/52 (88.5%) | One malformed staff-tool call rejected by the planner |

Shared gap on every model at benchmark time: the action-draft case — models reported credit
eligibility from `query_operations` without chaining into `prepare_support_action`.
**Resolved in Phase 6 (2026-08-21):** root cause was a missing structured signal, not model
weakness — (a) the system prompt now states when a draft must follow a trusted eligibility
result, (b) order results carry `supported_actions` (the draftable next steps implied by the
domain decisions), and (c) order/ticket results carry the caller's own `account_id`, without
which no valid `prepare_support_action` payload could be formed. Measured on the chosen model
by the recorded Layer B suite: draft-chaining success went from 0/1 (benchmark) to **3/3
(100%)** over three independent samples. Zero hallucinated tool names and zero identity
parameters across all 24 benchmark runs and every recorded Layer B run; the model never
reached a confirmation surface. Transient free-tier rate limits were observed; the client
retries 408/429/5xx with backoff and the runtime converts unrecoverable provider failures to
ESCALATE (recorded runs skip such cases rather than scoring them).
`backend/agent/config.CHOSEN_MODEL` is set to the winner.

### ADR-007: Framework-free application surface — stdlib HTTP + static vanilla-JS frontend
**Context:** §2 originally locked FastAPI (backend) and Next.js + TypeScript + Tailwind
(frontend). By Phase 7 the whole runtime was already framework-free by locked decision
(ADR-001/002: no vector store, no agent framework; the only non-test dependency anywhere
is nothing — the Groq client itself is hand-rolled on `urllib`). The actual application
surface is four JSON endpoints (`sessions`, `chat`, `confirm`, `insights`) plus static
files, and the UI is one chat view, one evidence list, one action card, one insights panel.
**Decision (deviation from the locked §2 rows, recorded per the doc's own change rule):**
- Backend: `backend/api/` on Python stdlib `http.server.ThreadingHTTPServer` — `app.py`
  (routing + authorization + response shaping), `server.py` (serve + static files),
  `sessions.py` (mocked-session registry; identity resolved server-side by session KEY
  only — identity fields in request bodies are never read).
- Frontend: single static `frontend/index.html` + `app.js` + `styles.css`. No build step,
  no framework, no npm dependency tree.
**Rationale:** Adding FastAPI + Next.js would have introduced the project's first real
dependency stack for ~150 lines of routing and one page of UI — exactly the not-right-
sized complexity the assessment penalizes, and inconsistent with the locked zero-
dependency convention the rest of the system was built under. A reviewer can run the
whole product with one stdlib command (`python -m backend.api`).
**Consequences:** No auto-generated OpenAPI, no component library — the API contract is
documented by its tests instead: `tests/layer_c_e2e/test_application_security.py` (11
application-level security checks) and the scripted e2e suite pin every endpoint's shape
and failure modes deterministically. Hosting rows in §2 remain viable unchanged (static
frontend anywhere; the stdlib server runs on any Python host).
**Alternatives considered:** FastAPI + Next.js as originally locked (rejected —
dependency weight unjustified at this scale, adds a build toolchain the demo doesn't
need); Flask (rejected — still a dependency, buys nothing over `http.server` here).

### ADR-008: OpenRouter as optional free-tier fallback provider
**Context:** The Groq free tier provides 200,000 tokens/day on a rolling 24h window.
Two live evaluation suites in one day exhausts this quota, leaving the system unable
to serve real agent turns until the window rolls over. The system already handles
provider failures safely (ESCALATE), but availability during demos or reviews is
improved by a second provider.
**Decision:** Add OpenRouter (`openrouter/free` default) as a secondary LLM provider.
The FallbackProvider in `backend/agent/provider.py` wraps the existing GroqClient and
tries Groq first; on qualifying provider-level failures (429/5xx/timeout/network),
it falls back to OpenRouter once. Application-level failures (invalid tool args,
planner rejection, authorization denial) do NOT trigger fallback. Both providers
use the same OpenAI-compatible chat completions format, so the agent loop in
`executor.py` works unchanged regardless of which provider answers. The provider
selection is transparent to the model — it never chooses the provider.
**Rationale:** Provider failover is infrastructure-level resilience, not a product
feature. OpenRouter's free router dynamically selects available free models with
tool-calling support, requiring no new dependencies (stdlib `urllib`, same as Groq).
If OPENROUTER_API_KEY is absent, the system degrades gracefully to Groq-only with
the existing safe ESCALATE behaviour.
**Consequences:** Trace metadata now records which provider/model actually answered
(`provider`, `model_used`, `fallback_used` fields). Both providers unavailable
yields ESCALATE (same as before). Security invariants are unchanged — the fallback
is completely below the trust boundary.
**Alternatives considered:** Hard-coding a specific free model (rejected — the
router is more resilient to individual model outages); adding a paid provider
(rejected — unnecessary cost for a demo/assessment); retrying Groq more aggressively
(rejected — wastes quota, doesn't solve the underlying exhaustion).

## 5. Demo video script (~5 min)

- **0:00–0:35** — Problem framing: the hard part isn't finding information, it's knowing
  which source wins when a customer's contract, the current SOP, and a wrong historical
  ticket all disagree.
- **0:35–1:10** — Architecture: show the four-layer diagram, state the core thesis from §1.
- **1:10–2:15** — Centerpiece: ask about ORD-1001 (Northstar → no fee) immediately followed
  by ORD-2001 (LumenWorks → ₹250 fee). Same question shape, opposite correct answer, both
  correctly sourced — proves it's not pattern-matching.
- **2:15–2:50** — Ask what a past ticket (TKT-450) said, then ask the same question fresh —
  show the system explicitly declining to repeat the wrong historical guidance.
- **2:50–3:20** — Switch to a LumenWorks session, attempt to ask about a Northstar order,
  show the rejection happening at the tool layer.
- **3:20–4:00** — Action flow: ask about TKT-501 (Northstar, breached P1) → watch it prepare
  an escalation → explicit confirm click → executed.
- **4:00–4:40** — Insights view: SLA risk list, KI-208/KI-211 known-issue matches.
- **4:40–5:00** — Close on the core thesis restated in one sentence.

## 6. Observability

Lightweight and self-contained (PRD §4 zero-infra constraint): every agent turn produces a
trace record — request/session ID, tools fired with per-tool latency, answer state,
evidence IDs, action ID when applicable (full schema in `03_AGENT_SPEC.md` §7). Traces are
returned to the UI (powers the FR-6 tool/evidence display) and appended to a local JSONL
file. No external observability service.
