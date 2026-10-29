"""Evaluation case files (``evals/cases/*.yaml``) and their reference answers (``evals/answers/*.json``)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

EVALS_ROOT = Path(__file__).resolve().parents[2]
CASES_DIR = EVALS_ROOT / "cases"
ANSWERS_DIR = EVALS_ROOT / "answers"

Behavior = Literal["answer", "answer_with_assumption", "clarify", "decline", "policy_restricted"]
Category = Literal[
    "covered",
    "adhoc",
    "unfamiliar",
    "trap",
    "multi_step",
    "investigation",
    "clarify",
    "decline",
    "restricted",
    "followup",
]
ANSWERING = frozenset({"answer", "answer_with_assumption"})


class ValueSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column: str
    abs: float | None = None
    rel: float | None = None
    # Duration unit of the reference value; reported durations in other time units are converted.
    unit: Literal["seconds", "minutes", "hours", "days"] | None = None

    @model_validator(mode="after")
    def _one_tolerance(self) -> ValueSpec:
        if (self.abs is None) == (self.rel is None):
            raise ValueError(f"value {self.column}: set exactly one of abs or rel")
        return self


class AnswerSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keys: list[str] = Field(default_factory=list)
    values: list[ValueSpec] = Field(default_factory=list)
    order_matters: bool = False
    # Terms the answer text must mention: each inner list is a set of alternatives (case-insensitive).
    must_mention: list[list[str]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _something_to_check(self) -> AnswerSpec:
        if not self.values and not self.must_mention:
            raise ValueError("an answer spec needs values or must_mention terms")
        return self


class AnswerVariant(BaseModel):
    """An alternative correct presentation (for example per site instead of per plant)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    reference_sql: str
    answer: AnswerSpec


class Turn(BaseModel):
    """An earlier question of a follow-up thread; only its behavior is scored (decision record 006)."""

    model_config = ConfigDict(extra="forbid")

    question: str
    expected_behavior: Behavior
    acceptable_behaviors: list[Behavior] = Field(default_factory=list)

    @model_validator(mode="after")
    def _acceptable(self) -> Turn:
        if not self.acceptable_behaviors:
            self.acceptable_behaviors = [self.expected_behavior]
        if self.expected_behavior not in self.acceptable_behaviors:
            raise ValueError("a turn's expected_behavior must be acceptable")
        return self


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    question: str
    role: str
    category: Category
    expected_behavior: Behavior
    acceptable_behaviors: list[Behavior] = Field(default_factory=list)
    trap_tags: list[str] = Field(default_factory=list)
    required_kb_docs: list[str] = Field(default_factory=list)
    required_tables: list[str] = Field(default_factory=list)
    notes: str = ""
    # A discovery task: the cause is documented nowhere the agent can read (EVAL_GUIDE.md §8). Reported
    # separately and kept out of the headline pass rate.
    discovery: bool = False
    # Earlier questions of a follow-up thread, asked in the same session before ``question`` (the last one).
    turns: list[Turn] = Field(default_factory=list)
    split: Literal["dev", "test"] = "dev"
    reference_sql: str | None = None
    answer: AnswerSpec | None = None
    answer_variants: list[AnswerVariant] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> Case:
        if not self.acceptable_behaviors:
            self.acceptable_behaviors = [self.expected_behavior]
        if self.expected_behavior not in self.acceptable_behaviors:
            raise ValueError(f"{self.id}: expected_behavior must be acceptable")
        answering = self.expected_behavior in ANSWERING
        if answering != (self.reference_sql is not None) or answering != (self.answer is not None):
            raise ValueError(f"{self.id}: answering cases need reference_sql and answer; others neither")
        if (self.category == "followup") != bool(self.turns):
            raise ValueError(f"{self.id}: follow-up cases, and only they, have earlier turns")
        if self.answer_variants and not answering:
            raise ValueError(f"{self.id}: answer variants need an answering case")
        return self

    def references(self) -> list[tuple[str, str, AnswerSpec]]:
        """(name, sql, spec) for the primary reference and every variant."""
        if self.reference_sql is None or self.answer is None:
            return []
        primary = [("primary", self.reference_sql, self.answer)]
        return primary + [(v.name, v.reference_sql, v.answer) for v in self.answer_variants]


class Suite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suite: str
    as_of: str
    data_snapshot: dict[str, Any]
    # Other suites whose cases are part of this one (same as-of date and data snapshot required).
    includes: list[str] = Field(default_factory=list)
    cases: list[Case]

    @model_validator(mode="after")
    def _unique_ids(self) -> Suite:
        ids = [case.id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate case ids")
        return self

    def case(self, case_id: str) -> Case:
        for case in self.cases:
            if case.id == case_id:
                return case
        raise KeyError(case_id)


def load_suite(name_or_path: str | Path) -> Suite:
    path = Path(name_or_path)
    if not path.suffix:
        path = CASES_DIR / f"{name_or_path}.yaml"
    with path.open(encoding="utf-8") as handle:
        suite = Suite.model_validate(yaml.safe_load(handle))
    included: list[Case] = []
    for name in suite.includes:
        other = load_suite(name)
        if (other.as_of, other.data_snapshot) != (suite.as_of, suite.data_snapshot):
            raise ValueError(f"{suite.suite} includes {name} with a different as-of date or data snapshot")
        included.extend(other.cases)
    if included:
        suite = suite.model_copy(update={"cases": [*included, *suite.cases], "includes": []})
        Suite.model_validate(suite.model_dump())  # re-check unique ids across included suites
    return suite


def answers_path(suite: Suite) -> Path:
    return ANSWERS_DIR / f"{suite.suite}.json"


def load_answers(suite: Suite) -> dict[str, Any]:
    return json.loads(answers_path(suite).read_text(encoding="utf-8"))
