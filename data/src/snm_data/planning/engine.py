"""Supply projection, cases, levers, and triage (PROPOSAL.md sections 4.2 and 4.3; decision record 003).

Everything here is deterministic arithmetic over the input frames. Each plant in scope has a main warehouse
(the issue point for the manufacturing department) and overflow hubs (cheaper storage with no demand of their
own). Two daily lines are projected per material and plant over the horizon: the pooled stock of the main
warehouse and its overflow hubs, and the main warehouse alone. Cases: shortage (pooled line below zero),
warning (pooled line below the minimum, the safety stock), pull (pooled line fine but the main warehouse
below the minimum: move stock in from an overflow hub), and excess (main warehouse above the maximum for the
excess window: move stock out to an overflow hub). Order levers with a surcharge (expedite, expedited new
order) are used only against a projected stock-out (decision record 001); moves between the main warehouse
and its overflow hubs are recommendations that logistics reviews; triage classifies order cases as
agent-eligible or human-handled.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import polars as pl

from . import demand
from .inputs import PlanningInputs, weekly_series
from .params import ENGINE_VERSION, PlanningParams

SITE_OF_PLANT = {"US10": "Round Rock", "CN10": "Longgang"}
# Order levers of the pooled conditions, in the fixed order; the first that covers by the trigger date wins.
LEVER_ORDER = ("expedite", "new_order", "expedited_new_order")
# Levers without a surcharge: the only ones recommended for a warning case.
STANDARD_LEVERS = ("new_order",)
# Levers of the move conditions (pull, excess), decided by logistics.
MOVE_LEVERS = ("transfer", "move_to_overflow")
CONDITION_CODE = {"shortage": "SH", "warning": "WA", "pull": "PU", "excess": "EX"}
QTY_TOLERANCE = 1e-6  # quantities closer than this to a threshold are on it (engine 1.0.1)


@dataclass
class PlanningOutputs:
    supply_position: pl.DataFrame
    replenishment_case: pl.DataFrame
    supplier_profile: pl.DataFrame


@dataclass
class Lever:
    lever: str
    coverage_date: date
    qty: float
    cost_usd: float
    detail: dict


# ---------------------------------------------------------------------------------------------- projection


def _cumulative_by_day(frame: pl.DataFrame, date_column: str, qty_column: str, as_of: date, horizon: int):
    """Quantities summed per day offset (0..horizon); dates before as_of land on day 0."""
    if frame.height == 0:
        return np.zeros(horizon + 1)
    offsets = (frame.get_column(date_column) - pl.Series([as_of] * frame.height)).dt.total_days().to_numpy()
    offsets = np.clip(offsets, 0, horizon)
    daily = np.zeros(horizon + 1)
    np.add.at(daily, offsets.astype(int), frame.get_column(qty_column).to_numpy().astype(float))
    return daily


def _forecast(daily_rate: float, explicit_daily: np.ndarray, horizon: int) -> np.ndarray:
    """Weekly forecast net of the explicit demand already dated in the same week, spread evenly over the
    week's days: the trailing rate fills only what reservations and planned orders do not cover."""
    out = np.zeros(horizon + 1)
    for start in range(1, horizon + 1, 7):
        end = min(start + 7, horizon + 1)
        days = end - start
        net = max(0.0, daily_rate * days - float(explicit_daily[start:end].sum()))
        out[start:end] = net / days
    return out


