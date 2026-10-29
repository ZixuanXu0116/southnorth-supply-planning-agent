"""The engine's demand model and stock-out risk (decision record 002, phases 2 and 3).

Croston's method with the Syntetos-Boylan correction (SBA) on weekly buckets of the outflow to production
gives the point forecast (engine 1.1.0). Simulated, the same model gives demand paths: every week has demand
with the estimated probability, its size drawn from the history's weeks with demand. A path is netted against
the explicit requirements week by week, as the point forecast is, so a week's demand is the larger of the
explicit requirements and the simulated amount. The stock-out risk of a supply plan is the share of paths
with a day below zero in a window.

The backtest found the model's 90th percentile honest but its upper tail too thin
(``evals/reports/forecast-accuracy-small.md``, phase 3): the model's probability is mapped through a
calibration curve per demand-frequency segment (the share of past windows that ran out at the same model
probability), and the calibrated probability is what is shown and checked against the service target.
Segments without a curve (no demand in the last 13 weeks) are not qualified: no risk is estimated for them and
the strict arrival rule applies.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from itertools import pairwise

import numpy as np

from .params import PlanningParams

TRAILING_WEEKS = 13
FREQUENCY_BUCKETS = (
    (0, "none in 13 weeks"),
    (3, "1-3 weeks"),
    (6, "4-6 weeks"),
    (9, "7-9 weeks"),
    (13, "10-13 weeks"),
)
QTY_TOLERANCE = 1e-6


def frequency_bucket(demand_weeks: int) -> str:
    """How regular the trailing demand was: weeks with any outflow out of the trailing 13."""
    for upper, label in FREQUENCY_BUCKETS:
        if demand_weeks <= upper:
            return label
    return FREQUENCY_BUCKETS[-1][1]


def sba(weekly: np.ndarray, alpha: float) -> tuple[float, float]:
    """Croston with the Syntetos-Boylan correction on a weekly series: (demand per week, probability that a
    week has demand). Sizes and intervals are smoothed on the weeks with demand; no demand gives (0, 0)."""
    weeks = np.flatnonzero(weekly > 0)
    if weeks.size == 0:
        return 0.0, 0.0
    size, interval = float(weekly[weeks[0]]), float(weeks[0] + 1)
    for previous, week in pairwise(weeks):
        size += alpha * (float(weekly[week]) - size)
        interval += alpha * (float(week - previous) - interval)
    return (1 - alpha / 2) * size / interval, min(1.0, 1.0 / interval)


def draw_weeks(
    probability: float, sizes: np.ndarray, paths: int, weeks: int, rng: np.random.Generator
) -> np.ndarray:
    """Simulated weekly demand, ``paths`` x ``weeks``: demand in a week with ``probability``, its size drawn
    from ``sizes``."""
    return (rng.random((paths, weeks)) < probability) * rng.choice(sizes, (paths, weeks))


@dataclass(frozen=True)
class DemandModel:
    """One material's demand model, stored with its case (``replenishment_case.demand_model``)."""

    model: str
    weeks: int
    alpha: float
    rate_per_week: float
    probability: float
    sizes: tuple[float, ...]
    frequency: str
    service_target: float
    # (model probability, calibrated probability) pairs of the segment; None where it did not qualify.
    calibration: tuple[tuple[float, float], ...] | None

    @property
    def qualified(self) -> bool:
        """Risk is estimated only where the backtest calibrated the model's probabilities."""
        return self.calibration is not None and self.probability > 0 and bool(self.sizes)

    def calibrate(self, probability: float) -> float:
        """The model's probability through the segment's curve: linear between its points, through (0, 0)
        and (1, 1), never decreasing."""
        if not self.calibration:
            return probability
        xs = [0.0, *(x for x, _ in self.calibration), 1.0]
        ys = np.maximum.accumulate([0.0, *(y for _, y in self.calibration), 1.0])
        return float(np.interp(probability, xs, ys))

    def to_json(self) -> str:
        data = asdict(self)
        data["sizes"] = list(self.sizes)
        data["calibration"] = [list(point) for point in self.calibration] if self.calibration else None
        return json.dumps(data, sort_keys=True)

    @classmethod
    def from_json(cls, text: str | dict | None) -> DemandModel | None:
        if not text:
            return None
        data = json.loads(text) if isinstance(text, str) else dict(text)
        data = {key: value for key, value in data.items() if key in cls.__dataclass_fields__}
        points = data.get("calibration")
        data["calibration"] = tuple((float(x), float(y)) for x, y in points) if points else None
        data["sizes"] = tuple(float(size) for size in data.get("sizes") or ())
        return cls(**data)


