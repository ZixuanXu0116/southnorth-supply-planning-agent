"""Batch runs: execute suite cases through the TypeScript agent CLI, then score and report.

The agent process receives only the question, role, and as-of date; reference answers never leave
this Python process. Batches are resumable: a (case, arm, repetition) already in results.jsonl is
not run again.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .cases import Case, Suite, load_answers
from .score import CaseScore, score_case
from .stats import compare_arms, consistency

REPO_ROOT = Path(__file__).resolve().parents[3]
HARNESS = REPO_ROOT / "harness"
CLI = HARNESS / "dist" / "cli" / "ask.js"
RUNS_ROOT = REPO_ROOT / "runs" / "evals"
ARMS = ("main", "main_v2", "main_v1", "baseline", "main_bare", "baseline_bare")
# Comparisons reported when both arms are present: (treatment, control, what the difference measures).
COMPARISONS = (
    ("main", "main_v2", "governed metric views and query_metric (prompt v4 vs v2)"),
    ("main_v2", "main_v1", "task card + numeric faithfulness gate (prompt v2 vs v1)"),
    ("main", "baseline_bare", "full stack vs naive (knowledge base, prompt, and metadata documentation)"),
    ("main", "baseline", "knowledge base + governed prompt, documented metadata"),
    ("main_bare", "baseline_bare", "knowledge base + governed prompt, bare metadata"),
    ("baseline", "baseline_bare", "metadata documentation, no knowledge base"),
    ("main", "main_bare", "metadata documentation, with knowledge base"),
)


def build_harness() -> None:
    subprocess.run(["npx", "tsc", "-p", "tsconfig.build.json"], cwd=HARNESS, check=True)


def main_model(env_overrides: dict[str, str] | None = None) -> str:
    """The main-role model a run will use, as the harness reports it (``provider/model``)."""
    env = {**os.environ, **(env_overrides or {})}
    return f"{env.get('SNM_MODEL_PROVIDER') or 'anthropic'}/{env.get('SNM_MODEL_MAIN', '')}"


def run_case(
    case: Case,
    suite: Suite,
    arm: str,
    rep: int,
    batch_dir: Path,
    timeout_s: int = 420,
    env_overrides: dict[str, str] | None = None,
    data_snapshot: dict[str, str] | None = None,
) -> dict[str, Any]:
    run_id = f"{case.id}-{arm}-r{rep}"
    command = [
        "node", str(CLI), "--json", "--arm", arm, "--role", case.role, "--as-of", suite.as_of,
        "--runs-dir", str(batch_dir / "runs"), "--run-id", run_id,
    ]  # fmt: skip
    if data_snapshot:
        command += ["--data-snapshot", json.dumps(data_snapshot, sort_keys=True)]
    for turn in case.turns:
        command += ["--earlier", turn.question]
    command.append(case.question)
    env = {**os.environ, **(env_overrides or {}), "NODE_USE_ENV_PROXY": "1", "NODE_NO_WARNINGS": "1"}
    timeout_s *= 1 + len(case.turns)  # budgets apply per question of a thread
    try:
        completed = subprocess.run(command, capture_output=True, text=True, env=env, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return {"run_id": run_id, "arm": arm, "status": "error", "error": "timeout"}
    lines = [line for line in completed.stdout.splitlines() if line.startswith("{")]
    if not lines:
        return {"run_id": run_id, "arm": arm, "status": "error", "error": completed.stderr[-2000:]}
    return json.loads(lines[-1])


def load_results(batch_dir: Path) -> list[dict[str, Any]]:
    path = batch_dir / "results.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _drop_errors(batch_dir: Path, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kept = [result for result in results if result.get("status") != "error"]
    if len(kept) == len(results):
        return results
    for result in results:
        if result.get("status") == "error":
            shutil.rmtree(batch_dir / "runs" / result["run_id"], ignore_errors=True)
    (batch_dir / "results.jsonl").write_text(
        "".join(json.dumps(result) + "\n" for result in kept), encoding="utf-8"
    )
    print(f"retrying {len(results) - len(kept)} errored runs", file=sys.stderr, flush=True)
    return kept


def run_batch(
    suite: Suite,
    arms: list[str],
    case_ids: list[str] | None,
    parallel: int,
    batch: str | None,
    repeats: int = 1,
    max_usd: float | None = None,
    env_overrides: dict[str, str] | None = None,
    data_snapshot: dict[str, str] | None = None,
    retry_errors: bool = False,
) -> Path:
    """Run every (case, arm, repetition) of the batch that has no result yet and append the results.

    ``retry_errors`` drops the batch's errored runs (an API outage, an exhausted credit balance, a timeout)
    so that they run again; their earlier run directories are removed.
    """
    batch_dir = RUNS_ROOT / (batch or f"{datetime.now(UTC):%Y%m%dT%H%M%S}-{suite.suite}")
    batch_dir.mkdir(parents=True, exist_ok=True)
    existing = load_results(batch_dir)
    if retry_errors:
        existing = _drop_errors(batch_dir, existing)
    # Pass rates are paired within a batch, so a batch must not mix main models.
    model = main_model(env_overrides)
    others = {r["model"] for r in existing if r.get("model") and r["model"] != model}
    if others:
        raise ValueError(f"batch {batch_dir.name} used {sorted(others)}, not {model}; use another batch")
    done = {(r["case_id"], r.get("arm"), int(r.get("rep", 1))) for r in existing}
    cases = [case for case in suite.cases if not case_ids or case.id in case_ids]
    # Repetition-major order: every arm finishes repetition 1 before repetition 2 starts.
    jobs = [
        (case, arm, rep)
        for rep in range(1, repeats + 1)
        for arm in arms
        for case in cases
        if (case.id, arm, rep) not in done
    ]
    results_path = batch_dir / "results.jsonl"
    lock = threading.Lock()
    spent = [0.0]

    def work(job: tuple[Case, str, int]) -> None:
        case, arm, rep = job
        with lock:
            if max_usd is not None and spent[0] >= max_usd:
                return
        result = run_case(
            case, suite, arm, rep, batch_dir, env_overrides=env_overrides, data_snapshot=data_snapshot
        )
        result["case_id"] = case.id
        result["rep"] = rep
        cost = float((result.get("usage") or {}).get("cost_usd", 0.0))
        with lock:
            spent[0] += cost
            with results_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(result) + "\n")
            total = spent[0]
        answer = result.get("answer") or {}
        print(
            f"{case.id:8} {arm:13} r{rep} {result.get('status'):16} behavior={answer.get('behavior')} "
            f"cost=${cost:.3f} spent=${total:.2f}",
            file=sys.stderr,
            flush=True,
        )

    print(f"{len(jobs)} runs to do ({len(done)} already in the batch)", file=sys.stderr, flush=True)
    with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
        list(pool.map(work, jobs))
    if max_usd is not None and spent[0] >= max_usd:
        print(f"stopped launching runs at the ${max_usd:.2f} cap", file=sys.stderr, flush=True)
    return batch_dir


def score_batch(suite: Suite, batch_dir: Path) -> list[CaseScore]:
    answers = load_answers(suite)
    if answers["data_snapshot"] != suite.data_snapshot:
        raise ValueError("reference answers were computed for a different data snapshot")
    scores = []
    for result in load_results(batch_dir):
        case = suite.case(result["case_id"])
        score = score_case(case, answers["results"].get(case.id), result)
        score.rep = int(result.get("rep", 1))
        score.model = result.get("model", "")
        scores.append(score)
    scores.sort(key=lambda score: (score.arm, score.case_id, score.rep))
    (batch_dir / "scores.json").write_text(
        json.dumps([asdict(score) for score in scores], indent=2) + "\n", encoding="utf-8"
    )
    return scores


def _pct(numerator: float, denominator: int) -> str:
    return f"{numerator:g}/{denominator} ({100 * numerator / denominator:.0f}%)" if denominator else "-"


def _signed_pct(value: float) -> str:
    return f"{100 * value:+.1f} pp"


def _cache_read_share(rows: list[CaseScore]) -> str:
    """Share of prompt tokens read from the cache (COST_GUIDE.md section 5); "-" without token counts."""
    cached = sum(s.cache_read_tokens for s in rows)
    prompt = cached + sum(s.input_tokens + s.cache_write_tokens for s in rows)
    return f"{100 * cached / prompt:.0f}%" if prompt else "-"


def render_report(suite: Suite, scores: list[CaseScore], batch_name: str) -> str:
    arms = [arm for arm in ARMS if any(score.arm == arm for score in scores)]
    discovery = {case.id for case in suite.cases if case.discovery}
    passes: dict[tuple[str, str], list[bool]] = defaultdict(list)
    costs: dict[tuple[str, str], list[float]] = defaultdict(list)
    runs: dict[tuple[str, str], list[CaseScore]] = defaultdict(list)
    # The headline table and the paired comparisons leave discovery tasks out; the other tables list them.
    headline_passes: dict[tuple[str, str], list[bool]] = defaultdict(list)
    headline_costs: dict[tuple[str, str], list[float]] = defaultdict(list)
    for score in scores:
        passes[(score.case_id, score.arm)].append(score.passed)
        costs[(score.case_id, score.arm)].append(score.cost_usd)
        runs[(score.case_id, score.arm)].append(score)
        if score.case_id not in discovery:
            headline_passes[(score.case_id, score.arm)].append(score.passed)
            headline_costs[(score.case_id, score.arm)].append(score.cost_usd)

    lines = [
        f"# Evaluation report: {suite.suite} ({batch_name})",
        "",
        f"As-of date {suite.as_of}; data snapshot {suite.data_snapshot}. Deterministic scoring "
        "(behavior routing, value matching with tolerances, operator-identity leak check). Pass rates are "
        "over runs; repetitions of a case are paired across arms.",
        "",
        "| Arm | Model | Runs | Passed | Answering runs correct | Behavior correct | Consistent cases "
        "| Traced values | Total cost | Mean cost | Cache read | Mean turns | Mean SQL | Mean latency |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for arm in arms:
        rows = [score for score in scores if score.arm == arm and score.case_id not in discovery]
        answering = [score for score in rows if score.values_ok is not None]
        n = len(rows)
        agree, repeated = consistency(headline_passes, arm)
        models = ", ".join(sorted({s.model.split("/")[-1] for s in rows if s.model})) or "-"
        lines.append(
            f"| {arm} | {models} | {n} | {_pct(sum(s.passed for s in rows), n)} "
            f"| {_pct(sum(bool(s.values_ok) for s in answering), len(answering))} "
            f"| {_pct(sum(s.behavior_ok for s in rows), n)} | {f'{agree}/{repeated}' if repeated else '-'} "
            f"| {_pct(sum(s.values_traced for s in rows), sum(s.values_checked for s in rows))} "
            f"| ${sum(s.cost_usd for s in rows):.2f} | ${sum(s.cost_usd for s in rows) / n:.3f} "
            f"| {_cache_read_share(rows)} "
            f"| {sum(s.turns for s in rows) / n:.1f} | {sum(s.sql_statements for s in rows) / n:.1f} "
            f"| {sum(s.wall_clock_s for s in rows) / n:.0f}s |"
        )
    discovery_runs = [score for score in scores if score.case_id in discovery]
    if discovery_runs:
        cells = []
        for arm in arms:
            outcomes = [score.passed for score in discovery_runs if score.arm == arm]
            cells.append(f"{arm} {_pct(sum(outcomes), len(outcomes))}")
        lines += [
            "",
            f"Discovery tasks ({', '.join(sorted({s.case_id for s in discovery_runs}))}; the cause is "
            f"documented nowhere the agent can read) are outside the table above: {', '.join(cells)}.",
        ]

    comparisons = [
        (compare_arms(a, b, headline_passes, headline_costs), meaning)
        for a, b, meaning in COMPARISONS
        if a in arms and b in arms
    ]
    if comparisons:
        lines += [
            "",
            "## Paired comparisons",
            "",
            "Pass-rate and cost differences (A minus B) with 95 % case-level bootstrap intervals; "
            "McNemar exact test on per-case majority outcomes (cases only A passes / only B passes).",
            "",
            "| A vs B | Measures | Cases | Pass rate diff (95 % CI) | Only A / only B | McNemar p "
            "| Cost diff per question (95 % CI) |",
            "|---|---|---|---|---|---|---|",
        ]
        for comparison, meaning in comparisons:
            if comparison is None:
                continue
            lo, hi = comparison.pass_ci
            cost_lo, cost_hi = comparison.cost_ci
            lines.append(
                f"| {comparison.arm_a} vs {comparison.arm_b} | {meaning} | {comparison.cases} "
                f"| {_signed_pct(comparison.pass_diff)} ({_signed_pct(lo)}, {_signed_pct(hi)}) "
                f"| {comparison.a_only} / {comparison.b_only} | {comparison.mcnemar_p:.3f} "
                f"| ${comparison.cost_diff:+.3f} (${cost_lo:+.3f}, ${cost_hi:+.3f}) |"
            )

    threads = [score for score in scores if len(score.question_prompt_tokens) > 1]
    if threads:
        lines += [
            "",
            "## Context per question",
            "",
            "Prompt tokens of the first response to each question of a follow-up thread "
            "(decision record 006).",
            "",
            "| Arm | Question | Threads | Mean prompt tokens | Max prompt tokens |",
            "|---|---|---|---|---|",
        ]
        for arm in arms:
            arm_threads = [score.question_prompt_tokens for score in threads if score.arm == arm]
            for index in range(max((len(tokens) for tokens in arm_threads), default=0)):
                sizes = [tokens[index] for tokens in arm_threads if len(tokens) > index]
                mean = sum(sizes) / len(sizes)
                lines.append(f"| {arm} | {index + 1} | {len(sizes)} | {mean:,.0f} | {max(sizes):,} |")

    run_cases = [case for case in suite.cases if any((case.id, arm) in runs for arm in arms)]
    for title, attribute in (("category", "category"), ("split", "split")):
        lines += [
            "",
            f"## By {title}",
            "",
            f"| {title.capitalize()} | " + " | ".join(arms) + " |",
            "|---" * (len(arms) + 1) + "|",
        ]
        for group in sorted({getattr(case, attribute) for case in run_cases}):
            ids = [case.id for case in run_cases if getattr(case, attribute) == group]
            cells = []
            for arm in arms:
                outcomes = [outcome for i in ids for outcome in passes.get((i, arm), [])]
                cells.append(_pct(sum(outcomes), len(outcomes)))
            lines.append(f"| {group} | " + " | ".join(cells) + " |")

    lines += [
        "",
        "## Cases",
        "",
        "Passed runs / runs, mean cost, and the first failure reason.",
        "",
        "| Case | Category | " + " | ".join(arms) + " |",
        "|---" * (len(arms) + 2) + "|",
    ]
    for case in run_cases:
        cells = []
        for arm in arms:
            case_runs = runs.get((case.id, arm), [])
            if not case_runs:
                cells.append("-")
                continue
            passed = sum(score.passed for score in case_runs)
            mean_cost = sum(score.cost_usd for score in case_runs) / len(case_runs)
            cell = f"{passed}/{len(case_runs)} (${mean_cost:.2f})"
            failed = next((score for score in case_runs if not score.passed), None)
            if failed is not None:
                detail = []
                if not failed.behavior_ok:
                    detail.append(f"behavior={failed.behavior}")
                if failed.values_ok is False and failed.missing:
                    detail.append("missing " + ", ".join(failed.missing[:2]))
                if not failed.policy_ok:
                    detail.append("operator id leaked")
                if failed.status != "answered":
                    detail.append(failed.status)
                cell += " — " + "; ".join(detail).replace("|", "\\|")  # keep the table's columns intact
            cells.append(cell)
        lines.append(f"| {case.id} | {case.category} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"
