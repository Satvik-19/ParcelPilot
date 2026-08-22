# ParcelPilot AI Support Agent — Product Requirements Document

**Status: LOCKED.** This is the source of truth for the build. Every other doc in this
package (`01_ARCHITECTURE.md`, `02_DOMAIN_SPEC.md`, `03_AGENT_SPEC.md`, `04_EVAL_SPEC.md`,
`05_CODING_AGENT_RULES.md`) derives from this PRD and must stay consistent with it. If a
build session discovers this PRD needs to change, follow the change-control rule in §8 —
don't silently drift.

Built for: CalQuity AI Engineer hiring assessment — ParcelPilot Customer Support agent.

---

## 1. Problem statement

ParcelPilot's operations team resolves support requests by manually cross-referencing
policies, SOPs, customer-specific contracts, and operational data — where **not all
sources are equally trustworthy**: one policy version is deprecated, customer agreements
can override general policy, and two real historical ticket resolutions in the supplied
data are factually wrong. The system must reason over this imperfect source set
*deliberately* — resolving conflicts by authority, not by textual similarity — rather than
behaving like a naive RAG chatbot that treats every retrieved passage as equally true.

## 2. Users & contexts

One backend, two session contexts (mocked at login, no real auth):

| Context | Session shape | Can do |
|---|---|---|
| Customer | `{role: "customer", account_id}` | Ask about their own account/orders/tickets, request cancellations/credits, escalate their own tickets. Cannot see or reference any other account's data, ever. |
| Internal staff | `{role: "staff", staff_id, permissions}` | Everything a customer can ask about any account, plus `analyze_support_activity`, plus the insights view. |

Mock accounts (from the real data pack — see `02_DOMAIN_SPEC.md` for full detail):
`ACCT-001` Northstar Logistics (Enterprise, has custom agreement), `ACCT-002` LumenWorks
(Growth, has custom agreement), `ACCT-003` Beacon Retail (Standard, no custom agreement),
`ACCT-004` Axis Labs (Enterprise, no custom agreement).

## 3. Functional requirements

Numbered to match the assessment brief's minimum requirements — each must be demonstrably
satisfied in the demo and traceable to a test in `04_EVAL_SPEC.md`.

**FR-1 — Natural-language chatbot.** Accepts free-text queries in both contexts. Answers
directly when evidence supports a confident answer; escalates when it doesn't (see the
three answer-states in `03_AGENT_SPEC.md` §2).

**FR-2 — Access control.** Enforced in the tool/data layer, never by prompt instruction.
A customer session must be structurally incapable of retrieving another account's data —
this must be provably true even under adversarial prompting.

**FR-3 — Four tools**, agent-selectable except where noted:
1. `search_knowledge` — document/evidence retrieval
2. `query_operations` — structured data lookup/calculation
3. `analyze_support_activity` — internal-only analytics (also powers the insights view)
4. `prepare_support_action` — drafts a state-changing action (escalate / update ticket /
   create follow-up), **never** executes it

`confirm_support_action` is **not** an LLM-selectable tool — see `03_AGENT_SPEC.md` §4 for
why and how it's wired instead.

**FR-4 — Confirmation before action.** No state-changing action fires without an explicit
user confirmation bound to the exact drafted payload. Cancellation requests and service
credits are drafted/mocked actions (no real carrier or payment integration) but follow the
same prepare → confirm flow — exact validation checklist in `03_AGENT_SPEC.md` §4.

**FR-5 — Multi-step requests.** The system must correctly chain order lookup → account
resolution → agreement/policy retrieval → calculation → action decision for questions that
require it (this is most of the golden test set in `02_DOMAIN_SPEC.md` §5).

**FR-6 — Interface.** Chat UI, visibly shows which tool(s) fired per turn and the evidence
used. Internal context additionally shows the insights view. Hosted, link submitted.

**FR-7 — Demo video.** ~5 minutes: architecture, live demo, key decisions. Script lives in
`01_ARCHITECTURE.md` §5.

