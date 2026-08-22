"""Runtime configuration for the agent layer.

The API credential is loaded from the process environment or the project-root
`.env` file (gitignored); it never appears in code, prompts, traces, or docs.
"""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Writable runtime directory for serverless / ephemeral state.
# On Vercel the deployment filesystem (/var/task) is read-only, so DB and
# trace files must go to /tmp which is the platform-supported writable temp.
# Locally this resolves to PROJECT_ROOT (unchanged behaviour).
if os.environ.get("VERCEL") or str(PROJECT_ROOT).startswith("/var/task"):
    RUNTIME_DIR = Path("/tmp") / "parcelpilot"
else:
    RUNTIME_DIR = PROJECT_ROOT

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Hard guardrail (03_AGENT_SPEC.md §1): enforced in the runtime, not the prompt.
MAX_TOOL_ITERATIONS = 8

# Selected by the ADR-006 benchmark on 2026-08-21 — see
# docs/01_ARCHITECTURE.md §4 (ADR-006 update) and data/benchmark_results.json.
CHOSEN_MODEL = "qwen/qwen3.6-27b"

# OpenRouter fallback model (ADR-008). Defaults to the free router; can be
# overridden via OPENROUTER_MODEL env var if a specific model is preferred.
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "openrouter/free")


def load_groq_api_key():
    """Resolve the Groq credential without ever logging or returning it for display."""
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        env_file = PROJECT_ROOT / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("GROQ_API_KEY="):
                    key = line.split("=", 1)[1].strip()
    if not key:
        raise RuntimeError(
            "GROQ_API_KEY is not configured. Set the environment variable or "
            "add it to the (gitignored) .env file at the project root."
        )
    return key


def load_openrouter_api_key():
    """Resolve the OpenRouter credential (optional — fallback provider).

    Returns None if not configured; the fallback provider then remains
    unavailable and the system degrades gracefully to Groq-only.
    """
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        env_file = PROJECT_ROOT / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("OPENROUTER_API_KEY="):
                    key = line.split("=", 1)[1].strip()
    return key
