"""Evidence for decision record 002 (demand-variability term): the shape of hub demand behind the engine's
cases, and what happened to those cases on the realized demand.

``shape`` classifies every shortage and warning case of 12 past Mondays by the trailing 28-day hub outflow
that produced its demand rate (order days, single-order dominance) and by its horizon, against whether the
hub really went below zero in the next 60 days (the oracle's ``do_nothing`` cost is positive). ``window``
reruns the replay and the oracle with 28-, 56-, and 91-day demand windows.

    uv run python evals/analysis/decision_002_demand_shape.py shape
    uv run python evals/analysis/decision_002_demand_shape.py window

Deterministic; ``shape`` takes about two minutes on the ``small`` profile, ``window`` about six.
"""

from __future__ import annotations

import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl

from snm_data.planning.engine import plan
from snm_data.planning.history import HISTORY_TABLES, load_tables, replay, tables_as_of
from snm_data.planning.inputs import PlanningInputs
from snm_data.planning.oracle import evaluate_day, realized_outflow, summarize
from snm_data.planning.params import PlanningParams
from snm_data.planning.run import RAW_INPUT_TABLES

REPO_ROOT = Path(__file__).resolve().parents[2]
START = date(2026, 5, 4)
ORACLE_WEEKS = 12
REPLAY_WEEKS = 16
HORIZON = 60


def _load() -> dict[str, pl.DataFrame]:
    return load_tables(REPO_ROOT / "out" / "small", RAW_INPUT_TABLES + HISTORY_TABLES)


def _table(frame: pl.DataFrame) -> str:
    columns = frame.columns
    lines = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    for row in frame.iter_rows():
        lines.append("| " + " | ".join(str(v) for v in row) + " |")
    return "\n".join(lines)


def shape(tables: dict[str, pl.DataFrame]) -> str:
    params = PlanningParams()
    window = params.forecast_window_days
    rows = []
    for week in range(ORACLE_WEEKS):
        as_of = START + timedelta(weeks=week)
        outputs = plan(PlanningInputs.from_tables(tables_as_of(tables, as_of), as_of, params), params)
        cases = outputs.replenishment_case.filter(pl.col("condition") != "excess")
        trailing = realized_outflow(tables, as_of - timedelta(days=window), window, params)
        outcomes = {o.case_id: o for o in evaluate_day(tables, as_of, HORIZON, params)}
        for case in cases.iter_rows(named=True):
            outcome = outcomes.get(case["case_id"])
            if outcome is None:
                continue
            series = trailing.get((case["material"], case["plant"]), np.zeros(window))
            total = float(series.sum())
            rows.append(
                {
                    "condition": case["condition"],
                    "criticality": case["criticality"],
                    "days_to_trigger": (case["trigger_date"] - as_of).days,
                    "order_days": int((series > 0).sum()),
                    "max_share": float(series.max() / total) if total > 0 else float("nan"),
                    "below_zero": outcome.costs["do_nothing"] > 0,
                    "oracle": outcome.oracle,
                }
            )
    df = pl.DataFrame(rows)
    out = []
    for condition in ("shortage", "warning"):
        sub = df.filter(pl.col("condition") == condition)
        out.append(
            f"**{condition}**: {sub.height} case instances; {sub['below_zero'].mean():.1%} went below zero on "
            f"the realized demand; the oracle chose do_nothing on {(sub['oracle'] == 'do_nothing').mean():.1%}; "
            f"{(sub['days_to_trigger'] == 0).mean():.0%} were dated the as-of day itself; median order days in "
            f"the trailing {window}: {sub['order_days'].median():.0f}; no outflow at all in the window: "
            f"{(sub['order_days'] == 0).mean():.0%}."
        )
    sh = df.filter(pl.col("condition") == "shortage")
    out.append("\nShortage cases by the shape of the trailing demand (one day holding at least half of the window's outflow):\n")
    out.append(
        _table(
            sh.with_columns(
                pl.when(pl.col("max_share") >= 0.5).then(pl.lit("one order dominates")).otherwise(pl.lit("spread")).alias("trailing demand")
            )
            .group_by("trailing demand")
            .agg(pl.len().alias("cases"), pl.col("below_zero").mean().round(3).alias("went below zero"))
            .sort("trailing demand")
        )
    )
    out.append("\nShortage cases by horizon of the predicted stock-out:\n")
    out.append(
        _table(
            sh.with_columns(
                pl.when(pl.col("days_to_trigger") <= 14).then(pl.lit("within 14 days"))
                .when(pl.col("days_to_trigger") <= 42).then(pl.lit("15 to 42 days"))
                .otherwise(pl.lit("beyond 42 days")).alias("horizon")
            )
            .group_by("horizon")
            .agg(pl.len().alias("cases"), pl.col("below_zero").mean().round(3).alias("went below zero"), (pl.col("oracle") == "do_nothing").mean().round(3).alias("oracle: do nothing"))
            .sort("went below zero", descending=True)
        )
    )
    out.append("\nShortage cases by criticality:\n")
    out.append(_table(sh.group_by("criticality").agg(pl.len().alias("cases"), pl.col("below_zero").mean().round(3).alias("went below zero")).sort("criticality")))
    wa = df.filter(pl.col("condition") == "warning")
    out.append("\nWarning cases by order days in the trailing window:\n")
    out.append(
        _table(
            wa.with_columns(
                pl.when(pl.col("order_days") == 0).then(pl.lit("0")).when(pl.col("order_days") <= 2).then(pl.lit("1 to 2")).otherwise(pl.lit("3 or more")).alias("order days")
            )
            .group_by("order days")
            .agg(pl.len().alias("cases"), pl.col("below_zero").mean().round(3).alias("went below zero"))
            .sort("order days")
        )
    )
    below = wa.filter(pl.col("below_zero"))
    out.append(
        f"\nWarnings that went below zero: {below.height}; the oracle's choice for them: "
        + ", ".join(f"{k} {v}" for k, v in sorted(below.group_by("oracle").len().iter_rows()))
        + "."
    )
    return "\n".join(out)


def window(tables: dict[str, pl.DataFrame]) -> str:
    lines = [
        "| Demand window (days) | Shortage cases in window | Caught (recall) | Precision | Median trigger minus emergency (days) | Oracle regret per case: all | shortage | warning | Agreement with the oracle |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for days in (28, 56, 91):
        params = PlanningParams(forecast_window_days=days)
        rows = replay(tables, [START + timedelta(weeks=w) for w in range(REPLAY_WEEKS)], 30, params)
        totals = {k: sum(getattr(r, k) for r in rows) for k in ("predicted", "realized", "hits")}
        errors = [e for r in rows for e in r.timing_errors]
        outcomes = []
        for week in range(ORACLE_WEEKS):
            outcomes += evaluate_day(tables, START + timedelta(weeks=week), HORIZON, params)
        regret = {}
        for condition in ("all", "shortage", "warning"):
            sub = outcomes if condition == "all" else [o for o in outcomes if o.condition == condition]
            regret[condition] = summarize(sub)["engine"]["regret"] / max(len(sub), 1)
        agree = summarize(outcomes)["engine"]["agree_with_oracle"]
        lines.append(
            f"| {days} | {totals['predicted']} | {totals['hits']} ({totals['hits'] / totals['realized']:.0%}) | "
            f"{totals['hits'] / totals['predicted']:.0%} | {statistics.median(errors) if errors else 'n/a':+} | "
            f"{regret['all']:,.0f} | {regret['shortage']:,.0f} | {regret['warning']:,.0f} | {agree:.0%} |"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "shape"
    loaded = _load()
    print(shape(loaded) if command == "shape" else window(loaded))
