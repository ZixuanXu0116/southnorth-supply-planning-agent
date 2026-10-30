"""The screen of a proposal (decision record 009, loop step 5): the affected dev cases on a candidate
knowledge base, paired against the source batch.

Cases: the target case three times; once each the dev cases of its category, the dev cases that require an
edited page, and the dev cases whose run in the source batch read an edited page (a prompt proposal: every
dev case of the source batch). Discovery tasks are left out, as in the headline. Model and effort are the
source batch's. The source batch is the control when its runs used the knowledge base the proposal was
made on and the prompt template, the role descriptions, and the reference answers have not changed since;
otherwise a control arm runs too.
"""

from __future__ import annotations

import subprocess
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..cases import Case, Suite
from ..run import REPO_ROOT, load_results, run_batch, score_batch
from ..score import CaseScore
from .excerpt import kb_pages_read, read_trace

SCREEN_MAX_USD = 4.0
TARGET_REPEATS = 3
# Files whose change between the source batch and now makes the rendered prompt differ.
PROMPT_INPUTS = ("harness/src/agent/roles.ts",)


@dataclass
class ScreenPlan:
    card_id: str
    batch: str
    target: str
    cases: list[str]
    model: str
    provider: str
    effort: str
    control_reused: bool
    control_problems: list[str]
    estimate_usd: float
    reasons: dict[str, list[str]] = field(default_factory=dict)
    target_repeats: int = TARGET_REPEATS

    @property
    def runs(self) -> int:
        return len(self.cases) + self.target_repeats - 1


