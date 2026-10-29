"""Launch the planner agent through the harness's headless CLI (the agents never run unasked in the app).

The harness reads its model key from its own environment; this process passes the environment on and never
reads, logs, or stores the key. Every run writes its trace under ``<runs>/agents/<run>/`` (``trace.jsonl``,
``result.json``), which ``snm-evals mlflow-upload`` can publish like any other agent run.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from .app import WebSettings

RUN_TIMEOUT_SECONDS = 420
# What the harness needs from the environment that starts the pages (names only; values are never read here).
PLANNER_ENVIRONMENT = ("SNM_ANTHROPIC_API_KEY", "SNM_MODEL_MAIN")
ENVIRONMENT_HINT = (
    "stop the pages, load your settings in the same terminal (set -a; source ~/.config/snm/env; set +a), "
    "and start them again (docs/ROLE_PLAY.md)"
)


def missing_environment() -> list[str]:
    """The planner's environment variables that are not set (checked by name, never by value)."""
    return [name for name in PLANNER_ENVIRONMENT if name not in os.environ]


def run_planner(settings: WebSettings, case_id: str, task: str, role: str, as_of: str) -> dict[str, Any]:
    if not settings.planner_available:
        return {"status": "error", "error": "the planner runs only in local mode with the harness built"}
    missing = missing_environment()
    if missing:
        return {"status": "error", "error": f"{', '.join(missing)} not set: {ENVIRONMENT_HINT}"}
    command = [
        "node", str(settings.harness_cli), "--json", "--case", case_id, "--task", task, "--role", role,
        "--as-of", as_of, "--store", str(settings.store_dir), "--planning", str(settings.planning_dir),
        "--runs-dir", str(settings.runs_dir / "agents"),
    ]  # fmt: skip
    env = {**os.environ, "NODE_USE_ENV_PROXY": "1", "NODE_NO_WARNINGS": "1"}
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, env=env, timeout=RUN_TIMEOUT_SECONDS, check=False
        )
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": f"the planner did not finish within {RUN_TIMEOUT_SECONDS} s"}
    lines = [line for line in completed.stdout.splitlines() if line.startswith("{")]
    if not lines:
        return {"status": "error", "error": completed.stderr.strip()[-500:] or "no result from the harness"}
    return json.loads(lines[-1])
