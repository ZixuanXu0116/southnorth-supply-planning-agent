from datetime import date, timedelta
from itertools import pairwise

import numpy as np
import pytest

from snm_data.generator.calendar import (
    fiscal_periods,
    fiscal_year_bounds,
    fiscal_year_of,
    fiscal_years_of,
    last_sunday_of_october,
)
from snm_data.generator.master import fiscal_calendar_tables


@pytest.mark.parametrize("fiscal_year", range(2020, 2031))
def test_fiscal_year_ends_on_last_sunday_of_october(fiscal_year: int) -> None:
    start, end = fiscal_year_bounds(fiscal_year)
    assert end.weekday() == 6 and end.month == 10 and (end + timedelta(days=7)).month == 11
    assert start == last_sunday_of_october(fiscal_year - 1) + timedelta(days=1)
    assert ((end - start).days + 1) // 7 in (52, 53)
    assert (end - start).days + 1 in (364, 371)


@pytest.mark.parametrize("fiscal_year", [2025, 2026, 2027])
def test_periods_are_contiguous_and_follow_4_4_5(fiscal_year: int) -> None:
    periods = fiscal_periods(fiscal_year)
    assert [period for period, _, _ in periods] == list(range(1, 13))
    for (_, _, previous_end), (_, next_start, _) in pairwise(periods):
        assert next_start == previous_end + timedelta(days=1)
    weeks = [((last - first).days + 1) // 7 for _, first, last in periods]
    assert weeks[:11] == [4, 4, 5, 4, 4, 5, 4, 4, 5, 4, 4]
    assert weeks[11] in (5, 6)


def test_fiscal_year_of_boundaries() -> None:
    assert fiscal_year_of(date(2024, 10, 27)) == 2024
    assert fiscal_year_of(date(2024, 10, 28)) == 2025
    assert fiscal_year_of(date(2025, 10, 26)) == 2025
    assert fiscal_year_of(date(2025, 10, 27)) == 2026


def test_vectorized_fiscal_year_matches_scalar() -> None:
    days = [date(2024, 10, 1) + timedelta(days=offset) for offset in range(0, 900, 3)]
    vectorized = fiscal_years_of(np.array(days, dtype="datetime64[D]"))
    assert list(vectorized) == [fiscal_year_of(day) for day in days]


def test_t009b_period_lookup_matches_fiscal_periods() -> None:
    """Apply the SAP rule on every day: rows of the date's calendar year, first month/day on or after it."""
    t009b = fiscal_calendar_tables(2025, 2027)["raw_sap_ecc.t009b"]
    by_year: dict[int, list[tuple[tuple[int, int], int, int]]] = {}
    for row in t009b.iter_rows(named=True):
        shift = 1 if row["reljr"] == "+1" else 0
        by_year.setdefault(int(row["bdatj"]), []).append(
            ((int(row["bumon"]), int(row["butag"])), int(row["poper"]), shift)
        )
    for entries in by_year.values():
        entries.sort()
    for fiscal_year in (2025, 2026, 2027):
        for period, first, last in fiscal_periods(fiscal_year):
            day = first
            while day <= last:
                if day.year in by_year:
                    _, poper, shift = next(e for e in by_year[day.year] if e[0] >= (day.month, day.day))
                    assert (poper, day.year + shift) == (period, fiscal_year), day
                day += timedelta(days=1)
