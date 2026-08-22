"""Per-turn context and the §7 trace object.

Every turn emits exactly one trace: {request_id, session_id, turn_id,
tools[{name, args_digest, latency_ms, status}], answer_state, evidence_ids[],
action_id?, total_latency_ms}. Traces append to a local JSONL file — no
external observability service.
"""

import hashlib
import json
import time
import uuid
from pathlib import Path

from .config import PROJECT_ROOT

DEFAULT_TRACE_PATH = PROJECT_ROOT / "data" / "traces.jsonl"


def args_digest(args):
    """Stable, compact digest of tool arguments (no raw payloads in traces)."""
    canonical = json.dumps(args, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


class TurnContext:
    def __init__(self, session, turn_id=1, request_id=None,
                 trace_path=DEFAULT_TRACE_PATH):
        self.session_id = session.get("session_id", "session")
        self.request_id = request_id or uuid.uuid4().hex
        self.turn_id = turn_id
        self.trace_path = trace_path
        self.tool_records = []       # {name, args_digest, latency_ms, status}
        self.evidence_ids = []       # ordered, deduplicated §5 evidence ids
        self.action_id = None
        self.provider = None         # groq | openrouter (set by executor)
        self.model_used = None       # actual model that answered
        self.fallback_used = False   # True if fallback provider answered
        self.model_calls = []        # [{iteration, latency_ms, tool_calls}]
        self._started_at = time.perf_counter()

    def record_tool(self, name, args, latency_ms, status):
        self.tool_records.append({
            "name": name,
            "args_digest": args_digest(args),
            "latency_ms": latency_ms,
            "status": status,
        })

    def add_evidence(self, records):
        for record in records:
            evidence_id = record.evidence_id
            if evidence_id not in self.evidence_ids:
                self.evidence_ids.append(evidence_id)

    def total_latency_ms(self):
        return int((time.perf_counter() - self._started_at) * 1000)

    def build_trace(self, answer_state):
        trace = {
            "request_id": self.request_id,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "tools": list(self.tool_records),
            "answer_state": answer_state,
            "evidence_ids": list(self.evidence_ids),
            "total_latency_ms": self.total_latency_ms(),
        }
        if self.action_id:
            trace["action_id"] = self.action_id
        # Provider metadata (ADR-008): which provider/model actually answered.
        if self.provider:
            trace["provider"] = self.provider
        if self.model_used:
            trace["model_used"] = self.model_used
        if self.fallback_used:
            trace["fallback_used"] = True
        if self.model_calls:
            trace["model_calls"] = self.model_calls
        return trace

    def write_trace(self, answer_state):
        trace = self.build_trace(answer_state)
        if self.trace_path is not None:
            self.trace_path = Path(self.trace_path)
            self.trace_path.parent.mkdir(parents=True, exist_ok=True)
            with self.trace_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(trace) + "\n")
        return trace