def project_one(
    inputs: PlanningInputs, params: PlanningParams, material: str, plant: str, stock: pl.DataFrame
) -> dict:
    """Daily projection vectors for one material and plant: the pooled line (main warehouse plus overflow
    hubs) and the main warehouse alone."""
    horizon = params.horizon_days
    as_of = inputs.as_of
    mains = list(params.main_locations[plant])
    overflows = list(params.overflow_locations.get(plant, ()))
    main = stock.filter(pl.col("storage_location").is_in(mains))
    overflow = stock.filter(pl.col("storage_location").is_in(overflows))
    main_on_hand = float(main.get_column("available_qty").sum())
    overflow_on_hand = float(overflow.get_column("available_qty").sum()) + float(
        overflow.get_column("in_transit_qty").fill_null(0.0).sum()
    )
    # Stock in transfer to the main warehouse arrives on the next day.
    in_transit = np.zeros(horizon + 1)
    in_transit[min(1, horizon)] += float(main.get_column("in_transit_qty").fill_null(0.0).sum())
    orders = inputs.open_orders.filter((pl.col("material") == material) & (pl.col("plant") == plant))
    receipts = _cumulative_by_day(orders, "expected_date", "open_qty", as_of, horizon)
    firm = inputs.firm_demand.filter((pl.col("material") == material) & (pl.col("plant") == plant))
    customer = firm.filter(pl.col("source") != "production")
    production = firm.filter(pl.col("source") == "production")
    customer_daily = _cumulative_by_day(customer, "requirement_date", "qty", as_of, horizon)
    production_daily = _cumulative_by_day(production, "requirement_date", "qty", as_of, horizon)
    planned = inputs.planned_demand.filter((pl.col("material") == material) & (pl.col("plant") == plant))
    planned_daily = _cumulative_by_day(planned, "requirement_date", "qty", as_of, horizon)
    backlog = float(customer_daily[0] + production_daily[0] + planned_daily[0])
    production_backlog = float(production_daily[0])
    customer_daily[0] = production_daily[0] = planned_daily[0] = 0.0
    firm_daily = customer_daily + production_daily
    rate = (
        inputs.demand_rate.filter(
            (pl.col("material") == material)
            & (pl.col("plant") == plant)
            & pl.col("storage_location").is_in(mains)
        )
        .get_column("daily_rate")
        .sum()
    )
    # The trailing rate is netted against every explicit requirement of the same week; the planned orders'
    # requirements are the MRP forecast and are added as dated demand.
    forecast = _forecast(float(rate), firm_daily + planned_daily, horizon) + planned_daily
    supply = main_on_hand + np.cumsum(in_transit + receipts)
    demand = backlog + np.cumsum(firm_daily + forecast)
    projected_main = supply - demand
    return {
        "on_hand": main_on_hand,
        "overflow_on_hand": overflow_on_hand,
        "backlog": backlog,
        "production_backlog": production_backlog,
        "in_transit": in_transit,
        "receipts": receipts,
        "firm": firm_daily,
        "production": production_daily,
        "planned": planned_daily,
        "forecast": forecast,
        "daily_rate": float(rate),
        "projected": projected_main + overflow_on_hand,
        "projected_main": projected_main,
    }


# ---------------------------------------------------------------------------------------------- levers


def surcharge_pct(vendor: dict | None, days_gained: int, params: PlanningParams) -> float:
    tiers = params.surcharge_default
    if vendor is None:
        return tiers["lt7"] if days_gained < 7 else tiers["7_13"] if days_gained < 14 else tiers["ge14"]
    if days_gained >= 14:
        return float(vendor["surcharge_pct_ge14"])
    if days_gained >= 7:
        return float(vendor["surcharge_pct_7_13"])
    return float(vendor["surcharge_pct_lt7"])


def _min_expedite_days(vendor: dict | None, planned_days: int, params: PlanningParams) -> int:
    if vendor is not None and vendor.get("min_expedite_days") is not None:
        return int(vendor["min_expedite_days"])
    fraction = round(planned_days * params.min_expedite_fraction)
    return max(params.min_expedite_floor_days, fraction)


def expedite_freight(vendor: dict | None, costs: dict[str, float]) -> float:
    if vendor is not None and vendor.get("expedite_freight_usd") is not None:
        return float(vendor["expedite_freight_usd"])
    return float(costs["expedite_freight_default"])


def _overflow_stock(params: PlanningParams, plant: str, stock: pl.DataFrame) -> list[dict]:
    """The plant's overflow hubs with their available stock, largest first."""
    out = []
    for row in stock.filter(
        pl.col("storage_location").is_in(list(params.overflow_locations.get(plant, ())))
    ).iter_rows(named=True):
        out.append(
            {
                "storage_location": row["storage_location"],
                "available_qty": float(row["available_qty"] or 0.0) + float(row["in_transit_qty"] or 0.0),
                "lead_days": params.lane_lead_days.get(row["storage_location"], 1),
            }
        )
    return sorted(out, key=lambda item: (-item["available_qty"], item["storage_location"]))


