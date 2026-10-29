"""Run the data agent through the harness's headless CLI (``harness/dist/cli/ask.js --json``).

The harness reads its model key and the role's Databricks service principal from its own environment; this
process passes the environment on and checks variables by name only, never reading, logging, or storing a
value. Every run writes its trace under ``<runs>/<run id>/`` (``prompt.md``, ``trace.jsonl``,
``result.json``), like any other agent run.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

RUN_TIMEOUT_SECONDS = 420
# Variables every run needs (names only; values are never read here).
AGENT_ENVIRONMENT = (
    "SNM_ANTHROPIC_API_KEY",
    "SNM_MODEL_MAIN",
    "SNM_MODEL_EXPLORER",
    "SNM_MODEL_VERIFIER",
    "DATABRICKS_HOST",
    "DATABRICKS_WAREHOUSE_ID",
)
ENVIRONMENT_HINT = (
    "stop the page, load your settings in the same terminal (set -a; source ~/.config/snm/env; set +a), "
    "and start it again (apps/ask/README.md)"
)


def role_environment(role: str) -> tuple[str, str]:
    """The role's service principal variables (named as ``roleCredentialVariables`` in the harness)."""
    suffix = role.upper()
    return f"SNM_DATABRICKS_CLIENT_ID_{suffix}", f"SNM_DATABRICKS_CLIENT_SECRET_{suffix}"


def missing_environment(role: str | None = None) -> list[str]:
    """The variables a run as ``role`` needs that are not set (checked by name, never by value)."""
    names = [*AGENT_ENVIRONMENT, *(role_environment(role) if role else ())]
    return [name for name in names if not os.environ.get(name)]


def run_agent(
    harness_cli: Path, question: str, earlier: list[str], role: str, as_of: str, runs_dir: Path
) -> dict[str, Any]:
    if not harness_cli.exists():
        return {
            "status": "error",
            "error": f"{harness_cli} not found: run npm ci && npm run build -w harness",
        }
    missing = missing_environment(role)
    if missing:
        return {"status": "error", "error": f"{', '.join(missing)} not set: {ENVIRONMENT_HINT}"}
    command = [
        "node",
        str(harness_cli),
        "--json",
        "--role",
        role,
        "--as-of",
        as_of,
        "--runs-dir",
        str(runs_dir),
    ]
    for text in earlier:
        command += ["--earlier", text]
    command.append(question)
    env = {**os.environ, "NODE_USE_ENV_PROXY": "1", "NODE_NO_WARNINGS": "1"}
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, env=env, timeout=RUN_TIMEOUT_SECONDS, check=False
        )
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": f"the agent did not finish within {RUN_TIMEOUT_SECONDS} s"}
    lines = [line for line in completed.stdout.splitlines() if line.startswith("{")]
    if not lines:
        return {"status": "error", "error": completed.stderr.strip()[-500:] or "no result from the harness"}
    return json.loads(lines[-1])
