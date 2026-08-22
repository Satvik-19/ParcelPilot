"""Executor — the agent loop (03_AGENT_SPEC.md §1, §2).

The LLM owns tool selection; everything else here is deterministic runtime:
- every model-proposed call is validated (planner) before dispatch;
- the authenticated session is injected server-side on dispatch — the model
  never supplies identity;
- the loop is hard-capped at MAX_TOOL_ITERATIONS; exhaustion or a provider
  failure always yields ESCALATE, never an unbounded retry;
- the answer state is derived from tool/domain results (never from LLM
  confidence): ESCALATE > INSUFFICIENT_EVIDENCE > ANSWER.
"""

import dataclasses
import json
import re
import time
from dataclasses import dataclass

from backend.tools._envelope import envelope_error
from backend.tools.analyze_support_activity import analyze_support_activity
from backend.tools.prepare_support_action import prepare_support_action
from backend.tools.query_operations import query_operations
from backend.tools.search_knowledge import search_knowledge

from .config import MAX_TOOL_ITERATIONS
from .context import DEFAULT_TRACE_PATH, TurnContext
from ._errors import ProviderError
from .planner import parse_tool_calls, validate_call
from .prompts import SYSTEM_PROMPT
from .tools_schema import MODEL_TOOLS

# Domain flags that force escalation regardless of anything the model says.
_ESCALATION_FLAGS = (
    "escalation_required",
    "security_incident",
    "requires_manager_approval",
)

_CAP_ESCALATE_TEXT = (
    "I could not complete this request within the allowed number of tool "
    "steps, so it has been escalated for human follow-up. No state-changing "
    "action has been taken."
)
_BLANK_ANSWER_ESCALATE_TEXT = (
    "I was unable to produce a reliable final answer for this request, so "
    "it has been escalated for human follow-up. No state-changing action "
    "has been taken."
)
_PROVIDER_ESCALATE_TEXT = (
    "The model service is currently unavailable, so I cannot verify any "
    "facts against trusted sources. This request has been escalated for human "
    "follow-up. No state-changing action has been taken."
)


@dataclass(frozen=True)
class TurnResult:
    answer: str
    answer_state: str   # ANSWER | ESCALATE | INSUFFICIENT_EVIDENCE
    state_reason: str
    trace: dict
    # UI-facing provenance (supersets of the §7 trace, which keeps digests
    # only): the tools that fired and the structured evidence behind them.
    # Both are trusted-layer records — never model reasoning.
    tools: tuple = ()
    evidence: tuple = ()


# --------------------------------------------------------------------------
# Tool dispatch — the ONLY place model choices touch the trusted layer.
# --------------------------------------------------------------------------

def execute_tool(name, arguments, conn, session, as_of=None):
    """Dispatch a validated call; the session is injected here, server-side.

    ``as_of`` is forwarded only to the action lifecycle (draft expiry) so a
    live application can keep a real confirmation window; it defaults to
    SNAPSHOT_TS everywhere, keeping the runtime deterministic for tests.
    """
    if name == "search_knowledge":
        return search_knowledge(
            conn, session,
            arguments.get("query", ""),
            account_scope=arguments.get("account_scope"),
            include_historical=bool(arguments.get("include_historical", False)),
        )
    if name == "query_operations":
        return query_operations(
            conn, session,
            arguments.get("entity"),
            arguments.get("entity_id"),
        )
    if name == "analyze_support_activity":
        return analyze_support_activity(
            conn, session,
            account_scope=arguments.get("account_scope"),
        )
    if name == "prepare_support_action":
        kwargs = {"as_of": as_of} if as_of is not None else {}
        return prepare_support_action(
            conn, session,
            arguments.get("action_type"),
            arguments.get("payload") or {},
            **kwargs,
        )
    return envelope_error("INVALID_TOOL_CALL", f"Unknown tool {name!r}.")


def serialize_envelope(envelope):
    """Compact JSON-safe view of a ToolEnvelope for the model's feedback.

    The model needs the structured result (including per-record ``text``
    for knowledge queries — it must read policy text to cite facts) plus
    evidence metadata to cite sources.  Only the evidence *records*
    (displayed by the UI panel) have their ``text`` stripped: those
    duplicate the result text and inflating the feedback without adding
    new information for the model.
    """
    compact_evidence = [
        {k: v for k, v in dataclasses.asdict(record).items() if k != "text"}
        for record in envelope.evidence
    ]
    return {
        "status": envelope.status,
        "result": envelope.result,
        "evidence": compact_evidence,
    }


# Safe deterministic text for the early-termination path: a single rejection
# is a trusted definitive outcome (Issue 3), so the turn ends as ANSWER.
# This text avoids an extra model call just to restate a denial.
_REJECTION_ANSWER = (
    "I'm unable to access that data from this session. "
    "No state-changing action has been taken."
)

