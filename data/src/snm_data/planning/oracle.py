"""Hindsight oracle, first version (PROPOSAL §10.4, evaluation layer L13).

For each case the engine opened on a past day, every option (do nothing and each engine lever) is priced on
the demand that actually followed: the case's pooled projection (main warehouse plus overflow hub) with the
realized outflow to manufacturing (staging to the production supply area) in place of the forecast, plus the
option's receipt; cost = the lever's cost + line-down cost for each day the pool would have been below zero.
Move cases (pull, excess) are not priced: they are not order decisions. The oracle takes the cheapest
option. Policies are scored by their regret against it: the engine's published lever (0.3.0: expedite only
against a stock-out), the rule of engine 0.2.0 (the first lever in the fixed order that covers by the
trigger date, for warnings too), the cheapest covering lever, doing nothing, and always an expedited new
order.

Record 002 phase 4 adds two risk-aware policies that choose ex ante, from the case's demand model (the
stock-out risk and expected line-down of each option on the case's simulated paths, as the case server
prices them, over the oracle's horizon): ``risk_target``, the cheapest order lever whose recalibrated
stock-out risk meets the service target (else the one with the lowest risk), and ``expected_cost``, the
option (doing nothing included) with the lowest lever cost plus expected line-down. Cases whose model is
not qualified keep the engine's lever under both. ``expected_cost_guarded`` is ``expected_cost`` with offers
outside the engine's levers (the synthetic late split) open to C parts only.

Supplier behavior is the engine's assumption (lead times, surcharges); the supplier simulator of Phase C adds
its variance. Holding cost is left out (short horizons, small quantities).
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import polars as pl

from .demand import DemandModel, stockout_risk
from .engine import LEVER_ORDER, plan
from .history import tables_as_of
from .inputs import PlanningInputs, manufacturing_outflow, outflow_sign
from .params import PlanningParams

POLICIES = (
    "engine",
    "first_cover_any_lever",
    "cheapest_cover",
    "do_nothing",
    "always_expedited_order",
    "risk_target",
    "expected_cost",
    "expected_cost_guarded",
)


def realized_outflow(
    tables: dict[str, pl.DataFrame], start: date, days: int, params: PlanningParams
) -> dict[tuple[str, str], np.ndarray]:
    """Daily outflow from the main warehouses to the manufacturing department per (material, plant) for
    ``days`` days from ``start`` (day 0 = ``start``): staging to the production supply area, plus customer
    goods issues net of reversals when customer demand is in scope."""
    end = start + timedelta(days=days)
    mseg = tables["raw_sap_ecc.mseg"].filter(
        (pl.col("budat_mkpf") >= start.strftime("%Y%m%d")) & (pl.col("budat_mkpf") < end.strftime("%Y%m%d"))
    )
    selector = manufacturing_outflow(params)
    if params.include_customer_demand:
        at_main = pl.lit(False)
        for plant in params.plants:
            for main in params.main_locations.get(plant, ()):
                at_main = at_main | ((pl.col("werks") == plant) & (pl.col("lgort") == main))
        selector = selector | (pl.col("bwart").is_in(["601", "602"]) & at_main)
    rows = mseg.filter(selector & pl.col("werks").is_in(list(params.plants)))
    signed = outflow_sign()
    daily = (
        rows.with_columns(
            pl.col("matnr").str.strip_chars_start("0").alias("material"),
            (pl.col("budat_mkpf").str.strptime(pl.Date, "%Y%m%d") - pl.lit(start))
            .dt.total_days()
            .alias("offset"),
            signed.alias("qty"),
        )
        .group_by("material", "werks", "offset")
        .agg(pl.col("qty").sum())
    )
    out: dict[tuple[str, str], np.ndarray] = {}
    for row in daily.iter_rows(named=True):
        key = (row["material"], row["werks"])
        series = out.setdefault(key, np.zeros(days))
        series[int(row["offset"])] += row["qty"]
    return out


@dataclass
class CaseOutcome:
    as_of: date
    case_id: str
    condition: str
    costs: dict[str, float]  # option -> cost on the realized path
    oracle: str
    choices: dict[str, str] = field(default_factory=dict)  # policy -> option
    criticality: str = "C"
    line_down_days: dict[str, int] = field(default_factory=dict)  # option -> realized days below zero
    qualified: bool = False  # the case's demand model priced the risk-aware policies


def _option_receipts(case: dict, as_of: date) -> dict[str, tuple[list[tuple[int, float]], float]]:
    """Option -> (receipt changes as (day offset, quantity), lever cost)."""
    costs = json.loads(case["alternative_costs_usd"] or "{}")
    levers = [
        {
            "lever": case["lever"],
            "coverage_date": case["coverage_date"],
            "qty": case["lever_qty"],
            **json.loads(case["lever_detail"] or "{}"),
        }
    ]
    levers += json.loads(case["alternatives"] or "[]")
    options: dict[str, tuple[list[tuple[int, float]], float]] = {"do_nothing": ([], 0.0)}
    for lever in levers:
        if not lever.get("coverage_date") or lever["lever"] in ("hold", "transfer", "move_to_overflow"):
            continue
        arrival = date.fromisoformat(str(lever["coverage_date"])[:10])
        changes = [((arrival - as_of).days, float(lever["qty"]))]
        if lever["lever"] == "expedite":
            expected = arrival + timedelta(days=int(lever.get("days_gained", 0)))
            changes.append(((expected - as_of).days, -float(lever["qty"])))
        options[lever["lever"]] = (changes, float(costs.get(lever["lever"], 0.0)))
    return options


def _policy_choices(case: dict, options: dict[str, tuple[list[tuple[int, float]], float]]) -> dict[str, str]:
    trigger = case["trigger_date"]
    arrivals: dict[str, int] = {}
    covering = []
    for name, (changes, cost) in options.items():
        if name not in LEVER_ORDER:  # doing nothing and offers outside the engine's levers
            continue
        receipts = [offset for offset, qty in changes if qty > 0]
        if not receipts:
            continue
        arrivals[name] = max(receipts)
        if case["as_of_date"] + timedelta(days=arrivals[name]) <= trigger:
            covering.append((cost, LEVER_ORDER.index(name), name))
    cheapest = min(covering)[2] if covering else case["lever"]
    # The rule of engine 0.2.0: the first lever in the fixed order that covers by the trigger date, for
    # warnings too; else the earliest lever.
    if covering:
        first_any = min(covering, key=lambda item: item[1])[2]
    elif arrivals:
        first_any = min(arrivals, key=lambda name: (arrivals[name], LEVER_ORDER.index(name)))
    else:
        first_any = case["lever"]
    expedited = "expedited_new_order" if "expedited_new_order" in options else case["lever"]
    return {
        "engine": case["lever"],
        "first_cover_any_lever": first_any,
        "cheapest_cover": cheapest,
        "do_nothing": "do_nothing",
        "always_expedited_order": expedited,
    }


def _ex_ante(
    case: dict,
    rows: pl.DataFrame,
    options: dict[str, tuple[list[tuple[int, float]], float]],
    per_day: float,
    params: PlanningParams,
) -> dict[str, tuple[float, bool, float]] | None:
    """Option -> (stock-out risk, meets the service target, lever cost + expected line-down) on the case's
    simulated paths over the rows' days; None when the case's demand model is not qualified."""
    model = DemandModel.from_json(case.get("demand_model"))
    if model is None or not model.qualified:
        return None

    def column(name: str) -> np.ndarray:
        return rows.get_column(name).cast(pl.Float64).to_numpy()

    projected, planned = column("projected_qty"), column("planned_order_qty")
    point_net, explicit = column("forecast_qty") - planned, column("firm_demand_qty") + planned
    trigger = (case["trigger_date"] - case["as_of_date"]).days
    out = {}
    for name, (changes, cost) in options.items():
        line = projected.copy()
        for offset, qty in changes:
            if 0 <= offset < line.size:
                line[offset:] += qty
        arrivals = [offset for offset, qty in changes if qty > 0]
        window = max(trigger, *arrivals) if arrivals else trigger
        risk = stockout_risk(model, line, point_net, explicit, window, case["case_id"], params.risk_paths)
        out[name] = (risk.probability, risk.meets_target, cost + per_day * risk.expected_days_below_zero)
    return out


def _risk_choices(
    case: dict,
    options: dict[str, tuple[list[tuple[int, float]], float]],
    ex_ante: dict[str, tuple[float, bool, float]] | None,
) -> dict[str, str]:
    if ex_ante is None:
        return dict.fromkeys(("risk_target", "expected_cost", "expected_cost_guarded"), case["lever"])
    orders = [name for name in options if name != "do_nothing"]
    rank = {name: LEVER_ORDER.index(name) if name in LEVER_ORDER else len(LEVER_ORDER) for name in orders}
    meeting = [name for name in orders if ex_ante[name][1]]
    if meeting:
        target = min(meeting, key=lambda name: (options[name][1], rank[name]))
    elif orders:
        target = min(orders, key=lambda name: (ex_ante[name][0], options[name][1], rank[name]))
    else:
        target = case["lever"]

    def cheapest(names: list[str]) -> str:
        return min(names, key=lambda name: (ex_ante[name][2], name != "do_nothing"))

    guarded = [
        name
        for name in options
        if name in LEVER_ORDER or name == "do_nothing" or str(case.get("criticality") or "C") == "C"
    ]
    return {
        "risk_target": target,
        "expected_cost": cheapest(list(options)),
        "expected_cost_guarded": cheapest(guarded),
    }


def late_split(
    case: dict, options: dict[str, tuple[list[tuple[int, float]], float]], discount: float
) -> tuple[list[tuple[int, float]], float] | None:
    """A synthetic supplier offer like the role play's: the engine's new order split in two, the first half on
    its date and the rest a week after the trigger date, at ``discount`` below the case's value (the cheaper
    offer outside the mandate on arrival)."""
    if "new_order" not in options:
        return None
    (offset, qty), *_ = options["new_order"][0]
    first = float(int(qty // 2))
    late = max(offset, (case["trigger_date"] - case["as_of_date"]).days) + 7
    cost = options["new_order"][1] - discount * float(case["value_usd"] or 0.0)
    return [(offset, first), (late, qty - first)], cost


def evaluate_day(
    tables: dict[str, pl.DataFrame],
    as_of: date,
    horizon: int,
    params: PlanningParams | None = None,
    late_split_discount: float | None = None,
) -> list[CaseOutcome]:
    params = params or PlanningParams()
    outputs = plan(PlanningInputs.from_tables(tables_as_of(tables, as_of), as_of, params), params)
    cases = outputs.replenishment_case.filter(pl.col("condition").is_in(["shortage", "warning"]))
    if cases.height == 0:
        return []
    position = outputs.supply_position
    outflow = realized_outflow(tables, as_of, horizon, params)
    results = []
    for case in cases.iter_rows(named=True):
        rows = position.filter(
            (pl.col("material") == case["material"]) & (pl.col("plant") == case["plant"])
        ).sort("day")
        rows = rows.head(horizon)
        supply = (
            (rows.get_column("in_transit_qty") + rows.get_column("receipt_qty")).cast(pl.Float64).to_numpy()
        )
        # The pooled stock (main warehouse and overflow hub), as the engine projects it (record 003).
        on_hand = float(rows.get_column("on_hand_qty")[0]) + float(rows.get_column("overflow_on_hand_qty")[0])
        demand = outflow.get((case["material"], case["plant"]), np.zeros(horizon))[: len(supply)]
        path = on_hand + np.cumsum(supply) - np.cumsum(demand)
        per_day = params.line_down_cost.get(str(case["criticality"] or "C"), params.line_down_cost["C"])
        options = _option_receipts(case, as_of)
        if late_split_discount is not None:
            offer = late_split(case, options, late_split_discount)
            if offer is not None:
                options["late_split"] = offer
        costs, days_below = {}, {}
        for name, (changes, lever_cost) in options.items():
            adjusted = path.copy()
            for offset, qty in changes:
                if 0 <= offset < len(adjusted):
                    adjusted[offset:] += qty
            days_below[name] = int((adjusted < -1e-9).sum())
            costs[name] = round(lever_cost + per_day * days_below[name], 2)
        oracle = min(costs, key=lambda name: (costs[name], name != "do_nothing"))
        ex_ante = _ex_ante(case, rows, options, per_day, params)
        results.append(
            CaseOutcome(
                as_of,
                case["case_id"],
                case["condition"],
                costs,
                oracle,
                {**_policy_choices(case, options), **_risk_choices(case, options, ex_ante)},
                str(case["criticality"] or "C"),
                days_below,
                ex_ante is not None,
            )
        )
    return results


def summarize(outcomes: list[CaseOutcome]) -> dict[str, dict[str, float]]:
    out = {}
    oracle_total = sum(o.costs[o.oracle] for o in outcomes)
    for policy in POLICIES:
        total = sum(o.costs[o.choices[policy]] for o in outcomes)
        agree = sum(o.choices[policy] == o.oracle for o in outcomes)
        out[policy] = {
            "total_cost": round(total, 2),
            "regret": round(total - oracle_total, 2),
            "agree_with_oracle": round(agree / max(len(outcomes), 1), 3),
            **{
                f"line_down_days_{level.lower()}": sum(
                    o.line_down_days.get(o.choices[policy], 0) for o in outcomes if o.criticality == level
                )
                for level in ("A", "B", "C")
            },
            "choices": dict(sorted(Counter(o.choices[policy] for o in outcomes).items())),
        }
    out["oracle"] = {"total_cost": round(oracle_total, 2), "regret": 0.0, "agree_with_oracle": 1.0}
    return out
