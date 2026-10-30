"""The review packet the owner reads (decision record 009, loop step 6) and the card's Measurement section."""

from __future__ import annotations

from typing import Any

from .guards import Change, GuardReport
from .screen import ScreenPlan, ScreenResult


def _share(value: float | None) -> str:
    return f"{100 * value:.0f} %" if value is not None else "-"


def _passes(passes: list[bool]) -> str:
    return f"{sum(passes)}/{len(passes)}" if passes else "-"


def measurement(plan: ScreenPlan, result: ScreenResult, control: str, candidate: str) -> str:
    """The card's Measurement section, in the words of the c109 card."""
    target = result.target_outcome
    improved = [o.case_id for o in result.outcomes if o.improved and o.case_id != result.target]
    others = [o for o in result.outcomes if o.case_id != result.target]
    before = sum(o.before[0] for o in others if o.before)
    after = sum(sum(o.after) / len(o.after) > 0.5 for o in others if o.after)
    regressions = ", ".join(result.regressions) or "none"
    return (
        f"Screening batch `{candidate}`, paired with `{control}` on the same {len(plan.cases)} cases "
        f"(same model `{plan.model}`, effort `{plan.effort}`, prompt, and data): the target {plan.target} "
        f"passes {_passes(target.after)} after against {_passes(target.before)} before; the other "
        f"{len(others)} cases pass {after} after against {before} before. Regressions: {regressions}. "
        f"Other cases that now pass: {', '.join(improved) or 'none'}. Cost per question "
        f"${result.cost_before:.3f} before, ${result.cost_after:.3f} after; cache read "
        f"{_share(result.cache_before)} before, {_share(result.cache_after)} after."
    )


def render_packet(
    card_id: str,
    proposal: dict[str, Any],
    changes: list[Change],
    guards: GuardReport,
    plan: ScreenPlan | None,
    result: ScreenResult | None,
    spent: dict[str, float],
    commits_between: list[str],
    control: str,
    candidate: str | None,
) -> str:
    verdict = result.verdict(guards.ok) if result else ("reject" if not guards.ok else "not screened")
    lines = [
        f"# Improvement packet: {card_id}",
        "",
        f"Advisory verdict: **{verdict}** (the owner decides: adopt, reject, or iterate with a note). "
        f"Spent: proposal ${spent.get('proposal', 0.0):.2f}, screen ${spent.get('screen', 0.0):.2f}.",
        "",
        "## Proposal",
        "",
        f"- **Root cause:** `{proposal.get('root_cause')}`",
        f"- **Layer:** {proposal.get('layer')}",
        f"- **Hypothesis:** {proposal.get('hypothesis')}",
    ]
    if proposal.get("affected"):
        lines.append(f"- **Affected:** {proposal['affected']}")
    lines += ["", "## Diff", ""]
    if changes:
        for change in changes:
            lines += ["```diff", change.diff().rstrip("\n"), "```", ""]
    else:
        lines += ["No edit (the proposal names a layer the loop does not edit).", ""]
    lines += ["## Guards", ""]
    lines += [f"- {check}" for check in guards.checks]
    lines += [f"- **Problem:** {problem}" for problem in guards.problems] or ["- No problem found."]
    lines.append("")
    if plan and result and candidate:
        lines += [
            "## Screen",
            "",
            measurement(plan, result, control, candidate),
            "",
            f"Control: {'the source batch' if plan.control_reused else 'a control arm'}"
            + (f" ({'; '.join(plan.control_problems)})" if plan.control_problems else "")
            + ". Prompt digests of the screen equal the control's: "
            + ("yes." if result.prompt_digests_match else "no."),
            "",
            "| Case | Why in the screen | Before | After |",
            "|---|---|---|---|",
        ]
        for outcome in result.outcomes:
            flag = " (regression)" if outcome.regressed else (" (fixed)" if outcome.improved else "")
            why = "; ".join(plan.reasons.get(outcome.case_id, []))
            lines.append(
                f"| {outcome.case_id} | {why} | {_passes(outcome.before)} | {_passes(outcome.after)}{flag} |"
            )
        lines.append("")
    elif plan:
        lines += [
            "## Screen",
            "",
            f"Not run. Planned: {plan.runs} runs on {len(plan.cases)} cases, "
            f"estimated ${plan.estimate_usd:.2f}.",
            "",
        ]
    if commits_between:
        lines += [
            "## Harness and tool-server commits since the source batch",
            "",
            *[f"- {commit}" for commit in commits_between],
            "",
        ]
    return "\n".join(lines)
