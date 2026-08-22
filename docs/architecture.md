# ParcelPilot — architecture note (submission version)

Trimmed from the full locked document
[`01_ARCHITECTURE.md`](01_ARCHITECTURE.md). ADR log and build-order
details live there; this file covers the reviewer-facing summary.

## Four-layer system

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

> The model may request evidence, but it does not determine evidence
> authority or applicability. The model may propose an action, but cannot
> execute it without application-level authorization and explicit user
> confirmation.

## Tech stack (framework-free, zero non-test dependencies)

| Layer | Choice |
|---|---|
| Backend | Python stdlib `http.server` (ADR-007) |
| Data + doc index | Single SQLite file with FTS5 (ADR-001) |
| Agent runtime | Hand-rolled loop, no framework (ADR-002) |
| Frontend | Single static page, vanilla JS + CSS (ADR-007) |
| LLM | Groq free tier (`qwen/qwen3.6-27b`) + OpenRouter fallback (`openrouter/free`) — ADR-006/008 |
| Hosting | Vercel (frontend) + Render/Railway free tier (backend) — $0 |

## Key decisions

**ADR-001 — SQLite + FTS5, not DuckDB or a vector DB.** The data pack is
six one-page PDFs and ~17 data rows. A vector store's semantic-recall
advantage is irrelevant at this scale — introducing it reads as not
right-sizing the solution.

**ADR-002 — Hand-rolled agent loop, no agent framework.** Four tools
doesn't justify a framework's abstraction overhead. A reviewer opens
`agent/executor.py` and reads the entire tool-calling loop.

**ADR-003 — Pure `domain/` layer for business rules.** Every business
rule is a testable Python function, independent of the tool layer and the
LLM. Tools are thin wrappers that call `domain/` and return structured
results with the evidence used.

**ADR-004 — `confirm_support_action` is not an LLM tool.** It's a plain
backend endpoint the frontend calls on confirm-click. The model has no
path to invoke it — ever. Confirmation is one-shot at the SQL level
(guarded `WHERE status = 'pending'` claim + effect in one transaction).

**ADR-006 — LLM chosen by benchmark, not assumed.** Three tool-capable
Groq models scored against Layer B prompts; `qwen/qwen3.6-27b` won at
47/52 (90.4%). Draft-chaining gap fixed in Phase 6 with a structured-
signal approach (not case-specific prompting) — 3/3 success post-fix.

**ADR-007 — Framework-free application surface.** stdlib HTTP + static
vanilla JS instead of the originally-locked FastAPI + Next.js. The API
contract is pinned by deterministic application tests instead of a
framework's OpenAPI. A reviewer runs the whole product with one stdlib
command: `python -m backend.api`.

**ADR-008 — OpenRouter as optional free-tier fallback.** Groq (primary)
+ OpenRouter (fallback) failover on provider-level failures only
(429/5xx/timeout/network). Application failures do not trigger fallback.
Both use the same OpenAI-compatible format; the agent loop is unchanged.
If `OPENROUTER_API_KEY` is absent, degrades to Groq-only with safe ESCALATE.
Trace records which provider/model actually answered.

## Access-control boundary

Session-scoped filtering in `security/authorization.py` — the single
chokepoint for every account-scoped read. Pure equality: staff may
access any account; a customer only their own. No phrasing, persistence
or prompt instruction can change the outcome. A customer session is
**structurally incapable** of retrieving another account's data.

## Evidence model

The evidence resolver (`trust/evidence.py`) returns structured records
with source document, section, status, authority rank and — where
applicable — override / exclusion metadata. Deprecated documents are
excluded by default; the caller's agreement is force-included for any
account-scoped query. Authority ranking: Agreement (1) > Policy/SOP (2)
> Product docs (3).

## Confirmation boundary

Every state-changing action goes through `prepare_support_action` (LLM-
selectable) → pending draft with token + session binding + 5-minute
expiry → `confirm_support_action` (plain backend function, never LLM-
selectable) → six-check validation inside one transaction → mocked
effect. Refresh, replay and double-click are structurally impossible —
the claim is `WHERE status = 'pending'` and the flip + effect are atomic.

## Deterministic analytics

`analyze_support_activity` (staff-only tool, also powering the insights
view) returns:

- SLA status per ticket (breached / at-risk / within, with minutes
  remaining or over)
- Known-issue pattern matches against the current ticket population
  (KI-208 → TKT-502, KI-211 → TKT-504)
- Activity clusters (keyword grouping over ticket subjects)

No ML. The dataset has seven tickets — clustering is not the right tool.

## Observability

Every agent turn emits a trace record (request/session ID, tools fired
with per-tool latency, answer state, evidence IDs, action ID when
applicable, provider/model metadata). Traces are returned to the UI and
appended to a local JSONL file. No external observability service.

## Demo video script (~5 min)

- **0:00–0:35** — problem framing (authority resolution over imperfect sources)
- **0:35–1:10** — four-layer architecture + core thesis
- **1:10–2:15** — Northstar (₹0 fee, agreement waiver) vs LumenWorks (₹250 fee, SOP); same question, opposite correct answers
- **2:15–2:50** — historical ticket retrieval then "what's correct today" — deprecated source explicitly declined
- **2:50–3:20** — cross-account attempt from a LumenWorks session — rejection at the tool layer
- **3:20–4:00** — action flow: prepare → pending card → explicit confirm → executed
- **4:00–4:40** — insights view: SLA risk list + KI-208/KI-211 matches
- **4:40–5:00** — core thesis restated

## What is intentionally not here

See [`product.md`](product.md#what-was-intentionally-left-out).
