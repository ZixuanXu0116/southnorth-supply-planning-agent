"""Planner agent evaluation: the ``evaluate`` task (failure card roleplay-20261019, prompt v3 against v4).

Order cases of a local engine run get a request under the engine's default mandate and scripted supplier
answers in fixed mixes, in one scratch case store; the planner then evaluates every case once per prompt
version, each version on its own copy of that store. Checks, all deterministic: one recommendation event and
no refused write; the order the planner submitted for the proposals equals the purchasing rule's
(``server_ranking`` on the event: record 007's expected-cost order where the case's risk is priced, else rule
(a), inside the mandate first). Whether the recommended option is the rule's first is reported, not failed:
the v4 instructions allow another choice with a stated reason.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from snm_cases.planning import LocalPlanningData
from snm_cases.service import Actor, CaseService
from snm_cases.store import LocalCaseStore

from .planner import CASE_CLI

BUYER = Actor("human", "purchasing")
# The supplier answers per mix, in the order the supplier gives them (the rule's order differs on purpose).
MIXES = {
    "cheaper_late_split": ("outside_two", "late_split", "inside"),  # the role-play case
    "all_inside": ("inside_dear", "inside"),
    "with_decline": ("decline", "outside_two", "inside_dear"),
    "outside_only": ("outside_two", "late_split"),
}


def _option(kind: str, mandate: dict[str, Any], price: float, today: date, option_id: int) -> dict[str, Any]:
    """One scripted supplier option of the given kind, built against the case's mandate."""
    latest = date.fromisoformat(str(mandate["latest_arrival_date"]))
    allowed = next(k for k in mandate["allowed_kinds"] if k != "partial")
    quantity = float(mandate["quantity_max"])
    surcharge, freight = float(mandate["surcharge_pct_max"]), float(mandate["freight_max_usd"])
    valid = (today + timedelta(days=int(mandate["min_validity_days"]) + 4)).isoformat()
    arrival = latest - timedelta(days=5)
    base = {
        "option_id": option_id,
        "kind": allowed,
        "quantity": quantity,
        "ship_date": (arrival - timedelta(days=3)).isoformat(),
        "arrival_date": arrival.isoformat(),
        "surcharge_pct": round(surcharge / 2, 1),
        "freight_amount": round(freight / 2, 2),
        "valid_until": valid,
    }
    if kind == "inside":
        return {**base, "unit_price": round(price * 1.01, 2)}
    if kind == "inside_dear":
        earlier = (arrival - timedelta(days=2)).isoformat()
        return {**base, "unit_price": round(price * 1.06, 2), "arrival_date": earlier}
    if kind == "outside_two":  # an expedite above both ceilings: misses the surcharge and the freight
        return {
            **base,
            "unit_price": round(price * 1.01, 2),
            "surcharge_pct": surcharge + 8.0,
            "freight_amount": freight + 120.0,
            "ship_date": (arrival - timedelta(days=13)).isoformat(),
            "arrival_date": (arrival - timedelta(days=10)).isoformat(),
        }
    if kind == "late_split":  # cheaper, but less than the minimum quantity arrives by the latest date
        first = float(max(1, math.floor(float(mandate["quantity_min"]) / 2)))
        return {
            **base,
            "kind": "partial",
            "unit_price": round(price * 0.95, 2),
            "surcharge_pct": 0.0,
            "freight_amount": 0.0,
            "split_deliveries": [
                {"quantity": first, "arrival_date": arrival.isoformat()},
                {"quantity": quantity - first, "arrival_date": (latest + timedelta(days=7)).isoformat()},
            ],
        }
    return {"option_id": option_id, "kind": "decline", "conditions": "Cannot supply in this period."}


def sample_cases(service: CaseService, per_mix: int) -> list[tuple[str, str]]:
    """Order cases with a supplier and a gap of at least two pieces (so a split can miss the minimum), spread
    over the engine's levers; each mix gets ``per_mix`` of them."""
    rows = []
    for view in sorted(service.all_views(), key=lambda v: v.case_id):
        snap = view.snapshot
        if view.cleared or snap.get("condition") not in ("shortage", "warning") or not snap.get("vendor"):
            continue
        if float(snap.get("gap_qty") or 0.0) < 2 or view.state != "new":
            continue
        rows.append((str(snap.get("lever")), view.case_id))
    by_lever: dict[str, list[str]] = {}
    for lever, case_id in rows:
        by_lever.setdefault(lever, []).append(case_id)
    picks: list[str] = []
    while len(picks) < per_mix * len(MIXES) and any(by_lever.values()):
        for lever in sorted(by_lever):
            if by_lever[lever] and len(picks) < per_mix * len(MIXES):
                picks.append(by_lever[lever].pop(0))
    mixes = list(MIXES)
    return [(case_id, mixes[i % len(mixes)]) for i, case_id in enumerate(picks)]


def prepare_store(service: CaseService, cases: list[tuple[str, str]]) -> None:
    """Request under the default mandate, then the scripted supplier's answer, for every sampled case."""
    today = service.clock().date()
    for case_id, mix in cases:
        snap = service.view(case_id).snapshot
        request_type = "expedite" if snap["lever"] == "expedite" else "quote"
        text = service.default_request_text(case_id, request_type)
        service.draft_supplier_request(BUYER, case_id, request_type, text)
        defaults = service.mandate_defaults(BUYER, case_id)
        mandate = defaults["mandate"]
        service.send_request(BUYER, case_id, mandate, "engine_default", defaults["basis"])
        price = float(snap["value_usd"]) / float(snap["gap_qty"])
        options = [_option(kind, mandate, price, today, i) for i, kind in enumerate(MIXES[mix], start=1)]
        service.supplier_reply(str(snap["vendor"]), case_id, "Our options are below.", options)


