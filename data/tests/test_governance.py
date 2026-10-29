from pathlib import Path

import pytest

from snm_data import governance as g
from snm_data.contracts import ContractRegistry
from snm_data.databricks import DatabricksSettings

CONTRACTS = Path(__file__).resolve().parents[2] / "data_contracts"
GRANTEES = {role.name: f"app-{role.name}" for role in g.provisioned_roles()}


@pytest.fixture(scope="module")
def registry() -> ContractRegistry:
    return ContractRegistry.load(CONTRACTS)


def test_contracts_are_governable(registry) -> None:
    assert g.validate(registry) == []
    keyed = {contract.table for contract in g.governed(registry) if contract.site_key}
    assert {"raw_sap_ecc.mseg", "raw_sap_ecc.likp", "raw_sap_ewm.scwm_ordim_c"} <= keyed


def test_site_key_values_come_from_the_organization(registry) -> None:
    assert g.SITE_KEY_VALUES["warehouse"] == {"US10": ("RR01",), "CN10": ("SZ01", "DG01")}
    assert g.SITE_KEY_VALUES["shipping_point"] == {"US10": ("U100",), "CN10": ("C100", "C300")}
    assert g.SITE_KEY_VALUES["organization"] == {"US10": ("1000",), "CN10": ("2000",)}


def test_row_filters_admit_admins_all_site_roles_and_own_site_managers() -> None:
    sql = g.row_filter_function("snm", "warehouse")
    assert "CREATE OR REPLACE FUNCTION `snm`.`governance`.`site_rows_warehouse`(site_key STRING)" in sql
    assert "is_member('admins')" in sql and "OR is_member('snm_ops_analyst_global')" in sql
    assert "(is_member('snm_site_manager_rr') AND site_key IN ('RR01'))" in sql
    assert "(is_member('snm_site_manager_cn') AND site_key IN ('SZ01', 'DG01'))" in sql
    assert "snm_executive" not in sql  # not provisioned yet


def test_operator_identities_are_masked_for_roles_without_people_level_access() -> None:
    sql = g.mask_function("snm")
    assert "is_member('snm_site_manager_rr') OR is_member('snm_site_manager_cn') THEN value" in sql
    assert "WHEN value IN ('WF-BATCH', 'AS-INTERFACE', 'RFC-EWM') THEN value END" in sql
    assert "snm_ops_analyst_global" not in sql and "snm_trade_compliance" not in sql


def test_commercial_values_are_masked_for_roles_without_cost_access(registry) -> None:
    sql = g.commercial_mask_function("snm", "DECIMAL(15,2)")
    assert "`commercial_amount_mask`(value DECIMAL(15,2))" in sql and "RETURNS DECIMAL(15,2)" in sql
    assert "CASE WHEN is_member('admins') THEN value END" in sql  # purchasing is not provisioned yet
    assert g.ROLES["purchasing"].cost_access and not g.ROLES["purchasing"].provisioned
    assert not any(role.cost_access for role in g.provisioned_roles())
    statements = g.table_policy_statements(registry["raw_ext.supplier_condition"], "snm")
    assert statements == [
        "ALTER TABLE `snm`.`raw_ext`.`supplier_condition` ALTER COLUMN `surcharge_pct_lt7` "
        "SET MASK `snm`.`governance`.`commercial_amount_mask`",
        "ALTER TABLE `snm`.`raw_ext`.`supplier_condition` ALTER COLUMN `surcharge_pct_7_13` "
        "SET MASK `snm`.`governance`.`commercial_amount_mask`",
        "ALTER TABLE `snm`.`raw_ext`.`supplier_condition` ALTER COLUMN `surcharge_pct_ge14` "
        "SET MASK `snm`.`governance`.`commercial_amount_mask`",
        "ALTER TABLE `snm`.`raw_ext`.`supplier_condition` ALTER COLUMN `expedite_freight_usd` "
        "SET MASK `snm`.`governance`.`commercial_amount_mask`",
    ]
    # The buyer identifier is personal data and keeps the operator-identity mask.
    interaction = g.table_policy_statements(registry["raw_ext.purchasing_interaction"], "snm")
    masks = {s.split("ALTER COLUMN ")[1] for s in interaction if "SET MASK" in s}
    assert "`handled_by` SET MASK `snm`.`governance`.`operator_identity_mask`" in masks
    assert "`surcharge_pct` SET MASK `snm`.`governance`.`commercial_amount_mask`" in masks


