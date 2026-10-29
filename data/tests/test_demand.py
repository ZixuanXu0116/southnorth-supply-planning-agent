"""The engine's demand model and stock-out risk (record 002 phase 3): hand-computed references."""

from datetime import date
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from snm_data.contracts import ContractRegistry
from snm_data.generator.config import PROFILES
from snm_data.generator.run import generate
from snm_data.planning import demand
from snm_data.planning.engine import _forecast, plan
from snm_data.planning.inputs import PlanningInputs, _demand_weekly, weekly_series
from snm_data.planning.params import PlanningParams

CONTRACTS = Path(__file__).resolve().parents[2] / "data_contracts"


# A curve like the backtest's: a model probability of 1 % is 5 % in fact, 10 % is 14 %.
CURVE = ((0.01, 0.05), (0.10, 0.14))


def _model(
    probability: float, sizes: tuple[float, ...], curve: tuple[tuple[float, float], ...] | None = None
) -> demand.DemandModel:
    return demand.DemandModel(
        model="sba", weeks=26, alpha=0.15, rate_per_week=probability * float(np.mean(sizes)),
        probability=probability, sizes=sizes, frequency="4-6 weeks", service_target=0.90,
        calibration=curve,
    )  # fmt: skip


def test_the_model_of_a_toy_history_by_hand() -> None:
    """Demand of 6 in weeks 14 and 17 of 26 (week 0 the oldest): SBA starts the interval at 15 (the first
    demand week, counted from one) and smooths it with the next interval of 3 to 15 + 0.15 * (3 - 15) =
    13.2; the size stays 6; the rate is 0.925 * 6 / 13.2 per week. Two weeks with demand in the last 13 is
    the 1-3 weeks segment: a B part gets the 97 % target and the segment's calibration curve."""
    weekly = np.zeros(26)
    weekly[[14, 17]] = 6.0
    model = demand.fit(weekly, "B", PlanningParams())
    assert model.rate_per_week == pytest.approx(0.925 * 6 / (15 + 0.15 * (3 - 15)), abs=1e-6)
    assert model.probability == pytest.approx(1 / (15 + 0.15 * (3 - 15)), abs=1e-6)
    assert model.sizes == (6.0, 6.0)
    assert model.frequency == "1-3 weeks"
    assert model.service_target == 0.97
    assert model.calibration == PlanningParams().risk_calibration["1-3 weeks"]
    assert model.qualified
    # No outflow in the last 13 weeks: the segment did not qualify, so no curve and no risk.
    old = np.zeros(26)
    old[3] = 6.0
    stale = demand.fit(old, "A", PlanningParams())
    assert stale.frequency == "none in 13 weeks" and stale.calibration is None and not stale.qualified
    assert demand.DemandModel.from_json(model.to_json()) == model


def test_paths_are_netted_like_the_point_forecast() -> None:
    """A path whose weekly amount equals the point rate times seven reproduces the point forecast."""
    horizon = 20
    explicit = np.zeros(horizon + 1)
    explicit[[2, 9, 10]] = [3.0, 10.0, 1.0]
    point = _forecast(1.5, explicit, horizon)
    paths = demand.net_paths(np.full((2, 3), 1.5 * 7), explicit, horizon)
    assert np.allclose(paths, point[None, :])


def test_a_regular_demand_runs_out_on_the_day_the_arithmetic_says() -> None:
    """Demand of exactly 7 every week (1 per day) from a stock of 10: below zero from day 11. The window
    decides the probability (0 up to day 10, 1 from day 11; the curve keeps 0 and 1); the expected days below
    zero count the whole horizon (11 to 20); the 90 % target is met up to day 10."""
    horizon = 20
    model = _model(1.0, (7.0,), CURVE)
    point_net = np.r_[0.0, np.ones(horizon)]
    projected = 10.0 - np.cumsum(point_net)
    explicit = np.zeros(horizon + 1)

    def risk(window_end: int) -> demand.Risk:
        return demand.stockout_risk(model, projected, point_net, explicit, window_end, "C-1", 200)

    assert risk(10).probability == 0.0 and risk(11).probability == 1.0
    assert risk(10).expected_days_below_zero == 10.0
    assert risk(10).meets_target and not risk(11).meets_target


def test_lumpy_demand_gives_the_binomial_probability() -> None:
    """A lump of 10 in a week with probability 0.5 against a stock of 5 and no point forecast: one week's
    window runs out with probability 0.5, two weeks' with 1 - 0.5 ** 2 = 0.75."""
    horizon = 28
    model = _model(0.5, (10.0,))
    projected = np.full(horizon + 1, 5.0)
    zeros = np.zeros(horizon + 1)
    one = demand.stockout_risk(model, projected, zeros, zeros, 7, "C-2", 20000)
    two = demand.stockout_risk(model, projected, zeros, zeros, 14, "C-2", 20000)
    assert one.probability == pytest.approx(0.5, abs=0.015)
    assert two.probability == pytest.approx(0.75, abs=0.015)


