"""Deterministic guards on a proposal (decision record 009, loop step 4).

A proposal is a list of exact text edits. Knowledge-base edits change existing pages under ``kb/`` (never
the INDEX, the policy pages, or the pages the replenishment track owns); prompt edits are written into a
new version of the ``main`` prompt, never into a shipped one. The added text must not carry evaluation
content: no case id, no reference value of any case in any suite (the test split included), no run of
eight words from any case question, no reference to the ``eval`` schema. A proposal changes one behavior:
at most two files and twelve added lines.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from ..cases import Suite

MAX_FILES = 2
MAX_ADDED_LINES = 12
SHINGLE_WORDS = 8
MIN_SIGNIFICANT_DIGITS = 3
DENIED_KB_PAGES = frozenset({"INDEX.md", "metrics/projected_stock.md", "guides/replenishment_planning.md"})
DENIED_KB_PREFIXES = ("policies/",)
PROMPT_PROFILE = "main"
NUMBER = re.compile(r"(?<![\w.])-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![\w])")
WORD = re.compile(r"[a-z0-9]+")
EVAL_SCHEMA = re.compile(r"\beval\s*(?:schema\b|\.\w)|`eval`|\bevals?/(?:cases|answers)\b", re.IGNORECASE)
PREFIX_UNSTABLE = (
    (re.compile(r"\b\d{4}-\d{2}-\d{2}\b"), "a date"),
    (re.compile(r"\b(?:claude|opus|sonnet|haiku|fable|gpt-?\d)\b", re.IGNORECASE), "a model name"),
    (re.compile(r"\br\d{4}\b"), "a result id"),
)
LAST_REVIEWED = re.compile(r"^last_reviewed:.*$", re.MULTILINE)


@dataclass(frozen=True)
class Edit:
    """Replace ``old`` (one occurrence) by ``new`` in ``path``, a ``kb/`` page or ``prompts/main/v<n>.md``."""

    path: str
    old: str
    new: str

    @staticmethod
    def parse(raw: dict[str, Any]) -> Edit:
        return Edit(str(raw.get("path", "")), str(raw.get("old", "")), str(raw.get("new", "")))


@dataclass
class Change:
    """One file the proposal changes: its repository path, the base text, and the text after the edits."""

    path: str
    before: str
    after: str
    new_file: bool = False

    def added_lines(self) -> list[str]:
        diff = difflib.unified_diff(self.before.splitlines(), self.after.splitlines(), lineterm="", n=0)
        return [
            line[1:]
            for line in diff
            if line.startswith("+") and not line.startswith("+++") and not LAST_REVIEWED.match(line[1:])
        ]

    def diff(self) -> str:
        source = f"a/{self.path}" if not self.new_file else "/dev/null"
        return "".join(
            difflib.unified_diff(
                self.before.splitlines(keepends=True),
                self.after.splitlines(keepends=True),
                fromfile=source,
                tofile=f"b/{self.path}",
            )
        )


@dataclass
class GuardReport:
    problems: list[str] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def _significant_digits(text: str) -> int:
    """Digits of a written number without leading zeros or a whole number's trailing zeros (3,000: one)."""
    whole, _, fraction = text.replace(",", "").replace("-", "").partition(".")
    digits = (whole + fraction).lstrip("0")
    return len(digits if fraction else digits.rstrip("0"))


def _decimal(text: str) -> Decimal | None:
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None


@dataclass
class LeakCorpus:
    """Everything an added text must not repeat, from every suite and its reference answers."""

    case_ids: set[str] = field(default_factory=set)
    values: list[Decimal] = field(default_factory=list)
    shingles: set[tuple[str, ...]] = field(default_factory=set)

    @staticmethod
    def build(suites: Iterable[tuple[Suite, dict[str, Any]]]) -> LeakCorpus:
        corpus = LeakCorpus()
        seen_values: set[Decimal] = set()
        for suite, answers in suites:
            results = answers.get("results") or {}
            for case in suite.cases:
                corpus.case_ids.add(case.id)
                for question in [case.question, *(turn.question for turn in case.turns)]:
                    corpus.shingles |= shingles(question)
                # Value columns only: key columns hold codes (plants, movement types) that the KB names.
                for name, _sql, spec in case.references():
                    reference = results.get(case.id, {}).get(name)
                    if not reference:
                        continue
                    columns = [
                        reference["columns"].index(v.column)
                        for v in spec.values
                        if v.column in reference["columns"]
                    ]
                    for row in reference.get("rows", []):
                        for index in columns:
                            value = _decimal(str(row[index]))
                            if (
                                value is not None
                                and value.is_finite()
                                and len(value.normalize().as_tuple().digits) >= MIN_SIGNIFICANT_DIGITS
                                and value not in seen_values
                            ):
                                seen_values.add(value)
                                corpus.values.append(value)
        return corpus


def shingles(text: str) -> set[tuple[str, ...]]:
    words = WORD.findall(text.lower())
    return {tuple(words[i : i + SHINGLE_WORDS]) for i in range(len(words) - SHINGLE_WORDS + 1)}


def _matches_reference(number: str, values: list[Decimal]) -> bool:
    reported = _decimal(number)
    if reported is None:
        return False
    places = len(number.split(".")[1]) if "." in number else 0
    quantum = Decimal(1).scaleb(-places)
    for value in values:
        for candidate in (value, value * 100):  # a rate may be written as a percentage
            if candidate.quantize(quantum) == reported:
                return True
    return False