def move_levers(
    inputs: PlanningInputs,
    params: PlanningParams,
    plant: str,
    condition: str,
    gap: float,
    stock: pl.DataFrame,
) -> list[Lever]:
    """The single lever of a move condition: pull the gap in from the overflow hub that holds the most
    (``pull``), or move the surplus out to the first overflow hub (``excess``)."""
    costs = inputs.cost_parameters
    main = params.main_locations[plant][0]
    overflows = _overflow_stock(params, plant, stock)
    targets = list(params.overflow_locations.get(plant, ()))
    if condition == "pull":
        source = next((item for item in overflows if item["available_qty"] >= gap), overflows[0])
        qty = min(gap, source["available_qty"])
        cost = costs["transfer_cost_fixed"] + costs["transfer_cost_per_unit"] * qty
        return [
            Lever(
                "transfer",
                inputs.as_of + timedelta(days=source["lead_days"]),
                qty,
                round(cost, 2),
                {
                    "storage_location": source["storage_location"],
                    "from_location": source["storage_location"],
                    "to_location": main,
                    "lead_days": source["lead_days"],
                },
            )
        ]
    target = targets[0]
    lead = params.lane_lead_days.get(target, 1)
    cost = costs["transfer_cost_fixed"] + costs["transfer_cost_per_unit"] * gap
    return [
        Lever(
            "move_to_overflow",
            inputs.as_of + timedelta(days=lead),
            gap,
            round(cost, 2),
            {"storage_location": target, "from_location": main, "to_location": target, "lead_days": lead},
        )
    ]


def evaluate_levers(
    inputs: PlanningInputs,
    params: PlanningParams,
    material: str,
    plant: str,
    gap: float,
    shortage_date: date,
    material_row: dict,
    vendor: dict | None,
) -> list[Lever]:
    """Every order lever with its coverage date and incremental cost, in the fixed order."""
    as_of = inputs.as_of
    costs = inputs.cost_parameters
    price = float(material_row["price_usd"])
    planned_days = int(material_row["planned_delivery_days"])
    levers: list[Lever] = []
    # 1. Expedite an open purchase order whose expected receipt is after the shortage date.
    min_days = _min_expedite_days(vendor, planned_days, params)
    achievable = as_of + timedelta(days=min_days)
    orders = inputs.open_orders.filter((pl.col("material") == material) & (pl.col("plant") == plant)).sort(
        ["expected_date", "ebeln"]
    )
    for row in orders.iter_rows(named=True):
        if row["expected_date"] <= shortage_date or row["open_qty"] < gap:
            continue
        days_gained = (row["expected_date"] - achievable).days
        if days_gained <= 0:
            continue
        pct = surcharge_pct(vendor, days_gained, params)
        order_price = float(row["unit_price_usd"] or price)
        cost = pct / 100.0 * row["open_qty"] * order_price + expedite_freight(vendor, costs)
        levers.append(
            Lever(
                "expedite",
                achievable,
                float(row["open_qty"]),
                round(cost, 2),
                {
                    "ebeln": row["ebeln"],
                    "ebelp": row["ebelp"],
                    "days_gained": days_gained,
                    "surcharge_pct": pct,
                },
            )
        )
        break
    # 2. New order at the planned delivery time; 3. expedited new order at the minimum expedite lead time.
    standard = as_of + timedelta(days=planned_days)
    levers.append(
        Lever("new_order", standard, gap, round(costs["order_cost"], 2), {"lead_days": planned_days})
    )
    if min_days < planned_days:
        days_gained = planned_days - min_days
        pct = surcharge_pct(vendor, days_gained, params)
        cost = costs["order_cost"] + pct / 100.0 * gap * price + expedite_freight(vendor, costs)
        levers.append(
            Lever(
                "expedited_new_order",
                achievable,
                gap,
                round(cost, 2),
                {"lead_days": min_days, "days_gained": days_gained, "surcharge_pct": pct},
            )
        )
    return levers