**FR-8 — Proactive issue detection (chosen "additional problem").** `analyze_support_activity`
surfaces SLA breaches/at-risk tickets, known-issue pattern matches (KI-208, KI-211), and
cross-account clustering — computed deterministically, not via ML, given the dataset size.

## 4. Non-functional requirements

- **Correctness over cleverness.** Every number the system states (a fee, a credit amount,
  an SLA deadline) must trace to a deterministic function in `domain/`, never to LLM
  arithmetic or LLM recall of a policy number.
- **Zero standing infrastructure cost.** Single SQLite file, no paid services, free-tier
  LLM only.
- **Right-sized engineering.** The data pack is six one-page PDFs and ~17 data rows total.
  Any component whose complexity isn't justified by that scale (vector DB, ML clustering,
  multi-agent frameworks) is a smell, not a feature — reviewers are explicitly told to
  watch for over-engineering as much as under-engineering.
- **No stale context across sessions.** Every coding session must start by reading the
  latest file in `docs/handoffs/` and end by writing a new one. See
  `06_SESSION_HANDOFF_TEMPLATE.md` — this is not optional.
- **Deterministic time.** All business logic runs against the fixed `SNAPSHOT_TS` from
  `02_DOMAIN_SPEC.md`; time-dependent domain functions take an explicit `as_of`. No
  `datetime.now()` anywhere in domain/business logic.
- **Lightweight observability.** Every agent turn emits a trace (request/session ID, tools
  used, latency, answer state, evidence IDs, action ID when applicable) returned to the UI
  and logged locally — no external observability service. Schema: `03_AGENT_SPEC.md` §7.

## 5. Definition of done

- [ ] All 4 tools implemented and independently unit-tested
- [ ] All 12 golden cases in `02_DOMAIN_SPEC.md` §5 pass at the domain-function level (Layer A)
- [ ] Global invariant GI-1 (deprecated policy never surfaces as authoritative —
  `02_DOMAIN_SPEC.md` §5) enforced as a regression test at both evidence-resolver and
  agent level
- [ ] At least 6 of the 12 pass end-to-end through the live agent (Layer B/C)
- [ ] Cross-account access attempt provably rejected at the tool layer, with a test proving it
- [ ] Confirmation flow cannot be bypassed by prompt injection (tested explicitly)
- [ ] Insights view renders SLA risk + known-issue matches from real data
- [ ] Hosted, public repo, README with setup/run instructions
- [ ] `architecture.md`, `product.md` written (can reuse/trim this package)
- [ ] 5-minute demo recorded per the script in `01_ARCHITECTURE.md` §5

## 6. Explicitly out of scope

Real carrier/ticketing integrations, production SSO, email/SMS notifications, ML-based
ticket clustering, LLM fine-tuning, multi-tenant infra beyond the mock session selector.
State this plainly in the product note as an intentional trade-off, not an omission.

## 7. Build order (locked — do not skip ahead)

1. `02_DOMAIN_SPEC.md` — schema + business rules + golden answer key (**already locked**)
2. `03_AGENT_SPEC.md` — tool contracts + system prompt rules (**already locked**)
3. `04_EVAL_SPEC.md` — test plan (**already locked**)
4. Domain layer + Layer A tests (no LLM, no UI)
5. Tool layer wrapping the domain layer + Layer A tests still passing
6. Agent runtime + Layer B tests
7. Frontend chat + insights view
8. Layer C end-to-end tests
9. Deploy, record demo, write final docs

No implementation work starts before steps 1–3 are read and understood by whichever coding
agent picks this up — that's what `05_CODING_AGENT_RULES.md` enforces.

## 8. Change control

If reality forces a change to a locked decision (e.g. the chosen Groq model turns out to
have unreliable tool-calling), don't just change the code — add an ADR entry to
`01_ARCHITECTURE.md` §4 explaining what changed and why, then update this PRD if the change
affects scope. A reviewer opening the repo cold should be able to read the ADR log and
understand the system's actual decision history, not just its current state.