# --- Knowledge-intent routing guard -------------------------------------------
# If the model skips all tools on its first response and the user message
# looks like a document/policy/knowledge query, inject one retry hint so the
# model gets a chance to call search_knowledge before answering.  This is a
# safety net for prompt non-compliance, not a replacement for good tool-
# selection guidance.
#
# The keyword sets are intentionally generic (no customer names, product
# names, document IDs, or test-specific wording).

_ENTITY_ID_RE = re.compile(r'\b(ORD-\d+|TKT-\d+|ACCT-\d+)\b')

_KNOWLEDGE_INTENT_KEYWORDS = frozenset({
    "issue", "known", "policy", "policies", "terms", "agreement",
    "rule", "rules", "sla", "coverage", "covered", "plan",
    "capability", "capabilities", "feature", "features",
    "problem", "bug", "defect",
})

_ACTION_KEYWORDS = frozenset({
    "cancel", "refund", "escalate", "update", "create",
    "prepare", "draft", "execute", "confirm",
})


def _is_knowledge_intent(message):
    """Heuristic: does the user message look like a document/policy query?

    Returns False when the message contains entity IDs (order/ticket/account
    references → query_operations) or action verbs (→ prepare_support_action),
    so the guard only fires on genuinely document-grounded questions.
    """
    if _ENTITY_ID_RE.search(str(message)):
        return False
    words = set(re.findall(r'\w+', str(message).lower()))
    if words & _ACTION_KEYWORDS:
        return False
    return bool(words & _KNOWLEDGE_INTENT_KEYWORDS)


_ROUTING_HINT = (
    "Before answering, call the appropriate tool to retrieve trusted facts. "
    "For policy, known-issue, or product-documentation questions, use "
    "search_knowledge. For account/order/ticket questions, use "
    "query_operations. Do not answer from memory."
)


# --------------------------------------------------------------------------
# Answer-state derivation — pure code over envelope results (§2).
# --------------------------------------------------------------------------

def _scan(obj, predicate):
    """Depth-first search for a predicate hit inside nested result data."""
    if predicate(obj):
        return True
    if isinstance(obj, dict):
        return any(_scan(value, predicate) for value in obj.values())
    if isinstance(obj, (list, tuple)):
        return any(_scan(item, predicate) for item in obj)
    return False


def _has_escalation_flag(envelopes):
    def flagged(obj):
        return isinstance(obj, dict) and any(
            key in _ESCALATION_FLAGS and value is True
            for key, value in obj.items()
        )
    return any(_scan(env.result, flagged) for env in envelopes)


def _has_insufficient_marker(envelopes):
    def marked(obj):
        return isinstance(obj, str) and "INSUFFICIENT_EVIDENCE" in obj
    return any(_scan(env.result, marked) for env in envelopes)


def _max_failure_streak(envelopes):
    """Consecutive structured errors only. Rejections (ACCESS_DENIED,
    STAFF_ONLY, ...) are deterministic trusted decisions, not failures —
    they must neither escalate a turn nor masquerade as missing evidence."""
    longest = current = 0
    for env in envelopes:
        if env.status == "error":
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def derive_answer_state(envelopes, cap_hit=False):
    """Return (answer_state, reason). Priority: ESCALATE > INSUFFICIENT > ANSWER."""
    if cap_hit:
        return "ESCALATE", "iteration cap reached without a final answer"
    if _max_failure_streak(envelopes) >= 2:
        return "ESCALATE", "repeated consecutive tool failures"
    if _has_escalation_flag(envelopes):
        return "ESCALATE", "tool result carries an escalation/security flag"
    if not any(env.status in ("ok", "rejected") for env in envelopes):
        return "INSUFFICIENT_EVIDENCE", "no supporting tool result available"
    if _has_insufficient_marker(envelopes):
        return "INSUFFICIENT_EVIDENCE", "tool result reports insufficient evidence"
    return "ANSWER", "supported by trusted tool results"


def _ui_evidence(envelopes):
    """Deduplicated §5 evidence records (as dicts) in encounter order.

    Includes overridden/excluded records intact — their overridden_by /
    excluded_reason fields are exactly what the reviewer UI must show.
    """
    seen = []
    ids = set()
    for env in envelopes:
        for record in env.evidence:
            if record.evidence_id in ids:
                continue
            ids.add(record.evidence_id)
            seen.append(dataclasses.asdict(record))
    return seen


# --------------------------------------------------------------------------
# The loop.
# --------------------------------------------------------------------------

def _assistant_message(message):
    return {
        "role": "assistant",
        "content": message.get("content") or "",
        "tool_calls": message.get("tool_calls") or [],
    }


def strip_internal_reasoning(text):
    """Defensive guard: some models wrap chain-of-thought in <think> blocks.

    Raw reasoning is never shown to the user (product quality gate); only
    the answer prose survives. Pure text surgery — no answer-state logic is
    touched by this.
    """
    if not text or "<think>" not in text:
        return text
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    cleaned = re.sub(r"<think>.*", "", cleaned, flags=re.DOTALL)  # unclosed block
    return cleaned.strip()


