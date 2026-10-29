from datetime import UTC, datetime
from pathlib import Path

import pytest

from snm_data.contracts import ContractRegistry
from snm_data.databricks import DatabricksSettings, SettingsError
from snm_data.ddl import insert_from_files, sql_string, table_ddl, tag_statements
from snm_data.load import (
    parse_snapshot,
    setup_statements,
    snapshot_of,
    snapshot_statements,
    table_statements,
    volume_path,
)

CONTRACTS = Path(__file__).resolve().parents[2] / "data_contracts"


@pytest.fixture(scope="module")
def registry() -> ContractRegistry:
    return ContractRegistry.load(CONTRACTS)


def test_sql_string_escapes_quotes_and_backslashes() -> None:
    assert sql_string("operator's bin") == "'operator\\'s bin'"
    assert sql_string("a\\b") == "'a\\\\b'"


def test_table_ddl_carries_types_nullability_and_comments(registry: ContractRegistry) -> None:
    ddl = table_ddl(registry["raw_sap_ecc.likp"], "snm")
    assert ddl.startswith("CREATE OR REPLACE TABLE `snm`.`raw_sap_ecc`.`likp` (")
    assert "`vbeln` STRING NOT NULL COMMENT 'Delivery number (zero-padded, 10 characters).'" in ddl
    assert "`btgew` DECIMAL(15,3) NOT NULL" in ddl
    assert "Grain: One row per client and outbound delivery." in ddl


def test_insert_selects_contract_columns_from_parquet(registry: ContractRegistry) -> None:
    statement = insert_from_files(
        registry["raw_sap_ecc.t001w"], "snm", "/Volumes/snm/landing/files/x.parquet"
    )
    assert "read_files('/Volumes/snm/landing/files/x.parquet', format => 'parquet')" in statement
    assert statement.count("`werks`") == 2


def test_tags_include_domain_layer_and_column_sensitivity(registry: ContractRegistry) -> None:
    statements = tag_statements(registry["raw_sap_ewm.scwm_ordim_c"], "snm")
    assert "'domain' = 'WarehouseOperations'" in statements[0]
    assert "'sensitivity' = 'restricted'" in statements[0]
    assert any("ALTER COLUMN `confirmed_by` SET TAGS ('sensitivity' = 'restricted')" in s for s in statements)


def test_load_plan_orders_setup_before_tables(registry: ContractRegistry) -> None:
    setup = setup_statements("snm", {"raw_sap_ecc"})
    assert setup[0] == "CREATE CATALOG IF NOT EXISTS `snm`"
    assert any("`landing`" in statement for statement in setup)
    assert setup[-1].startswith("CREATE VOLUME IF NOT EXISTS")
    statements = table_statements(registry["raw_sap_ecc.t001w"], "snm", "small")
    assert statements[0].startswith("CREATE OR REPLACE TABLE")
    assert statements[1].startswith("INSERT OVERWRITE")  # idempotent if the connector retries
    assert (
        volume_path("snm", "small", "raw_sap_ecc.t001w")
        == "/Volumes/snm/landing/files/small/raw_sap_ecc/t001w.parquet"
    )


def test_settings_require_credentials_and_hide_token() -> None:
    with pytest.raises(SettingsError):
        DatabricksSettings.from_env({"DATABRICKS_HOST": "dbc-example.cloud.databricks.com"})
    settings = DatabricksSettings.from_env(
        {
            "DATABRICKS_HOST": "dbc-example.cloud.databricks.com/",
            "DATABRICKS_TOKEN": "secret-value",
            "DATABRICKS_WAREHOUSE_ID": "abc123",
        }
    )
    assert settings.host == "https://dbc-example.cloud.databricks.com"
    assert settings.http_path == "/sql/1.0/warehouses/abc123"
    assert settings.catalog == "snm"
    assert "secret-value" not in repr(settings)


def test_snapshot_properties_round_trip() -> None:
    manifest = {
        "generator_version": "0.6.0",
        "profile": {"name": "small", "seed": "20260924"},
        "contracts_digest": "b05346bb64576b77",
    }
    snapshot = snapshot_of(manifest)
    at = datetime(2026, 10, 2, 1, 30, tzinfo=UTC)
    [statement] = snapshot_statements("snm", {"raw_sap_ecc"}, snapshot, "complete", at)
    assert statement.startswith("ALTER SCHEMA `snm`.`raw_sap_ecc` SET DBPROPERTIES (")
    assert "'snm.generator_version' = '0.6.0'" in statement and "'snm.load_status' = 'complete'" in statement
    # The Properties row of DESCRIBE SCHEMA EXTENDED, as Databricks returns it.
    described = (
        "((snm.contracts_digest,b05346bb64576b77), (snm.generator_version,0.6.0), "
        "(snm.load_status,complete), (snm.loaded_at,2026-10-02T01:30:00+00:00), (snm.profile,small), "
        "(snm.seed,20260924), "
        "(unity.catalog.managed.iceberg.defaults.delta.feature.catalogManaged,supported))"
    )
    assert parse_snapshot(described) == {
        **snapshot,
        "load_status": "complete",
        "loaded_at": "2026-10-02T01:30:00+00:00",
    }
    assert parse_snapshot("") == {}
