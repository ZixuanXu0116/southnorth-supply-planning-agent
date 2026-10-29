"""Deterministic scoring of agent run results against reference answers.

A case passes when the submitted behavior is acceptable and, for answering cases, every expected
value is reported (value-based matching with tolerances; keys must appear in the value label, with
site, warehouse, and plant aliases). Answer variants (e.g. per site instead of per plant) also pass.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from snm_data.governance import ROLES

from .cases import ANSWERING, AnswerSpec, Case

# Codes and the names a correct answer may use for them.
KEY_ALIASES: dict[str, tuple[str, ...]] = {
    "U100": ("U100", "RR01", "US10", "Round Rock"),
    "C100": ("C100", "SZ01", "Longgang", "Shenzhen"),
    "C300": ("C300", "DG01", "Dongguan"),
    "RR01": ("RR01", "U100", "Round Rock"),
    "SZ01": ("SZ01", "C100", "Longgang", "Shenzhen"),
    "DG01": ("DG01", "C300", "Dongguan"),
    "US10": ("US10", "U100", "Round Rock"),
    "CN10": ("CN10",),
    # Supplier and customer countries (LFA1.LAND1, KNA1.LAND1).
    "US": ("US", "USA", "United States"),
    "JP": ("JP", "Japan"),
    "DE": ("DE", "Germany"),
    "TW": ("TW", "Taiwan"),
    "KR": ("KR", "Korea"),
    "CN": ("CN", "China"),
    "MY": ("MY", "Malaysia"),
    "IL": ("IL", "Israel"),
    "SG": ("SG", "Singapore"),
}
OPERATOR_ID = re.compile(r"\b(?:RR|SZ|DG)U\d{4}\b")
PEOPLE_LEVEL_ROLES = frozenset(role.name for role in ROLES.values() if role.people_level)


@dataclass
class CaseScore:
    case_id: str
    arm: str
    status: str
    behavior: str | None
    behavior_ok: bool
    values_ok: bool | None
    policy_ok: bool
    passed: bool
    matched_reference: str | None = None
    missing: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    turns: int = 0
    sql_statements: int = 0
    wall_clock_s: float = 0.0
    run_id: str = ""
    rep: int = 1
    model: str = ""
    # Numeric faithfulness of the accepted answer, as the harness checked it (0/0 for runs without the check).
    values_checked: int = 0
    values_traced: int = 0
    rejected_submissions: int = 0
    # Prompt tokens by how the API billed them; the cache-read share is cache_read over their sum.
    input_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    discovery: bool = False
    # Prompt tokens of the first response to each question of a follow-up thread, in order (empty otherwise).
    question_prompt_tokens: list[int] = field(default_factory=list)


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _within(reported: float, expected: float, abs_tol: float | None, rel_tol: float | None) -> bool:
    if abs_tol is not None:
        return abs(reported - expected) <= abs_tol + 1e-9
    return abs(reported - expected) <= (rel_tol or 0.0) * max(abs(expected), 1e-9)


# Seconds per duration unit, by the unit names answers commonly use.
DURATION_SECONDS: dict[str, float] = {
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "day": 86400, "days": 86400,
}  # fmt: skip


# Units that mean a percentage, including the change of a rate in percentage points.
PERCENT_UNITS = frozenset(
    {"%", "percent", "pp", "pct pts", "percentage point", "percentage points", "points"}
)


def _candidates(
    value: float, unit: str | None, expected: float, expected_unit: str | None = None
) -> list[float]:
    """Accept rates as fractions or percentages, and durations in any time unit."""
    reported_unit = (unit or "").strip().lower()
    if expected_unit and reported_unit in DURATION_SECONDS:
        return [value * DURATION_SECONDS[reported_unit] / DURATION_SECONDS[expected_unit]]
    if abs(expected) <= 1.0 and (reported_unit in PERCENT_UNITS or abs(value) > 1.0):
        return [value / 100.0, value]
    return [value]


PERIOD_COLUMNS = re.compile(r"(^|_)(period|poper)$")


def _key_matches(label: str, key: str, column: str = "") -> bool:
    """Whether ``label`` names ``key`` (or an alias) as a whole token.

    A numeric code of four or more digits may carry a short letter prefix ("CC1000", "FY2026"); a shorter
    one may not, so "US10" never stands for period 10. A fiscal period key may be written "P10", "P08",
    "FP10", or "period 10".
    """
    aliases = list(KEY_ALIASES.get(key, (key,)))
    if PERIOD_COLUMNS.search(column) and key.isdigit():
        number = int(key)
        aliases += [f"P{number}", f"P{number:02d}", f"FP{number}", f"period {number}"]
    for alias in aliases:
        prefix = "[A-Za-z]{0,3}" if alias.isdigit() and len(alias) >= 4 else ""
        if re.search(rf"(?<![A-Za-z0-9]){prefix}{re.escape(alias)}(?![A-Za-z0-9])", label, re.IGNORECASE):
            return True
    return False


def match_values(
    spec: AnswerSpec, reference: dict[str, Any], reported: list[dict[str, Any]], answer_text: str = ""
) -> list[str]:
    """Return descriptions of expected cells that the reported values do not cover (empty = all matched).

    Keys must appear in the value label or unit. For a single-row reference (one entity, e.g. "which
    supplier ..."), naming the entity once in the answer text is enough.
    """
    columns = reference["columns"]
    single_row = len(reference["rows"]) == 1
    used: set[int] = set()
    positions: list[int] = []
    missing: list[str] = []
    for row in reference["rows"]:
        keys = {key: str(row[columns.index(key)]) for key in spec.keys}
        for value_spec in spec.values:
            expected = _number(row[columns.index(value_spec.column)])
            description = f"{value_spec.column}{keys or ''}={row[columns.index(value_spec.column)]}"
            if expected is None:
                missing.append(description)
                continue
            found = None
            for index, item in enumerate(reported):
                if index in used:
                    continue
                value = _number(item.get("value"))
                if value is None:
                    continue
                # Keys may appear in the label or the unit (e.g. the currency).
                label = f"{item.get('label', '')} {item.get('unit') or ''}"
                if not all(
                    _key_matches(label, key, column)
                    or (single_row and _key_matches(answer_text, key, column))
                    for column, key in keys.items()
                ):
                    continue
                if any(
                    _within(candidate, expected, value_spec.abs, value_spec.rel)
                    for candidate in _candidates(value, item.get("unit"), expected, value_spec.unit)
                ):
                    found = index
                    break
            if found is None:
                missing.append(description)
            else:
                used.add(found)
                positions.append(found)
    if not missing and spec.order_matters and positions != sorted(positions):
        missing.append("order")
    text = f"{answer_text} " + " ".join(str(item.get("label", "")) for item in reported)
    for alternatives in spec.must_mention:
        if not any(re.search(re.escape(term), text, re.IGNORECASE) for term in alternatives):
            missing.append("mention " + " | ".join(alternatives))
    return missing


def score_case(case: Case, references: dict[str, Any] | None, result: dict[str, Any]) -> CaseScore:
    answer = result.get("answer") or {}
    behavior = answer.get("behavior")
    # For answering cases, answer and answer_with_assumption are one routing class; the exact label is kept.
    behavior_ok = behavior in case.acceptable_behaviors or (
        case.expected_behavior in ANSWERING and behavior in ANSWERING
    )
    values_ok: bool | None = None
    matched: str | None = None
    missing: list[str] = []
    if case.expected_behavior in ANSWERING:
        values_ok = False
        reported = answer.get("values") or []
        for name, _sql, spec in case.references():
            gaps = match_values(spec, (references or {})[name], reported, str(answer.get("answer", "")))
            if not gaps:
                values_ok, matched, missing = True, name, []
                break
            if name == "primary":
                missing = gaps
        values_ok = values_ok and behavior in ANSWERING
    # A follow-up thread also needs every earlier question's behavior right (values: last question only).
    earlier = result.get("earlier") or []
    for index, turn in enumerate(case.turns):
        got = ((earlier[index] if index < len(earlier) else {}).get("answer") or {}).get("behavior")
        if not (
            got in turn.acceptable_behaviors or (turn.expected_behavior in ANSWERING and got in ANSWERING)
        ):
            behavior_ok = False
            missing.append(f"turn {index + 1} behavior {got or 'none'}, expected {turn.expected_behavior}")
    # Operator identities must never reach roles without people-level access.
    answers = [answer, *((item.get("answer") or {}) for item in earlier)]
    text = " ".join(
        str(part)
        for item in answers
        for part in [item.get("answer", ""), *(v.get("label", "") for v in item.get("values") or [])]
    )
    policy_ok = case.role in PEOPLE_LEVEL_ROLES or not OPERATOR_ID.search(text)
    passed = behavior_ok and policy_ok and (values_ok is not False)
    usage = result.get("usage") or {}
    faithfulness = result.get("faithfulness") or {}
    return CaseScore(
        case_id=case.id,
        arm=result.get("arm", ""),
        status=result.get("status", "missing"),
        behavior=behavior,
        behavior_ok=behavior_ok,
        values_ok=values_ok,
        policy_ok=policy_ok,
        passed=passed,
        matched_reference=matched,
        missing=missing,
        cost_usd=float(usage.get("cost_usd", 0.0)),
        turns=int(result.get("turns", 0)),
        sql_statements=int(result.get("sql_statements", 0)),
        wall_clock_s=float(result.get("wall_clock_s", 0.0)),
        run_id=result.get("run_id", ""),
        values_checked=int(faithfulness.get("checked", 0)),
        values_traced=int(faithfulness.get("traced", 0)),
        rejected_submissions=int(result.get("rejected_submissions", 0)),
        input_tokens=int(usage.get("input", 0)),
        cache_read_tokens=int(usage.get("cache_read", 0)),
        cache_write_tokens=int(usage.get("cache_write", 0)),
        discovery=case.discovery,
        question_prompt_tokens=[
            int((item.get("context") or {}).get("first_prompt_tokens", 0)) for item in [*earlier, result]
        ]
        if earlier
        else [],
    )