def test_the_curve_maps_the_model_probability_by_hand() -> None:
    """Linear between the curve's points, through (0, 0) and (1, 1): 0.5 % -> 2.5 %, 5.5 % -> 5 % + 4.5 / 9
    of 9 points = 9.5 %, 55 % -> 14 % + 45 / 90 of 86 points = 57 %; without a curve, unchanged."""
    model = _model(0.5, (10.0,), CURVE)
    assert model.calibrate(0.005) == pytest.approx(0.025)
    assert model.calibrate(0.055) == pytest.approx(0.095)
    assert model.calibrate(0.55) == pytest.approx(0.57)
    assert _model(0.5, (10.0,)).calibrate(0.3) == 0.3
    # A lump of 10 against a stock of 5 in one week: the model's 50 % is shown as 50.67 % (14 + 40/90 * 86).
    horizon = 14
    stock, zeros = np.full(horizon + 1, 5.0), np.zeros(horizon + 1)
    risk = demand.stockout_risk(model, stock, zeros, zeros, 7, "C-4", 20000)
    assert risk.probability == pytest.approx(model.calibrate(risk.model_probability))
    assert risk.model_probability == pytest.approx(0.5, abs=0.015) and not risk.meets_target


def test_options_of_a_case_share_their_paths() -> None:
    """The same case draws the same paths, so an earlier or larger receipt never shows a higher risk."""
    horizon = 56
    model = _model(0.4, (4.0, 12.0))
    point_net = np.r_[0.0, np.full(horizon, 0.4 * 8 / 7)]
    base = 20.0 - np.cumsum(point_net)
    zeros = np.zeros(horizon + 1)
    receipt = np.zeros(horizon + 1)
    receipt[20:] = 15.0
    late = np.zeros(horizon + 1)
    late[30:] = 15.0
    risks = [
        demand.stockout_risk(model, line, point_net, zeros, 40, "C-3", 2000).probability
        for line in (base, base + late, base + receipt, base + 2 * receipt)
    ]
    assert risks == sorted(risks, reverse=True) and risks[0] > risks[-1]
    again = demand.stockout_risk(model, base + late, point_net, zeros, 40, "C-3", 2000).probability
    assert again == risks[1]


def test_weekly_outflow_counts_staging_by_week_before_the_as_of_day() -> None:
    """Staging to the production supply area (net of reversals) in the 26 weeks before the as-of day, by
    week from the oldest; the as-of day itself and moves to the overflow hub are not counted."""

    def row(bwart, shkzg, lgort, umlgo, menge, day):
        return {"bwart": bwart, "shkzg": shkzg, "matnr": "000000000040000001", "werks": "US10",
                "lgort": lgort, "umlgo": umlgo, "menge": float(menge), "budat_mkpf": day}  # fmt: skip

    as_of = date(2026, 9, 21)  # 26 weeks start on 2026-03-23
    mseg = pl.DataFrame(
        [
            row("311", "H", "1000", "2100", 5, "20260323"),  # week 0
            row("311", "H", "1000", "2100", 4, "20260920"),  # week 25 (the day before the as-of day)
            row("312", "S", "1000", "2100", 1, "20260919"),  # reversal in week 25
            row("311", "H", "1000", "2100", 9, "20260921"),  # the as-of day: not history
            row("311", "H", "1000", "2100", 9, "20260322"),  # before the 26 weeks
            row("311", "H", "1000", "2001", 7, "20260601"),  # to the overflow hub: not demand
        ]
    )
    series = weekly_series(_demand_weekly(mseg, as_of, PlanningParams()), "40000001", "US10", 26)
    expected = np.zeros(26)
    expected[0], expected[25] = 5.0, 3.0
    assert np.array_equal(series, expected)


def test_the_sba_point_forecast_is_the_model_rate() -> None:
    """With ``forecast_model: sba`` (kept for comparisons) the point forecast rate is the model average."""
    registry = ContractRegistry.load(CONTRACTS)
    profile = PROFILES["test"]
    tables = generate(profile, registry).tables
    params = PlanningParams(forecast_model="sba")
    cases = plan(
        PlanningInputs.from_tables(tables, profile.ecc_as_of.date(), params), params
    ).replenishment_case
    assert cases.height > 0
    for row in cases.iter_rows(named=True):
        model = demand.DemandModel.from_json(row["demand_model"])
        assert row["daily_demand_rate"] == pytest.approx(model.rate_per_week / 7, abs=1e-4)


def test_engine_cases_carry_the_model_and_the_risk_on_the_test_profile() -> None:
    registry = ContractRegistry.load(CONTRACTS)
    profile = PROFILES["test"]
    tables = generate(profile, registry).tables
    params = PlanningParams()
    inputs = PlanningInputs.from_tables(tables, profile.ecc_as_of.date(), params)
    cases = plan(inputs, params).replenishment_case
    assert cases.get_column("demand_model").null_count() == 0
    for row in cases.iter_rows(named=True):
        model = demand.DemandModel.from_json(row["demand_model"])
        risk = row["stockout_risk_no_action"]
        if row["condition"] in ("pull", "excess") or not model.qualified:
            assert risk is None
        else:
            assert 0.0 <= risk <= 1.0
            assert f"{risk * 100:.2f}%" in row["rationale"]
