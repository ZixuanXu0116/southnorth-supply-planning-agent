"""Forecast-accuracy backtest (decision record 002, phase 1).

On each past as-of day the engine's demand forecast is rebuilt as the extraction of that day would have
shown it (``history.tables_as_of``, then the engine's own projection) and compared with the outflow to
manufacturing that followed (``oracle.realized_outflow``: staging to the production supply area). The
comparison is made over fixed horizons and over each material's planned delivery time, the window an order
decision has to protect. Windows are segmented by criticality, by the shape of the trailing demand
(Syntetos-Boylan classes on 13 weekly buckets), and by how much of the window's forecast is explicit
(released orders' reservations and the backlog) rather than the trailing rate.

Calibration: per segment, an empirical band (the 90th percentile of actual over forecast) is fitted on the
first half of the as-of days and checked on the second half. Coverage close to 90 % means the band can be
trusted; the band's width says how much safety the forecast needs.

Phase 2 compares forecasters on the same windows, each netted against the explicit requirements the way the
engine nets its rate: the engine's own (trailing 28-day staging rate), a 91-day staging rate, a 91-day rate
of the production requirements themselves (uncensored by stock-outs), and Croston-SBA on 26 weekly buckets
with a 90th percentile from its own model (demand weeks drawn with the estimated probability, sizes resampled
from the history), whose calibration is checked directly.

Approximations (stated in the report): past days have no planned orders (they exist only at the
extraction), so the past forecast is the released orders' reservations plus the trailing rate; reservations
are due at the order start while staging happens two days earlier; realized outflow is staging as far as
stock allowed (censored demand); windows of consecutive weeks overlap and are not independent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import polars as pl

from . import demand
from .demand import FREQUENCY_BUCKETS, TRAILING_WEEKS, draw_weeks, frequency_bucket
from .engine import _forecast, plan
from .history import tables_as_of
from .inputs import PlanningInputs
from .oracle import realized_outflow
from .params import PlanningParams

HORIZONS = (7, 14, 28, 56)
LEAD_TIME = "lead time"
# Syntetos-Boylan cut-offs: average inter-demand interval (in weeks) and squared coefficient of variation.
ADI_CUT = 1.32
CV2_CUT = 0.49
BAND_QUANTILE = 0.9
# Record 002 (accepted): a segment qualifies when its 90 % band holds in at least 85 % of the test windows;
# the band's width is reported, not capped (the cost comparison weighs it).
COVERAGE_MIN = 0.85
MIN_TEST_WINDOWS = 30
EXPLICIT_BUCKETS = ((0.8, "mostly explicit"), (0.2, "mixed"), (-1.0, "mostly trailing rate"))
# Phase 2 forecasters (record 002, options 2 to 4), in report order.
FORECASTERS = ("engine", "rate_91", "requirements_91", "sba")
FORECASTER_NOTES = {
    "engine": "the engine today: trailing 28-day staging rate, netted weekly against the released orders",
    "rate_91": "trailing 91-day staging rate, netted the same way (option 2)",
    "requirements_91": "trailing 91-day rate of production requirements, uncensored by stock-outs (option 4)",
    "sba": "Croston-SBA on 26 weekly buckets, with its own 90th percentile (option 3)",
}
SBA_WEEKS = 26
SBA_ALPHA = 0.15
SBA_DRAWS = 2000
# The model's percentiles checked against what followed: the band's 90 % and the service targets of record 002
# (stock-out probability at most 10 % for C, 3 % for B, 1 % for A parts, as amended on 2026-10-19).
MODEL_QUANTILES = (0.90, 0.95, 0.97, 0.98, 0.99)


def demand_pattern(weekly: np.ndarray) -> str:
    """Syntetos-Boylan class of a weekly demand series: smooth, erratic, intermittent, lumpy, or none."""
    sizes = weekly[weekly > 0]
    if sizes.size == 0:
        return "none"
    adi = weekly.size / sizes.size
    cv2 = float((sizes.std() / sizes.mean()) ** 2) if sizes.size > 1 else 0.0
    if adi < ADI_CUT:
        return "smooth" if cv2 < CV2_CUT else "erratic"
    return "intermittent" if cv2 < CV2_CUT else "lumpy"


def sba(weekly: np.ndarray, alpha: float = SBA_ALPHA) -> tuple[float, float]:
    """The engine's SBA model (``demand.sba``) with the backtest's smoothing constant."""
    return demand.sba(weekly, alpha)


def sba_quantile(
    weekly: np.ndarray,
    lengths: list[int],
    rng: np.random.Generator,
    quantiles: tuple[float, ...] = MODEL_QUANTILES,
) -> dict[int, np.ndarray]:
    """The ``quantiles`` of demand over each window length (days) under the SBA model: every week has demand
    with the estimated probability, its size drawn from the history's weeks with demand."""
    sizes = weekly[weekly > 0]
    _, probability = sba(weekly)
    if sizes.size == 0 or probability == 0:
        return {length: np.zeros(len(quantiles)) for length in lengths}
    weeks = max(1, -(-max(lengths) // 7))
    draws = draw_weeks(probability, sizes, SBA_DRAWS, weeks, rng)
    cumulative = np.cumsum(draws, axis=1)
    out = {}
    for length in lengths:
        full, part = divmod(length, 7)
        total = cumulative[:, full - 1] if full else np.zeros(SBA_DRAWS)
        if part and full < weeks:
            total = total + draws[:, full] * part / 7
        out[length] = np.quantile(total, quantiles)
    return out


def _q(quantile: float) -> str:
    """Column name of a model percentile: 0.9 -> model_p90."""
    return f"model_p{round(quantile * 100)}"


def _requirement_rates(resb: pl.DataFrame, day: date, plants: tuple[str, ...], days: int = 91) -> dict:
    """Daily rate of the released orders' requirements (RESB, type SB) dated in the trailing ``days``."""
    start = (day - timedelta(days=days)).strftime("%Y%m%d")
    frame = (
        resb.filter(
            (pl.col("bdart") == "SB")
            & pl.col("werks").is_in(list(plants))
            & (pl.col("bdter") >= start)
            & (pl.col("bdter") < day.strftime("%Y%m%d"))
        )
        .with_columns(pl.col("matnr").str.strip_chars_start("0").alias("material"))
        .group_by("material", "werks")
        .agg((pl.col("bdmng").cast(pl.Float64).sum() / days).alias("rate"))
    )
    return {(r["material"], r["werks"]): r["rate"] for r in frame.iter_rows(named=True)}


def _data_end(tables: dict[str, pl.DataFrame]) -> date:
    """The first day the extraction has no material documents for."""
    last = tables["raw_sap_ecc.mseg"].get_column("budat_mkpf").max()
    return date(int(last[:4]), int(last[4:6]), int(last[6:8])) + timedelta(days=1)


def forecast_windows(
    tables: dict[str, pl.DataFrame],
    days: list[date],
    horizons: tuple[int, ...] = HORIZONS,
    params: PlanningParams | None = None,
) -> pl.DataFrame:
    """One row per as-of day, material, plant, window, and forecaster: forecast, its explicit part, the
    forecaster's own 90th percentile when it has one, and the actual outflow.

    A window starts on the as-of day (day 0 carries the backlog) and is dropped when it would end after the
    last day of data."""
    params = params or PlanningParams()
    end = _data_end(tables)
    rows: list[dict] = []
    for day in days:
        past = tables_as_of(tables, day)
        inputs = PlanningInputs.from_tables(past, day, params)
        position = plan(inputs, params).supply_position
        if position.height == 0:
            continue
        lead = {
            (r["material"], r["plant"]): int(r["planned_delivery_days"] or 0)
            for r in inputs.materials.iter_rows(named=True)
        }
        criticality = {
            (r["material"], r["plant"]): r["criticality"] for r in inputs.materials.iter_rows(named=True)
        }
        longest = max([*horizons, *lead.values()])
        actual = realized_outflow(tables, day, longest, params)
        history = realized_outflow(tables, day - timedelta(days=7 * SBA_WEEKS), 7 * SBA_WEEKS, params)
        requirements = _requirement_rates(past["raw_sap_ecc.resb"], day, params.plants)
        rng = np.random.default_rng([2026, day.toordinal()])
        for (material, plant), frame in (
            position.sort("day_offset").partition_by(["material", "plant"], as_dict=True).items()
        ):
            key = (material, plant)
            firm = frame.get_column("firm_demand_qty").to_numpy()
            planned = frame.get_column("planned_order_qty").to_numpy()
            backlog = float(frame.get_column("backlog_qty")[0])
            horizon = firm.size - 1
            explicit = firm + planned
            explicit[0] += backlog
            daily = history.get(key, np.zeros(7 * SBA_WEEKS))
            weekly = daily.reshape(SBA_WEEKS, 7).sum(axis=1)
            recent = weekly[-TRAILING_WEEKS:]
            rates = {
                "rate_91": float(daily[-91:].sum()) / 91,
                "requirements_91": float(requirements.get(key, 0.0)),
                "sba": sba(weekly)[0] / 7,
            }
            demand = {"engine": firm + frame.get_column("forecast_qty").to_numpy()}
            for name, rate in rates.items():
                demand[name] = firm + _forecast(rate, firm + planned, horizon) + planned
            for series in demand.values():
                series[0] += backlog
            realized = actual.get(key, np.zeros(longest))
            windows = [(str(h), h) for h in horizons]
            if lead.get(key, 0) > 0:
                windows.append((LEAD_TIME, lead[key]))
            windows = [(label, n) for label, n in windows if day + timedelta(days=n) <= end and n <= horizon]
            model = sba_quantile(weekly, [n for _, n in windows], rng) if windows else {}
            base = {
                "as_of": day,
                "material": material,
                "plant": plant,
                "criticality": criticality.get(key) or "C",
                "pattern": demand_pattern(recent),
                "frequency": frequency_bucket(int((recent > 0).sum())),
            }
            for label, length in windows:
                window_explicit = float(explicit[:length].sum())
                for name in FORECASTERS:
                    # The model's percentiles of the whole window: at least its explicit requirements.
                    levels = (
                        {
                            _q(q): max(window_explicit, float(v))
                            for q, v in zip(MODEL_QUANTILES, model[length], strict=True)
                        }
                        if name == "sba"
                        else dict.fromkeys((_q(q) for q in MODEL_QUANTILES), None)
                    )
                    rows.append(
                        {
                            **base,
                            "forecaster": name,
                            "horizon": label,
                            "horizon_days": length,
                            "forecast": float(demand[name][:length].sum()),
                            "explicit": window_explicit,
                            **levels,
                            "actual": float(realized[:length].sum()),
                        }
                    )
    schema = {
        "as_of": pl.Date, "material": pl.String, "plant": pl.String, "criticality": pl.String,
        "pattern": pl.String, "frequency": pl.String, "forecaster": pl.String, "horizon": pl.String,
        "horizon_days": pl.Int64, "forecast": pl.Float64, "explicit": pl.Float64,
        **{_q(q): pl.Float64 for q in MODEL_QUANTILES},
        "actual": pl.Float64,
    }  # fmt: skip
    frame = pl.DataFrame(rows, schema=schema)
    share = pl.when(pl.col("forecast") > 0).then(pl.col("explicit") / pl.col("forecast")).otherwise(0.0)
    bucket = pl.when(share >= EXPLICIT_BUCKETS[0][0]).then(pl.lit(EXPLICIT_BUCKETS[0][1]))
    bucket = bucket.when(share >= EXPLICIT_BUCKETS[1][0]).then(pl.lit(EXPLICIT_BUCKETS[1][1]))
    return frame.with_columns(bucket.otherwise(pl.lit(EXPLICIT_BUCKETS[2][1])).alias("forecast_source"))


@dataclass(frozen=True)
class Verdict:
    label: str
    reason: str


def verdict(coverage: float | None, band: float | None, test_windows: int) -> Verdict:
    """Whether a segment's forecast can carry a probability-based decision (record 002)."""
    if test_windows < MIN_TEST_WINDOWS or coverage is None or band is None:
        return Verdict("too few windows", f"fewer than {MIN_TEST_WINDOWS} test windows")
    if coverage < COVERAGE_MIN:
        return Verdict("not calibrated", f"the 90 % band holds in {coverage:.0%} of the test windows")
    return Verdict("calibrated", f"the 90 % band ({band:.2f} x the forecast) holds in {coverage:.0%}")


def accuracy(windows: pl.DataFrame, by: list[str]) -> pl.DataFrame:
    """Accuracy and calibration per segment of the active windows (forecast or actual above zero).

    bias = sum(forecast) / sum(actual) - 1; wape = sum(|forecast - actual|) / sum(actual); under = share of
    windows whose actual exceeds the forecast; missed = share with actual demand and no forecast. The band is
    the 90th percentile of actual / forecast on the first half of the as-of days (windows with a forecast);
    coverage is the share of the second half's windows whose actual stays within band x forecast. A window
    is active when it had demand or any forecaster forecast some, so forecasters share their windows; model
    coverage is the share of active windows within the forecaster's own 90th percentile, when it has one."""
    keys = ["as_of", "material", "plant", "horizon"]
    active = windows.filter((pl.col("forecast").max().over(keys) > 0) | (pl.col("actual") > 0))
    if active.height == 0:
        return pl.DataFrame()
    days = active.select("horizon", "as_of").unique().sort(["horizon", "as_of"])
    days = days.with_columns(
        (pl.col("as_of").rank("ordinal").over("horizon") > pl.len().over("horizon") / 2).alias("test")
    )
    active = active.join(days, on=["horizon", "as_of"])
    tol = 1e-9
    summary = active.group_by(by).agg(
        pl.len().alias("windows"),
        pl.col("material").n_unique().alias("materials"),
        pl.col("forecast").sum().alias("forecast_total"),
        pl.col("actual").sum().alias("actual_total"),
        (pl.col("forecast") - pl.col("actual")).abs().sum().alias("abs_error"),
        (pl.col("actual") > pl.col("forecast") + tol).mean().alias("under"),
        ((pl.col("forecast") <= tol) & (pl.col("actual") > tol)).mean().alias("missed"),
        pl.col("explicit").sum().alias("explicit_total"),
        *[
            pl.when(pl.col(_q(q)).is_not_null())
            .then(pl.col("actual") <= pl.col(_q(q)) + tol)
            .otherwise(None)
            .mean()
            .alias(f"holds_{_q(q)}")
            for q in MODEL_QUANTILES
        ],
    )
    band = (
        active.filter(~pl.col("test") & (pl.col("forecast") > tol))
        .group_by(by)
        .agg((pl.col("actual") / pl.col("forecast")).quantile(BAND_QUANTILE, "linear").alias("band"))
    )
    test = active.filter(pl.col("test")).join(band, on=by, how="left")
    coverage = test.group_by(by).agg(
        pl.len().alias("test_windows"),
        (pl.col("actual") <= pl.col("band") * pl.col("forecast") + tol).mean().alias("coverage"),
    )
    out = (
        summary.join(band, on=by, how="left")
        .join(coverage, on=by, how="left")
        .with_columns(
            pl.when(pl.col("actual_total") > 0)
            .then(pl.col("forecast_total") / pl.col("actual_total") - 1)
            .otherwise(None)
            .alias("bias"),
            pl.when(pl.col("actual_total") > 0)
            .then(pl.col("abs_error") / pl.col("actual_total"))
            .otherwise(None)
            .alias("wape"),
            pl.when(pl.col("forecast_total") > 0)
            .then(pl.col("explicit_total") / pl.col("forecast_total"))
            .otherwise(0.0)
            .alias("explicit_share"),
            pl.col("test_windows").fill_null(0),
        )
    )
    labels = [
        verdict(row["coverage"], row["band"], int(row["test_windows"])).label
        for row in out.iter_rows(named=True)
    ]
    return out.with_columns(pl.Series("verdict", labels, dtype=pl.String)).sort(by)


# Record 002 service targets (amended 2026-10-19): the percentile of lead-time demand an order must cover, per
# criticality; the engine's parameters hold them.
SERVICE_TARGET = PlanningParams().service_target
TAIL_TARGETS = tuple(sorted(set(SERVICE_TARGET.values())))


def _halves(windows: pl.DataFrame) -> pl.DataFrame:
    """Mark the second half of each horizon's as-of days as the test half (as ``accuracy`` does)."""
    days = windows.select("horizon", "as_of").unique().sort(["horizon", "as_of"])
    days = days.with_columns(
        (pl.col("as_of").rank("ordinal").over("horizon") > pl.len().over("horizon") / 2).alias("test")
    )
    return windows.join(days, on=["horizon", "as_of"])


def tail_calibration(windows: pl.DataFrame, by: list[str]) -> pl.DataFrame:
    """Phase 3, step 1: per segment and target percentile, the multiplier on the SBA model's own percentile
    that reaches the target on the first half of the days (the target-th percentile of actual / model on the
    calibration windows), and the coverage of the raw and of the recalibrated percentile on the second half.
    Windows with no demand and no model demand are left out; demand against a zero percentile stays
    uncovered whatever the multiplier. The multiplier is never below 1: a percentile that already holds is not
    thinned."""
    sba_rows = windows.filter(pl.col("forecaster") == "sba")
    active = _halves(sba_rows.filter((pl.col("actual") > 0) | (pl.col("model_p90") > 0)))
    tol = 1e-9
    frames = []
    for quantile in TAIL_TARGETS:
        column = _q(quantile)
        multiplier = (
            active.filter(~pl.col("test") & (pl.col(column) > tol))
            .group_by(by)
            .agg(
                (pl.col("actual") / pl.col(column))
                .quantile(quantile, "higher")
                .clip(lower_bound=1.0)
                .alias("multiplier")
            )
        )
        test = active.filter(pl.col("test")).join(multiplier, on=by, how="left")
        frames.append(
            test.group_by(by)
            .agg(
                pl.len().alias("test_windows"),
                pl.col("multiplier").first(),
                (pl.col("actual") <= pl.col(column) + tol).mean().alias("raw"),
                (pl.col("actual") <= pl.col("multiplier") * pl.col(column) + tol)
                .mean()
                .alias("recalibrated"),
            )
            .with_columns(pl.lit(quantile).alias("target"))
        )
    return pl.concat(frames).sort([*by, "target"])


def risk_calibration(windows: pl.DataFrame, by: list[str]) -> pl.DataFrame:
    """Phase 3, the calibration curve the engine uses: per segment and model percentile, the model's own
    exceedance probability (one minus the level), the share of windows whose actual exceeded that percentile
    on the first half of the days (the calibrated probability), and the same share on the second half (the
    check). Windows with no demand and no model demand are left out, as in ``tail_calibration``."""
    sba_rows = windows.filter(pl.col("forecaster") == "sba")
    active = _halves(sba_rows.filter((pl.col("actual") > 0) | (pl.col("model_p90") > 0)))
    tol = 1e-9
    frames = []
    for quantile in MODEL_QUANTILES:
        exceeded = pl.col("actual") > pl.col(_q(quantile)) + tol
        fit = active.filter(~pl.col("test")).group_by(by).agg(exceeded.mean().alias("calibrated"))
        check = (
            active.filter(pl.col("test"))
            .group_by(by)
            .agg(exceeded.mean().alias("observed"), pl.len().alias("test_windows"))
        )
        frames.append(
            fit.join(check, on=by, how="full", coalesce=True).with_columns(
                pl.lit(round(1 - quantile, 2)).alias("model_risk")
            )
        )
    return pl.concat(frames).sort([*by, "model_risk"])


def _calibration_table(frame: pl.DataFrame, by: list[str]) -> list[str]:
    head = [*by, "Model's risk", "Calibrated (first half)", "Observed (second half)", "Test windows"]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for row in _ordered(frame, by).iter_rows(named=True):
        cells = [
            *(str(row[key]) for key in by),
            f"{row['model_risk']:.0%}",
            _share(row["calibrated"]),
            _share(row["observed"]),
            f"{row['test_windows'] or 0:,}",
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def _tail_table(frame: pl.DataFrame, by: list[str]) -> list[str]:
    head = [*by, "Target", "Test windows", "Multiplier", "Raw percentile holds", "Recalibrated holds"]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for row in _ordered(frame, by).iter_rows(named=True):
        multiplier = "n/a" if row["multiplier"] is None else f"{row['multiplier']:.2f}"
        cells = [
            *(str(row[key]) for key in by),
            f"P{round(row['target'] * 100)}",
            f"{row['test_windows']:,}",
            multiplier,
            _share(row["raw"]),
            _share(row["recalibrated"]),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return lines


# ---------------------------------------------------------------------------------------------- report


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.0%}" if value < 0 or value > 0 else "0%"


def _share(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


ORDER = {
    "forecaster": list(FORECASTERS),
    "horizon": [*(str(h) for h in HORIZONS), LEAD_TIME],
    "frequency": [label for _, label in FREQUENCY_BUCKETS],
    "pattern": ["smooth", "erratic", "intermittent", "lumpy", "none"],
    "forecast_source": [label for _, label in EXPLICIT_BUCKETS],
}


def _ordered(frame: pl.DataFrame, by: list[str]) -> pl.DataFrame:
    """Segments in their natural order (horizons by length, frequency from rare to regular, …)."""
    keys = [
        pl.col(key).replace_strict({v: i for i, v in enumerate(ORDER[key])}, default=len(ORDER[key]))
        if key in ORDER
        else pl.col(key)
        for key in by
    ]
    return frame.sort(keys)


def _table(frame: pl.DataFrame, by: list[str]) -> list[str]:
    frame = _ordered(frame, by)
    head = [
        *by,
        "Windows",
        "Materials",
        "Bias",
        "WAPE",
        "Under",
        "Missed",
        "Explicit",
        "Band (P90)",
        "Coverage",
        "Verdict",
        "Model holds at " + " / ".join(f"P{round(q * 100)}" for q in MODEL_QUANTILES),
    ]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for row in frame.iter_rows(named=True):
        band = "n/a" if row["band"] is None else f"{row['band']:.2f}"
        cells = [
            *(str(row[key]) for key in by),
            f"{row['windows']:,}",
            str(row["materials"]),
            _pct(row["bias"]),
            _share(row["wape"]),
            _share(row["under"]),
            _share(row["missed"]),
            _share(row["explicit_share"]),
            band,
            _share(row["coverage"]),
            row["verdict"],
            "n/a"
            if row[f"holds_{_q(MODEL_QUANTILES[0])}"] is None
            else " / ".join(_share(row[f"holds_{_q(q)}"]) for q in MODEL_QUANTILES),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return lines


COLUMNS_NOTE = (
    "On each as-of day the engine's demand forecast is rebuilt as that day's extraction would have shown "
    "it and compared with the staging to production that followed. Columns: **Bias** = total forecast / "
    "total actual - 1 (negative: the forecast is too low); **WAPE** = total absolute error / total actual; "
    "**Under** = share of windows whose actual exceeded the forecast; **Missed** = share with demand but no "
    "forecast; **Explicit** = share of the forecast that is released orders' reservations and backlog "
    "rather than the trailing rate; **Band (P90)** = the 90th percentile of actual / forecast on the first "
    "half of the days; **Coverage** = share of the second half's windows within band x forecast "
    "(calibrated near 90 %). **Verdict** (record 002): *calibrated* when coverage is at least "
    f"{COVERAGE_MIN:.0%} (the segment qualifies; the band's width enters the cost comparison), *not "
    f"calibrated* below, *too few windows* below {MIN_TEST_WINDOWS} test windows."
)
APPROXIMATIONS = (
    "- Every forecaster is netted against the released orders' requirements week by week, as the engine "
    "nets its rate; the SBA percentile is the larger of the window's explicit requirements and the model's "
    "90th percentile of demand over the window (an approximation of the netting).",
    "- Past days have no planned orders (they exist only at the extraction), so the past forecast is the "
    "released orders' reservations plus the trailing rate; the live engine adds the planned orders' "
    "requirements for weeks 2 to 13. In this synthetic data the planned orders are drawn around each "
    "product's trailing rate, so they carry little information beyond it; a real MRP run would.",
    "- Reservations are due at the order start; staging happens two days earlier, which moves demand "
    "across a window's edge by up to two days.",
    "- Realized outflow is staging as far as stock allowed: a stock-out hides demand until it is staged.",
    "- Windows of consecutive weeks overlap, so the counts overstate the independent evidence.",
)


def render_report(windows: pl.DataFrame, name: str, days: list[date], profile: str) -> str:
    engine = windows.filter(pl.col("forecaster") == "engine")
    lead = engine.filter(pl.col("horizon") == LEAD_TIME)
    lead_all = windows.filter(pl.col("horizon") == LEAD_TIME)
    active = engine.filter((pl.col("forecast") > 0) | (pl.col("actual") > 0))
    plants = ", ".join(sorted(windows["plant"].unique()))
    lines = [
        f"# Forecast accuracy backtest: {name}",
        "",
        f"Decision record 002, phases 1 and 2. Profile `{profile}`, plant {plants}; {len(days)} weekly as-of "
        f"days from {days[0]} to {days[-1]}; {active.height:,} active windows (forecast or actual above "
        f"zero) of {engine.height:,} per forecaster. Deterministic, no model calls (`uv run snm-data "
        "forecast-backtest`).",
        "",
        COLUMNS_NOTE,
        "",
        "# Phase 1: the engine's forecast",
        "",
        "## By horizon",
        "",
        *_table(accuracy(engine, ["horizon"]), ["horizon"]),
        "",
        "## Over the planned delivery time (the window an order decision protects)",
        "",
        "### By criticality",
        "",
        *_table(accuracy(lead, ["criticality"]), ["criticality"]),
        "",
        "### By trailing demand pattern (13 weekly buckets)",
        "",
        *_table(accuracy(lead, ["pattern"]), ["pattern"]),
        "",
        "### By trailing demand frequency (weeks with outflow in the last 13)",
        "",
        *_table(accuracy(lead, ["frequency"]), ["frequency"]),
        "",
        "### By forecast source",
        "",
        *_table(accuracy(lead, ["forecast_source"]), ["forecast_source"]),
        "",
        "### By criticality and demand frequency (the candidate segments)",
        "",
        *_table(accuracy(lead, ["criticality", "frequency"]), ["criticality", "frequency"]),
        "",
        "# Phase 2: forecasters compared over the planned delivery time",
        "",
        *[f"- `{key}`: {note}." for key, note in FORECASTER_NOTES.items()],
        "",
        "*Model holds at P90 / P95 / P97 / P98 / P99* is the share of windows whose actual stayed within "
        "the forecaster's own percentile (Croston-SBA only). Each should reach its level for the model's "
        "probabilities to be used as they are; P90, P97, and P99 are the service targets of record 002 "
        "(amended 2026-10-19) for C, B, and A parts.",
        "",
        "## All materials",
        "",
        *_table(accuracy(lead_all, ["forecaster"]), ["forecaster"]),
        "",
        "## By demand frequency",
        "",
        *_table(accuracy(lead_all, ["frequency", "forecaster"]), ["frequency", "forecaster"]),
        "",
        "## By criticality",
        "",
        *_table(accuracy(lead_all, ["criticality", "forecaster"]), ["criticality", "forecaster"]),
        "",
        "# Phase 3: the model's probabilities calibrated per segment",
        "",
        "The model's stock-out probabilities are too low in the tail. The engine maps each one through a "
        "calibration curve per demand-frequency segment: at each of the model's percentiles (90th to 99th), "
        "the model's own exceedance probability against the share of windows whose demand exceeded that "
        "percentile on the first half of the days; the second half checks it. Between the points the curve "
        "is linear, through 0 and 1 at the ends; the calibrated probability is what the case pages show and "
        "what the service targets (A 1 %, B 3 %, C 10 %) are checked against. In every segment with outflow "
        "in the last 13 weeks the second half ran out at most as often as the curve says, so the curve is "
        "conservative there; materials without outflow in 13 weeks stay unqualified (the second half ran out "
        "in 62 % of windows whatever the model said).",
        "",
        "## By demand frequency",
        "",
        *_calibration_table(risk_calibration(lead_all, ["frequency"]), ["frequency"]),
        "",
        "## By criticality",
        "",
        *_calibration_table(risk_calibration(lead_all, ["criticality"]), ["criticality"]),
        "",
        "## Step 1, superseded: a multiplier on the model's percentile",
        "",
        "The first approach multiplied the model's percentile per segment until it held at the target. It "
        "holds, but demand comes in whole lumps, so the multiplier overshoots (it lands on ratios such as "
        "12 / 8) and, applied to the simulated lumps, turns a model risk of 1-3 % into about 13 % where the "
        "backtest observes 4-7 %. Kept for the record; the engine uses the curve above.",
        "",
        "### By demand frequency",
        "",
        *_tail_table(tail_calibration(lead_all, ["frequency"]), ["frequency"]),
        "",
        "### By criticality, at its service target (A 99 %, B 97 %, C 90 %)",
        "",
        *_tail_table(
            tail_calibration(lead_all, ["criticality"]).filter(
                pl.col("target")
                == pl.col("criticality").replace_strict(SERVICE_TARGET, default=SERVICE_TARGET["C"])
            ),
            ["criticality"],
        ),
        "",
        "## Approximations",
        "",
        *APPROXIMATIONS,
    ]
    return "\n".join(lines) + "\n"
