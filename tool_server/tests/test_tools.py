import json
from pathlib import Path

import pytest

from snm_data.contracts import ContractRegistry
from snm_tools.audit import AuditLog
from snm_tools.backend import BackendError, QueryResult
from snm_tools.policy import AccessPolicy
from snm_tools.sql_guard import SqlGuard
from snm_tools.tools import DataTools, ToolFailure

CONTRACTS = Path(__file__).resolve().parents[2] / "data_contracts"


class FakeBackend:
    """Records executed SQL and returns canned rows."""

    def __init__(
        self, rows: list[list] | None = None, columns: list[str] | None = None, error: str | None = None
    ):
        self.rows = rows if rows is not None else [[1]]
        self.columns = columns or ["value"]
        self.error = error
        self.executed: list[str] = []

    def execute(self, sql: str) -> QueryResult:
        self.executed.append(sql)
        if self.error:
            raise BackendError(self.error)
        return QueryResult(columns=self.columns, rows=self.rows, elapsed_s=0.01)


@pytest.fixture(scope="module")
def registry() -> ContractRegistry:
    return ContractRegistry.load(CONTRACTS)


def make_tools(
    registry, backend, role="ops_analyst_global", max_rows=100, audit=None, metadata="full"
) -> DataTools:
    policy = AccessPolicy(registry, role)
    return DataTools(
        policy, SqlGuard(policy, max_rows=max_rows), backend, audit=audit, preview_rows=5, metadata=metadata
    )


def test_list_tables_is_filtered_by_role(registry) -> None:
    analyst = {t["table"] for t in make_tools(registry, FakeBackend()).list_tables()["tables"]}
    manager = {
        t["table"]
        for t in make_tools(registry, FakeBackend(), role="site_manager_rr").list_tables()["tables"]
    }
    assert "raw_ext.workforce_roster" not in analyst
    assert "raw_ext.workforce_roster" in manager
    ewm_only = make_tools(registry, FakeBackend()).list_tables(schema="raw_sap_ewm")["tables"]
    assert ewm_only and all(t["table"].startswith("raw_sap_ewm.") for t in ewm_only)


def test_commercial_columns_need_cost_access(registry) -> None:
    analyst = make_tools(registry, FakeBackend())
    described = analyst.describe_table("raw_ext.supplier_condition")
    flags = {column["name"]: column["restricted_for_role"] for column in described["columns"]}
    assert flags["surcharge_pct_ge14"] and flags["expedite_freight_usd"] and not flags["min_expedite_days"]
    purchasing = AccessPolicy(registry, "purchasing")
    assert purchasing.restricted_columns("raw_ext.supplier_condition") == set()
    # Purchasing has cost access but no people-level access.
    assert purchasing.restricted_columns("raw_ext.purchasing_interaction") == {"handled_by"}


def test_describe_table_flags_restricted_columns(registry) -> None:
    described = make_tools(registry, FakeBackend()).describe_table("raw_sap_ewm.scwm_ordim_c")
    flags = {column["name"]: column["restricted_for_role"] for column in described["columns"]}
    assert flags["confirmed_by"] is True and flags["tanum"] is False
    assert any(rel["references"] == "raw_sap_ewm.scwm_wavehdr" for rel in described["relationships"])
    with pytest.raises(ToolFailure) as failure:
        make_tools(registry, FakeBackend()).describe_table("raw_ext.workforce_roster")
    assert failure.value.code == "TABLE_NOT_ALLOWED"


def test_get_result_returns_the_sql_behind_a_handle(registry) -> None:
    tools = make_tools(registry, FakeBackend(rows=[[3]], columns=["n"]))
    handle = tools.run_sql("SELECT COUNT(*) AS n FROM raw_sap_ecc.likp")["result_id"]
    stored = tools.get_result(handle)
    assert stored["sql"].startswith("SELECT COUNT(*) AS n FROM") and "metric_view" not in stored


def test_run_sql_returns_preview_handle_and_truncation(registry) -> None:
    backend = FakeBackend(rows=[[index] for index in range(11)], columns=["n"])
    tools = make_tools(registry, backend, max_rows=10)
    response = tools.run_sql("SELECT vbeln FROM raw_sap_ecc.likp", purpose="test")
    assert backend.executed[-1].endswith("LIMIT 11")
    assert response["truncated"] is True and response["row_count"] == 10
    assert len(response["preview"]) == 5 and response["stats"]["n"]["max"] == 9.0
    page = tools.get_result(response["result_id"], offset=8, limit=5)
    assert page["rows"] == [[8], [9]] and page["total_rows"] == 10