def run_case(
    case_id: str, version: str, store_dir: Path, planning_dir: Path, runs_dir: Path
) -> dict[str, Any]:
    command = [
        "node", str(CASE_CLI), "--json", "--case", case_id, "--task", "evaluate", "--role", "purchasing",
        "--as-of", "2026-09-21", "--store", str(store_dir), "--planning", str(planning_dir),
        "--runs-dir", str(runs_dir), "--prompt-version", version,
    ]  # fmt: skip
    env = {**os.environ, "NODE_USE_ENV_PROXY": "1", "NODE_NO_WARNINGS": "1"}
    completed = subprocess.run(command, capture_output=True, text=True, env=env, timeout=420, check=False)
    lines = [line for line in completed.stdout.splitlines() if line.startswith("{")]
    return json.loads(lines[-1]) if lines else {"status": "error", "error": completed.stderr[-1000:]}


def score(
    case_id: str, mix: str, version: str, result: dict[str, Any], service: CaseService
) -> dict[str, Any]:
    events = [e for e in service.store.events(case_id) if e.actor_kind == "planner_agent"]
    proposed = events[0] if len(events) == 1 and events[0].event_type == "recommendation_proposed" else None
    rule = proposed.payload.get("server_ranking") if proposed else None
    submitted = None
    if proposed:
        ranked = sorted(proposed.payload["options"], key=lambda o: o["rank"])
        submitted = [o["option"] for o in ranked if o["option"].startswith("P-")]
    checks = {
        "one_event": result.get("status") == "written" and proposed is not None,
        "no_refusal": not result.get("refused_writes"),
        "rule_order": bool(rule) and submitted == rule,
    }
    return {
        "case_id": case_id,
        "mix": mix,
        "version": version,
        **checks,
        "passed": all(checks.values()),
        "recommended_first": bool(proposed and rule and proposed.payload["recommended"] == rule[0]),
        "recommended": proposed.payload["recommended"] if proposed else None,
        "cost_usd": round((result.get("usage") or {}).get("cost_usd", 0.0), 4),
        "turns": result.get("turns"),
        "error": result.get("error"),
    }


def run_evaluate_suite(
    planning_dir: Path,
    cost_parameter: Path,
    out_dir: Path,
    versions: list[str],
    per_mix: int = 3,
    parallel: int = 3,
) -> list[dict[str, Any]]:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    base = out_dir / "base-store"
    service = CaseService(LocalCaseStore(base), LocalPlanningData(planning_dir, cost_parameter))
    service.sync_engine_run("planner-evaluate")
    cases = sample_cases(service, per_mix)
    prepare_store(service, cases)
    rows: list[dict[str, Any]] = []
    for version in versions:
        store = out_dir / f"store-{version}"
        shutil.copytree(base, store)
        runs = out_dir / f"runs-{version}"
        jobs = [(case_id, version, store, planning_dir, runs) for case_id, _ in cases]
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            results = list(pool.map(lambda job: run_case(*job), jobs))
        scored = CaseService(LocalCaseStore(store), LocalPlanningData(planning_dir, cost_parameter))
        rows += [score(c, m, version, r, scored) for (c, m), r in zip(cases, results, strict=True)]
    (out_dir / "results.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return rows


def render(rows: list[dict[str, Any]]) -> str:
    versions = sorted({row["version"] for row in rows})
    lines = [
        "# Planner agent evaluation: evaluate task",
        "",
        "Failure card `evals/failures/roleplay-20261019-C-US10-40001795-SH-20261122.md`. Each case gets a "
        "request under the default mandate and a scripted supplier answer from one mix; every prompt version "
        "evaluates the same cases from the same store state. Passed = one recommendation event, no refused "
        "write, and the proposals submitted in the purchasing rule's order (`rank_options`).",
        "",
        "| Version | Passed | Rule order | Recommended the rule's first | Cost | Cost per case |",
        "|---|---|---|---|---|---|",
    ]
    for version in versions:
        subset = [row for row in rows if row["version"] == version]
        cost = sum(row["cost_usd"] for row in subset)
        n = max(len(subset), 1)
        lines.append(
            f"| {version} | {sum(r['passed'] for r in subset)}/{len(subset)} | "
            f"{sum(r['rule_order'] for r in subset)}/{len(subset)} | "
            f"{sum(r['recommended_first'] for r in subset)}/{len(subset)} | ${cost:.2f} | ${cost / n:.3f} |"
        )
    header = " | ".join(f"{v}: rule order / first" for v in versions)
    lines += ["", f"| Case | Mix | {header} |", "|---|---|" + "---|" * len(versions)]
    mark = {True: "yes", False: "no"}
    for case_id, mix in dict.fromkeys((row["case_id"], row["mix"]) for row in rows):
        cells = []
        for version in versions:
            row = next(r for r in rows if r["case_id"] == case_id and r["version"] == version)
            cells.append(f"{mark[row['rule_order']]} / {mark[row['recommended_first']]}")
        lines.append(f"| {case_id} | {mix} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"
