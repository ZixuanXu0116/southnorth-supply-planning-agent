"""Forecast-accuracy backtest (record 002 phase 1): segment classes, metrics, calibration, and windows."""

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from snm_data.contracts import ContractRegistry
from snm_data.generator.config import PROFILES
from snm_data.generator.run import generate
from snm_data.planning.backtest import (
    FORECASTERS,
    accuracy,
    demand_pattern,
    forecast_windows,
    frequency_bucket,
    render_report,
    sba,
    sba_quantile,
    tail_calibration,
    verdict,
)
from snm_data.planning.oracle import realized_outflow
from snm_data.planning.params import PlanningParams

CONTRACTS = Path(__file__).resolve().parents[2] / "data_contracts"


def test_demand_pattern_follows_syntetos_boylan() -> None:
    assert demand_pattern(np.zeros(13)) == "none"
    assert demand_pattern(np.full(13, 5.0)) == "smooth"  # every week, same size
    assert demand_pattern(np.array([1.0, 9.0] * 6 + [1.0])) == "erratic"  # every week, varying sizes
    assert demand_pattern(np.array([4.0, 0, 0, 4.0, 0, 0, 4.0, 0, 0, 4.0, 0, 0, 0])) == "intermittent"
    assert demand_pattern(np.array([1.0, 0, 0, 12.0, 0, 0, 0, 2.0, 0, 0, 0, 0, 0])) == "lumpy"


def test_frequency_buckets() -> None:
    assert [frequency_bucket(n) for n in (0, 1, 3, 4, 6, 7, 9, 10, 13)] == [
        "none in 13 weeks", "1-3 weeks", "1-3 weeks", "4-6 weeks", "4-6 weeks", "7-9 weeks", "7-9 weeks",
        "10-13 weeks", "10-13 weeks",
    ]  # fmt: skip


