"""Fiscal calendar (52/53-week year ending on the last Sunday of October) and demand seasonality."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np

FISCAL_YEAR_VARIANT = "Z5"
# Weeks per fiscal period: 4-4-5 per quarter; a 53rd week is added to period 12.
PERIOD_WEEKS = (4, 4, 5, 4, 4, 5, 4, 4, 5, 4, 4, 5)


def last_sunday_of_october(year: int) -> date:
    october_31 = date(year, 10, 31)
    return october_31 - timedelta(days=(october_31.weekday() - 6) % 7)


def fiscal_year_bounds(fiscal_year: int) -> tuple[date, date]:
    """First and last day of a fiscal year (the year it ends in)."""
    end = last_sunday_of_october(fiscal_year)
    start = last_sunday_of_october(fiscal_year - 1) + timedelta(days=1)
    return start, end


def fiscal_periods(fiscal_year: int) -> list[tuple[int, date, date]]:
    """(period, first day, last day) for the 12 periods of a fiscal year."""
    start, end = fiscal_year_bounds(fiscal_year)
    total_weeks = ((end - start).days + 1) // 7
    weeks = list(PERIOD_WEEKS)
    weeks[-1] += total_weeks - sum(PERIOD_WEEKS)
    periods = []
    cursor = start
    for index, count in enumerate(weeks, start=1):
        last = cursor + timedelta(days=7 * count - 1)
        periods.append((index, cursor, last))
        cursor = last + timedelta(days=1)
    assert periods[-1][2] == end
    return periods


def fiscal_year_of(day: date) -> int:
    return day.year if day <= last_sunday_of_october(day.year) else day.year + 1


def fiscal_years_of(days: np.ndarray) -> np.ndarray:
    """Vectorized fiscal year for an array of numpy datetime64[D] values."""
    years = days.astype("datetime64[Y]").astype(int) + 1970
    ends = np.array(
        [np.datetime64(last_sunday_of_october(int(y))) for y in range(years.min(), years.max() + 1)]
    )
    end_of_year = ends[years - years.min()]
    return np.where(days <= end_of_year, years, years + 1)


def quarter_end_windows(fiscal_years: range, days: int = 10) -> list[tuple[date, date]]:
    """The last ``days`` days of every fiscal quarter (periods 3, 6, 9, 12)."""
    windows = []
    for fiscal_year in fiscal_years:
        for period, _first, last in fiscal_periods(fiscal_year):
            if period % 3 == 0:
                windows.append((last - timedelta(days=days - 1), last))
    return windows


# Holiday demand multipliers (0 = closed). Chinese New Year and National Day produce expected dips.
US_HOLIDAYS: dict[date, float] = {
    date(2024, 11, 28): 0.0,
    date(2024, 11, 29): 0.5,
    date(2024, 12, 24): 0.3,
    date(2024, 12, 25): 0.0,
    date(2024, 12, 31): 0.5,
    date(2025, 1, 1): 0.0,
    date(2025, 5, 26): 0.0,
    date(2025, 7, 4): 0.0,
    date(2025, 9, 1): 0.0,
    date(2025, 11, 27): 0.0,
    date(2025, 11, 28): 0.5,
    date(2025, 12, 24): 0.3,
    date(2025, 12, 25): 0.0,
    date(2025, 12, 31): 0.5,
    date(2026, 1, 1): 0.0,
    date(2026, 5, 25): 0.0,
    date(2026, 7, 3): 0.0,
    date(2026, 9, 7): 0.0,
}


def _span(first: date, last: date, value: float) -> dict[date, float]:
    return {first + timedelta(days=offset): value for offset in range((last - first).days + 1)}


CN_HOLIDAYS: dict[date, float] = {
    **_span(date(2025, 1, 28), date(2025, 2, 4), 0.05),  # Chinese New Year 2025
    **_span(date(2025, 2, 5), date(2025, 2, 9), 0.6),
    **_span(date(2025, 5, 1), date(2025, 5, 5), 0.2),  # Labor Day
    **_span(date(2025, 10, 1), date(2025, 10, 8), 0.1),  # National Day
    **_span(date(2026, 2, 15), date(2026, 2, 23), 0.05),  # Chinese New Year 2026
    **_span(date(2026, 2, 24), date(2026, 2, 28), 0.6),
    **_span(date(2026, 5, 1), date(2026, 5, 5), 0.2),
}

WEEKDAY_FACTORS = {
    "US": (1.0, 1.0, 1.0, 1.0, 1.0, 0.15, 0.05),
    "CN": (1.0, 1.0, 1.0, 1.0, 1.0, 0.6, 0.1),
}


def demand_multipliers(days: list[date], country: str, start: date) -> np.ndarray:
    """Relative demand per day: weekday profile x holidays x quarter-end push x linear growth."""
    holidays = US_HOLIDAYS if country == "US" else CN_HOLIDAYS
    weekday = WEEKDAY_FACTORS[country]
    fiscal_range = range(fiscal_year_of(days[0]), fiscal_year_of(days[-1]) + 1)
    windows = quarter_end_windows(fiscal_range)
    factors = np.empty(len(days))
    for index, day in enumerate(days):
        value = weekday[day.weekday()] * holidays.get(day, 1.0)
        if any(first <= day <= last for first, last in windows):
            value *= 1.25
        value *= 1.0 + 0.12 * (day - start).days / 365.0
        factors[index] = value
    return factors