def leaks(added: str, base_text: str, corpus: LeakCorpus) -> list[str]:
    """Evaluation content in ``added`` that is not already in ``base_text`` (knowledge base or prompt)."""
    problems = []
    base_tokens = set(re.findall(r"[\w,.-]+", base_text))
    for case_id in sorted(corpus.case_ids):
        if re.search(rf"(?<![\w-]){re.escape(case_id)}(?![\w-])", added) and case_id not in base_tokens:
            problems.append(f"names case id {case_id}")
    for match in NUMBER.finditer(added):
        number = match.group(0)
        if _significant_digits(number) < MIN_SIGNIFICANT_DIGITS or number in base_tokens:
            continue
        if number.replace(",", "") in base_tokens:
            continue
        if _matches_reference(number, corpus.values):
            problems.append(f"contains {number}, a reference value of an evaluation case")
    copied = (shingles(added) & corpus.shingles) - shingles(base_text)
    if copied:
        problems.append(f"repeats a case question ({' '.join(sorted(copied)[0])} …)")
    if EVAL_SCHEMA.search(added):
        problems.append("refers to the evaluation schema or files")
    return problems


def _kb_page(path: str) -> str | None:
    """The page path relative to ``kb/``, or None when ``path`` is not a knowledge-base page."""
    if not path.startswith("kb/") or not path.endswith(".md") or ".." in path.split("/"):
        return None
    return path.removeprefix("kb/")


def _prompt_version(path: str) -> int | None:
    match = re.fullmatch(rf"prompts/{PROMPT_PROFILE}/v(\d+)\.md", path)
    return int(match.group(1)) if match else None


def apply_edits(
    edits: list[Edit], base_kb: Path, base_prompts: Path, prompt_version: str, today: date
) -> tuple[list[Change], list[str]]:
    """The files the edits produce, and the problems that stop them (paths, edits that do not apply)."""
    problems: list[str] = []
    changes: dict[str, Change] = {}
    current = int(prompt_version.removeprefix("v"))
    for edit in edits:
        page = _kb_page(edit.path)
        version = _prompt_version(edit.path)
        if page is not None:
            if page in DENIED_KB_PAGES or page.startswith(DENIED_KB_PREFIXES):
                problems.append(f"{edit.path}: this page is not open to proposals")
                continue
            source = base_kb / page
            if not source.is_file():
                problems.append(f"{edit.path}: no such page (proposals edit existing pages only)")
                continue
            target = edit.path
        elif version is not None:
            if version != current:
                problems.append(
                    f"{edit.path}: edit the current version v{current}; the edits go into v{current + 1}"
                )
                continue
            source = base_prompts / PROMPT_PROFILE / f"v{current}.md"
            target = f"prompts/{PROMPT_PROFILE}/v{current + 1}.md"
        else:
            problems.append(f"{edit.path}: proposals may edit knowledge-base pages and the main prompt only")
            continue
        change = changes.get(target)
        if change is None:
            text = source.read_text(encoding="utf-8")
            change = changes[target] = Change(target, text, text, new_file=version is not None)
        if not edit.old:
            problems.append(f"{edit.path}: the old text is empty; quote the text to replace")
            continue
        count = change.after.count(edit.old)
        if count != 1:
            problems.append(f"{edit.path}: the old text occurs {count} times (it must occur exactly once)")
            continue
        change.after = change.after.replace(edit.old, edit.new, 1)
    for change in changes.values():
        if change.path.startswith("kb/") and change.after != change.before:
            change.after = LAST_REVIEWED.sub(f"last_reviewed: {today.isoformat()}", change.after, count=1)
    return [change for change in changes.values() if change.after != change.before], problems


def _front_matter(text: str) -> dict[str, str] | None:
    match = re.match(r"^---\n(?P<body>.*?)\n---\n", text, re.DOTALL)
    if not match:
        return None
    fields = {}
    for line in match.group("body").splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


def check(changes: list[Change], base_kb_text: str, corpus: LeakCorpus) -> GuardReport:
    """Every guard on the files a proposal changes (``apply_edits`` checks the paths and the edits)."""
    report = GuardReport()
    if not changes:
        report.problems.append("the proposal changes nothing")
        return report
    if len(changes) > MAX_FILES:
        report.problems.append(
            f"changes {len(changes)} files (at most {MAX_FILES}: one behavior per proposal)"
        )
    added = [line for change in changes for line in change.added_lines()]
    if len(added) > MAX_ADDED_LINES:
        report.problems.append(f"adds {len(added)} lines (at most {MAX_ADDED_LINES})")
    report.checks.append(f"{len(changes)} file(s), {len(added)} added line(s)")
    for change in changes:
        text = "\n".join(change.added_lines())
        base = base_kb_text if change.path.startswith("kb/") else change.before + "\n" + base_kb_text
        report.problems += [f"{change.path}: {problem}" for problem in leaks(text, base, corpus)]
        if change.path.startswith("kb/"):
            before, after = _front_matter(change.before), _front_matter(change.after)
            if after is None:
                report.problems.append(f"{change.path}: the front matter no longer parses")
            elif before and (before.get("id"), before.get("title")) != (after.get("id"), after.get("title")):
                report.problems.append(
                    f"{change.path}: the page id or title changed (page names stay stable)"
                )
        else:
            for pattern, what in PREFIX_UNSTABLE:
                if pattern.search(text):
                    report.problems.append(f"{change.path}: the frozen prompt would contain {what}")
    report.checks.append("paths, leakage, size, front matter, and prompt prefix checked")
    return report


def kb_text(root: Path) -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(root.rglob("*.md")))