def main_runs(results: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """The source batch's answered runs of the ``main`` arm, by case."""
    runs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        if result.get("arm") == "main" and result.get("status") != "error":
            runs[result["case_id"]].append(result)
    return runs


def select_cases(
    suite: Suite,
    target: Case,
    edited_pages: list[str],
    source_runs: dict[str, list[dict[str, Any]]],
    batch_dir: Path,
    prompt_change: bool = False,
) -> dict[str, list[str]]:
    """Case id -> why it is in the screen, for the dev cases the source batch ran (in suite order)."""
    pages = {page.removeprefix("kb/") for page in edited_pages}
    chosen: dict[str, list[str]] = {}
    for case in suite.cases:
        if case.split != "dev" or case.discovery or case.id not in source_runs:
            continue
        reasons = []
        if case.id == target.id:
            reasons.append("target")
        if prompt_change:
            reasons.append("prompt change: every dev case")
        if case.category == target.category:
            reasons.append(f"category {case.category}")
        if pages & set(case.required_kb_docs):
            reasons.append("requires an edited page")
        for result in source_runs[case.id]:
            trace = batch_dir / "runs" / result["run_id"] / "trace.jsonl"
            if trace.is_file() and pages & set(kb_pages_read(read_trace(trace))):
                reasons.append("read an edited page in the source batch")
                break
        if reasons:
            chosen[case.id] = reasons
    return chosen


def _changed_since(commits: set[str], paths: list[str]) -> list[str]:
    changed = []
    for commit in sorted(commits):
        completed = subprocess.run(
            ["git", "diff", "--quiet", commit, "HEAD", "--", *paths], cwd=REPO_ROOT, capture_output=True
        )
        if completed.returncode != 0:
            changed.append(commit[:7])
    return changed


def control_problems(
    runs: list[dict[str, Any]], base_kb_digest: str, prompt_template: str, kb_proposal: bool
) -> list[str]:
    """Why the source batch cannot serve as the control (empty: it can)."""
    problems = []
    manifests = [result.get("manifest") or {} for result in runs]
    digests = {manifest.get("kb_digest") for manifest in manifests}
    if digests != {base_kb_digest}:
        used = ", ".join(sorted(map(str, digests)))
        problems.append(f"the source runs used knowledge base {used}, the proposal {base_kb_digest}")
    commits = {manifest.get("git_commit") for manifest in manifests}
    if None in commits:
        problems.append("a source run has no commit in its manifest")
    elif kb_proposal:
        changed = _changed_since({str(c) for c in commits}, [prompt_template, *PROMPT_INPUTS])
        if changed:
            problems.append(f"the prompt template or role descriptions changed since {', '.join(changed)}")
    if len({(result.get("model"), result.get("effort")) for result in runs}) > 1:
        problems.append("the source runs mix models or efforts")
    return problems


def reference_changes(suite: Suite, answers: dict[str, Any], case_ids: list[str], backend: Any) -> list[str]:
    """Screen cases whose reference answers, recomputed on the loaded data, differ from the stored ones."""
    from ..refresh import compute_answers

    subset = suite.model_copy(update={"cases": [case for case in suite.cases if case.id in case_ids]})
    fresh = compute_answers(subset, backend)["results"]
    stored = answers.get("results") or {}
    return sorted(case for case in fresh if _normalized(fresh[case]) != _normalized(stored.get(case)))


def _normalized(references: Any) -> Any:
    """Reference rows as strings, so that 141723 and "141723.000" compare equal."""
    if not references:
        return references

    def cell(value: Any) -> str:
        try:
            return f"{float(value):.6g}"
        except (TypeError, ValueError):
            return str(value)

    return {
        name: {"columns": ref["columns"], "rows": [[cell(value) for value in row] for row in ref["rows"]]}
        for name, ref in references.items()
    }


def estimate(
    source_runs: dict[str, list[dict[str, Any]]], cases: list[str], target: str, repeats: int
) -> float:
    """Expected cost: each case's mean cost in the source batch, the target case ``repeats`` times."""

    def cost(case_id: str) -> float:
        runs = source_runs[case_id]
        return sum(float((run.get("usage") or {}).get("cost_usd") or 0.0) for run in runs) / len(runs)

    return sum(cost(case_id) for case_id in cases) + (repeats - 1) * cost(target)


def plan_screen(
    card_id: str,
    suite: Suite,
    target: Case,
    edited_pages: list[str],
    batch_dir: Path,
    base_kb_digest: str,
    prompt_change: bool = False,
) -> ScreenPlan:
    runs = main_runs(load_results(batch_dir))
    reasons = select_cases(suite, target, edited_pages, runs, batch_dir, prompt_change)
    cases = list(reasons)
    if target.id not in reasons:
        raise ValueError(f"the source batch has no answered run of {target.id}")
    selected = [run for case_id in cases for run in runs[case_id]]
    version = (selected[0].get("prompt") or {}).get("version", "v4")
    problems = control_problems(selected, base_kb_digest, f"prompts/main/{version}.md", not prompt_change)
    if prompt_change:
        problems.append("a prompt proposal runs its control arm on the current version")
    model = str(selected[0].get("model", ""))
    provider, _, model_id = model.rpartition("/")
    cost = estimate(runs, cases, target.id, TARGET_REPEATS)
    return ScreenPlan(
        card_id=card_id,
        batch=f"improve-{card_id}",
        target=target.id,
        cases=cases,
        model=model_id,
        provider=provider or "anthropic",
        effort=str(selected[0].get("effort") or ""),
        control_reused=not problems,
        control_problems=problems,
        estimate_usd=round(cost * (1 if not problems else 2), 2),
        reasons=reasons,
    )


Runner = Callable[..., Path]


def run_screen(
    plan: ScreenPlan,
    suite: Suite,
    candidate_kb: Path,
    data_snapshot: dict[str, Any] | None,
    max_usd: float = SCREEN_MAX_USD,
    parallel: int = 3,
    control_kb: Path | None = None,
    runner: Runner = run_batch,
) -> tuple[Path, Path | None]:
    """Run the candidate (and, when the source batch cannot serve, the control); return their batch dirs."""
    if plan.estimate_usd > max_usd:
        raise ValueError(f"the screen is estimated at ${plan.estimate_usd:.2f}, above the ${max_usd:.2f} cap")
    arms = [(plan.batch, candidate_kb)]
    if not plan.control_reused:
        if control_kb is None:
            raise ValueError(
                "the source batch cannot be the control, and no control knowledge base was given"
            )
        arms.append((f"{plan.batch}-control", control_kb))
    dirs = []
    for batch, kb in arms:
        overrides = {
            "SNM_KB_ROOT": str(kb),
            "SNM_MODEL_MAIN": plan.model,
            "SNM_MODEL_PROVIDER": plan.provider,
            "SNM_EFFORT_MAIN": plan.effort,
        }
        share = max_usd / len(arms)
        batch_dir = runner(
            suite, ["main"], plan.cases, parallel, batch, repeats=1, max_usd=share,
            env_overrides=overrides, data_snapshot=data_snapshot,
        )  # fmt: skip
        spent = spent_usd(batch_dir)
        runner(
            suite, ["main"], [plan.target], parallel, batch, repeats=plan.target_repeats,
            max_usd=max(0.0, share - spent), env_overrides=overrides, data_snapshot=data_snapshot,
        )  # fmt: skip
        dirs.append(batch_dir)
    return dirs[0], dirs[1] if len(dirs) > 1 else None


def spent_usd(batch_dir: Path) -> float:
    return sum(float((run.get("usage") or {}).get("cost_usd") or 0.0) for run in load_results(batch_dir))


@dataclass
class CaseOutcome:
    case_id: str
    before: list[bool]
    after: list[bool]

    @property
    def regressed(self) -> bool:
        return _majority(self.before) and not _majority(self.after)

    @property
    def improved(self) -> bool:
        return not _majority(self.before) and _majority(self.after)


def _majority(passes: list[bool]) -> bool:
    return bool(passes) and sum(passes) / len(passes) > 0.5


@dataclass
class ScreenResult:
    target: str
    outcomes: list[CaseOutcome]
    cost_before: float
    cost_after: float
    cache_before: float | None
    cache_after: float | None
    runs_after: int
    prompt_digests_match: bool

    @property
    def target_outcome(self) -> CaseOutcome:
        return next(outcome for outcome in self.outcomes if outcome.case_id == self.target)

    @property
    def regressions(self) -> list[str]:
        return [o.case_id for o in self.outcomes if o.regressed and o.case_id != self.target]

    def verdict(self, guards_ok: bool = True) -> str:
        """Advisory only; the owner decides (record 009, loop step 6)."""
        if not guards_ok or self.regressions:
            return "reject"
        target = self.target_outcome.after
        return "adopt" if target and all(target) and len(target) >= TARGET_REPEATS else "iterate"


def _cache_share(scores: list[CaseScore]) -> float | None:
    cached = sum(score.cache_read_tokens for score in scores)
    prompt = cached + sum(score.input_tokens + score.cache_write_tokens for score in scores)
    return cached / prompt if prompt else None


def pair(
    plan: ScreenPlan,
    suite: Suite,
    answers: dict[str, Any],
    control_dir: Path,
    candidate_dir: Path,
) -> ScreenResult:
    wanted = set(plan.cases)
    before = [s for s in score_batch(suite, control_dir, answers) if s.arm == "main" and s.case_id in wanted]
    after = [s for s in score_batch(suite, candidate_dir, answers) if s.arm == "main" and s.case_id in wanted]
    before = [s for s in before if s.status != "error"]
    after = [s for s in after if s.status != "error"]
    outcomes = [
        CaseOutcome(
            case_id,
            [s.passed for s in before if s.case_id == case_id],
            [s.passed for s in after if s.case_id == case_id],
        )
        for case_id in plan.cases
    ]
    digests_before = {
        (r["case_id"], (r.get("prompt") or {}).get("digest"))
        for r in load_results(control_dir)
        if r["case_id"] in wanted
    }
    digests_after = {
        (r["case_id"], (r.get("prompt") or {}).get("digest"))
        for r in load_results(candidate_dir)
        if r["case_id"] in wanted and r.get("status") != "error"
    }
    return ScreenResult(
        target=plan.target,
        outcomes=outcomes,
        cost_before=sum(s.cost_usd for s in before) / len(before) if before else 0.0,
        cost_after=sum(s.cost_usd for s in after) / len(after) if after else 0.0,
        cache_before=_cache_share(before),
        cache_after=_cache_share(after),
        runs_after=len(after),
        prompt_digests_match=digests_after <= digests_before,
    )
