"""Model-visible tool schemas (03_AGENT_SPEC.md §3).

Two hard boundaries enforced here:
- `confirm_support_action` is NEVER in this schema (ADR-004) — the model has
  no path to execute anything;
- no schema exposes session/identity parameters — the runtime injects the
  authenticated session server-side on every call.
"""

MODEL_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": (
                "Search current policy and product documentation. Returns "
                "evidence records with authority metadata; your active "
                "agreement is always included for your account scope."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural-language search query.",
                    },
                    "account_scope": {
                        "type": "string",
                        "description": (
                            "Optional account scope; requests outside the "
                            "caller's authorization are rejected."
                        ),
                    },
                    "include_historical": {
                        "type": "boolean",
                        "description": (
                            "Only true when the user explicitly asks for "
                            "historical resolutions or deprecated documents."
                        ),
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_operations",
            "description": (
                "Look up an account, order or ticket. Returns trusted "
                "computed state: cancellation outcome, service-credit "
                "outcome, severity, SLA status and known-issue attribution. "
                "Order results also carry supported_actions — the draftable "
                "next steps implied by those outcomes. Never recomputes "
                "anything client-side."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity": {
                        "type": "string",
                        "enum": ["account", "order", "ticket"],
                    },
                    "entity_id": {
                        "type": "string",
                        "description": "The account_id, order_id or ticket_id.",
                    },
                },
                "required": ["entity", "entity_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_support_activity",
            "description": (
                "Internal analytics: SLA breach states, known-issue matches "
                "and deterministic ticket groupings. Internal-staff sessions "
                "only; other sessions receive a structured rejection."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "account_scope": {
                        "type": "string",
                        "description": "Optional narrowing to one account.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "prepare_support_action",
            "description": (
                "DRAFT a state-changing support action (it is not executed). "
                "Returns a confirmation card the user must approve through "
                "the UI. Never describe a drafted action as completed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action_type": {
                        "type": "string",
                        "enum": [
                            "escalate_ticket",
                            "update_ticket",
                            "create_follow_up",
                            "request_cancellation",
                            "grant_service_credit",
                        ],
                    },
                    "payload": {
                        "type": "object",
                        "description": (
                            "Action payload; must include the account_id the "
                            "action targets plus the relevant entity ids."
                        ),
                    },
                },
                "required": ["action_type", "payload"],
            },
        },
    },
]

TOOL_NAMES = tuple(tool["function"]["name"] for tool in MODEL_TOOLS)

# A model turn can never reach confirmation/execution: the endpoint exists
# only as a backend route for the UI (ADR-004) and is intentionally absent
# from every schema here.
FORBIDDEN_TOOL_NAMES = ("confirm_support_action", "execute_action", "confirm")