def fit(weekly: np.ndarray, criticality: str | None, params: PlanningParams) -> DemandModel:
    """The model of one weekly outflow series (oldest week first)."""
    rate, probability = sba(weekly, params.sba_alpha)
    recent = weekly[-TRAILING_WEEKS:]
    frequency = frequency_bucket(int((recent > 0).sum()))
    target = params.service_target.get(str(criticality or "C"), params.service_target["C"])
    curve = params.risk_calibration.get(frequency)
    return DemandModel(
        model="sba",
        weeks=int(weekly.size),
        alpha=params.sba_alpha,
        rate_per_week=round(rate, 6),
        probability=round(probability, 6),
        sizes=tuple(round(float(size), 3) for size in weekly[weekly > 0]),
        frequency=frequency,
        service_target=target,
        calibration=tuple((float(x), float(y)) for x, y in curve) if curve else None,
    )


def seed(case_id: str) -> int:
    """The simulation seed of a case: every option of the case is priced on the same demand paths."""
    return int(hashlib.sha256(case_id.encode()).hexdigest()[:16], 16)


def net_paths(weekly: np.ndarray, explicit: np.ndarray, horizon: int) -> np.ndarray:
    """Daily forecast per path, ``paths`` x (horizon + 1): each week's simulated amount net of the explicit
    demand of the same week, spread evenly over the week's days (the point forecast's netting, per path)."""
    out = np.zeros((weekly.shape[0], horizon + 1))
    for week, start in enumerate(range(1, horizon + 1, 7)):
        end = min(start + 7, horizon + 1)
        days = end - start
        net = np.maximum(0.0, weekly[:, week] * days / 7 - float(explicit[start:end].sum()))
        out[:, start:end] = (net / days)[:, None]
    return out


@dataclass(frozen=True)
class Risk:
    """Stock-out risk of one supply plan (a projection with an option's receipts)."""

    probability: float  # calibrated chance of a day below zero up to the window's last day
    model_probability: float  # the share of simulated paths with such a day, before calibration
    expected_days_below_zero: float  # over the whole horizon, on the model's paths
    meets_target: bool  # the calibrated probability is at most one minus the service target
    window_end: int  # day offset of the window's last day


def stockout_risk(
    model: DemandModel,
    projected: np.ndarray,
    point_net: np.ndarray,
    explicit: np.ndarray,
    window_end: int,
    case_id: str,
    paths: int,
) -> Risk:
    """The risk of a projection (``projected``, the point line with the plan's receipts) under the model.

    ``point_net`` is the point forecast net of explicit demand per day and ``explicit`` the explicit demand
    (reservations and planned orders) per day; a path's stock is the point line plus the cumulative
    difference between the point forecast and the path's forecast. The same seed gives every plan of a case
    the same paths."""
    horizon = projected.size - 1
    weeks = -(-horizon // 7)
    rng = np.random.default_rng(seed(case_id))
    weekly = draw_weeks(model.probability, np.asarray(model.sizes), paths, weeks, rng)
    end = min(max(window_end, 0), horizon)

    forecast = net_paths(weekly, explicit, horizon)
    below = projected[None, :] + np.cumsum(point_net[None, :] - forecast, axis=1) < -QTY_TOLERANCE
    share = float(below[:, : end + 1].any(axis=1).mean())
    probability = model.calibrate(share)
    return Risk(
        probability=probability,
        model_probability=share,
        expected_days_below_zero=float(below.sum(axis=1).mean()),
        meets_target=bool(probability <= 1 - model.service_target + 1e-12),
        window_end=end,
    )