def recommend(levers: list[Lever], trigger_date: date, expedite_allowed: bool = True) -> tuple[Lever, int]:
    """The first lever in the fixed order that covers the gap by the trigger date; when none does, the
    lever with the earliest coverage date, and the number of days it falls short. Without
    ``expedite_allowed`` (a warning case) only the standard levers are considered. A move lever is the only
    candidate of its case."""
    if levers and levers[0].lever in MOVE_LEVERS:
        lever = levers[0]
        return lever, max(0, (lever.coverage_date - trigger_date).days)
    candidates = [lever for lever in levers if expedite_allowed or lever.lever in STANDARD_LEVERS]
    for name in LEVER_ORDER:
        for lever in candidates:
            if lever.lever == name and lever.coverage_date <= trigger_date:
                return lever, 0
    earliest = min(candidates, key=lambda lever: (lever.coverage_date, LEVER_ORDER.index(lever.lever)))
    return earliest, (earliest.coverage_date - trigger_date).days


# ---------------------------------------------------------------------------------------------- triage


def classify_vendors(vendors: pl.DataFrame, params: PlanningParams) -> pl.DataFrame:
    """ABC class by cumulative trailing spend and the agent-eligibility of the vendor as a whole."""
    ordered = vendors.sort(["spend_usd_12m", "vendor"], descending=[True, False])
    total = float(ordered.get_column("spend_usd_12m").sum()) or 1.0
    share = (ordered.get_column("spend_usd_12m").cum_sum() / total).round(9)
    ordered = ordered.with_columns(
        pl.when(pl.Series(share) <= params.class_a_share)
        .then(pl.lit("A"))
        .when(pl.Series(share) <= params.class_b_share)
        .then(pl.lit("B"))
        .otherwise(pl.lit("C"))
        .alias("supplier_class")
    )
    tail = (pl.col("supplier_class") == "C") | (pl.col("po_count_12m") <= params.tail_po_count_max)
    effortful = (
        pl.col("median_rounds").is_null()
        | (pl.col("median_rounds") >= params.effort_rounds_min)
        | (pl.col("median_days_to_resolution") >= params.effort_days_min)
    )
    return ordered.with_columns(
        (tail & effortful & ~pl.col("strategic").fill_null(False)).alias("agent_eligible_supplier")
    ).sort("vendor")


def triage(case: dict, vendor: dict | None, params: PlanningParams) -> tuple[str, list[str]]:
    reasons = []
    if vendor is None:
        return "no_supplier", ["no purchase order history for the material at the plant"]
    if vendor.get("strategic"):
        reasons.append("strategic supplier")
    if not ((vendor["supplier_class"] == "C") or (vendor["po_count_12m"] <= params.tail_po_count_max)):
        reasons.append(f"supplier class {vendor['supplier_class']} with {vendor['po_count_12m']} orders")
    if case["value_usd"] > params.case_value_max_usd:
        reasons.append(f"case value {case['value_usd']:.0f} USD above {params.case_value_max_usd:.0f}")
    rounds, days = vendor.get("median_rounds"), vendor.get("median_days_to_resolution")
    if rounds is not None and not (
        rounds >= params.effort_rounds_min or (days or 0) >= params.effort_days_min
    ):
        reasons.append(f"low historical effort ({rounds:.0f} rounds, {days or 0:.0f} days)")
    return (
        ("human_handled", reasons)
        if reasons
        else ("agent_eligible", ["tail supplier, within value and effort thresholds"])
    )


# ---------------------------------------------------------------------------------------------- plan


