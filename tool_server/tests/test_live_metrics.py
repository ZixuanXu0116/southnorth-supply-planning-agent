"""Live check: the governed metric views reproduce the evaluation reference answers they cover.

Each case's question is expressed as a query_metric request and run as the ops analyst's service principal;
the result must match the reference answer computed from the raw tables. Requires the role credentials.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from snm_data.governance import ROLES
from snm_tools.metrics import MetricFilter, MetricOrder
from snm_tools.server import create_tools
from snm_tools.tools import ToolFailure

ANSWERS = Path(__file__).resolve().parents[2] / "evals" / "answers" / "bank_v7.json"
OPS = ROLES["ops_analyst_global"]
pytestmark = pytest.mark.skipif(
    not os.environ.get(OPS.client_secret_env), reason="needs the agent role credentials"
)


def f(dimension: str, op: str, value) -> MetricFilter:
    return MetricFilter(dimension=dimension, op=op, value=value)


FY26 = f("fiscal_year", "=", 2026)
P11 = f("fiscal_period", "=", 11)
P10 = f("fiscal_period", "=", 10)
DUE = "mv_outbound_due"
SHIPPED = "mv_outbound_shipped"
POD = "mv_outbound_pod"
TASKS = "mv_warehouse_tasks"
INBOUND = "mv_inbound_putaway"
AUTOSTORE = "mv_autostore_availability"
INVENTORY = "mv_inventory"
SUPPLIER = "mv_supplier_delivery"
PICKS = f("is_pick", "=", True)
SEP20 = f("as_of_date", "=", "2026-09-20")
# case: (metric view, measures, dimensions, filters); the reference's key column is matched to the dimension.
CASES = {
    "seed_01": (DUE, ["on_time_pgi_rate"], ["shipping_point"], [FY26, f("fiscal_quarter", "=", 3)]),
    "c021": (DUE, ["on_time_pgi_rate"], ["shipping_point"], [FY26, f("fiscal_period", "=", 9)]),
    "c023": (
        DUE,
        ["on_time_pgi_rate"],
        ["shipping_point"],
        [f("planned_gi_date", "between", ["2025-10-01", "2025-10-31"])],
    ),
    "c101": (DUE, ["on_time_pgi_rate"], ["shipping_point"], [FY26, f("fiscal_quarter", "=", 4)]),
    "c112": (
        DUE,
        ["on_time_pgi_rate"],
        ["shipping_point"],
        [f("planned_gi_date", "between", ["2026-09-14", "2026-09-20"])],
    ),
    "seed_02": (
        SHIPPED,
        ["lines_shipped"],
        [],
        [f("site", "=", "Longgang"), FY26, f("fiscal_period", "=", 10)],
    ),
    "c025": (
        SHIPPED,
        ["lines_shipped"],
        [],
        [f("site", "=", "Round Rock"), FY26, f("fiscal_period", "=", 5)],
    ),
    "c039": (
        SHIPPED,
        ["deliveries_shipped", "lines_shipped"],
        [],
        [f("site", "=", "Dongguan"), FY26, f("fiscal_period", "=", 11)],
    ),
    "c050": (SHIPPED, ["lines_per_delivery"], ["shipping_point"], [FY26, f("fiscal_period", "=", 11)]),
    "c163": (
        SHIPPED,
        ["deliveries_shipped", "lines_shipped"],
        [],
        [f("site", "=", "Round Rock"), FY26, f("fiscal_period", "=", 11)],
    ),
    # wave-to-PGI lead time
    "seed_04": (SHIPPED, ["median_wave_to_pgi_minutes"], [], [f("site", "=", "Dongguan"), FY26, P11]),
    "c028": (SHIPPED, ["median_wave_to_pgi_minutes"], ["warehouse"], [FY26, P10]),
    # proof of delivery
    "c029": (POD, ["on_time_delivery_rate"], [], [f("site", "=", "Round Rock"), FY26, P10]),
    "seed_05": (POD, ["on_time_delivery_rate", "pod_pending"], ["shipping_point"], [FY26, P11]),
    # pick cycle time and task counts
    "seed_03": (
        TASKS,
        ["median_cycle_minutes"],
        ["warehouse"],
        [PICKS, f("status", "=", "confirmed"), FY26, P11],
    ),
    "c026": (
        TASKS,
        ["median_cycle_minutes", "p90_cycle_minutes"],
        [],
        [f("warehouse", "=", "RR01"), PICKS, f("status", "=", "confirmed"), FY26, P10],
    ),
    "c027": (
        TASKS,
        ["median_execution_minutes"],
        [],
        [
            f("warehouse", "=", "SZ01"),
            f("is_autostore", "=", True),
            PICKS,
            f("status", "=", "confirmed"),
            FY26,
            P11,
        ],
    ),
    "c110": (
        TASKS,
        ["median_cycle_minutes"],
        ["shift"],
        [
            f("warehouse", "=", "DG01"),
            PICKS,
            f("status", "=", "confirmed"),
            f("shift", "in", ["A", "B"]),
            FY26,
            P11,
        ],
    ),
    "c123": (TASKS, ["confirmed_tasks"], [], [f("warehouse", "=", "RR01"), PICKS, FY26, P11]),
    # dock-to-stock
    "c117": (
        INBOUND,
        ["median_dock_to_stock_minutes"],
        ["warehouse"],
        [f("is_put_away", "=", True), FY26, P11],
    ),
    "c118": (
        INBOUND,
        ["median_dock_to_stock_minutes", "p90_dock_to_stock_minutes"],
        [],
        [f("warehouse", "=", "SZ01"), f("is_put_away", "=", True), FY26, f("fiscal_quarter", "=", 3)],
    ),
    # AutoStore availability
    "c128": (AUTOSTORE, ["availability"], [], [f("subject", "=", "SYSTEM"), FY26, P11]),
    "c129": (
        AUTOSTORE,
        ["availability"],
        ["subject"],
        [f("subject_type", "=", "port"), FY26, f("fiscal_quarter", "=", 2)],
    ),
    "c134": (AUTOSTORE, ["downtime_hours"], [], [f("subject", "=", "AS-PORT-03"), FY26]),
    # available stock and days of supply
    "c031": (
        INVENTORY,
        ["available_qty"],
        [],
        [SEP20, f("site", "=", "Round Rock"), f("material", "=", "40000231")],
    ),
    "c033": (INVENTORY, ["median_days_of_supply"], [], [SEP20, f("site", "=", "Longgang")]),
    "c034": (
        INVENTORY,
        ["materials"],
        [],
        [SEP20, f("site", "=", "Dongguan"), f("material_days_of_supply", "<", 7)],
    ),
    "c035": (
        INVENTORY,
        ["days_of_supply"],
        ["site"],
        [SEP20, f("material", "=", "40000481"), f("has_demand", "=", True)],
    ),
    "c116": (
        INVENTORY,
        ["median_days_of_supply"],
        [],
        [f("as_of_date", "=", "2026-08-23"), f("site", "=", "Round Rock")],
    ),
    # supplier on-time delivery
    "c036": (
        SUPPLIER,
        ["on_time_rate"],
        ["company_code"],
        [f("order_type", "=", "standard"), FY26, f("fiscal_quarter", "=", 3)],
    ),
    "c038": (
        SUPPLIER,
        ["on_time_rate"],
        [],
        [f("order_type", "=", "standard"), f("vendor_name", "=", "Lindmark Sensorik"), FY26, P10],
    ),
    "c055": (SUPPLIER, ["overdue_open_lines"], ["company_code"], []),
}


@pytest.fixture(scope="module")
def tools():
    patch = pytest.MonkeyPatch()
    patch.setenv("DATABRICKS_CLIENT_ID", os.environ[OPS.client_id_env])
    patch.setenv("DATABRICKS_CLIENT_SECRET", os.environ[OPS.client_secret_env])
    yield create_tools()
    patch.undo()


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_metric_views_reproduce_reference_answers(tools, case_id) -> None:
    view, measures, dimensions, filters = CASES[case_id]
    result = tools.query_metric(view, measures, dimensions, filters)
    reference = json.loads(ANSWERS.read_text(encoding="utf-8"))["results"][case_id]["primary"]
    expected = {tuple(row[: len(dimensions)]): row[len(dimensions) :] for row in reference["rows"]}
    actual = {tuple(row[: len(dimensions)]): row[len(dimensions) :] for row in result["preview"]}
    if dimensions == ["site"]:  # the reference keys this case by shipping point
        points = {"Round Rock": "U100", "Longgang": "C100", "Dongguan": "C300"}
        actual = {(points[key[0]],): values for key, values in actual.items()}
    assert actual.keys() == expected.keys(), result["sql"]
    for key, values in expected.items():
        # References are rounded to 2 to 5 decimals; minutes and days compare to a hundredth.
        assert [float(v) for v in actual[key]] == pytest.approx([float(v) for v in values], abs=6e-3), key


def test_top_lists_reproduce_reference_rankings(tools) -> None:
    answers = json.loads(ANSWERS.read_text(encoding="utf-8"))["results"]
    slowest = tools.query_metric(
        INBOUND, ["median_dock_to_stock_minutes"], ["destination_storage_type"],
        [f("warehouse", "=", "RR01"), f("is_put_away", "=", True), FY26, P11],
        [MetricOrder(name="median_dock_to_stock_minutes", descending=True)], 1,
    )  # fmt: skip
    assert slowest["preview"][0][0] == answers["c125"]["primary"]["rows"][0][0]
    worst = tools.query_metric(
        SUPPLIER, ["on_time_rate"], ["vendor_name"], [f("order_type", "=", "standard"), FY26],
        [MetricOrder(name="on_time_rate"), MetricOrder(name="vendor_name")], 3,
    )  # fmt: skip
    assert [row[0] for row in worst["preview"]] == [row[0] for row in answers["c037"]["primary"]["rows"]]


def test_dongguan_rate_across_fiscal_years(tools) -> None:
    filters = [f("site", "=", "Dongguan"), f("fiscal_year", "in", [2025, 2026]), f("fiscal_period", "=", 11)]
    result = tools.query_metric(DUE, ["on_time_pgi_rate"], ["fiscal_year"], filters)
    rates = {row[0]: float(row[1]) for row in result["preview"]}
    reference = json.loads(ANSWERS.read_text(encoding="utf-8"))["results"]["c107"]["primary"]["rows"][0]
    assert rates == pytest.approx({2026: reference[0], 2025: reference[1]}, abs=6e-4)


def test_site_managers_get_their_site_only_through_metric_views(monkeypatch) -> None:
    rr = ROLES["site_manager_rr"]
    monkeypatch.setenv("SNM_ROLE", rr.name)
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", os.environ[rr.client_id_env])
    monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", os.environ[rr.client_secret_env])
    result = create_tools().query_metric(SHIPPED, ["lines_shipped"], ["site"], [FY26])
    assert [row[0] for row in result["preview"]] == ["Round Rock"]


def test_planning_views_agree_with_their_tables(tools) -> None:
    """The planning metric views aggregate the engine's tables; costs are not in the views."""
    as_of = tools.run_sql("SELECT MAX(as_of_date) FROM curated.replenishment_case")["preview"][0][0]
    run = f("as_of_date", "=", str(as_of))
    measures = ["cases", "shortage_cases", "agent_eligible_cases"]
    cases = tools.query_metric("mv_replenishment_case", measures, [], [run])
    counted = tools.run_sql(
        "SELECT COUNT(*), COUNT_IF(condition = 'shortage'), COUNT_IF(triage_class = 'agent_eligible') "
        f"FROM curated.replenishment_case WHERE as_of_date = DATE'{as_of}'"
    )["preview"][0]
    assert [int(v) for v in cases["preview"][0]] == [int(v) for v in counted]
    material, plant, on_hand, overflow, backlog = tools.run_sql(
        "SELECT material, plant, on_hand_qty, overflow_on_hand_qty, backlog_qty FROM curated.supply_position "
        f"WHERE as_of_date = DATE'{as_of}' AND day_offset = 0 ORDER BY material, plant LIMIT 1"
    )["preview"][0]
    day0 = tools.query_metric(
        "mv_supply_position", ["projected_qty", "projected_main_qty"], [],
        [run, f("material", "=", material), f("plant", "=", plant), f("day_offset", "=", 0)],
    )  # fmt: skip
    pooled, main = (float(value) for value in day0["preview"][0])
    # Engine 1.0 (record 003): the pooled line counts the overflow hub's stock, the main line does not.
    assert main == pytest.approx(float(on_hand) - float(backlog))
    assert pooled == pytest.approx(float(on_hand) + float(overflow) - float(backlog))
    described = tools.describe_metric("mv_replenishment_case")
    assert not any("cost" in item["name"] or "usd" in item["name"] for item in described["measures"])


def test_operator_counts_reach_site_managers_only(tools, monkeypatch) -> None:
    """c096: picks per operator at Round Rock; the mask passes operator identities through the views for
    people-level roles and the tool server refuses the dimension for the others."""
    filters = [f("warehouse", "=", "RR01"), PICKS, f("is_system_user", "=", False), FY26, P11]
    with pytest.raises(ToolFailure) as failure:
        tools.query_metric(TASKS, ["confirmed_tasks"], ["operator"], filters)
    assert failure.value.code == "RESTRICTED_DIMENSION"
    rr = ROLES["site_manager_rr"]
    monkeypatch.setenv("SNM_ROLE", rr.name)
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", os.environ[rr.client_id_env])
    monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", os.environ[rr.client_secret_env])
    result = create_tools().query_metric(TASKS, ["confirmed_tasks"], ["operator"], filters)
    reference = json.loads(ANSWERS.read_text(encoding="utf-8"))["results"]["c096"]["primary"]
    assert {row[0]: row[1] for row in result["preview"]} == {row[0]: row[1] for row in reference["rows"]}
