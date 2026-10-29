from pathlib import Path

import pytest
import sqlglot
from sqlglot import exp

from snm_data.contracts import ContractRegistry
from snm_data.semantic import PIPELINES_DIR, load_metric_views, view_sql
from snm_tools.backend import QueryResult
from snm_tools.metrics import MetricFilter, MetricOrder, MetricQueryError, compile_query, literal
from snm_tools.policy import AccessPolicy
from snm_tools.sql_guard import SqlGuard
from snm_tools.tools import DataTools, ToolFailure

CONTRACTS = Path(__file__).resolve().parents[2] / "data_contracts"
VIEWS = {view.name: view for view in load_metric_views()}


class FakeBackend:
    def __init__(self, rows=None, columns=None):
        self.rows = rows or [["Longgang", 0.97]]
        self.columns = columns or ["site", "on_time_pgi_rate"]
        self.executed: list[str] = []

    def execute(self, sql: str) -> QueryResult:
        self.executed.append(sql)
        return QueryResult(columns=self.columns, rows=self.rows, elapsed_s=0.01)


@pytest.fixture(scope="module")
def registry() -> ContractRegistry:
    return ContractRegistry.load(CONTRACTS)


def tools_for(registry, role="ops_analyst_global", backend=None, max_rows=100) -> DataTools:
    policy = AccessPolicy(registry, role)
    guard = SqlGuard(policy, max_rows=max_rows)
    return DataTools(policy, guard, backend or FakeBackend(), metric_views=list(VIEWS.values()))


def test_compiled_sql_uses_measure_and_checked_names() -> None:
    sql = compile_query(
        VIEWS["mv_outbound_due"],
        "snm",
        ["on_time_pgi_rate", "deliveries_due"],
        ["site"],
        [
            MetricFilter(dimension="fiscal_year", op="=", value=2026),
            MetricFilter(dimension="planned_gi_date", op="between", value=["2025-10-01", "2025-10-31"]),
            MetricFilter(dimension="site", op="in", value=["Longgang", "Dongguan"]),
        ],
        [MetricOrder(name="on_time_pgi_rate", descending=True)],
        10,
    )
    assert sql == (
        "SELECT `site`, MEASURE(`on_time_pgi_rate`) AS `on_time_pgi_rate`, "
        "MEASURE(`deliveries_due`) AS `deliveries_due`\n"
        "FROM `snm`.`semantic`.`mv_outbound_due`\n"
        "WHERE `fiscal_year` = 2026\n"
        "  AND `planned_gi_date` BETWEEN '2025-10-01' AND '2025-10-31'\n"
        "  AND `site` IN ('Longgang', 'Dongguan')\n"
        "GROUP BY ALL\nORDER BY `on_time_pgi_rate` DESC\nLIMIT 10"
    )
    total = compile_query(VIEWS["mv_outbound_shipped"], "snm", ["lines_shipped"])
    assert "GROUP BY" not in total and "ORDER BY" not in total


def where(dimension: str, op: str, value) -> list[MetricFilter]:
    return [MetricFilter(dimension=dimension, op=op, value=value)]


@pytest.mark.parametrize(
    ("measures", "dimensions", "filters", "order_by", "message"),
    [
        ([], [], [], [], "at least one measure"),
        (["lines_shipped"], [], [], [], "no measure"),
        (["on_time_pgi_rate"], ["region"], [], [], "no dimension"),
        (["on_time_pgi_rate"], [], where("vstel", "=", "C100"), [], "no dimension"),
        (["on_time_pgi_rate"], ["site", "site"], [], [], "once"),
        (["on_time_pgi_rate"], [], where("site", "between", ["a"]), [], "two values"),
        (["on_time_pgi_rate"], [], where("site", "=", ["a", "b"]), [], "single"),
        (["on_time_pgi_rate"], ["site"], [], [MetricOrder(name="fiscal_year")], "selected"),
    ],
)
def test_invalid_requests_are_rejected(measures, dimensions, filters, order_by, message) -> None:
    with pytest.raises(MetricQueryError, match=message):
        compile_query(VIEWS["mv_outbound_due"], "snm", measures, dimensions, filters, order_by)


def test_literals_are_escaped() -> None:
    assert literal("O'Brien") == "'O\\'Brien'"
    assert literal(True) == "TRUE" and literal(2026) == "2026"
    with pytest.raises(MetricQueryError):
        literal(float("nan"))