def plan(inputs: PlanningInputs, params: PlanningParams | None = None) -> PlanningOutputs:
    params = params or PlanningParams()
    as_of = inputs.as_of
    horizon = params.horizon_days
    vendors = classify_vendors(inputs.vendors, params)
    vendor_rows = {row["vendor"]: row for row in vendors.iter_rows(named=True)}
    material_vendor = {
        (row["material"], row["plant"]): row["vendor"] for row in inputs.material_vendor.iter_rows(named=True)
    }
    stock_by_key = inputs.stock.partition_by(["material", "plant"], as_dict=True)
    digest = params.digest()
    position_rows: list[dict] = []
    case_rows: list[dict] = []
    days = [as_of + timedelta(days=offset) for offset in range(horizon + 1)]
    for material_row in inputs.materials.iter_rows(named=True):
        material, plant = material_row["material"], material_row["plant"]
        if plant not in params.plants:
            continue
        stock = stock_by_key.get((material, plant))
        if stock is None:
            stock = inputs.stock.clear()
        projection = project_one(inputs, params, material, plant, stock)
        projected = projection["projected"]
        main_line = projection["projected_main"]
        safety = float(material_row["safety_stock"] or 0.0)
        maximum = float(material_row["max_stock"] or 0.0)
        for offset in range(horizon + 1):
            position_rows.append(
                {
                    "as_of_date": as_of,
                    "day": days[offset],
                    "day_offset": offset,
                    "material": material,
                    "plant": plant,
                    "site": SITE_OF_PLANT[plant],
                    "on_hand_qty": projection["on_hand"],
                    "overflow_on_hand_qty": projection["overflow_on_hand"],
                    "backlog_qty": projection["backlog"],
                    "in_transit_qty": float(projection["in_transit"][offset]),
                    "receipt_qty": float(projection["receipts"][offset]),
                    "firm_demand_qty": float(projection["firm"][offset]),
                    "production_demand_qty": float(projection["production"][offset]),
                    "forecast_qty": float(projection["forecast"][offset]),
                    "planned_order_qty": float(projection["planned"][offset]),
                    "projected_qty": float(projected[offset]),
                    "projected_main_qty": float(main_line[offset]),
                    "safety_stock_qty": safety,
                    "max_stock_qty": maximum,
                    "engine_version": ENGINE_VERSION,
                    "params_digest": digest,
                }
            )
        condition, index = None, None
        # Floating-point noise (a projection of -1e-14 after fractional daily rates) is not a stock-out.
        below_zero = np.flatnonzero(projected < -QTY_TOLERANCE)
        below_safety = np.flatnonzero(projected < safety - QTY_TOLERANCE)
        main_below_safety = np.flatnonzero(main_line < safety - QTY_TOLERANCE)
        excess_days = min(params.excess_weeks * 7, horizon)
        has_overflow = bool(params.overflow_locations.get(plant))
        if below_zero.size:
            condition, index = "shortage", int(below_zero[0])
            gap = float(-projected.min())
        elif below_safety.size and safety > 0:
            condition, index = "warning", int(below_safety[0])
            gap = float(safety - projected.min())
        elif main_below_safety.size and has_overflow:
            condition, index = "pull", int(main_below_safety[0])
            gap = float(min(safety - main_line.min(), projection["overflow_on_hand"]))
        elif (
            maximum > 0
            and has_overflow
            and float(main_line[: excess_days + 1].min()) > maximum + QTY_TOLERANCE
        ):
            condition, index = "excess", 0
            gap = float(main_line[: excess_days + 1].min() - maximum)
        if condition is None or gap <= QTY_TOLERANCE:
            continue
        vendor_id = material_vendor.get((material, plant))
        vendor = vendor_rows.get(vendor_id) if vendor_id else None
        price = float(material_row["price_usd"])
        case_id = f"C-{plant}-{material}-{CONDITION_CODE[condition]}-{days[index]:%Y%m%d}"
        series = weekly_series(inputs.demand_weekly, material, plant, params.sba_weeks)
        model = demand.fit(series, material_row["criticality"], params) if series is not None else None
        risk = None
        if model is not None and model.qualified and condition in ("shortage", "warning"):
            # If nothing is done: the chance of a day below zero up to the trigger date.
            risk = demand.stockout_risk(
                model,
                projected,
                projection["forecast"] - projection["planned"],
                projection["firm"] + projection["planned"],
                index,
                case_id,
                params.risk_paths,
            ).probability
        case = {
            "case_id": case_id,
            "as_of_date": as_of,
            "material": material,
            "description": material_row["description"],
            "plant": plant,
            "site": SITE_OF_PLANT[plant],
            "criticality": material_row["criticality"],
            "condition": condition,
            "trigger_date": days[index],
            "gap_qty": round(gap, 3),
            "value_usd": round(gap * price, 2),
            "on_hand_qty": projection["on_hand"],
            "overflow_on_hand_qty": projection["overflow_on_hand"],
            "backlog_qty": projection["backlog"],
            "production_backlog_qty": projection["production_backlog"],
            "open_order_qty": float(projection["receipts"].sum()),
            "daily_demand_rate": round(projection["daily_rate"], 4),
            "demand_model": model.to_json() if model is not None else None,
            "stockout_risk_no_action": round(risk, 4) if risk is not None else None,
            "safety_stock_qty": safety,
            "max_stock_qty": maximum,
            "vendor": vendor_id,
            "vendor_name": vendor["name"] if vendor else None,
            "supplier_class": vendor["supplier_class"] if vendor else None,
        }
        if condition in ("pull", "excess"):
            levers = move_levers(inputs, params, plant, condition, gap, stock)
            triage_class, reasons = (
                "human_handled",
                [
                    "pull from the overflow hub decided by logistics"
                    if condition == "pull"
                    else "move to the overflow hub decided by logistics"
                ],
            )
            stockout_cost = 0.0
        else:
            levers = evaluate_levers(inputs, params, material, plant, gap, days[index], material_row, vendor)
            triage_class, reasons = triage(case, vendor, params)
        chosen, days_short = recommend(levers, days[index], expedite_allowed=condition == "shortage")
        per_day = inputs.cost_parameters[f"line_down_cost_{str(material_row['criticality']).lower()}"]
        # Line-down cost accrues only below zero; a warning's days short are days below the minimum.
        stockout_cost = per_day * days_short if condition == "shortage" else 0.0
        alternatives = [
            {
                "lever": lever.lever,
                "coverage_date": lever.coverage_date.isoformat(),
                "qty": round(lever.qty, 3),
                **{key: value for key, value in lever.detail.items() if key != "surcharge_pct"},
            }
            for lever in levers
            if lever is not chosen
        ]
        costs = {lever.lever: lever.cost_usd for lever in levers}
        case.update(
            {
                "lever": chosen.lever,
                "coverage_date": chosen.coverage_date,
                "covers_in_time": days_short == 0,
                "days_short": days_short,
                "lever_qty": round(chosen.qty, 3),
                "lever_detail": json.dumps(
                    {key: value for key, value in chosen.detail.items() if key != "surcharge_pct"},
                    default=str,
                ),
                "lever_cost_usd": chosen.cost_usd,
                "stockout_cost_usd": round(stockout_cost, 2),
                "alternatives": json.dumps(alternatives, default=str),
                "alternative_costs_usd": json.dumps(costs),
                "rationale": _rationale(case, chosen, projection, days[index], days_short, params),
                "triage_class": triage_class,
                "triage_reasons": "; ".join(reasons),
                "engine_version": ENGINE_VERSION,
                "params_digest": digest,
                "status": "new",
            }
        )
        case_rows.append(case)
    supply_position = pl.DataFrame(position_rows) if position_rows else pl.DataFrame()
    cases = pl.DataFrame(case_rows) if case_rows else pl.DataFrame()
    return PlanningOutputs(supply_position, cases, _supplier_profile(vendors, as_of, digest))


