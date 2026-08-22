# ParcelPilot — product note

Trimmed product note for the CalQuity assessment submission. The full
requirements live in [`00_PRD.md`](00_PRD.md); the architecture and
decision log in [`01_ARCHITECTURE.md`](01_ARCHITECTURE.md).

## What was built

A four-layer customer-support agent whose thesis is that the hard part of
support isn't retrieval — it's knowing which source wins when a customer's
contract, the current SOP, and two factually wrong historical ticket
resolutions all disagree.

- A model that calls four deterministic tools (search / query / analyze /
  draft) and never owns facts, policy, fees, dates, severity, access or
  execution.
- A trusted application layer where every business rule is a pure Python
  function — testable without an LLM, independent of the runtime.
- A confirmation gate that the model cannot reach (ADR-004): the confirm
  endpoint is not in the tool schema, is session-bound, token-bound,
  payload-bound and one-shot at the SQL level.
- A chat UI that surfaces the answer state, the tools used, and the
  structured evidence behind each turn — never the model's internal
  reasoning.
- A staff-only proactive insights view powered by the deterministic
  analytics tool.
- An optional OpenRouter fallback provider for resilience when the primary
  Groq free tier is exhausted (ADR-008) — transparent failover, no
  provider-specific prompts or business logic.

## The chosen additional problem — proactive issue detection

FR-8 of the PRD: proactive detection of emerging support issues across
accounts, before customers report them. Chosen over the alternatives
(sentiment analysis, auto-categorisation) because it is the one that
directly reduces resolution time on the dataset's real shape — SLA
breaches, known-issue pattern matches (KI-208, KI-211) and emerging
cross-account activity clusters. Implemented deterministically with no
ML, because the dataset has 7 tickets and 6 PDFs: clustering is not the
right tool at this scale.

## What would be built next

For a production deployment (out of scope for this assessment):

1. Real authentication (OAuth / SSO) replacing the mocked session registry.
2. Real carrier and payment integrations replacing the mocked
   cancellation and service-credit effects.
3. Notification surfaces (email, Slack) for the proactive insights view.
4. A persistent trace store and dashboard for observability beyond the
   per-turn JSONL.
5. A richer frontend (component library, design system) once the UI
   surface grows past one chat view and one insights panel.

## What was intentionally left out

These are deliberate trade-offs, not omissions — stated in
[`00_PRD.md`](00_PRD.md) §6:

- No real carrier / ticketing integrations (cancellations and credits are
  mocked ledger entries following the same prepare → confirm flow).
- No production SSO / authentication (mocked session selector).
- No email / SMS notifications.
- No ML-based ticket clustering (deterministic keyword grouping is the
  right-sized answer at this scale).
- No LLM fine-tuning (the chosen model is used as-is with a small system
  prompt).
- No multi-tenant infrastructure beyond the mock session selector.

Adding any of these at the current dataset size (17 rows, 6 PDFs) would
read as over-engineering — the assessment explicitly asks reviewers to
watch for that.

## One concrete product-success metric

**Agreement-override correctness across the 12 golden cases:** the same
question ("what is the cancellation fee for ORD-2001?") must yield
opposite answers under Northstar's agreement (₹0, agreement waiver) vs.
LumenWorks' default SOP (₹250), with the correct authority source cited
in each case. Both answers trace to deterministic domain functions — the
model selects the tool and phrases the answer, but the fee is computed by
`resolve_cancellation_fee()` in `domain/`, not by the LLM.

This metric is enforced as a Layer A unit test, a Layer B recorded live
case (LB-01 / LB-02) and a Layer C scripted end-to-end case (LC-01 /
LC-02) — so any regression surfaces at all three layers simultaneously.

## AI tool usage in this build

AI coding assistants (Qoder / similar) were used throughout:

- **Scaffolding and exploration:** reading and cross-referencing the six
  locked spec documents, tracing call graphs, locating symbols, reviewing
  diff surfaces.
- **Test authoring:** generating initial test skeletons from spec
  contracts, then iteratively tightening assertions to match the actual
  semantics (e.g. FTS5 AND-token behaviour, one-shot claim semantics).
- **Bug diagnosis:** running targeted probes, reading trace JSONL,
  correlating recorded exchanges with deterministic expectations —
  particularly for the confirmation cap double-count bug and the
  draft-chaining gap from the Phase 5 benchmark.
- **Documentation:** drafting ADRs, session handoffs and README/product
  notes in the project's own voice (the content and locked-decision
  judgements are always the author's).

The assistant did not design architecture, choose the model, write
business rules, or make locked-decision changes — all ADRs were written
by the coding agent only after the author had identified the decision
and the rationale. Every line of production code was reviewed before
commit.
