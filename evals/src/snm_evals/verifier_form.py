"""Which form the planner's recommendation check takes (change card
``evals/failures/change-20261101-verifier-form.md``).

The cases and scripted supplier answers of the planner-evaluate suite (the sampling of
``verifier-mutations-v1``). The planner (prompt v5) evaluates every case once per arm, each arm from its own
copy of the same store:

- ``none`` (A): no check;
- ``deterministic`` (B): code only, the figure check and the facts check of ``harness/src/agent/checks.ts``;
- ``self_check`` (C): the ``check_recommendation`` tool and its skill text, required before the write;
- ``subagent`` (D): the verifier sub-agent.

Every form runs on every recommendation (``--verify always``), so the arms differ by form only; the triggers
that would have fired in production are recorded. The final recommendation of every arm is then audited by the
verifier's checks on another model tier than arm D's verifier (``auditor_model``), with the deterministic
findings alongside; every auditor fail is listed for a person to read.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from snm_cases.planning import LocalPlanningData
from snm_cases.service import Actor, CaseService
from snm_cases.store import LocalCaseStore

from .planner_evaluate import CASE_CLI, prepare_store, sample_cases
from .verifier_mutations import run_verify

ARMS: dict[str, list[str]] = {
    "none": [],
    "deterministic": ["--verify", "always", "--verify-form", "deterministic"],
    "self_check": ["--verify", "always", "--verify-form", "self_check"],
    "subagent": ["--verify", "always", "--verify-form", "subagent"],
    # The production setting (docs/TECHNICAL.md §7): only when the recommendation departs from purchasing's
    # order, misses its service target, or the case is worth 10,000 USD or more.
    "subagent_triggered": ["--verify", "triggered", "--verify-form", "subagent"],
}
PLANNER_ACTOR = Actor("planner_agent", "purchasing")
LABELS = {
    "none": "A none",
    "deterministic": "B deterministic",
    "self_check": "C self_check",
    "subagent": "D subagent",
    "subagent_triggered": "D subagent, triggered",
}


def run_planner(
    case_id: str, arm: str, store: Path, planning: Path, runs: Path, version: str = "v6"
) -> dict[str, Any]:
    command = [
        "node", str(CASE_CLI), "--json", "--case", case_id, "--task", "evaluate", "--role", "purchasing",
        "--as-of", "2026-09-21", "--store", str(store), "--planning", str(planning),
        "--runs-dir", str(runs), "--prompt-version", version, *ARMS[arm],
    ]  # fmt: skip
    env = {**os.environ, "NODE_USE_ENV_PROXY": "1", "NODE_NO_WARNINGS": "1"}
    completed = subprocess.run(command, capture_output=True, text=True, env=env, timeout=600, check=False)
    lines = [line for line in completed.stdout.splitlines() if line.startswith("{")]
    return json.loads(lines[-1]) if lines else {"status": "error", "error": completed.stderr[-1000:]}


def final_recommendation(service: CaseService, case_id: str) -> tuple[dict[str, Any] | None, str | None]:
    """The planner's recommendation event as the verifier reads it, or the note it wrote instead."""
    events = [e for e in service.store.events(case_id) if e.actor_kind == "planner_agent"]
    for event in events:
        if event.event_type == "recommendation_proposed":
            payload = event.payload
            rec = {
                "case_id": case_id,
                "options": [
                    {"option": o["option"], "evaluation_id": o["evaluation_id"], "rank": o["rank"]}
                    for o in payload["options"]
                ],
                "recommended": payload["recommended"],
                "rationale_text": payload["rationale_text"],
            }
            return rec, None
    notes = [e for e in events if e.event_type == "note_added"]
    return None, (str(notes[-1].payload.get("text", "")) if notes else None)


def _check_summary(result: dict[str, Any]) -> dict[str, Any]:
    reports = result.get("verifications") or []
    verdicts = [(r.get("verdict") or {}).get("verdict") for r in reports]
    triggers = sorted({t for r in reports for t in r.get("triggered_by", []) if t != "always"})
    return {
        "checks": len([r for r in reports if r.get("status") in ("verified", "checked")]),
        "check_fails": verdicts.count("fail"),
        "would_trigger": triggers,
        "protocol_refusals": result.get("protocol_refusals", 0),
        "self_check_flags": sum(
            1
            for r in reports
            if r.get("status") == "checked"
            and (
                (r.get("findings") or {}).get("target_unsaid")
                or (r.get("findings") or {}).get("fields_unsaid")
                or r.get("untraced_figures")
            )
        ),
    }