def test_rejected_sql_never_reaches_the_backend(registry, tmp_path) -> None:
    backend = FakeBackend()
    audit_path = tmp_path / "audit.jsonl"
    tools = make_tools(registry, backend, audit=AuditLog(audit_path))
    with pytest.raises(ToolFailure) as failure:
        tools.run_sql("DROP TABLE raw_sap_ecc.likp")
    assert failure.value.code == "NOT_READ_ONLY"
    assert backend.executed == []
    entry = json.loads(audit_path.read_text().splitlines()[-1])
    assert entry["allowed"] is False and entry["code"] == "NOT_READ_ONLY"


def test_backend_errors_become_tool_failures(registry) -> None:
    tools = make_tools(registry, FakeBackend(error="[UNRESOLVED_COLUMN] `vbelnx` cannot be resolved"))
    with pytest.raises(ToolFailure) as failure:
        tools.run_sql("SELECT vbeln FROM raw_sap_ecc.likp")
    assert failure.value.code == "QUERY_FAILED" and "vbelnx" in failure.value.message


def test_sample_rows_omits_restricted_columns(registry) -> None:
    backend = FakeBackend(rows=[["x"]], columns=["tanum"])
    response = make_tools(registry, backend).sample_rows("raw_sap_ewm.scwm_ordim_c", n=50)
    assert "`confirmed_by`" not in backend.executed[-1]
    assert backend.executed[-1].endswith("LIMIT 20")
    assert response["omitted_columns"] == ["confirmed_by"]


def test_profile_column_validates_column_and_role(registry) -> None:
    tools = make_tools(registry, FakeBackend(rows=[[10, 9, 3, "A", "C"]], columns=["a", "b", "c", "d", "e"]))
    with pytest.raises(ToolFailure) as unknown:
        tools.profile_column("raw_sap_ecc.likp", "nope")
    assert unknown.value.code == "UNKNOWN_COLUMN"
    with pytest.raises(ToolFailure) as restricted:
        tools.profile_column("raw_sap_ewm.scwm_ordim_c", "confirmed_by")
    assert restricted.value.code == "RESTRICTED_COLUMN"
    profile_backend = FakeBackend(rows=[[10, 9, 3, "A", "C"]], columns=["a", "b", "c", "d", "e"])
    manager = make_tools(registry, profile_backend, role="site_manager_cn")
    assert manager.profile_column("raw_sap_ewm.scwm_ordim_c", "confirmed_by")["summary"]["a"] == 10


def test_invalid_table_names_are_rejected(registry) -> None:
    tools = make_tools(registry, FakeBackend())
    for name, code in [("likp", "INVALID_TABLE"), ("raw_sap_ecc.likp; drop", "INVALID_TABLE"),
                       ("raw_sap_ecc.nope", "UNKNOWN_TABLE")]:  # fmt: skip
        with pytest.raises(ToolFailure) as failure:
            tools.describe_table(name)
        assert failure.value.code == code


def test_bare_metadata_hides_documentation(registry) -> None:
    tools = make_tools(registry, FakeBackend(), metadata="bare")
    listed = tools.list_tables(schema="raw_sap_ecc")["tables"]
    assert listed and all(set(entry) == {"table"} for entry in listed)
    described = tools.describe_table("raw_sap_ewm.scwm_ordim_c")
    assert set(described) == {"table", "columns"}
    assert all(set(column) == {"name", "type", "restricted_for_role"} for column in described["columns"])
    with pytest.raises(ValueError):
        make_tools(registry, FakeBackend(), metadata="partial")


def test_site_managers_see_their_row_scope(registry) -> None:
    manager = make_tools(registry, FakeBackend(), role="site_manager_rr")
    assert manager.list_tables()["sites"] == ["US10"]
    assert "row filter on werks" in manager.describe_table("raw_sap_ecc.mseg")["row_scope"]
    assert "Not filtered by site" in manager.describe_table("raw_sap_ecc.mkpf")["row_scope"]
    bare = make_tools(registry, FakeBackend(), role="site_manager_cn", metadata="bare")
    assert "CN10" in bare.describe_table("raw_sap_ewm.scwm_ordim_c")["row_scope"]
    # All-site roles see no scope notes, and single-site tables need none.
    analyst = make_tools(registry, FakeBackend())
    assert "sites" not in analyst.list_tables()
    assert "row_scope" not in analyst.describe_table("raw_sap_ecc.mseg")
    assert "row_scope" not in manager.describe_table("raw_ext.lts_rfid_scan")
