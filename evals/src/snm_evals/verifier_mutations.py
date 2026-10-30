"""Verifier mutation testing (docs/TECHNICAL.md §7).

The cases and scripted supplier answers of the planner-evaluate suite; the planner (current prompt, verifier
off) writes one real recommendation per case. The verifier then checks that recommendation and up to five
mutated copies built from the same evaluations, each with exactly one defect:

- ``figure``: one percentage (else one cost) of the planner's own rationale shifted;
- ``mandate_claim``: a proposal that misses mandate fields recommended as "inside the mandate";
- ``cheapest_claim``: the dearest proposal recommended as the lowest expected total cost;
- ``target_claim``: a proposal that misses its service target recommended as meeting it;
- ``omission``: a proposal that misses mandate fields recommended without saying so.

Every mutation should fail. A fail on an original is a false alarm unless its discrepancy is real, so the
report lists those for a person to read. All figures in the claims are the evaluated ones, so only the claims
are wrong.
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

from snm_cases.planning import LocalPlanningData
from snm_cases.service import Actor, CaseService
from snm_cases.store import LocalCaseStore

from .planner_evaluate import prepare_store, sample_cases
from .planner_evaluate import run_case as run_planner
from .run import HARNESS

VERIFY_CLI = HARNESS / "dist" / "cli" / "verify.js"
PLANNER = Actor("planner_agent", "purchasing")
MUTATIONS = ("figure", "mandate_claim", "cheapest_claim", "target_claim", "omission")


def _short(option: str) -> str:
    """P-1-8-C-US10-... -> P-1-8 (the case page's short form)."""
    match = re.match(r"(P-\d+-\d+)", option)
    return match.group(1) if match else option


def _total(row: dict[str, Any]) -> float | None:
    value = row.get("expected_total_cost", row.get("total_cost"))
    return float(value) if value is not None else None


def mutations(rec: dict[str, Any], ranking: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The applicable mutations of one recommendation, each with exactly one false claim."""
    proposals = [row for row in ranking["ranked"] if str(row["option"]).startswith("P-")]
    out: dict[str, dict[str, Any]] = {}
    text = rec["rationale_text"]
    match = re.search(r"(\d+(?:\.\d+)?)%", text) or re.search(r"(\d+\.\d{2})(?= USD)", text)
    if match:
        shifted = float(match.group(1)) + (3.0 if match.group(0).endswith("%") else 25.0)
        out["figure"] = {
            **rec,
            "rationale_text": f"{text[: match.start(1)]}{shifted:.2f}{text[match.end(1) :]}",
        }

    def claim(row: dict[str, Any], sentence: str) -> dict[str, Any]:
        return {**rec, "recommended": row["option"], "rationale_text": sentence}

    def label(row: dict[str, Any]) -> str:
        return f"{_short(str(row['option']))} ({row.get('kind')}, arrival {row.get('coverage_date')})"

    missed = [row for row in proposals if row.get("missed")]
    if missed:
        row = missed[0]
        out["mandate_claim"] = claim(
            row,
            f"Recommend {label(row)}: it is inside the mandate, with stock-out risk "
            f"{row.get('stockout_risk')} and expected total cost {_total(row)} USD.",
        )
        out["omission"] = claim(
            row,
            f"Recommend {label(row)}: it covers the gap at a total cost of {row.get('total_cost')} USD "
            f"and an expected total cost of {_total(row)} USD.",
        )
    priced = [row for row in proposals if _total(row) is not None]
    if len(priced) >= 2:
        dear = max(priced, key=lambda row: _total(row) or 0.0)
        if (_total(dear) or 0.0) > min(_total(row) or 0.0 for row in priced):
            out["cheapest_claim"] = claim(
                dear,
                f"Recommend {label(dear)}: it has the lowest expected total cost of the proposals "
                f"({_total(dear)} USD).",
            )
    short = [row for row in proposals if row.get("meets_service_target") is False]
    if short:
        row = short[0]
        out["target_claim"] = claim(
            row,
            f"Recommend {label(row)}: its stock-out risk of {row.get('stockout_risk')} meets the service "
            f"target, at an expected total cost of {_total(row)} USD.",
        )
    return out


def run_verify(
    case_id: str, variant: str, rec: dict[str, Any], store: Path, planning: Path, work: Path
) -> dict:
    path = work / f"{case_id}-{variant}.json"
    path.write_text(json.dumps(rec), encoding="utf-8")
    command = [
        "node", str(VERIFY_CLI), "--json", "--case", case_id, "--store", str(store),
        "--planning", str(planning), "--recommendation", str(path),
    ]  # fmt: skip
    env = {**os.environ, "NODE_USE_ENV_PROXY": "1", "NODE_NO_WARNINGS": "1"}
    completed = subprocess.run(command, capture_output=True, text=True, env=env, timeout=300, check=False)
    lines = [line for line in completed.stdout.splitlines() if line.startswith("{")]
    result = json.loads(lines[-1]) if lines else {"error": completed.stderr[-1000:]}
    verdict = (result.get("verdict") or {}).get("verdict")
    return {
        "case_id": case_id,
        "variant": variant,
        "verdict": verdict,
        "discrepancies": (result.get("verdict") or {}).get("discrepancies", []),
        "untraced_figures": result.get("untraced_figures", []),
        "cost_usd": round((result.get("usage") or {}).get("cost_usd", 0.0), 4),
        "error": result.get("error"),
    }


def run_mutation_suite(
    planning_dir: Path,
    cost_parameter: Path,
    out_dir: Path,
    per_mix: int = 3,
    parallel: int = 3,
    max_usd: float = 5.0,
) -> list[dict[str, Any]]:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    base = out_dir / "base-store"
    service = CaseService(LocalCaseStore(base), LocalPlanningData(planning_dir, cost_parameter))
    service.sync_engine_run("verifier-mutations")
    cases = sample_cases(service, per_mix)
    prepare_store(service, cases)
    store = out_dir / "store"
    shutil.copytree(base, store)
    runs = out_dir / "planner-runs"
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        planned = list(pool.map(lambda c: run_planner(c[0], "v5", store, planning_dir, runs), cases))
    spent = sum((p.get("usage") or {}).get("cost_usd", 0.0) for p in planned)
    scored = CaseService(LocalCaseStore(store), LocalPlanningData(planning_dir, cost_parameter))
    work = out_dir / "recommendations"
    work.mkdir(parents=True)
    jobs: list[tuple[str, str, dict[str, Any]]] = []
    for case_id, _mix in cases:
        events = [e for e in scored.store.events(case_id) if e.event_type == "recommendation_proposed"]
        if not events:
            continue
        payload = events[-1].payload
        rec = {
            "case_id": case_id,
            "options": [
                {"option": o["option"], "evaluation_id": o["evaluation_id"], "rank": o["rank"]}
                for o in payload["options"]
            ],
            "recommended": payload["recommended"],
            "rationale_text": payload["rationale_text"],
        }
        ranking = scored.rank_options(PLANNER, case_id)
        jobs.append((case_id, "original", rec))
        jobs += [(case_id, name, variant) for name, variant in mutations(rec, ranking).items()]
    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        pending = []
        for case_id, variant, rec in jobs:
            pending.append(pool.submit(run_verify, case_id, variant, rec, store, planning_dir, work))
        for future in pending:
            row = future.result()
            spent += row["cost_usd"]
            rows.append(row)
            if spent >= max_usd:
                for other in pending:
                    other.cancel()
                break
    (out_dir / "results.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    (out_dir / "spent.json").write_text(json.dumps({"usd": round(spent, 4)}), encoding="utf-8")
    return rows


def render(rows: list[dict[str, Any]], spent: float) -> str:
    lines = [
        "# Verifier evaluation: mutation testing",
        "",
        "Cases and supplier answers of the planner-evaluate suite; one real recommendation per case from "
        "the planner (prompt v5, verifier off), then the verifier on it and on mutated copies with one false "
        "claim each (`evals/src/snm_evals/verifier_mutations.py`). A mutation is caught when the verdict is "
        "`fail`; a `fail` on an original is listed for review (a false alarm or a real defect of the "
        "planner's text).",
        "",
        "| Variant | Runs | fail | uncertain | pass | no verdict |",
        "|---|---|---|---|---|---|",
    ]
    for variant in ("original", *MUTATIONS):
        subset = [row for row in rows if row["variant"] == variant]
        if not subset:
            continue
        count = {v: sum(row["verdict"] == v for row in subset) for v in ("fail", "uncertain", "pass")}
        missing = sum(row["verdict"] is None for row in subset)
        cells = f"{count['fail']} | {count['uncertain']} | {count['pass']} | {missing}"
        lines.append(f"| {variant} | {len(subset)} | {cells} |")
    mutated = [row for row in rows if row["variant"] != "original"]
    caught = sum(row["verdict"] == "fail" for row in mutated)
    originals = [row for row in rows if row["variant"] == "original"]
    alarms = [row for row in originals if row["verdict"] == "fail"]
    lines += [
        "",
        f"Mutations caught: {caught}/{len(mutated)}. Originals failed: {len(alarms)}/{len(originals)}. "
        f"Spent: USD {spent:.2f} (planner and verifier).",
    ]
    if alarms:
        lines += ["", "## Originals the verifier failed (to review)", ""]
        for row in alarms:
            items = "; ".join(f"{d['claim']}: {d['evidence']}" for d in row["discrepancies"])
            lines.append(f"- {row['case_id']}: {items}")
    missed = [row for row in mutated if row["verdict"] != "fail"]
    if missed:
        lines += ["", "## Mutations not caught", ""]
        lines += [
            f"- {row['case_id']} `{row['variant']}`: {row['verdict'] or row['error']}" for row in missed
        ]
    return "\n".join(lines) + "\n"