def _rationale(
    case: dict, chosen: Lever, projection: dict, trigger: date, days_short: int, params: PlanningParams
) -> str:
    gap, condition = case["gap_qty"], case["condition"]
    production_backlog = projection["production_backlog"]
    rate_label = (
        f"{params.forecast_window_days}-day demand rate"
        if params.forecast_model == "trailing"
        else "demand rate"
    )
    backlog_note = f" (production {production_backlog:.0f})" if production_backlog else ""
    stock_note = (
        f"main warehouse {projection['on_hand']:.0f}, overflow hub {projection['overflow_on_hand']:.0f}, "
        f"backlog {projection['backlog']:.0f}{backlog_note}, production demand "
        f"{projection['production'].sum():.0f}, planned orders {projection['planned'].sum():.0f}, "
        f"open orders {projection['receipts'].sum():.0f}, {rate_label} {projection['daily_rate']:.2f} per day"
    )
    if condition == "pull":
        return (
            f"The main warehouse falls below its minimum of {case['safety_stock_qty']:g} on "
            f"{trigger:%Y-%m-%d} while the plant's pooled stock stays above it ({stock_note}); gap {gap:g}. "
            f"Pull {chosen.qty:g} from overflow hub {chosen.detail['from_location']} "
            f"(arrives {chosen.coverage_date:%Y-%m-%d}); no order is needed."
        )
    if condition == "excess":
        return (
            f"Main warehouse stock stays above the maximum of {case['max_stock_qty']:g} for "
            f"{params.excess_weeks} weeks ({stock_note}); surplus {gap:g}. Move {chosen.qty:g} to overflow "
            f"hub {chosen.detail['to_location']} for review by logistics; no order is needed."
        )
    head = (
        f"Pooled stock of the main warehouse and its overflow hub falls below "
        f"{'zero' if condition == 'shortage' else 'the minimum'} on {trigger:%Y-%m-%d} ({stock_note}); "
        f"gap {gap:g}."
    )
    if chosen.lever == "expedite":
        tail = (
            f" Expedite purchase order {chosen.detail['ebeln']} to {chosen.coverage_date:%Y-%m-%d} "
            f"({chosen.detail['days_gained']} days gained)."
        )
    elif chosen.lever == "new_order":
        tail = f" A new order at the planned delivery time arrives {chosen.coverage_date:%Y-%m-%d}."
    else:
        tail = (
            f" A new order expedited to {chosen.detail['lead_days']} days arrives "
            f"{chosen.coverage_date:%Y-%m-%d}."
        )
    if projection["overflow_on_hand"] > 0:
        overflow = projection["overflow_on_hand"]
        tail += f" The overflow hub's {overflow:.0f} are counted and should be pulled first."
    if condition == "warning":
        tail += " Stock stays above zero, so no expedite is recommended"
        tail += (
            f"; stock stays below the minimum for {days_short} days until it arrives."
            if days_short > 0
            else "."
        )
    elif days_short > 0:
        tail += f" No lever reaches the date; this is the earliest, {days_short} days short."
    return head + tail + _risk_note(case, trigger)


