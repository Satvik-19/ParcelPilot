# Agent Specification

**Status: LOCKED.** Derives from `00_PRD.md`, `01_ARCHITECTURE.md`, `02_DOMAIN_SPEC.md`.
PRD FR-1 references §2 (answer states) and FR-3 references §4 (confirmation) of this doc.

## 1. Runtime boundaries

- Hand-rolled loop per ADR-002: `agent/planner.py` (tool selection), `agent/executor.py`
  (call/execute/feed-back), `agent/context.py` (session + conversation state),
  `agent/prompts.py` (system prompt).
- The LLM owns exactly two things: **which tool to call next** and **how to phrase the
  final answer**. It owns nothing about truth, authority, applicability, access, or
  execution — those live in the trusted application layer (`security/`, `trust/`,
  `domain/`).
- Hard guardrails in the runtime, not the prompt: max 8 tool iterations per turn (then the
  turn ends ESCALATE with the reason); tool schemas expose **no session/identity
  parameters** — the runtime injects the authenticated session server-side on every call.
- No hardcoded answers for example questions. Behavior must emerge from tools + domain
  functions + retrieval rules; a reviewer grepping for "250" or "NORTHSTAR" in `agent/`
  should find nothing.

## 2. The three answer states

Every substantive turn lands on exactly one of ANSWER / ESCALATE / INSUFFICIENT_EVIDENCE
(definitions in `02_DOMAIN_SPEC.md` §4). The state is **determined from trusted
domain/tool results wherever possible, not from the LLM's confidence**:

- **ANSWER** — a domain/tool result returned a definitive outcome with applicable evidence.
- **ESCALATE** — the domain result says so: manager approval required (credit > ₹1,000),
  security incident (e.g. TKT-505), unsupported exception, repeated tool failure, or the
  iteration cap hit.
- **INSUFFICIENT_EVIDENCE** — the domain/data result says so: required facts missing
  (e.g. `carrier_fault` unknown), no applicable authoritative source found.

The LLM **explains** the returned result in natural language with citations. It must not
invent policy outcomes, calculations, or missing evidence, must not upgrade
INSUFFICIENT_EVIDENCE into a guess, and must never state an answer that no tool/domain
result supports. If the model's preferred wording conflicts with a domain result, the
domain result wins.

## 3. Tool contracts

Common envelope for all four tools: `{status, result, evidence[], warnings[]}`. All
evidence entries follow the structured schema in §5. Errors are returned as structured
results (never raw stack traces) so the agent can react deterministically.

1. **`search_knowledge(query, account_scope?, include_historical=false)`** — FTS5 retrieval
   over document chunks. Force-includes the active agreement chunks for the account in
   scope; excludes DEPRECATED documents and historical resolutions unless
   `include_historical=true` (see §6). Customer sessions can only retrieve chunks
   authorized for their own account — enforced in `security/authorization.py`, not by
   filtering prompt output.
2. **`query_operations(entity, id|filters)`** — structured lookup of accounts/orders/
   tickets plus computed fields produced by `domain/` functions: cancellation fee, service
   credit, SLA status, known-issue match. Every computation passes `as_of=SNAPSHOT_TS`
   explicitly. Session-scoped in the data layer.
3. **`analyze_support_activity(scope)`** — staff-only; a customer session calling it gets a
   structured rejection. Returns deterministic SLA breach/at-risk lists, KI-208/KI-211
   matches, and cross-account pattern grouping (keyword/category + account/order linkage —
   no ML). Also backs the insights view (FR-8).
4. **`prepare_support_action(action_type, payload)`** — `action_type ∈ {escalate_ticket,
   update_ticket, create_follow_up, request_cancellation, grant_service_credit}`. Drafts a
   pending action per §4; **never executes anything**. The agent must stop and present the
   confirmation card, and must not describe the action as executed.

## 4. Confirmation & security model

**`prepare_support_action`** creates an `actions` row (`02_DOMAIN_SPEC.md` §1):
`status=pending`, bound to the **exact** `payload_json` (stored with a canonical
`payload_hash`), the drafting `session_id` and `created_by`, and `expires_at` = creation
+ 5 minutes. It returns `{action_id, token, rendered_card}` where `token` is one-time and
the rendered card shows the exact payload that will execute.

**`confirm_support_action`** is a plain backend endpoint called by the **frontend** when
the user clicks confirm (ADR-004). It is never in the LLM tool schema and the model has no
path to invoke it — a chat message saying "confirm" cannot execute anything. Before any
execution it validates **all** of:

1. action exists;
2. `status == pending`;
3. calling session/user matches `session_id` / `created_by`;
4. one-time `token` matches;
5. payload integrity — hash of the payload presented for confirmation equals stored
   `payload_hash`;
6. not expired (`SNAPSHOT_TS`-based comparison in tests; `expires_at` checked server-side
   against the confirmation request time).

On success: `status → confirmed → executed` (mocked effect only — ticket status change,
order cancellation flag, credit ledger entry), `confirmed_at` recorded. On any failed
check: reject with a structured reason, no state mutation, attempt recorded in the trace.

Cancellation and credit requests are **drafted/mocked actions** — there are no real carrier
or payment integrations (PRD §6) — but they pass through this exact flow; nothing
state-changing bypasses it.

## 5. Structured evidence

Every evidence record carries:

```
{ evidence_id, source_doc,          # document file id or table/row id
  section,                          # chunk id / section heading
  status,                           # CURRENT | DEPRECATED | ACTIVE | HISTORICAL_RESOLUTION
  authority_rank,                   # per 02_DOMAIN_SPEC.md §2 hierarchy (1..5)
  applicable_to,                    # account scope + date validity at SNAPSHOT_TS
  overridden_by | excluded_reason,  # set when a source lost a conflict or was excluded
  text }
```

**Retrieval authorization ≠ evidence applicability.** A chunk may be authorized for
display to a session (e.g. a historical resolution shown on explicit request) while
carrying `authority_rank = NONE` — displayable but never usable to determine a policy
outcome. Conflict resolution is deterministic (`trust/conflicts.py`): higher
`authority_rank` wins; the losing record stays in the trace with `overridden_by` set.

## 6. Retrieval rules — historical & deprecated material

- **Default:** authoritative retrieval returns only CURRENT/ACTIVE sources, scoped to the
  session's account.
- **DEPRECATED documents** (Policy v2) and **historical ticket resolutions** are excluded
  from normal authoritative retrieval. They are surfaced **only when explicitly requested**
  (user asks about historical/deprecated material), always flagged `historical=true`,
  `authority_rank=NONE`, and the agent must state they are context-only, not current policy.
- No policy answer may cite a deprecated or historical source as its authority. Golden
  cases #10/#11 and invariant GI-1 (`02_DOMAIN_SPEC.md` §5) enforce this.

## 7. Observability — per-turn trace

Every turn emits one trace object, returned to the UI and appended to a local JSONL file
(no external observability service):

```
{ request_id, session_id, turn_id,
  tools: [{name, args_digest, latency_ms, status}],
  answer_state, evidence_ids[], action_id?, total_latency_ms }
```

## 8. System prompt rules

The system prompt may state role, tone, tool descriptions, and the rule "present drafted
actions and wait for confirmation". It must not carry policy numbers, account data, or
access-control instructions — those are enforced in the trusted layer, and the prompt is
treated as untrusted input anyway (adversarial prompting is in scope, case #12).