def test_table_policies(registry) -> None:
    assert g.table_policy_statements(registry["raw_sap_ewm.scwm_ordim_c"], "snm") == [
        "ALTER TABLE `snm`.`raw_sap_ewm`.`scwm_ordim_c` SET ROW FILTER "
        "`snm`.`governance`.`site_rows_warehouse` ON (`lgnum`)",
        "ALTER TABLE `snm`.`raw_sap_ewm`.`scwm_ordim_c` ALTER COLUMN `confirmed_by` "
        "SET MASK `snm`.`governance`.`operator_identity_mask`",
    ]
    # Single-site tables are scoped by their grants alone.
    assert g.table_policy_statements(registry["raw_ext.lts_rfid_scan"], "snm") == []


def test_grants_mirror_contract_access(registry) -> None:
    grants = g.desired_grants(registry, "snm", GRANTEES)
    tables = {(grantee, securable) for grantee, kind, securable, _ in grants if kind == "TABLE"}
    assert ("app-ops_analyst_global", "snm.raw_sap_ecc.mseg") in tables
    assert ("app-trade_compliance", "snm.raw_sap_ecc.kna1") in tables
    assert ("app-trade_compliance", "snm.raw_sap_ecc.mseg") not in tables
    assert ("app-ops_analyst_global", "snm.raw_ext.workforce_roster") not in tables
    assert not any(securable.startswith("snm.eval") for _, _, securable, _ in grants)
    assert ("app-site_manager_rr", "CATALOG", "snm", "USE CATALOG") in grants
    assert {privilege for *_, privilege in grants} == {"USE CATALOG", "USE SCHEMA", "SELECT"}
    # Roles without a principal get nothing.
    assert g.desired_grants(registry, "snm", {}) == set()


def test_grant_statements_quote_principals() -> None:
    grant = ("app-1", "TABLE", "snm.raw_sap_ecc.mseg", "SELECT")
    assert g.grant_statement(grant) == "GRANT SELECT ON TABLE `snm`.`raw_sap_ecc`.`mseg` TO `app-1`"
    assert g.grant_statement(grant, revoke=True).startswith("REVOKE SELECT ON TABLE")


def test_policy_drift_finds_missing_wrong_and_stray_policies(registry) -> None:
    deployed_filters = {
        contract.table: (g.row_filter_name(contract.site_key.kind), contract.site_key.column)
        for contract in g.governed(registry)
        if contract.site_key
    }
    deployed_masks = {  # views carry no masks of their own; the mask of the table under them applies
        (contract.table, column)
        for contract in g.governed(registry)
        if contract.materialization == "table"
        for column in g.masked_columns(contract)
    }
    assert g.policy_drift(registry, deployed_filters, deployed_masks) == ([], [])
    deployed_filters.pop("raw_sap_ecc.mseg")
    deployed_filters["raw_sap_ecc.likp"] = ("site_rows_plant", "werks")
    deployed_masks.add(("raw_sap_ecc.mara", "matnr"))
    deployed_filters["raw_sap_ecc.gone"] = ("site_rows_plant", "werks")
    stale, strays = g.policy_drift(registry, deployed_filters, deployed_masks)
    assert sorted(contract.table for contract in stale) == [
        "raw_sap_ecc.likp", "raw_sap_ecc.mara", "raw_sap_ecc.mseg"
    ]  # fmt: skip
    assert strays == ["raw_sap_ecc.gone"]


def test_names_read_from_information_schema_are_normalized() -> None:
    assert g._bare("snm.governance.site_rows_plant") == "site_rows_plant"
    assert g._bare("`werks`") == "werks"


def test_oauth_settings_need_no_token_and_hide_the_secret() -> None:
    settings = DatabricksSettings.from_env(
        {
            "DATABRICKS_HOST": "dbc-example.cloud.databricks.com",
            "DATABRICKS_WAREHOUSE_ID": "abc123",
            "DATABRICKS_CLIENT_ID": "client",
            "DATABRICKS_CLIENT_SECRET": "very-secret",
        }
    )
    assert settings.uses_oauth and settings.token == ""
    assert "very-secret" not in repr(settings) and "auth=oauth" in repr(settings)
    with pytest.raises(Exception, match="DATABRICKS_CLIENT_ID and DATABRICKS_CLIENT_SECRET"):
        DatabricksSettings.from_env(
            {"DATABRICKS_HOST": "h", "DATABRICKS_WAREHOUSE_ID": "w", "DATABRICKS_CLIENT_ID": "client"}
        )
