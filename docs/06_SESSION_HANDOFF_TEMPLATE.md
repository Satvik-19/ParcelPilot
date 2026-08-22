# Session Handoff Template

Copy this to `docs/handoffs/session-NN.md` (NN = zero-padded sequence number, strictly
increasing) at the **end** of every coding session. The next session starts by reading the
latest file in `docs/handoffs/` — this is mandatory (PRD §4, `05_CODING_AGENT_RULES.md` §1).

```markdown
# Session NN — <date>

## Goal of this session
<one or two sentences>

## Current phase
<IMPLEMENTATION_PLAN.md phase number + name, and whether it is complete>

## What was done
- <bullet list of concrete changes: files/modules touched, tests added>

## Decisions made
- <any decision, with pointer to the ADR if a locked doc changed — never describe a
  locked-doc change without its ADR entry>

## Test status
- Layer A: <pass/fail count, what's covered>
- Layer B: <run/not run, model used, result>
- Layer C: <run/not run, which golden cases passed>

## Known issues / gotchas
- <anything the next session could trip over — data quirks, flaky model behavior,
  schema notes>

## Blockers & open questions
- <what's blocked, by what, and what decision is needed from the project owner>

## Next steps (in order)
1. <first thing the next session should do>
2. ...
```

Rules:
- Never edit a previous session's handoff; append a new file instead.
- Test status must reflect actually-run results, not intent.
- If a session ends mid-phase, say exactly which acceptance criteria are still unmet.
