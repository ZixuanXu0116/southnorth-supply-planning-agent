"""Replenishment engine: hand-computed references, lever order, triage, properties, profile smoke run."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from snm_data.contracts import ContractRegistry, check_frame, conform
from snm_data.generator.config import PROFILES
from snm_data.generator.run import generate
from snm_data.planning import PlanningInputs, PlanningParams, plan
from snm_data.planning.engine import classify_vendors, project_one, triage

CONTRACTS = Path(__file__).resolve().parents[2] / "data_contracts"
AS_OF = date(2026, 9, 21)
VENDOR = {
    "vendor": "0000100001", "name": "Kestrel Vacuum Components", "country": "US", "spend_usd_12m": 1000.0,
    "po_count_12m": 4, "on_time_rate_12m": 0.9, "median_lateness_days": 2.0, "median_rounds": 4.0,
    "median_days_to_resolution": 6.0, "strategic": False, "portal_onboarded": True, "min_expedite_days": 5,
    "surcharge_pct_lt7": 10.0, "surcharge_pct_7_13": 15.0, "surcharge_pct_ge14": 25.0,
    "expedite_freight_usd": 150.0,
}  # fmt: skip


def _inputs(
    *,
    on_hand: float = 10.0,
    safety: float = 0.0,
    maximum: float = 1000.0,
    plifz: int = 20,
    price: float = 100.0,
    criticality: str = "B",
    firm: list[tuple[int, float]] = (),
    production: list[tuple[int, float]] = (),
    planned: list[tuple[int, float]] = (),
    orders: list[tuple[int, float]] = (),
    rate: float = 0.0,
    overflow_stock: float = 0.0,
    vendor: dict | None = VENDOR,
    with_history: bool = True,
) -> PlanningInputs:
    material, plant = "40000001", "US10"
    on_hand, overflow_stock = float(on_hand), float(overflow_stock)
    safety, maximum, price, rate = float(safety), float(maximum), float(price), float(rate)
    stock = pl.DataFrame(
        {
            "material": [material, material],
            "plant": [plant, plant],
            "storage_location": ["1000", "2001"],
            "available_qty": [on_hand, overflow_stock],
            "in_transit_qty": [0.0, 0.0],
            "reorder_point": [0.0, 0.0],
        }
    )
    order_rows = [
        {
            "ebeln": f"45000{index:05d}", "ebelp": "00010", "material": material, "plant": plant,
            "vendor": VENDOR["vendor"], "open_qty": qty, "agreed_date": AS_OF + timedelta(days=offset),
            "expected_date": AS_OF + timedelta(days=offset), "unit_price_usd": price,
        }
        for index, (offset, qty) in enumerate(orders, start=1)
    ]  # fmt: skip
    order_schema = {
        "ebeln": pl.String, "ebelp": pl.String, "material": pl.String, "plant": pl.String,
        "vendor": pl.String, "open_qty": pl.Float64, "agreed_date": pl.Date, "expected_date": pl.Date,
        "unit_price_usd": pl.Float64,
    }  # fmt: skip
    firm_rows = [
        {"material": material, "plant": plant, "requirement_date": AS_OF + timedelta(days=offset), "qty": qty,
         "source": "delivery"}
        for offset, qty in firm
    ] + [
        {"material": material, "plant": plant, "requirement_date": AS_OF + timedelta(days=offset), "qty": qty,
         "source": "production"}
        for offset, qty in production
    ]  # fmt: skip
    planned_rows = [
        {"material": material, "plant": plant, "requirement_date": AS_OF + timedelta(days=offset), "qty": qty,
         "source": "planned_order"}
        for offset, qty in planned
    ]  # fmt: skip
    firm_schema = {
        "material": pl.String, "plant": pl.String, "requirement_date": pl.Date, "qty": pl.Float64,
        "source": pl.String,
    }  # fmt: skip
    vendors = pl.DataFrame([vendor or VENDOR])
    return PlanningInputs(
        as_of=AS_OF,
        materials=pl.DataFrame(
            {
                "material": [material],
                "plant": [plant],
                "criticality": [criticality],
                "safety_stock": [safety],
                "max_stock": [maximum],
                "planned_delivery_days": [plifz],
                "price_usd": [price],
                "description": ["VALVE, GATE"],
            }
        ),
        stock=stock,
        open_orders=pl.DataFrame(order_rows, schema=order_schema),
        firm_demand=pl.DataFrame(firm_rows, schema=firm_schema),
        planned_demand=pl.DataFrame(planned_rows, schema=firm_schema),
        demand_rate=pl.DataFrame(
            {"material": [material], "plant": [plant], "storage_location": ["1000"], "daily_rate": [rate]}
        ),
        material_vendor=pl.DataFrame(
            {
                "material": [material] if with_history else [],
                "plant": [plant] if with_history else [],
                "vendor": [VENDOR["vendor"]] if with_history else [],
            },
            schema={"material": pl.String, "plant": pl.String, "vendor": pl.String},
        ),
        vendors=vendors,
        cost_parameters={
            "line_down_cost_a": 5000.0,
            "line_down_cost_b": 1000.0,
            "line_down_cost_c": 200.0,
            "holding_rate_annual": 0.2,
            "transfer_cost_fixed": 40.0,
            "transfer_cost_per_unit": 0.5,
            "order_cost": 75.0,
            "expedite_freight_default": 150.0,
        },
    )


def _case(outputs) -> dict:
    assert outputs.replenishment_case.height == 1
    return outputs.replenishment_case.to_dicts()[0]


def test_projection_arithmetic_by_hand() -> None:
    inputs = _inputs(on_hand=10, firm=[(3, 4.0), (10, 8.0)], orders=[(6, 5.0)], safety=5.0)
    projection = project_one(inputs, PlanningParams(), "40000001", "US10", inputs.stock)
    projected = projection["projected"]
    assert projected[0] == 10 and projected[2] == 10 and projected[3] == 6
    assert projected[6] == 11 and projected[9] == 11 and projected[10] == 3 and projected[91] == 3
    # Conservation: each day's change is that day's supply minus demand.
    change = np.diff(projected)
    expected = (
        projection["in_transit"] + projection["receipts"] - projection["firm"] - projection["forecast"]
    )[1:]
    assert np.allclose(change, expected)
    # Below safety stock (5) from day 10: a warning with gap 2.
    case = _case(plan(inputs))
    assert case["condition"] == "warning" and case["trigger_date"] == AS_OF + timedelta(days=10)
    # A new order (20 days) is late and an expedited one (the vendor's 5 days) would be in time, but a
    # warning never gets an expedite: the standard order is recommended, ten days after the warning date,
    # and no line-down cost accrues above zero.
    assert case["gap_qty"] == 2.0 and case["lever"] == "new_order" and case["covers_in_time"] is False
    assert case["days_short"] == 10 and case["stockout_cost_usd"] == 0.0
    assert case["coverage_date"] == AS_OF + timedelta(days=20)
    assert {item["lever"] for item in json.loads(case["alternatives"])} == {"expedited_new_order"}
    assert "no expedite is recommended; stock stays below the minimum for 10 days" in case["rationale"]


def test_forecast_consumes_firm_demand_within_the_week() -> None:
    inputs = _inputs(on_hand=100, rate=1.0, firm=[(2, 3.0)])
    projection = project_one(inputs, PlanningParams(), "40000001", "US10", inputs.stock)
    # Week 1 (days 1-7): 7 of forecast minus 3 firm = 4 spread over 7 days; week 2: 7.
    assert projection["forecast"][1:8].sum() == pytest.approx(4.0)
    assert projection["forecast"][8:15].sum() == pytest.approx(7.0)
    assert projection["projected"][7] == pytest.approx(100 - 7)


def test_production_reservations_are_firm_and_the_rate_fills_what_requirements_do_not_cover() -> None:
    inputs = _inputs(
        on_hand=100, rate=1.0, firm=[(2, 3.0)], production=[(2, 10.0), (-1, 4.0)], planned=[(20, 6.0)]
    )
    projection = project_one(inputs, PlanningParams(), "40000001", "US10", inputs.stock)
    # Week 1 has 13 of explicit demand against a rate of 7: nothing is added. Week 3 has 6 of planned-order
    # requirements against 7: the rate adds 1, and the planned orders are demand on their own date.
    assert projection["forecast"][1:8].sum() == pytest.approx(0.0)
    assert projection["forecast"][15:22].sum() == pytest.approx(7.0)
    assert projection["production"][2] == 10.0 and projection["production_backlog"] == 4.0
    assert projection["backlog"] == 4.0 and projection["planned"][20] == 6.0
    # Day 2: 100 - 4 backlog - 3 customer - 10 production.
    assert projection["projected"][2] == pytest.approx(100 - 4 - 3 - 10)
    case = _case(plan(inputs))
    assert case["production_backlog_qty"] == 4.0 and "production 4" in case["rationale"]


def test_expedite_covers_a_shortage_before_a_new_order_would() -> None:
    inputs = _inputs(on_hand=10, firm=[(10, 15.0)], orders=[(30, 20.0)], plifz=20)
    case = _case(plan(inputs))
    assert case["condition"] == "shortage" and case["trigger_date"] == AS_OF + timedelta(days=10)
    assert case["gap_qty"] == 5.0 and case["lever"] == "expedite" and case["covers_in_time"]
    assert case["coverage_date"] == AS_OF + timedelta(days=5)  # the vendor's minimum expedite lead time
    # 25 days gained -> 25 % surcharge on the whole open order plus freight.
    assert case["lever_cost_usd"] == pytest.approx(0.25 * 20 * 100 + 150)
    costs = json.loads(case["alternative_costs_usd"])
    assert set(costs) == {"expedite", "new_order", "expedited_new_order"} and costs["new_order"] == 75.0
    assert "Expedite purchase order 4500000001" in case["rationale"]


def test_pull_from_the_overflow_hub_when_only_the_main_warehouse_runs_short() -> None:
    # The main warehouse alone goes to -5 on day 2; with the overflow hub's 40 the pool stays at 35.
    inputs = _inputs(on_hand=0, firm=[(2, 5.0)], overflow_stock=40.0, orders=[(3, 50.0)])
    case = _case(plan(inputs))
    assert case["condition"] == "pull" and case["trigger_date"] == AS_OF + timedelta(days=2)
    assert case["gap_qty"] == 5.0 and case["overflow_on_hand_qty"] == 40.0
    assert case["lever"] == "transfer" and case["coverage_date"] == AS_OF  # satellite lane: same day
    assert case["lever_cost_usd"] == pytest.approx(40 + 0.5 * 5) and case["covers_in_time"]
    detail = json.loads(case["lever_detail"])
    assert detail["from_location"] == "2001" and detail["to_location"] == "1000"
    assert case["triage_class"] == "human_handled" and "logistics" in case["triage_reasons"]
    assert "Pull 5 from overflow hub 2001" in case["rationale"] and case["stockout_cost_usd"] == 0.0


def test_new_order_when_the_planned_lead_time_is_in_time() -> None:
    inputs = _inputs(on_hand=0, firm=[(30, 5.0)], plifz=20)
    case = _case(plan(inputs))
    assert case["lever"] == "new_order" and case["coverage_date"] == AS_OF + timedelta(days=20)
    assert case["triage_class"] == "agent_eligible"


def test_expedited_new_order_when_only_the_minimum_lead_time_is_in_time() -> None:
    inputs = _inputs(on_hand=0, firm=[(8, 5.0)], plifz=20)
    case = _case(plan(inputs))
    assert case["lever"] == "expedited_new_order" and case["coverage_date"] == AS_OF + timedelta(days=5)
    assert case["lever_cost_usd"] == pytest.approx(75 + 0.25 * 5 * 100 + 150)  # 15 days gained


def test_nothing_in_time_reports_the_earliest_lever_and_days_short() -> None:
    inputs = _inputs(on_hand=0, firm=[(2, 5.0)], plifz=20, criticality="A")
    case = _case(plan(inputs))
    assert case["lever"] == "expedited_new_order" and case["covers_in_time"] is False
    assert case["days_short"] == 3 and case["stockout_cost_usd"] == 5000.0 * 3
    assert "3 days short" in case["rationale"]


def test_warning_never_takes_an_expedite_even_when_only_an_expedite_is_in_time() -> None:
    # Safety stock 5 is undercut on day 8 (gap 2); an open order of 20 is expected on day 30.
    inputs = _inputs(on_hand=10, firm=[(8, 7.0)], orders=[(30, 20.0)], safety=5.0, plifz=20)
    case = _case(plan(inputs))
    assert case["condition"] == "warning" and case["trigger_date"] == AS_OF + timedelta(days=8)
    assert case["gap_qty"] == 2.0
    # Expediting the open order (the vendor's 5 days) or an expedited new order would be in time; the
    # standard order is recommended anyway, and both expedites are listed as alternatives with their costs.
    assert case["lever"] == "new_order" and case["coverage_date"] == AS_OF + timedelta(days=20)
    assert case["covers_in_time"] is False and case["days_short"] == 12 and case["stockout_cost_usd"] == 0.0
    alternatives = {item["lever"]: item for item in json.loads(case["alternatives"])}
    assert set(alternatives) == {"expedite", "expedited_new_order"}
    assert alternatives["expedite"]["coverage_date"] == (AS_OF + timedelta(days=5)).isoformat()
    costs = json.loads(case["alternative_costs_usd"])
    assert costs["new_order"] == 75.0 and costs["expedite"] == pytest.approx(0.25 * 20 * 100 + 150)
    assert "no expedite is recommended" in case["rationale"]


def test_pooled_stock_decides_between_a_pull_and_a_warning() -> None:
    # Main warehouse 10, overflow hub 40, minimum 5, 7 needed on day 8: the pool stays at 43, the main
    # warehouse alone dips to 3, so this is a pull of 2, not a warning.
    inputs = _inputs(on_hand=10, firm=[(8, 7.0)], safety=5.0, overflow_stock=40.0)
    case = _case(plan(inputs))
    assert case["condition"] == "pull" and case["gap_qty"] == 2.0 and case["lever"] == "transfer"
    # Without the overflow hub the same numbers are a warning covered in time by a standard order.
    inputs = _inputs(on_hand=10, firm=[(30, 7.0)], safety=5.0, plifz=20)
    case = _case(plan(inputs))
    assert case["condition"] == "warning" and case["lever"] == "new_order" and case["covers_in_time"]
    assert case["days_short"] == 0 and "no expedite is recommended." in case["rationale"]
    # A shortage counts the overflow hub's stock and says so.
    inputs = _inputs(on_hand=0, firm=[(2, 50.0)], overflow_stock=20.0, plifz=20)
    case = _case(plan(inputs))
    assert case["condition"] == "shortage" and case["gap_qty"] == 30.0
    assert "overflow hub's 20 are counted" in case["rationale"]
    assert "transfer" not in json.loads(case["alternative_costs_usd"])


def test_excess_recommends_a_move_to_the_overflow_hub() -> None:
    inputs = _inputs(on_hand=500, maximum=100.0, safety=10.0)
    case = _case(plan(inputs))
    assert case["condition"] == "excess" and case["lever"] == "move_to_overflow" and case["gap_qty"] == 400.0
    detail = json.loads(case["lever_detail"])
    assert detail["from_location"] == "1000" and detail["to_location"] == "2001"
    assert case["lever_cost_usd"] == pytest.approx(40 + 0.5 * 400) and case["covers_in_time"]
    assert case["triage_class"] == "human_handled" and "logistics" in case["triage_reasons"]
    assert "Move 400 to overflow hub 2001" in case["rationale"]


def test_no_case_when_stock_covers_the_horizon() -> None:
    inputs = _inputs(on_hand=50, maximum=1000.0, safety=5.0, firm=[(5, 10.0)])
    assert plan(inputs).replenishment_case.height == 0


def test_triage_rules() -> None:
    params = PlanningParams()
    base = {**VENDOR, "supplier_class": "C"}
    case = {"value_usd": 1000.0}
    assert triage(case, base, params) == (
        "agent_eligible",
        ["tail supplier, within value and effort thresholds"],
    )
    assert triage(case, None, params)[0] == "no_supplier"
    assert "strategic supplier" in triage(case, {**base, "strategic": True}, params)[1]
    assert triage({"value_usd": 30_000.0}, base, params)[1] == ["case value 30000 USD above 25000"]
    big = {**base, "supplier_class": "A", "po_count_12m": 40}
    assert triage(case, big, params)[1] == ["supplier class A with 40 orders"]
    easy = {**base, "median_rounds": 1.0, "median_days_to_resolution": 2.0}
    assert triage(case, easy, params)[1] == ["low historical effort (1 rounds, 2 days)"]
    # No history counts as eligible.
    assert triage(case, {**base, "median_rounds": None, "median_days_to_resolution": None}, params)[0] == (
        "agent_eligible"
    )


def test_vendor_classes_follow_cumulative_spend() -> None:
    vendors = pl.DataFrame(
        [
            {**VENDOR, "vendor": "V1", "spend_usd_12m": 700.0, "po_count_12m": 30, "strategic": True},
            {**VENDOR, "vendor": "V2", "spend_usd_12m": 200.0, "po_count_12m": 20},
            {**VENDOR, "vendor": "V3", "spend_usd_12m": 60.0, "po_count_12m": 3},
            {**VENDOR, "vendor": "V4", "spend_usd_12m": 40.0, "po_count_12m": 2, "median_rounds": 1.0,
             "median_days_to_resolution": 1.0},
        ]
    )  # fmt: skip
    classified = classify_vendors(vendors, PlanningParams())
    rows = {row["vendor"]: row for row in classified.to_dicts()}
    assert [rows[v]["supplier_class"] for v in ("V1", "V2", "V3", "V4")] == ["A", "B", "C", "C"]
    assert [rows[v]["agent_eligible_supplier"] for v in ("V1", "V2", "V3", "V4")] == [
        False,
        False,
        True,
        False,
    ]


def test_more_stock_never_worsens_the_projection() -> None:
    low = _inputs(on_hand=5, firm=[(3, 4.0), (10, 8.0)], rate=0.5)
    high = _inputs(on_hand=25, firm=[(3, 4.0), (10, 8.0)], rate=0.5)
    params = PlanningParams()
    a = project_one(low, params, "40000001", "US10", low.stock)["projected"]
    b = project_one(high, params, "40000001", "US10", high.stock)["projected"]
    assert np.all(b - a == pytest.approx(20.0))


def test_engine_outputs_conform_to_their_contracts_on_the_test_profile() -> None:
    registry = ContractRegistry.load(CONTRACTS)
    profile = PROFILES["test"]
    tables = generate(profile, registry).tables
    inputs = PlanningInputs.from_tables(tables, profile.ecc_as_of.date(), PlanningParams())
    outputs = plan(inputs)
    frames = {
        "curated.supply_position": outputs.supply_position,
        "curated.replenishment_case": outputs.replenishment_case,
        "curated.supplier_profile": outputs.supplier_profile,
    }
    for name, frame in frames.items():
        conformed = conform(frame, registry[name])
        assert check_frame(conformed, registry[name]) == [], name
    cases = outputs.replenishment_case
    assert cases.height > 0 and cases.get_column("case_id").n_unique() == cases.height
    assert outputs.supply_position.height == inputs.materials.height * (PlanningParams().horizon_days + 1)
    # Every case's rationale names its trigger date and its lever is one of the fixed set.
    for row in cases.iter_rows(named=True):
        assert f"{row['trigger_date']:%Y-%m-%d}" in row["rationale"] or row["condition"] == "excess"
    # The same inputs give the same outputs.
    again = plan(inputs)
    assert again.replenishment_case.equals(cases) and again.supply_position.equals(outputs.supply_position)


def test_demand_rate_is_the_staging_to_the_production_supply_area() -> None:
    """What the main warehouse issued to manufacturing (311 staging to 2100, net of 312 reversals) is the
    demand rate; customer goods issues and transfers to the overflow hub are not, and plants outside the
    scope are ignored."""
    from snm_data.planning.inputs import _demand_rate

    def row(bwart, shkzg, werks, lgort, umlgo, menge):
        return {"bwart": bwart, "shkzg": shkzg, "matnr": "000000000040000001", "werks": werks, "lgort": lgort,
                "umlgo": umlgo, "menge": float(menge), "budat_mkpf": "20260915"}  # fmt: skip

    mseg = pl.DataFrame(
        [
            row("311", "H", "US10", "1000", "2100", 56),  # staging for production: demand
            row("311", "S", "US10", "2100", "1000", 56),  # the receiving item is not counted again
            row("312", "H", "US10", "2100", "1000", 28),  # reversal: the issuing item at the supply area
            row("312", "S", "US10", "1000", "2100", 28),  # reversal: back at the warehouse, counts negative
            row("601", "H", "US10", "1000", "", 28),  # legacy customer issue: out of scope
            row("311", "H", "US10", "1000", "2001", 28),  # move to the overflow hub: not demand
            row("311", "H", "CN10", "1000", "2100", 280),  # plant outside the scope
        ]
    )
    rates = {
        (r["plant"], r["storage_location"]): r["daily_rate"]
        for r in _demand_rate(mseg, date(2026, 9, 21), PlanningParams()).iter_rows(named=True)
    }
    assert rates == {("US10", "1000"): pytest.approx(1.0)}
    with_customers = _demand_rate(mseg, date(2026, 9, 21), PlanningParams(include_customer_demand=True))
    assert with_customers.get_column("daily_rate").to_list() == [pytest.approx(2.0)]


def test_floating_point_noise_below_zero_opens_no_case() -> None:
    """Engine 1.0.1: a projection that ends at -1e-15 after fractional daily rates is at zero, not short
    (engine 1.0.0 opened shortage cases with a gap of 0 on such noise)."""
    rate = 1 / 19
    outputs = plan(_inputs(on_hand=91 * rate, rate=rate, safety=0.0), PlanningParams())
    low = outputs.supply_position.get_column("projected_qty").min()
    assert -1e-9 < low < 0  # the noise is there
    assert outputs.replenishment_case.height == 0
