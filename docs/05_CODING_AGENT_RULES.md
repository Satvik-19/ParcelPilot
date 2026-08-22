# Coding Agent Rules

**Status: LOCKED.** Operating rules for any coding agent (human or AI) working in this
repo. Violating these is treated as a defect, same as a failing test.

1. **Session discipline.** Start every session by reading the latest file in
   `docs/handoffs/`; end every session by writing a new one from
   `06_SESSION_HANDOFF_TEMPLATE.md`. No silent context carry-over (PRD §4).
2. **Read before writing.** Before any implementation, read `00_PRD.md` →
   `04_EVAL_SPEC.md` in order and follow the build order in
   `IMPLEMENTATION_PLAN.md` (which elaborates PRD §7). Do not start a phase whose
   dependencies are not green.
3. **Locked decisions change only via ADR.** If reality forces a change to a locked
   decision (model choice, stack element), add an ADR entry to `01_ARCHITECTURE.md` §4
   and update the affected doc — never silently drift (PRD §8).
4. **Deterministic business logic.** Every number the system states comes from a `domain/`
   function. Time-dependent logic takes an explicit `as_of` (default `SNAPSHOT_TS`);
   `datetime.now()` is banned in domain/business code.
5. **Security in code, not prompts.** Access control and confirmation gating are enforced
   in `security/` and the confirmation endpoint. Prompt text is untrusted input; never
   rely on it for a security property that has a code-level chokepoint.
6. **No hardcoded answers.** Nothing in `agent/`, `tools/`, or prompts may encode golden-
   case outputs (fees, limits, ticket-specific facts). Behavior emerges from data +
   domain functions + retrieval rules.
7. **Test before dependents.** Layer A tests exist and pass before the tool layer is built
   on a domain function; tool-layer tests before agent work; confirmation-gate security
   tests before the UI ships the confirm button (see `04_EVAL_SPEC.md`).
8. **Right-size everything.** No vector DBs, agent frameworks, extra services, or ML
   where the PRD/ADRs chose simpler (ADR-001/002). New dependencies need justification in
   the handoff.
9. **Spec disagreement → fix the doc first.** If code and a locked doc disagree, treat the
   code as buggy unless you re-derive from the source data and find a doc error — then fix
   the doc, note it in an ADR, and record it in the handoff. Never invent an implementation
   workaround for a documentation contradiction.
10. **Evidence over similarity.** Retrieval and conflict resolution use authority rank and
    applicability metadata (`03_AGENT_SPEC.md` §5–6), never lexical/semantic similarity
    alone.
