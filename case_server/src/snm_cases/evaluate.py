"""Deterministic evaluation of one option for a case: re-project the case's stock with the option's
receipts and price it under the cost model (PROPOSAL §10.3). The planner agent cites the result; it never
does this arithmetic itself."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any

import numpy as np
import polars as pl

from snm_data.planning.demand import DemandModel, stockout_risk

HOLDING_DAYS_IN_YEAR = 365.0
RISK_PATHS = 2000  # simulated demand paths per case (the engine's ``risk_paths``)


@dataclass(frozen=True)
class Receipt:
    quantity: float
    arrival_date: date


@dataclass(frozen=True)
class OptionSpec:
    """An option to evaluate: a supplier proposal or an engine lever, reduced to receipts and terms."""

    option: str  # proposal_id or lever name
    # expedite | new_order | expedited_new_order | partial | alternative_part | transfer | decline | no_action
    kind: str
    receipts: tuple[Receipt, ...]
    # Receipts this option removes from the projection (an expedited open order no longer arrives later).
    removes: tuple[Receipt, ...] = ()
    unit_price: float | None = None
    surcharge_pct: float | None = None
    surcharge_amount: float | None = None
    freight_amount: float | None = None
    fixed_cost: float = 0.0  # order cost or transfer cost


@dataclass
class Evaluation:
    evaluation_id: str
    case_id: str
    option: str
    kind: str
    quantity: float
    coverage_date: date | None
    min_projected_qty: float
    first_short_day: date | None
    days_below_zero: int
    days_below_safety: int
    covers_gap: bool
    price_difference_cost: float  # quoted unit price minus the reference price, times the quantity
    surcharge_cost: float
    freight_cost: float
    fixed_cost: float
    stockout_cost: float
    holding_cost: float
    total_cost: float
    notes: list[str] = field(default_factory=list)
    # Record 002 phase 3, where the case's demand model is qualified (None elsewhere): the share of simulated
    # demand paths with a day below zero up to the later of the trigger date and the option's last arrival,
    # whether the recalibrated tail keeps that share within the service target, and the expected days below
    # zero over the horizon with their line-down cost, if nothing else is ordered.
    stockout_risk: float | None = None
    risk_window_end: date | None = None
    meets_service_target: bool | None = None
    expected_days_below_zero: float | None = None
    expected_line_down_cost: float | None = None

    def public(self, cost_access: bool) -> dict[str, Any]:
        out = asdict(self)
        out["coverage_date"] = self.coverage_date.isoformat() if self.coverage_date else None
        out["first_short_day"] = self.first_short_day.isoformat() if self.first_short_day else None
        out["risk_window_end"] = self.risk_window_end.isoformat() if self.risk_window_end else None
        # Shown and cited as a percentage with two decimals (the owner, 2026-10-19).
        out["stockout_risk"] = None if self.stockout_risk is None else f"{self.stockout_risk * 100:.2f}%"
        if not cost_access:
            for key in (
                "price_difference_cost",
                "surcharge_cost",
                "freight_cost",
                "fixed_cost",
                "stockout_cost",
                "holding_cost",
                "total_cost",
                "expected_line_down_cost",
            ):
                out[key] = None
        return out


def evaluation_id(case_id: str, basis_seq: int, spec: OptionSpec) -> str:
    """Stable identifier of an evaluation: the case, the engine snapshot it used, and the option."""
    material = json.dumps([case_id, basis_seq, asdict(spec)], default=str, sort_keys=True)
    return "E-" + hashlib.sha256(material.encode()).hexdigest()[:12]


def evaluate(
    case_id: str,
    basis_seq: int,
    condition: str,
    criticality: str | None,
    position: pl.DataFrame,
    spec: OptionSpec,
    costs: dict[str, float],
    price_usd: float,
    trigger: date | None = None,
    model: DemandModel | None = None,
) -> Evaluation:
    """Projected stock with the option, the remaining shortage and safety days, the option's cost, and,
    where the case's demand model is qualified, its stock-out risk on the case's simulated demand paths."""
    # The lakehouse returns the day as text, a local run as a date: compare dates either way.
    days = [
        d if isinstance(d, date) else date.fromisoformat(str(d)[:10])
        for d in position.get_column("day").to_list()
    ]
    projected = position.get_column("projected_qty").cast(pl.Float64).to_numpy().copy()
    safety = float(position.get_column("safety_stock_qty").cast(pl.Float64)[0]) if position.height else 0.0
    maximum = float(position.get_column("max_stock_qty").cast(pl.Float64)[0]) if position.height else 0.0
    notes: list[str] = []
    horizon_end = days[-1] if days else None

    def add(receipt: Receipt, sign: float) -> None:
        if horizon_end is not None and receipt.arrival_date > horizon_end:
            notes.append(f"receipt on {receipt.arrival_date.isoformat()} is after the projection horizon")
            return
        for index, day in enumerate(days):
            if day >= receipt.arrival_date:
                projected[index:] += sign * receipt.quantity
                return

    for receipt in spec.receipts:
        add(receipt, +1.0)
    for receipt in spec.removes:
        add(receipt, -1.0)

    below_zero = [day for day, value in zip(days, projected, strict=True) if value < -1e-9]
    below_safety = [day for day, value in zip(days, projected, strict=True) if value < safety - 1e-9]
    quantity = sum(receipt.quantity for receipt in spec.receipts)
    coverage = max((receipt.arrival_date for receipt in spec.receipts), default=None)
    line_value = quantity * (spec.unit_price if spec.unit_price is not None else price_usd)
    if spec.surcharge_amount is not None:
        surcharge = spec.surcharge_amount
    elif spec.surcharge_pct is not None:
        surcharge = spec.surcharge_pct / 100.0 * line_value
    else:
        surcharge = 0.0
    freight = spec.freight_amount or 0.0
    # A quoted unit price above (or below) the reference price is a cost (or a saving) of the option; an
    # expedited open order keeps its purchase-order price, so only new supply is compared.
    price_difference = 0.0
    if spec.unit_price is not None and price_usd > 0 and not spec.removes:
        price_difference = (spec.unit_price - price_usd) * quantity
    per_day = costs.get(
        f"line_down_cost_{str(criticality or 'c').lower()}", costs.get("line_down_cost_c", 0.0)
    )
    stockout = per_day * len(below_zero)
    rate = costs.get("holding_rate_annual", 0.2)
    excess_units_days = sum(max(0.0, float(value) - maximum) for value in projected) if maximum > 0 else 0.0
    holding = excess_units_days * price_usd * rate / HOLDING_DAYS_IN_YEAR
    covers = not below_zero if condition == "shortage" else not below_safety
    if spec.kind == "decline":
        notes.append("the supplier declined; the projection is unchanged")
    risk = None
    if model is not None and model.qualified and trigger is not None and condition in ("shortage", "warning"):
        window_day = max(trigger, coverage or trigger)
        window_end = sum(1 for day in days if day <= window_day) - 1

        def column(name: str) -> np.ndarray:
            return position.get_column(name).cast(pl.Float64).to_numpy()

        planned = column("planned_order_qty")
        risk = stockout_risk(
            model,
            projected,
            column("forecast_qty") - planned,
            column("firm_demand_qty") + planned,
            window_end,
            case_id,
            RISK_PATHS,
        )
    return Evaluation(
        evaluation_id=evaluation_id(case_id, basis_seq, spec),
        case_id=case_id,
        option=spec.option,
        kind=spec.kind,
        quantity=round(quantity, 3),
        coverage_date=coverage,
        min_projected_qty=round(float(projected.min()), 3) if len(projected) else 0.0,
        first_short_day=below_zero[0] if below_zero else None,
        days_below_zero=len(below_zero),
        days_below_safety=len(below_safety),
        covers_gap=covers,
        price_difference_cost=round(price_difference, 2),
        surcharge_cost=round(surcharge, 2),
        freight_cost=round(freight, 2),
        fixed_cost=round(spec.fixed_cost, 2),
        stockout_cost=round(stockout, 2),
        holding_cost=round(holding, 2),
        total_cost=round(price_difference + surcharge + freight + spec.fixed_cost + stockout + holding, 2),
        notes=notes,
        stockout_risk=round(risk.probability, 4) if risk else None,
        risk_window_end=days[risk.window_end] if risk else None,
        meets_service_target=risk.meets_target if risk else None,
        expected_days_below_zero=round(risk.expected_days_below_zero, 2) if risk else None,
        expected_line_down_cost=round(per_day * risk.expected_days_below_zero, 2) if risk else None,
    )


def shift(day: date, days: int) -> date:
    return day + timedelta(days=days)