def test_metric_tools_respect_role_access_and_store_results(registry) -> None:
    menu = tools_for(registry).list_metrics()
    names = [view["metric_view"] for view in menu["metric_views"]]
    assert names == [
        "mv_autostore_availability", "mv_inbound_putaway", "mv_inventory", "mv_outbound_due",
        "mv_outbound_pod", "mv_outbound_shipped", "mv_replenishment_case", "mv_supplier_delivery",
        "mv_supply_position", "mv_warehouse_tasks",
    ]  # fmt: skip
    # The AutoStore view reads Longgang-only tables, so the Round Rock manager does not get it.
    rr = [
        view["metric_view"]
        for view in tools_for(registry, role="site_manager_rr").list_metrics()["metric_views"]
    ]
    assert "mv_autostore_availability" not in rr and "mv_warehouse_tasks" in rr
    # The menu is compact: names only; describe_metric carries the definitions.
    due = menu["metric_views"][3]
    assert due["definitions"] == ["metrics/on_time_pgi_rate.md"] and "on_time_pgi_rate" in due["measures"]
    assert all(isinstance(name, str) for name in due["measures"] + due["dimensions"])
    assert "describe_metric" in menu["note"]
    described = tools_for(registry).describe_metric("mv_outbound_due")
    assert {"name", "description"} <= set(described["measures"][0])
    assert described["dimensions"][0]["description"]
    with pytest.raises(ToolFailure) as failure:
        tools_for(registry, role="trade_compliance").describe_metric("mv_outbound_due")
    assert failure.value.code == "UNKNOWN_METRIC_VIEW"
    assert tools_for(registry, role="trade_compliance").list_metrics()["metric_views"] == []
    with pytest.raises(ToolFailure) as failure:
        tools_for(registry, role="trade_compliance").query_metric("mv_outbound_due", ["on_time_pgi_rate"])
    assert failure.value.code == "UNKNOWN_METRIC_VIEW"
    backend = FakeBackend()
    tools = tools_for(registry, backend=backend)
    result = tools.query_metric("mv_outbound_due", ["on_time_pgi_rate"], ["site"])
    assert result["metric_view"] == "mv_outbound_due" and result["sql"] == backend.executed[0]
    assert result["sql"].endswith("LIMIT 101")  # one row beyond the cap detects truncation
    stored = tools.get_result(result["result_id"])
    assert stored["rows"] == [["Longgang", 0.97]]
    # The SQL and the metric view behind a result_id are available for the answer's provenance.
    assert stored["sql"] == result["sql"] and stored["metric_view"] == "mv_outbound_due"
    with pytest.raises(ToolFailure) as failure:
        tools.query_metric("mv_outbound_due", ["lines_shipped"])
    assert failure.value.code == "INVALID_METRIC_QUERY" and "Measures:" in failure.value.hints[0]


def test_operator_dimension_is_for_people_level_roles_only(registry) -> None:
    analyst = tools_for(registry)
    menu = {view["metric_view"]: view for view in analyst.list_metrics()["metric_views"]}
    assert menu["mv_warehouse_tasks"]["restricted_for_role"] == ["operator"]
    described = analyst.describe_metric("mv_warehouse_tasks")
    flags = {item["name"]: item.get("restricted_for_role") for item in described["dimensions"]}
    assert flags["operator"] is True and flags["shift"] is None
    for dimensions, filters in (
        (["operator"], []),
        ([], [MetricFilter(dimension="operator", op="=", value="RRU0001")]),
    ):
        with pytest.raises(ToolFailure) as failure:
            analyst.query_metric("mv_warehouse_tasks", ["confirmed_tasks"], dimensions, filters)
        assert failure.value.code == "RESTRICTED_DIMENSION" and "aggregates" in failure.value.hints[0]
    backend = FakeBackend(rows=[["RRU0001", 60]], columns=["operator", "confirmed_tasks"])
    manager = tools_for(registry, role="site_manager_rr", backend=backend)
    assert "restricted_for_role" not in manager.list_metrics()["metric_views"][-1]
    result = manager.query_metric("mv_warehouse_tasks", ["confirmed_tasks"], ["operator"])
    assert result["columns"] == ["operator", "confirmed_tasks"] and "`operator`" in result["sql"]


def test_query_metric_truncates_at_the_row_cap(registry) -> None:
    backend = FakeBackend(rows=[[str(index), 1.0] for index in range(6)])
    result = tools_for(registry, backend=backend, max_rows=5).query_metric(
        "mv_outbound_shipped", ["lines_shipped"], ["work_center"]
    )
    assert result["truncated"] and result["row_count"] == 5


def test_curated_views_declare_the_tables_they_read(registry) -> None:
    for contract in registry:
        if contract.materialization != "view":
            continue
        tree = sqlglot.parse_one(view_sql(contract, "snm", PIPELINES_DIR), read="databricks")
        ctes = {cte.alias_or_name for cte in tree.find_all(exp.CTE)}
        tables = [table for table in tree.find_all(exp.Table) if table.name not in ctes and table.db]
        read = {f"{table.db}.{table.name}" for table in tables}
        assert read == set(contract.depends_on), contract.table
