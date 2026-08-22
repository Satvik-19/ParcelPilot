"""Planner — validates the model's tool selections before anything executes.

The LLM owns tool selection; the planner owns whether that selection is
admissible: known name, well-formed arguments, required fields, enums, and —
critically — NO identity parameters (the session is injected server-side;
03_AGENT_SPEC.md §1). Invalid selections become structured feedback the model
can react to, never crashes.
"""

import json

from .tools_schema import MODEL_TOOLS, TOOL_NAMES

_IDENTITY_PARAMETERS = frozenset(
    {"session", "session_id", "staff_id", "role", "permissions", "created_by"}
)

_SCHEMAS = {tool["function"]["name"]: tool["function"]["parameters"]
            for tool in MODEL_TOOLS}


def parse_tool_calls(message):
    """Normalise a provider message into [{id, name, arguments}] (may be empty)."""
    calls = []
    for raw in message.get("tool_calls") or []:
        function = raw.get("function", {})
        arguments = function.get("arguments") or "{}"
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = None  # flagged as malformed by validate_call
        calls.append({
            "id": raw.get("id", ""),
            "name": function.get("name", ""),
            "arguments": arguments,
        })
    return calls


def validate_call(name, arguments):
    """Return (ok, error_message)."""
    if name not in TOOL_NAMES:
        return False, (
            f"Unknown tool {name!r}. Available tools: {', '.join(TOOL_NAMES)}. "
            "Confirmation/execution endpoints are not available to the model."
        )
    if not isinstance(arguments, dict):
        return False, f"Malformed arguments for {name}: expected a JSON object."

    schema = _SCHEMAS[name]
    properties = schema.get("properties", {})

    leaked = _IDENTITY_PARAMETERS & set(arguments)
    if leaked:
        return False, (
            f"Identity parameters {sorted(leaked)} are not accepted; the "
            "authenticated session is injected server-side."
        )

    unknown = set(arguments) - set(properties)
    if unknown:
        return False, f"Unknown parameter(s) for {name}: {sorted(unknown)}."

    for required in schema.get("required", []):
        if required not in arguments:
            return False, f"Missing required parameter {required!r} for {name}."

    for key, value in arguments.items():
        spec = properties[key]
        expected = spec.get("type")
        if expected == "string" and not isinstance(value, str):
            return False, f"Parameter {key!r} for {name} must be a string."
        if expected == "boolean" and not isinstance(value, bool):
            return False, f"Parameter {key!r} for {name} must be a boolean."
        if expected == "object" and not isinstance(value, dict):
            return False, f"Parameter {key!r} for {name} must be an object."
        if "enum" in spec and value not in spec["enum"]:
            return False, (
                f"Parameter {key!r} for {name} must be one of {spec['enum']}."
            )
    return True, ""
