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
        reason = "the planner needs the built harness (in the lakehouse also SNM_CASE_BACKEND=databricks)"
        return {"status": "error", "error": reason}
    missing = missing_environment()
    if missing:
        return {"status": "error", "error": f"{', '.join(missing)} not set: {ENVIRONMENT_HINT}"}
    command = [
        "node", str(settings.harness_cli), "--json", "--case", case_id, "--task", task, "--role", role,
        "--as-of", as_of, "--runs-dir", str(settings.runs_dir / "agents"),
    ]  # fmt: skip
    if settings.mode == "local":  # in the lakehouse the case server reads the cases schema itself
        command += ["--store", str(settings.store_dir), "--planning", str(settings.planning_dir)]
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


# What the extractor needs (names only): the key and a model (SNM_MODEL_EXTRACTOR, else SNM_MODEL_EXPLORER).
EXTRACTOR_CLI_NAME = "extract.js"


def extractor_environment_missing() -> list[str]:
    missing = [] if "SNM_ANTHROPIC_API_KEY" in os.environ else ["SNM_ANTHROPIC_API_KEY"]
    if "SNM_MODEL_EXTRACTOR" not in os.environ and "SNM_MODEL_EXPLORER" not in os.environ:
        missing.append("SNM_MODEL_EXTRACTOR")
    return missing


def run_extractor(
    settings: WebSettings, content: bytes, file_name: str, request: dict[str, Any]
) -> dict[str, Any]:
    """A supplier document through the extractor (change card change-20261102-supplier-documents) and the code
    checks: the checked draft, the method, cost, and run. Without the model, the code-only extractor."""
    import tempfile
    from pathlib import Path

    from snm_cases.documents import check_draft, read_document, rules_extract

    view = read_document(content)
    cli = Path(settings.harness_cli).with_name(EXTRACTOR_CLI_NAME)
    missing = extractor_environment_missing()
    if missing or not cli.exists():
        draft = rules_extract(view)
        draft["problems"] = [
            *draft.get("problems", []),
            "read by code only: the model extractor is not set up",
        ]
        return {"draft": check_draft(draft, view), "method": "rules", "cost_usd": 0.0, "run_id": None}
    with tempfile.TemporaryDirectory() as tmp:
        images = []
        for index, (media, data) in enumerate(view.images, start=1):
            path = Path(tmp) / f"page-{index}.png"
            path.write_bytes(data)
            images.append({"path": str(path), "media_type": media})
        document = {"kind": view.kind, "file_name": file_name, "pages": view.pages}
        payload = {"request": request, "document": document,
                   "text": view.text, "images": images}  # fmt: skip
        (Path(tmp) / "input.json").write_text(json.dumps(payload), encoding="utf-8")
        command = ["node", str(cli), "--input", str(Path(tmp) / "input.json"),
                   "--runs-dir", str(settings.runs_dir / "agents"), "--json"]  # fmt: skip
        env = {**os.environ, "NODE_USE_ENV_PROXY": "1", "NODE_NO_WARNINGS": "1"}
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True, env=env, timeout=240, check=False
            )
            run = json.loads(completed.stdout.strip().splitlines()[-1]) if completed.stdout.strip() else {}
        except (subprocess.TimeoutExpired, json.JSONDecodeError) as error:
            run = {"status": "error", "error": str(error)}
    draft = run.get("extraction") or {
        "options": [],
        "instructions_found": [],
        "problems": [f"the extractor failed: {run.get('error') or completed.stderr.strip()[-300:]}"],
    }
    return {
        "draft": check_draft(draft, view),
        "method": str(run.get("model") or "model").removeprefix("anthropic/"),
        "cost_usd": float((run.get("usage") or {}).get("cost_usd") or 0.0),
        "run_id": run.get("run_id"),
    }
