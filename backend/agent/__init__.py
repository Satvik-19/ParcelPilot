"""Agent runtime (IMPLEMENTATION_PLAN Phase 5, 03_AGENT_SPEC.md §1).

Hand-rolled loop per ADR-002: planner (tool selection validation), executor
(call/execute/feed-back + answer-state derivation), context (session +
conversation state + §7 trace), prompts (system prompt). The LLM owns only
tool selection and final phrasing — truth, authority, access and execution
stay in the trusted layer.
"""