def run_form_suite(
    planning_dir: Path,
    cost_parameter: Path,
    out_dir: Path,
    auditor_model: str,
    arms: list[str],
    per_mix: int = 3,
    parallel: int = 4,
    max_usd: float = 5.0,
    versions: list[str] | None = None,
    repetitions: int = 1,
) -> tuple[list[dict[str, Any]], float]:
    """Every arm and planner prompt version, ``repetitions`` times, each from its own copy of the store."""
    versions = versions or ["v6"]
    if out_dir.exists():
        shutil.rmtree(out_dir)
    base = out_dir / "base-store"
    service = CaseService(LocalCaseStore(base), LocalPlanningData(planning_dir, cost_parameter))
    service.sync_engine_run("verifier-form")
    cases = sample_cases(service, per_mix)
    prepare_store(service, cases)
    # One condition per arm, version, and repetition; the first run of v5 keeps the first pass's names.
    conditions = [
        (
            arm,
            version,
            rep,
            arm if (len(versions), repetitions) == (1, 1) else f"{arm}-{version}-r{rep}",
        )
        for arm in arms
        for version in versions
        for rep in range(1, repetitions + 1)
    ]
    for *_, key in conditions:
        shutil.copytree(base, out_dir / f"store-{key}")
    jobs = [(case_id, mix, condition) for case_id, mix in cases for condition in conditions]
    spent = 0.0
    planned: dict[tuple[str, str], dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futures = {
            pool.submit(
                run_planner, c, arm, out_dir / f"store-{key}", planning_dir, out_dir / f"runs-{key}", version
            ): (c, key)
            for c, _m, (arm, version, _rep, key) in jobs
        }
        for future, done in futures.items():
            result = future.result()
            planned[done] = result
            spent += (result.get("usage") or {}).get("cost_usd", 0.0)
            if spent >= max_usd:
                for other in futures:
                    other.cancel()
                break
    work = out_dir / "audit"
    work.mkdir(parents=True)
    audit_env = {"SNM_MODEL_VERIFIER": auditor_model, "SNM_EFFORT_VERIFIER": "high"}
    rows: list[dict[str, Any]] = []
    audits: dict[tuple[str, str], Any] = {}
    previous = {name: os.environ.get(name) for name in audit_env}
    os.environ.update(audit_env)
    try:
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            for case_id, _mix, (_arm, _version, _rep, key) in jobs:
                if (case_id, key) not in planned:
                    continue
                store = out_dir / f"store-{key}"
                scored = CaseService(LocalCaseStore(store), LocalPlanningData(planning_dir, cost_parameter))
                rec, _note = final_recommendation(scored, case_id)
                if rec is not None:
                    audits[(case_id, key)] = pool.submit(
                        run_verify, case_id, key, rec, store, planning_dir, work
                    )
            audited = {done: future.result() for done, future in audits.items()}
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    for case_id, mix, (arm, version, rep, key) in jobs:
        result = planned.get((case_id, key))
        if result is None:
            continue
        store = out_dir / f"store-{key}"
        scored = CaseService(LocalCaseStore(store), LocalPlanningData(planning_dir, cost_parameter))
        rec, note = final_recommendation(scored, case_id)
        ranking = scored.rank_options(PLANNER_ACTOR, case_id)
        rule = [r["option"] for r in ranking["ranked"]]
        submitted = (
            [
                o["option"]
                for o in sorted(rec["options"], key=lambda o: o["rank"])
                if o["option"].startswith("P-")
            ]
            if rec
            else None
        )
        audit = audited.get((case_id, key))
        findings = (audit or {}).get("findings") or {}
        audit_cost = (audit or {}).get("cost_usd", 0.0)
        spent += audit_cost
        rows.append(
            {
                "case_id": case_id,
                "mix": mix,
                "arm": arm,
                "version": version,
                "rep": rep,
                "group": LABELS[arm] if len(versions) == 1 else f"{LABELS[arm]}, {version}",
                "status": result.get("status"),
                "written": (result.get("written") or {}).get("tool"),
                "refused_writes": len(result.get("refused_writes") or []),
                **_check_summary(result),
                "rule_order": bool(rec) and submitted == [o for o in rule if o.startswith("P-")],
                "recommended": rec["recommended"] if rec else None,
                "recommended_first": bool(rec) and bool(rule) and rec["recommended"] == rule[0],
                "rationale_text": rec["rationale_text"] if rec else None,
                "note": note,
                "target_unsaid": bool(findings.get("target_unsaid")),
                "fields_unsaid": findings.get("fields_unsaid", []),
                "untraced_figures": (audit or {}).get("untraced_figures", []),
                "audit_verdict": (audit or {}).get("verdict"),
                "audit_discrepancies": (audit or {}).get("discrepancies", []),
                "audit_cost_usd": audit_cost,
                "cost_usd": round((result.get("usage") or {}).get("cost_usd", 0.0), 4),
                "turns": result.get("turns"),
                "wall_clock_s": result.get("wall_clock_s"),
                "error": result.get("error") or (audit or {}).get("error"),
            }
        )
    (out_dir / "results.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    (out_dir / "spent.json").write_text(json.dumps({"usd": round(spent, 4)}), encoding="utf-8")
    return rows, spent


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _group(row: dict[str, Any]) -> str:
    return str(row.get("group") or LABELS[row["arm"]])


def render(rows: list[dict[str, Any]], spent: float, auditor_model: str) -> str:
    groups = list(dict.fromkeys(_group(row) for row in rows))
    versions = sorted({str(row.get("version", "v5")) for row in rows})
    reps = max(int(row.get("rep", 1)) for row in rows) if rows else 1
    if len(versions) > 1:
        head = [
            f"# Planner prompt versions {' against '.join(reversed(versions))}",
            "",
            "Change card [`change-20261030-planner-recommendation-facts`]"
            "(../failures/change-20261030-planner-recommendation-facts.md). The planner-evaluate cases and "
            "scripted supplier answers (the sampling of `verifier-mutations-v1`); each version evaluates "
            f"every case {reps} times, each run from its own copy of the same store state. Every final "
            f"recommendation is audited by the verifier's checks (`{auditor_model}`), next to the "
            "deterministic findings (a missed service target or mandate field left unsaid; figures found in "
            "no evaluation).",
        ]
    else:
        head = [
            "# Planner check forms: none, deterministic, self-check, sub-agent",
            "",
            "Change card [`change-20261101-verifier-form`](../failures/change-20261101-verifier-form.md). "
            "The planner-evaluate cases and scripted supplier answers (the sampling of "
            f"`verifier-mutations-v1`); the planner (prompt {versions[0]}) evaluates each case once per arm "
            "from the same store state, every form on every recommendation (`--verify always`). The final "
            "recommendation of each arm is audited by the verifier's checks on another model tier than arm "
            "D's verifier "
            f"(`{auditor_model}`), next to the "
            "deterministic findings (a missed service target or mandate field left unsaid; figures found in "
            "no evaluation).",
        ]
    lines = [
        *head,
        "",
        "| Arm | Runs | Written | Rule order | Target unsaid | Fields unsaid | Untraced figures "
        "| Auditor fail | Checks / fails | Refused writes | Mean cost | Mean turns | Mean latency |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name in groups:
        group = [row for row in rows if _group(row) == name]
        recs = [row for row in group if row["recommended"]]
        lines.append(
            f"| {name} | {len(group)} | {len(recs)} "
            f"| {sum(row['rule_order'] for row in group)}/{len(group)} "
            f"| {sum(row['target_unsaid'] for row in recs)} "
            f"| {sum(bool(row['fields_unsaid']) for row in recs)} "
            f"| {sum(bool(row['untraced_figures']) for row in recs)} "
            f"| {sum(row['audit_verdict'] == 'fail' for row in recs)}/{len(recs)} "
            f"| {sum(row['checks'] for row in group)} / {sum(row['check_fails'] for row in group)} "
            f"| {sum(row['refused_writes'] for row in group)} "
            f"| ${_mean([row['cost_usd'] for row in group]):.3f} "
            f"| {_mean([float(row['turns'] or 0) for row in group]):.1f} "
            f"| {_mean([float(row['wall_clock_s'] or 0) for row in group]):.0f}s |"
        )
    lines += [
        "",
        "Cost is the planner run with its check (arm D includes the verifier); the audit is not in it. "
        f"Spent in all, audit included: ${spent:.2f}.",
        "",
        "## Per case",
        "",
        "Auditor passes over runs, then the deterministic findings counted over runs.",
        "",
        "| Case | Mix | " + " | ".join(groups) + " |",
        "|---|---|" + "---|" * len(groups),
    ]
    cases = sorted({(row["case_id"], row["mix"]) for row in rows})
    for case_id, mix in cases:
        cells = []
        for name in groups:
            runs = [r for r in rows if r["case_id"] == case_id and _group(r) == name]
            if not runs:
                cells.append("-")
                continue
            recs = [r for r in runs if r["recommended"]]
            cell = f"audit pass {sum(r['audit_verdict'] == 'pass' for r in recs)}/{len(runs)}"
            marks = [
                f"target unsaid {n}" if (n := sum(r["target_unsaid"] for r in recs)) else "",
                f"fields unsaid {n}" if (n := sum(bool(r["fields_unsaid"]) for r in recs)) else "",
                f"untraced {n}" if (n := sum(bool(r["untraced_figures"]) for r in recs)) else "",
                f"no recommendation {len(runs) - len(recs)}" if len(recs) < len(runs) else "",
            ]
            tail = "; ".join(mark for mark in marks if mark)
            cells.append(cell + (f"; {tail}" if tail else ""))
        lines.append(f"| {case_id} | {mix} | " + " | ".join(cells) + " |")
    lines += ["", "## Auditor fails and notes (for a person to read)", ""]
    for row in rows:
        if row["audit_verdict"] == "fail" or not row["recommended"]:
            rep = f", run {row['rep']}" if reps > 1 else ""
            lines.append(f"- **{row['case_id']}, {_group(row)}{rep}**:")
            if row["recommended"]:
                lines.append(f"  - Rationale: {row['rationale_text']}")
                for item in row["audit_discrepancies"]:
                    lines.append(f"  - {item.get('claim')}: {item.get('evidence')}")
            else:
                lines.append(f"  - Status {row['status']}; note: {row['note']}")
    return "\n".join(lines) + "\n"
