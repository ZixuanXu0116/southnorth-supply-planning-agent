"""Planner agent evaluation, first slice (layer L16, PROPOSAL §10.2): the ``prepare`` task.

A stratified sample of open cases from a local engine run is prepared by the planner agent in a scratch copy
of the case store. Every check is deterministic: the run writes exactly one case event; the event is the
action the engine's lever calls for (an expedite request for an open order, a quote request for a new or
expedited new order, a transfer for a pull from or a move to the overflow hub, a note for a hold); the case
server refused no write (an outward-field violation or an invalid payload counts as a refusal).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import polars as pl

from snm_cases.planning import LocalPlanningData
from snm_cases.service import Actor, CaseService
from snm_cases.store import LocalCaseStore

from .run import HARNESS

CASE_CLI = HARNESS / "dist" / "cli" / "case.js"
EVALUATION_ID = re.compile(r"E-[0-9a-f]{12}")
EXPECTED = {
    "expedite": ("draft_supplier_request", "expedite"),
    "new_order": ("draft_supplier_request", "quote"),
    "expedited_new_order": ("draft_supplier_request", "quote"),
    "transfer": ("prepare_transfer", None),
    "move_to_overflow": ("prepare_transfer", None),
    "hold": ("add_note", None),
}
ROLE_FOR = {
    "transfer": {"US10": "site_manager_rr", "CN10": "site_manager_cn"},
    "move_to_overflow": {"US10": "site_manager_rr", "CN10": "site_manager_cn"},
}


def sample_cases(planning_dir: Path, per_lever: int) -> pl.DataFrame:
    cases = pl.read_parquet(planning_dir / "curated" / "replenishment_case.parquet")
    picks = []
    for lever in EXPECTED:
        subset = cases.filter(pl.col("lever") == lever).sort(["triage_class", "trigger_date", "case_id"])
        picks.append(subset.head(per_lever))
    return pl.concat(picks)


def run_case(
    case: dict[str, Any],
    store_dir: Path,
    planning_dir: Path,
    runs_dir: Path,
    as_of: str,
    prompt_version: str = "v5",
) -> dict[str, Any]:
    role = ROLE_FOR.get(case["lever"], {}).get(case["plant"], "purchasing")
    command = [
        "node",
        str(CASE_CLI),
        "--json",
        "--case",
        case["case_id"],
        "--task",
        "prepare",
        "--role",
        role,
        "--as-of",
        as_of,
        "--store",
        str(store_dir),
        "--planning",
        str(planning_dir),
        "--runs-dir",
        str(runs_dir),
        "--prompt-version",
        prompt_version,
    ]
    env = {**os.environ, "NODE_USE_ENV_PROXY": "1", "NODE_NO_WARNINGS": "1"}
    completed = subprocess.run(command, capture_output=True, text=True, env=env, timeout=420)
    lines = [line for line in completed.stdout.splitlines() if line.startswith("{")]
    result = json.loads(lines[-1]) if lines else {"status": "error", "error": completed.stderr[-1000:]}
    return {"case": case, "role": role, "result": result}


def dominated_by_alternative(service: CaseService, case_id: str) -> bool:
    """Another engine lever covers on the same or an earlier date, with no more days below zero, for less."""
    buyer = Actor("human", "purchasing")
    snapshot = service.view(case_id).snapshot
    levers = [snapshot["lever"]] + [item["lever"] for item in snapshot.get("alternatives") or []]
    evaluations = {lever: service.evaluate_option(buyer, case_id, lever) for lever in levers}
    engine = evaluations[snapshot["lever"]]
    for lever, other in evaluations.items():
        if lever == snapshot["lever"] or not other["coverage_date"] or not engine["coverage_date"]:
            continue
        if (
            other["coverage_date"] <= engine["coverage_date"]
            and other["days_below_zero"] <= engine["days_below_zero"]
            and other["total_cost"] < engine["total_cost"]
        ):
            return True
    return False


def score(run: dict[str, Any], service: CaseService) -> dict[str, Any]:
    case, result = run["case"], run["result"]
    tool, request_type = EXPECTED[case["lever"]]
    written = result.get("written") or {}
    events = service.store.events(case["case_id"])
    agent_events = [e for e in events if e.actor_kind == "planner_agent"]
    checks = {
        "one_event": result.get("status") == "written" and len(agent_events) == 1,
        "right_action": written.get("tool") == tool,
        "no_refusal": not result.get("refused_writes"),
    }
    # A deviation: instead of the engine's action, a note that compares evaluated options (cites at least two
    # evaluation ids). It passes only when justified: another engine lever covers on the same or an earlier
    # date, with no more days below zero, for less (checked here, deterministically).
    note = (
        agent_events[0].payload.get("text", "")
        if agent_events and agent_events[0].event_type == "note_added"
        else ""
    )
    deviation = (
        written.get("tool") == "add_note"
        and tool != "add_note"
        and len(set(EVALUATION_ID.findall(note))) >= 2
    )
    justified = deviation and dominated_by_alternative(service, case["case_id"])
    checks["right_action"] = checks["right_action"] or justified
    if request_type and agent_events and agent_events[0].event_type == "request_drafted":
        checks["right_action"] = (
            checks["right_action"] and agent_events[0].facts["outward"]["request_type"] == request_type
        )
    return {
        "case_id": case["case_id"],
        "lever": case["lever"],
        "triage_class": case["triage_class"],
        "role": run["role"],
        "passed": all(checks.values()),
        "deviation": deviation,
        "justified_deviation": justified,
        **checks,
        "cost_usd": round(result.get("usage", {}).get("cost_usd", 0.0), 4),
        "turns": result.get("turns"),
        "error": result.get("error"),
    }


def run_prepare_suite(
    planning_dir: Path,
    cost_parameter: Path,
    out_dir: Path,
    per_lever: int = 3,
    parallel: int = 3,
    as_of: str = "2026-09-21",
    prompt_version: str = "v5",
) -> list[dict[str, Any]]:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    store_dir = out_dir / "store"
    service = CaseService(LocalCaseStore(store_dir), LocalPlanningData(planning_dir, cost_parameter))
    service.sync_engine_run("planner-eval")
    cases = sample_cases(planning_dir, per_lever).to_dicts()
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        runs = list(
            pool.map(
                lambda c: run_case(c, store_dir, planning_dir, out_dir / "runs", as_of, prompt_version), cases
            )
        )
    rows = [score(run, service) for run in runs]
    (out_dir / "results.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return rows


def render(rows: list[dict[str, Any]]) -> str:
    passed = sum(row["passed"] for row in rows)
    deviations = sum(row.get("deviation", False) for row in rows)
    justified = sum(row.get("justified_deviation", False) for row in rows)
    cost = sum(row["cost_usd"] for row in rows)
    lines = [
        "# Planner agent evaluation: prepare task",
        "",
        f"{passed}/{len(rows)} cases passed; total cost ${cost:.2f} "
        f"(${cost / max(len(rows), 1):.3f} per case); {deviations} notes instead of the engine's action, "
        f"{justified} of them justified (another lever covers as early, with no more days below zero, "
        "for less).",
        "",
        "| Case | Lever | Triage | Role | One event | Right action | No refusal | Cost | Turns |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        mark = {True: "yes", False: "no"}
        lines.append(
            f"| {row['case_id']} | {row['lever']} | {row['triage_class']} | {row['role']} | "
            f"{mark[row['one_event']]} | {mark[row['right_action']]} | {mark[row['no_refusal']]} | "
            f"${row['cost_usd']:.3f} | {row['turns']} |"
        )
    return "\n".join(lines) + "\n"