def _windows(rows: list[tuple[date, float, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "as_of": [r[0] for r in rows],
            "material": [f"m{i}" for i in range(len(rows))],
            "plant": ["US10"] * len(rows),
            "horizon": ["28"] * len(rows),
            "segment": ["s"] * len(rows),
            "model_p90": [None] * len(rows),
            "model_p95": [None] * len(rows),
            "model_p97": [None] * len(rows),
            "model_p98": [None] * len(rows),
            "model_p99": [None] * len(rows),
            "forecast": [r[1] for r in rows],
            "explicit": [0.0] * len(rows),
            "actual": [r[2] for r in rows],
        }
    )


def test_accuracy_metrics_and_calibration_by_hand() -> None:
    first, second = date(2026, 1, 5), date(2026, 1, 12)
    rows = [
        # first half (calibration): actual / forecast = 0.5, 1.0, 1.5, 2.0 and one quiet window (ignored)
        (first, 10.0, 5.0), (first, 10.0, 10.0), (first, 10.0, 15.0), (first, 10.0, 20.0), (first, 0.0, 0.0),
        # second half (test): within 1.9 x forecast, beyond it, and demand with no forecast
        (second, 10.0, 12.0), (second, 10.0, 25.0), (second, 0.0, 3.0),
    ]  # fmt: skip
    [row] = accuracy(_windows(rows), ["segment"]).iter_rows(named=True)
    forecast, actual = 60.0, 90.0
    assert row["windows"] == 7  # the quiet window is not active
    assert row["bias"] == pytest.approx(forecast / actual - 1)
    assert row["wape"] == pytest.approx((5 + 0 + 5 + 10 + 2 + 15 + 3) / actual)
    assert row["under"] == pytest.approx(5 / 7) and row["missed"] == pytest.approx(1 / 7)
    assert row["band"] == pytest.approx(1.85)  # 90th percentile of 0.5, 1.0, 1.5, 2.0 (linear)
    assert row["test_windows"] == 3 and row["coverage"] == pytest.approx(1 / 3)
    assert row["verdict"] == "too few windows"


def test_verdict_thresholds() -> None:
    assert verdict(0.9, 1.2, 100).label == "calibrated"
    assert verdict(0.9, 2.5, 100).label == "calibrated"  # width is weighed by cost, not capped
    assert verdict(0.7, 1.2, 100).label == "not calibrated"
    assert verdict(0.9, 1.2, 10).label == "too few windows"


def test_sba_by_hand() -> None:
    # Demand in weeks 1 and 4: size 4 then 2, first interval 2 weeks, then 3. With alpha 0.15:
    # size 4 + 0.15 (2 - 4) = 3.7; interval 2 + 0.15 (3 - 2) = 2.15; SBA = (1 - 0.075) x 3.7 / 2.15.
    rate, probability = sba(np.array([0.0, 4.0, 0.0, 0.0, 2.0, 0.0]))
    assert rate == pytest.approx(0.925 * 3.7 / 2.15) and probability == pytest.approx(1 / 2.15)
    assert sba(np.zeros(6)) == (0.0, 0.0)


def test_sba_quantiles_of_a_regular_demand_are_exact() -> None:
    # Five every week: every draw is the same path, so every percentile is the window's demand.
    levels = sba_quantile(np.full(26, 5.0), [14, 10], np.random.default_rng(1))
    assert levels[14] == pytest.approx([10.0] * 5) and levels[10] == pytest.approx([5 + 5 * 3 / 7] * 5)
    assert sba_quantile(np.zeros(26), [7], np.random.default_rng(1))[7] == pytest.approx([0.0] * 5)


def test_tail_calibration_by_hand() -> None:
    # SBA windows of one segment: on the first day the model's percentile is 10 and the actuals are 5, 10,
    # and 20 (ratios 0.5, 1, 2); the multiplier at each target is the ratio reaching it, 2. On the second
    # day the actuals are 9 and 15 against 10: raw, one of two holds; recalibrated (x 2), both.
    first, second = date(2026, 1, 5), date(2026, 1, 12)
    rows = [(first, 5.0), (first, 10.0), (first, 20.0), (second, 9.0), (second, 15.0)]
    windows = pl.DataFrame(
        {
            "as_of": [r[0] for r in rows],
            "material": [f"m{i}" for i in range(len(rows))],
            "plant": ["US10"] * len(rows),
            "horizon": ["lead time"] * len(rows),
            "segment": ["s"] * len(rows),
            "forecaster": ["sba"] * len(rows),
            "actual": [r[1] for r in rows],
            **{f"model_p{p}": [10.0] * len(rows) for p in (90, 95, 97, 98, 99)},
        }
    )
    table = tail_calibration(windows, ["segment"])
    assert table["target"].to_list() == [0.90, 0.97, 0.99]  # the service targets in use
    assert table["multiplier"].to_list() == pytest.approx([2.0] * 3)
    assert table["raw"].to_list() == pytest.approx([0.5] * 3)
    assert table["recalibrated"].to_list() == pytest.approx([1.0] * 3)


@pytest.fixture(scope="module")
def tables() -> dict[str, pl.DataFrame]:
    return generate(PROFILES["test"], ContractRegistry.load(CONTRACTS)).tables


def test_windows_compare_the_forecast_with_what_followed(tables) -> None:
    day = date(2026, 9, 7)
    windows = forecast_windows(tables, [day], horizons=(7,))
    assert windows.height > 0 and set(windows["horizon"].unique()) <= {"7", "lead time"}
    assert set(windows["forecaster"].unique()) == set(FORECASTERS)
    # Every forecaster sees the same windows and the same actuals; only SBA has its own percentiles.
    keys = ["material", "plant", "horizon"]
    per_window = windows.group_by(keys).agg(pl.len(), pl.col("actual").n_unique().alias("actuals"))
    assert (per_window["len"] == len(FORECASTERS)).all() and (per_window["actuals"] == 1).all()
    sba_rows = windows.filter(pl.col("forecaster") == "sba")
    assert (
        sba_rows["model_p90"].null_count() == 0
        and windows.filter(pl.col("forecaster") != "sba")["model_p90"].is_null().all()
    )
    assert (sba_rows["model_p99"] >= sba_rows["model_p90"] - 1e-9).all()
    assert (sba_rows["model_p90"] >= sba_rows["explicit"] - 1e-9).all()
    # No window runs past the last day of data, and the actual is the realized outflow of the window.
    last = tables["raw_sap_ecc.mseg"].get_column("budat_mkpf").max()
    end = date(int(last[:4]), int(last[4:6]), int(last[6:8])) + timedelta(days=1)
    assert all(day + timedelta(days=n) <= end for n in windows["horizon_days"].to_list())
    realized = realized_outflow(tables, day, 7, PlanningParams())
    week = windows.filter((pl.col("horizon") == "7") & (pl.col("forecaster") == "engine"))
    for row in week.iter_rows(named=True):
        expected = float(realized.get((row["material"], row["plant"]), np.zeros(7)).sum())
        assert row["actual"] == pytest.approx(expected)
    assert (week["explicit"] <= week["forecast"] + 1e-9).all()
    assert "# Forecast accuracy backtest: t" in render_report(windows, "t", [day], "test")