def run_turn(conn, session, user_message, client, model=None, history=None,
             trace_path=DEFAULT_TRACE_PATH, turn_id=1, request_id=None,
             as_of=None):
    """Run one full agent turn; always returns a TurnResult, never raises."""
    ctx = TurnContext(session, turn_id=turn_id, request_id=request_id,
                      trace_path=trace_path)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history or [])
    messages.append({"role": "user", "content": user_message})

    envelopes = []
    cap_hit = False
    final_content = None
    # Early-termination flag: when the model's only tool call produces a
    # deterministic rejection (ACCESS_DENIED / STAFF_ONLY / ...), the turn
    # has nothing to synthesize — a rejection IS a definitive trusted
    # outcome. Skipping the next model call saves one full inference round
    # on every scope-denial request (the highest-frequency short path).
    early_rejection = False
    # Per-model-call latency tracking: each entry is
    # {"iteration": N, "latency_ms": M, "tool_calls": bool}.
    model_call_log = []

    try:
        for _iter in range(MAX_TOOL_ITERATIONS):
            model_start = time.perf_counter()
            response = client.complete(messages, tools=MODEL_TOOLS, model=model)
            model_latency_ms = int((time.perf_counter() - model_start) * 1000)
            message = response["choices"][0]["message"]
            calls = parse_tool_calls(message)
            ctx.model_calls.append({
                "iteration": _iter,
                "latency_ms": model_latency_ms,
                "tool_calls": bool(calls),
            })
            if not calls:
                final_content = strip_internal_reasoning(message.get("content") or "")
                # Knowledge-intent routing guard: if the model skipped all
                # tools on its first response and the user message looks like
                # a document/policy query, give it one more chance with a
                # routing hint.  This catches prompt non-compliance without
                # forcing search_knowledge on every query.
                if (not envelopes
                        and _is_knowledge_intent(user_message)):
                    messages.append({
                        "role": "system",
                        "content": _ROUTING_HINT,
                    })
                    final_content = None
                    continue
                break

            messages.append(_assistant_message(message))
            for call in calls:
                ok, error = validate_call(call["name"], call["arguments"])
                started = time.perf_counter()
                if ok:
                    envelope = execute_tool(
                        call["name"], call["arguments"], conn, session,
                        as_of=as_of)
                else:
                    envelope = envelope_error("INVALID_TOOL_CALL", error)
                latency_ms = int((time.perf_counter() - started) * 1000)

                ctx.record_tool(call["name"],
                                call["arguments"] or {},
                                latency_ms, envelope.status)
                ctx.add_evidence(envelope.evidence)
                if envelope.status == "ok" and call["name"] == "prepare_support_action":
                    ctx.action_id = (envelope.result or {}).get("action_id")
                envelopes.append(envelope)

                messages.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": json.dumps(
                        serialize_envelope(envelope), default=str),
                })

            # Early termination: if every envelope so far is a deterministic
            # rejection and no ok/error results exist, the model has hit an
            # access wall — one more call can only restate the denial.
            if (envelopes
                    and all(e.status == "rejected" for e in envelopes)):
                early_rejection = True
                break
        else:
            cap_hit = True  # model never produced a final answer in budget
    except ProviderError as exc:
        trace = ctx.write_trace("ESCALATE")
        return TurnResult(_PROVIDER_ESCALATE_TEXT, "ESCALATE",
                          f"model provider failure: {exc}", trace,
                          tools=tuple(ctx.tool_records),
                          evidence=tuple(_ui_evidence(envelopes)))

    answer_state, reason = derive_answer_state(envelopes, cap_hit=cap_hit)
    answer = _CAP_ESCALATE_TEXT if cap_hit else final_content

    # Early-termination: a single rejection is already a definitive
    # trusted outcome — skip the synthesis model call entirely.
    if early_rejection and not cap_hit and not final_content:
        answer_state = "ANSWER"
        reason = "deterministic rejection — no synthesis required"
        answer = _REJECTION_ANSWER

    # A blank final message is a generation failure, never an answer: the UI
    # must not present an empty ANSWER when trusted evidence exists. Escalate
    # safely instead (the cap-hit text is already non-blank).
    if not cap_hit and not (answer or "").strip():
        answer_state = "ESCALATE"
        reason = "model produced no final answer text"
        answer = _BLANK_ANSWER_ESCALATE_TEXT

    # Provider metadata (ADR-008): record which provider/model answered.
    # FallbackProvider exposes provider_name/model_used; raw clients may not.
    ctx.provider = getattr(client, "provider_name", None)
    ctx.model_used = getattr(client, "model_used", None)
    if ctx.provider == "openrouter":
        ctx.fallback_used = True

    trace = ctx.write_trace(answer_state)
    return TurnResult(answer, answer_state, reason, trace,
                      tools=tuple(ctx.tool_records),
                      evidence=tuple(_ui_evidence(envelopes)))
