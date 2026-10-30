"""Which failing runs of a batch become failure cards (decision record 009, loop step 1).

A failure is a scored run of the ``main`` arm that did not pass, on a ``dev`` case that is not a discovery
task. Errored runs are infrastructure (rerun them with ``--retry-errors``), and a case that an open card
already covers is not carded twice (``PROMPT_CHANGES.md`` §4).
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..cases import EVALS_ROOT, Case, Suite
from ..run import load_results, score_batch
from ..score import CaseScore

FAILURES_DIR = EVALS_ROOT / "failures"
CARD_TITLE = re.compile(r"^# Failure card: (?P<batch>\S+) / (?P<case>\S+)\s*$", re.MULTILINE)
CARD_STATUS = re.compile(r"^- \*\*Status:\*\* (?P<status>[a-z ]+?)\s*(?:\(|$)", re.MULTILINE)
# A card in one of these states is still being worked on, so it covers new failures of its case.
OPEN_STATUSES = frozenset({"open", "needs case", "fix proposed", "measured"})


@dataclass(frozen=True)
class CardRef:
    card_id: str
    case_id: str
    status: str
    decision: str


@dataclass
class Failure:
    card_id: str
    batch: str
    case: Case
    result: dict[str, Any]
    score: CaseScore
    run_dir: Path
    failed_runs: int
    runs: int
    earlier_cards: list[CardRef] = field(default_factory=list)


def _section(text: str, heading: str) -> str:
    match = re.search(
        rf"^## {re.escape(heading)}\s*$(?P<body>.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL
    )
    return " ".join(match.group("body").split()) if match else ""


def existing_cards(failures_dir: Path = FAILURES_DIR) -> dict[str, list[CardRef]]:
    """Cards by case id, from their titles and status lines."""
    cards: dict[str, list[CardRef]] = defaultdict(list)
    for path in sorted(failures_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        title, status = CARD_TITLE.search(text), CARD_STATUS.search(text)
        if not title or not status:
            continue
        cards[title.group("case")].append(
            CardRef(path.stem, title.group("case"), status.group("status"), _section(text, "Decision"))
        )
    return cards


def collect(
    suite: Suite,
    answers: dict[str, Any],
    batch_dir: Path,
    *,
    arm: str = "main",
    replay: bool = False,
    failures_dir: Path = FAILURES_DIR,
) -> tuple[list[Failure], list[tuple[str, str]]]:
    """The batch's failures to card, and the skipped failing cases with the reason.

    ``replay`` re-runs the loop on an old failure: existing cards are ignored and the card id says so.
    """
    batch = batch_dir.name
    results = {result["run_id"]: result for result in load_results(batch_dir) if result.get("arm") == arm}
    scores = [score for score in score_batch(suite, batch_dir, answers) if score.arm == arm]
    by_case: dict[str, list[CaseScore]] = defaultdict(list)
    for score in scores:
        by_case[score.case_id].append(score)
    cards = {} if replay else existing_cards(failures_dir)
    known = {case.id: case for case in suite.cases}

    failures: list[Failure] = []
    skipped: list[tuple[str, str]] = []
    for case_id in sorted(by_case):
        runs = sorted(by_case[case_id], key=lambda score: score.rep)
        failed = [score for score in runs if not score.passed and score.status != "error"]
        if not failed:
            if any(score.status == "error" for score in runs):
                skipped.append((case_id, "errored run (infrastructure; rerun with --retry-errors)"))
            continue
        case = known.get(case_id)
        card_id = f"{batch}-{case_id}" + ("-replay" if replay else "")
        reason = None
        if case is None:
            reason = "case no longer in the suite"
        elif case.split != "dev":
            reason = "locked test split"
        elif case.discovery:
            reason = "discovery task (EVAL_GUIDE.md §8)"
        elif any(card.card_id == card_id for card in cards.get(case_id, [])):
            reason = f"already carded ({card_id})"
        else:
            covering = [card for card in cards.get(case_id, []) if card.status in OPEN_STATUSES]
            if covering:
                reason = f"covered by card {covering[0].card_id} ({covering[0].status})"
        if reason or case is None:
            skipped.append((case_id, reason or "unknown"))
            continue
        first = failed[0]
        failures.append(
            Failure(
                card_id=card_id,
                batch=batch,
                case=case,
                result=results[first.run_id],
                score=first,
                run_dir=batch_dir / "runs" / first.run_id,
                failed_runs=len(failed),
                runs=len(runs),
                earlier_cards=list(cards.get(case_id, [])),
            )
        )
    return failures, skipped