def _risk_note(case: dict, trigger: date) -> str:
    """One sentence on the demand model and, where it is qualified, the stock-out risk of doing nothing."""
    model = demand.DemandModel.from_json(case.get("demand_model"))
    if model is None:
        return ""
    if case.get("stockout_risk_no_action") is None:
        return (
            " No outflow in the last 13 weeks, so no stock-out risk is estimated (the strict rule applies)."
        )
    return (
        f" Demand model: {model.rate_per_week:.1f} per week on average, outflow in {model.frequency} of the "
        f"last 13; if nothing is done, the chance of running out by {trigger:%Y-%m-%d} is "
        f"{case['stockout_risk_no_action'] * 100:.2f}%."
    )


def _supplier_profile(vendors: pl.DataFrame, as_of: date, digest: str) -> pl.DataFrame:
    return vendors.select(
        pl.lit(as_of).alias("as_of_date"),
        "vendor",
        "name",
        "country",
        "supplier_class",
        pl.col("spend_usd_12m").round(2),
        "po_count_12m",
        pl.col("on_time_rate_12m").cast(pl.Float64),
        pl.col("median_lateness_days").cast(pl.Float64),
        pl.col("median_rounds").cast(pl.Float64),
        pl.col("median_days_to_resolution").cast(pl.Float64),
        pl.col("strategic").fill_null(False),
        pl.col("portal_onboarded").fill_null(False),
        pl.col("min_expedite_days").cast(pl.Int64),
        "surcharge_pct_lt7",
        "surcharge_pct_7_13",
        "surcharge_pct_ge14",
        "expedite_freight_usd",
        "agent_eligible_supplier",
        pl.lit(ENGINE_VERSION).alias("engine_version"),
        pl.lit(digest).alias("params_digest"),
    )
