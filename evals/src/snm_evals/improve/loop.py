"""The loop's steps as ``snm-evals improve`` runs them, with their state in ``runs/improve/<card>/``.

``card.md`` (the failure card), ``input.json`` (what the proposer reads), ``base_kb/`` (the knowledge base the
proposal is made on), ``proposal.json``, ``guards.json``, ``kb/`` (the candidate knowledge base),
``packet.md`` (what the owner reads), and ``state.json`` (suite, batch, commit, money spent).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

from ..cases import EVALS_ROOT, Suite
from ..run import HARNESS, REPO_ROOT, render_report, score_batch
from .card import proposer_input, render_card
from .collect import FAILURES_DIR, collect
from .guards import Change, Edit, GuardReport, LeakCorpus, apply_edits, check, kb_text
from .packet import measurement, render_packet
from .screen import SCREEN_MAX_USD, ScreenPlan, pair, plan_screen, reference_changes, run_screen, spent_usd
from .sources import (
    IMPROVE_ROOT,
    PROMPTS_DIR,
    all_suites,
    directory_digest,
    git_log,
    kb_snapshot,
    locate_batch,
    suite_at,
)

IMPROVER_CLI = HARNESS / "dist" / "cli" / "improve.js"
PROPOSER_MAX_USD = 0.50
EDITABLE_LAYERS = frozenset({"kb", "prompt"})


def card_dir(card_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", card_id):
        raise ValueError(f"unsupported card id {card_id!r}")
    return IMPROVE_ROOT / card_id


def load_state(card_id: str) -> dict[str, Any]:
    path = card_dir(card_id) / "state.json"
    if not path.is_file():
        raise FileNotFoundError(
            f"no card {card_id} under {IMPROVE_ROOT}; run `snm-evals improve collect` first"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(card_id: str, state: dict[str, Any]) -> None:
    (card_dir(card_id) / "state.json").write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def set_section(card: str, heading: str, body: str) -> str:
    pattern = re.compile(rf"^## {re.escape(heading)}[ \t]*\n.*?(?=^## |\Z)", re.MULTILINE | re.DOTALL)
    replaced = pattern.sub(lambda _match: f"## {heading}\n\n{body.strip()}\n\n", card, count=1)
    return replaced.rstrip("\n") + "\n"


def set_status(card: str, status: str) -> str:
    return re.sub(r"^- \*\*Status:\*\* .*$", f"- **Status:** {status}", card, count=1, flags=re.MULTILINE)


# ---------------------------------------------------------------- step 1-2: collect and draft the cards


def collect_cards(
    suite_name: str, batch: str, *, at: str | None = None, replay: bool = False, today: date | None = None
) -> tuple[list[str], list[tuple[str, str]]]:
    """Draft a card (and the proposer's input) for every failure of the batch; return the card ids."""
    today = today or date.today()
    batch_dir = locate_batch(batch)
    workdir = IMPROVE_ROOT / ".work" / batch
    suite, answers = suite_at(suite_name, at, workdir)
    failures, skipped = collect(suite, answers, batch_dir, replay=replay or at is not None)
    ids = []
    for failure in failures:
        target = card_dir(failure.card_id)
        target.mkdir(parents=True, exist_ok=True)
        card = render_card(failure, suite.suite, answers, today)
        (target / "card.md").write_text(card, encoding="utf-8")
        payload = proposer_input(failure, answers, card)
        (target / "input.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        kb_snapshot(at, target / "base_kb")
        state = {
            "card_id": failure.card_id,
            "suite": suite_name,
            "batch": batch,
            "case_id": failure.case.id,
            "at": at,
            "spent": {"proposal": 0.0, "screen": 0.0},
            "iteration": 1,
        }
        save_state(failure.card_id, state)
        ids.append(failure.card_id)
    return ids, skipped


# ---------------------------------------------------------------- step 3-4: propose and guard


def base_prompts(card_id: str) -> Path:
    """The prompts the proposal is made on: the working tree's, or a replay's as of its commit."""
    state = load_state(card_id)
    if not state.get("at"):
        return PROMPTS_DIR
    target = card_dir(card_id) / "base_prompts"
    if not target.is_dir():
        kb_snapshot_prompts(state["at"], target)
    return target


def run_proposer(
    card_id: str, repair: list[str] | None = None, max_usd: float = PROPOSER_MAX_USD
) -> dict[str, Any]:
    """Run the ``improver`` profile on the card's input; the proposal comes back as JSON."""
    folder = card_dir(card_id)
    if not IMPROVER_CLI.is_file():
        raise FileNotFoundError(f"{IMPROVER_CLI} is missing; build the harness (`npm run build -w harness`)")
    command = [
        "node", str(IMPROVER_CLI), "--input", str(folder / "input.json"),
        "--kb-root", str(folder / "base_kb"), "--prompts-dir", str(base_prompts(card_id)),
        "--runs-dir", str(folder / "runs"), "--max-usd", f"{max_usd:.4f}",
    ]  # fmt: skip
    if repair:
        command += ["--repair", json.dumps(repair)]
    env = {**os.environ, "NODE_USE_ENV_PROXY": "1", "NODE_NO_WARNINGS": "1"}
    completed = subprocess.run(command, capture_output=True, text=True, env=env, timeout=600)
    lines = [line for line in completed.stdout.splitlines() if line.startswith("{")]
    if not lines:
        raise RuntimeError(f"the proposer produced no result: {completed.stderr[-2000:]}")
    return json.loads(lines[-1])


def guard_proposal(
    card_id: str, proposal: dict[str, Any], today: date | None = None
) -> tuple[list[Change], GuardReport]:
    today = today or date.today()
    folder, state = card_dir(card_id), load_state(card_id)
    edits = [Edit.parse(raw) for raw in proposal.get("edits") or []]
    report = GuardReport()
    if proposal.get("layer") not in EDITABLE_LAYERS:
        if edits:
            report.problems.append(f"layer {proposal.get('layer')!r} takes no edits from the loop")
        report.checks.append(f"layer {proposal.get('layer')}: hypothesis only, nothing to screen")
        return [], report
    version = (json.loads((folder / "input.json").read_text(encoding="utf-8")).get("prompt") or {}).get(
        "version", "v4"
    )
    changes, problems = apply_edits(edits, folder / "base_kb", base_prompts(card_id), version, today)
    workdir = IMPROVE_ROOT / ".work" / state["batch"]
    suites = all_suites(None, workdir) + (all_suites(state["at"], workdir) if state.get("at") else [])
    report = check(changes, kb_text(folder / "base_kb"), LeakCorpus.build(suites))
    report.problems = problems + report.problems
    return changes, report


def kb_snapshot_prompts(commit: str, target: Path) -> Path:
    from .sources import git_export

    if target.exists():
        shutil.rmtree(target)
    export = git_export(commit, ["prompts"], target.parent / f"{target.name}.export")
    shutil.move(str(export / "prompts"), target)
    shutil.rmtree(export)
    return target


def write_candidate(card_id: str, changes: list[Change]) -> Path:
    """The candidate knowledge base: the base copy with the proposal's page changes applied."""
    folder = card_dir(card_id)
    candidate = folder / "kb"
    if candidate.exists():
        shutil.rmtree(candidate)
    shutil.copytree(folder / "base_kb", candidate)
    for change in changes:
        if change.path.startswith("kb/"):
            (candidate / change.path.removeprefix("kb/")).write_text(change.after, encoding="utf-8")
    return candidate


def propose(card_id: str, proposal_file: Path | None = None, today: date | None = None) -> dict[str, Any]:
    """Get a proposal (the proposer, or a hand-written file), guard it (one repair), and record it."""
    folder, state = card_dir(card_id), load_state(card_id)
    if proposal_file is not None:
        proposal = json.loads(proposal_file.read_text(encoding="utf-8"))
    else:
        proposal = run_proposer(card_id)
        state["spent"]["proposal"] += float(proposal.get("cost_usd") or 0.0)
    changes, report = _guard_or_stop(card_id, proposal, today)
    left = PROPOSER_MAX_USD - state["spent"]["proposal"]
    if not report.ok and proposal_file is None and proposal.get("status") == "proposed" and left > 0.05:
        proposal = run_proposer(card_id, repair=report.problems, max_usd=left)
        state["spent"]["proposal"] += float(proposal.get("cost_usd") or 0.0)
        changes, report = _guard_or_stop(card_id, proposal, today)
    (folder / "proposal.json").write_text(json.dumps(proposal, indent=2) + "\n", encoding="utf-8")
    (folder / "guards.json").write_text(json.dumps(asdict(report), indent=2) + "\n", encoding="utf-8")
    (folder / "changes.json").write_text(
        json.dumps([asdict(change) for change in changes], indent=2) + "\n", encoding="utf-8"
    )
    if report.ok and changes:
        write_candidate(card_id, changes)
    card = (folder / "card.md").read_text(encoding="utf-8")
    if proposal.get("status", "proposed") == "proposed":
        reason = proposal.get("root_cause_reason", "")
        card = set_section(card, "Root cause", f"`{proposal.get('root_cause')}` (proposed). {reason}")
        diff = "\n".join(change.diff() for change in changes)
        hypothesis = f"Layer: {proposal.get('layer')}. {proposal.get('hypothesis', '')}"
        if diff:
            hypothesis += f"\n\n```diff\n{diff.rstrip()}\n```"
        if not report.ok:
            hypothesis += "\n\nRefused by the guards: " + "; ".join(report.problems)
        card = set_section(card, "Fix hypothesis", hypothesis)
    else:
        card = set_section(card, "Fix hypothesis", "No proposal: " + "; ".join(report.problems))
    card = set_status(card, "fix proposed" if report.ok and changes else "open")
    (folder / "card.md").write_text(card, encoding="utf-8")
    save_state(card_id, state)
    return {"proposal": proposal, "changes": changes, "guards": report}


def _guard_or_stop(
    card_id: str, proposal: dict[str, Any], today: date | None
) -> tuple[list[Change], GuardReport]:
    """The guards, or the reason the proposer did not propose (no submission, budget, error)."""
    status = proposal.get("status", "proposed")  # a hand-written proposal has no status
    if status != "proposed":
        reason = proposal.get("error") or proposal.get("final_text") or "no proposal"
        return [], GuardReport(problems=[f"the proposer ended with {status}: {str(reason)[:300]}"])
    return guard_proposal(card_id, proposal, today)


def load_changes(card_id: str) -> list[Change]:
    path = card_dir(card_id) / "changes.json"
    if not path.is_file():
        return []
    return [Change(**raw) for raw in json.loads(path.read_text(encoding="utf-8"))]


# ---------------------------------------------------------------- step 5-6: screen and packet


def plan_for(card_id: str) -> tuple[ScreenPlan, Suite, dict[str, Any], Path]:
    folder, state = card_dir(card_id), load_state(card_id)
    suite, answers = suite_at(state["suite"], state.get("at"), IMPROVE_ROOT / ".work" / state["batch"])
    changes = load_changes(card_id)
    pages = [change.path for change in changes if change.path.startswith("kb/")]
    prompt_change = any(change.path.startswith("prompts/") for change in changes)
    batch_dir = locate_batch(state["batch"])
    plan = plan_screen(
        card_id,
        suite,
        suite.case(state["case_id"]),
        pages,
        batch_dir,
        directory_digest(folder / "base_kb"),
        prompt_change,
    )
    return plan, suite, answers, batch_dir


def screen(
    card_id: str,
    *,
    max_usd: float = SCREEN_MAX_USD,
    parallel: int = 3,
    backend: Any = None,
    data_snapshot: dict[str, Any] | None = None,
) -> str:
    """Run the screen and write the packet; returns the packet's path. ``backend`` rechecks the references."""
    folder, state = card_dir(card_id), load_state(card_id)
    guards = GuardReport(**json.loads((folder / "guards.json").read_text(encoding="utf-8")))
    changes = load_changes(card_id)
    if not guards.ok or not changes:
        raise ValueError(f"{card_id}: nothing to screen (guards failed or no edit); read guards.json")
    if any(change.path.startswith("prompts/") for change in changes):
        raise ValueError(
            f"{card_id}: a prompt proposal is screened only after the owner's hypothesis and diff reviews"
        )
    plan, suite, answers, batch_dir = plan_for(card_id)
    if backend is not None:
        changed = reference_changes(suite, answers, plan.cases, backend)
        if changed:
            plan.control_problems.append(
                f"reference answers changed on the loaded data: {', '.join(changed)}"
            )
            plan.control_reused = False
            plan.estimate_usd *= 2
    if plan.estimate_usd > max_usd:
        raise ValueError(
            f"{card_id}: the screen is estimated at ${plan.estimate_usd:.2f} ({plan.runs} runs), above the "
            f"${max_usd:.2f} cap; the owner approves a larger screen with --max-usd"
        )
    candidate_dir, control_arm = run_screen(
        plan, suite, folder / "kb", data_snapshot, max_usd, parallel, control_kb=folder / "base_kb"
    )
    control_dir = control_arm or batch_dir
    state["spent"]["screen"] += spent_usd(candidate_dir) + (spent_usd(control_arm) if control_arm else 0.0)
    save_state(card_id, state)
    result = pair(plan, suite, answers, control_dir, candidate_dir)
    report = render_report(suite, score_batch(suite, candidate_dir, answers), candidate_dir.name)
    (candidate_dir / "report.md").write_text(report, encoding="utf-8")
    measured = measurement(plan, result, control_dir.name, candidate_dir.name)
    proposal = json.loads((folder / "proposal.json").read_text(encoding="utf-8"))
    since = _source_commit(batch_dir)
    packet = render_packet(
        card_id,
        proposal,
        changes,
        guards,
        plan,
        result,
        state["spent"],
        git_log(since, ["harness", "tool_server"]),
        control_dir.name,
        candidate_dir.name,
    )
    (folder / "packet.md").write_text(packet, encoding="utf-8")
    (folder / "screen.json").write_text(
        json.dumps(
            {
                "plan": asdict(plan),
                "result": asdict(result),
                "verdict": result.verdict(guards.ok),
                "measurement": measured,
                "batch": candidate_dir.name,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    card = (folder / "card.md").read_text(encoding="utf-8")
    card = set_section(card, "Measurement", measured)
    affected = "; ".join(sorted({reason for reasons in plan.reasons.values() for reason in reasons}))
    card = set_section(card, "Affected categories", f"{len(plan.cases)} dev cases ({affected}).")
    card = set_status(card, "measured")
    (folder / "card.md").write_text(card, encoding="utf-8")
    return str(folder / "packet.md")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _source_commit(batch_dir: Path) -> str | None:
    from ..run import load_results

    commits = [(r.get("manifest") or {}).get("git_commit") for r in load_results(batch_dir)]
    known = [commit for commit in commits if commit]
    return known[-1] if known else None


# ---------------------------------------------------------------- step 7: the owner decides


def decide(card_id: str, decision: str, note: str, today: date | None = None) -> list[str]:
    """Record the owner's decision; ``adopt`` applies the change to the working tree. Returns the files."""
    today = today or date.today()
    folder, state = card_dir(card_id), load_state(card_id)
    card = (folder / "card.md").read_text(encoding="utf-8")
    written: list[str] = []
    if decision == "iterate":
        state["iteration"] = int(state.get("iteration", 1)) + 1
        state.setdefault("notes", []).append(note)
        save_state(card_id, state)
        payload = json.loads((folder / "input.json").read_text(encoding="utf-8"))
        payload.setdefault("iterations", []).append(
            {
                "proposal": json.loads((folder / "proposal.json").read_text(encoding="utf-8")),
                "packet": _read(folder / "packet.md"),
                "owner_note": note,
            }
        )
        (folder / "input.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        (folder / "card.md").write_text(set_status(card, "open"), encoding="utf-8")
        return written
    if decision == "adopt":
        for change in load_changes(card_id):
            target = REPO_ROOT / change.path
            if change.new_file:
                if target.exists():
                    raise ValueError(
                        f"{change.path} exists already; a shipped prompt version is never edited"
                    )
            elif target.read_text(encoding="utf-8") != change.before:
                raise ValueError(
                    f"{change.path} changed since the proposal; propose again on the current page"
                )
            target.write_text(change.after, encoding="utf-8")
            written.append(change.path)
        status, review = f"adopted ({today.isoformat()})", "approved"
        screen = folder / "screen.json"
        if screen.is_file():
            measured = json.loads(screen.read_text(encoding="utf-8"))
            report = REPO_ROOT / "runs" / "evals" / measured["batch"] / "report.md"
            if report.is_file():
                target = EVALS_ROOT / "reports" / f"{measured['batch']}.md"
                notes = f"\n## Notes ({today.isoformat()})\n\n- {measured['measurement']}\n"
                target.write_text(report.read_text(encoding="utf-8") + notes, encoding="utf-8")
                written.append(str(target.relative_to(REPO_ROOT)))
    elif decision in ("reject", "wontfix"):
        word = "reverted" if decision == "reject" else "wontfix"
        status, review = f"{word} ({note or 'no reason given'})", "rejected"
    else:
        raise ValueError(f"unknown decision {decision!r}: adopt, reject, wontfix, or iterate")
    card = set_status(card, status)
    card = set_section(
        card,
        "Owner review",
        f"- Hypothesis and diff review ({today.isoformat()}, one review of the whole packet, record 009): "
        f"{review}" + (f", {note}" if note and decision == "adopt" else ""),
    )
    card = set_section(card, "Decision", f"{status[0].upper()}{status[1:]}, by the owner. {note}".strip())
    (folder / "card.md").write_text(card, encoding="utf-8")
    target = FAILURES_DIR / f"{card_id}.md"
    target.write_text(card, encoding="utf-8")
    written.append(str(target.relative_to(REPO_ROOT)))
    return written
