"""Live checks of the database-side governance, run as each role's service principal.

The queries go straight to the warehouse, bypassing the SQL guard, to show that Unity Catalog enforces the
roles on its own: grants, site row filters, operator-identity masks, and read-only access. Requires the
role credentials (`snm-data govern --issue-credentials`) in the environment.
"""

from __future__ import annotations

import dataclasses
import os

import pytest

from snm_data.databricks import DatabricksSettings
from snm_data.governance import ROLES, provisioned_roles
from snm_tools.backend import BackendError, DatabricksBackend
from snm_tools.server import LazyDatabricksBackend

HAS_ROLE_CREDENTIALS = all(
    os.environ.get(role.client_id_env) and os.environ.get(role.client_secret_env)
    for role in provisioned_roles()
)
pytestmark = pytest.mark.skipif(not HAS_ROLE_CREDENTIALS, reason="needs the agent role credentials")


def role_settings(role_name: str) -> DatabricksSettings:
    role = ROLES[role_name]
    admin = DatabricksSettings.from_env()
    return dataclasses.replace(
        admin,
        token="",
        client_id=os.environ[role.client_id_env],
        client_secret=os.environ[role.client_secret_env],
    )


@pytest.fixture(scope="module")
def backends():
    opened = {role.name: DatabricksBackend(role_settings(role.name)) for role in provisioned_roles()}
    yield opened
    for backend in opened.values():
        backend.close()


def values(backend: DatabricksBackend, sql: str) -> set:
    return {row[0] for row in backend.execute(sql).rows}


def test_each_role_connects_as_its_own_principal(backends) -> None:
    for name, backend in backends.items():
        role = ROLES[name]
        user, member, admin = backend.execute(
            f"SELECT current_user(), is_member('{role.group}'), is_member('admins')"
        ).rows[0]
        assert user == os.environ[role.client_id_env]
        assert str(member).lower() == "true" and str(admin).lower() == "false"


@pytest.mark.parametrize(
    ("role", "plants", "shipping_points", "warehouses"),
    [
        ("site_manager_rr", {"US10"}, {"U100"}, {"RR01"}),
        ("site_manager_cn", {"CN10"}, {"C100", "C300"}, {"SZ01", "DG01"}),
        ("ops_analyst_global", {"US10", "CN10"}, {"U100", "C100", "C300"}, {"RR01", "SZ01", "DG01"}),
    ],
)
def test_row_filters_limit_site_managers_to_their_site(backends, role, plants, shipping_points, warehouses):
    backend = backends[role]
    assert values(backend, "SELECT DISTINCT werks FROM snm.raw_sap_ecc.mseg") == plants
    assert values(backend, "SELECT DISTINCT vstel FROM snm.raw_sap_ecc.likp") == shipping_points
    assert values(backend, "SELECT DISTINCT lgnum FROM snm.raw_sap_ewm.scwm_ordim_c") == warehouses


def test_operator_identities_are_masked_without_people_level_access(backends) -> None:
    sql = (
        "SELECT COUNT(*), COUNT(confirmed_by), "
        "COUNT_IF(confirmed_by IN ('WF-BATCH', 'AS-INTERFACE', 'RFC-EWM')) FROM snm.raw_sap_ewm.scwm_ordim_c"
    )
    tasks, visible, system = backends["ops_analyst_global"].execute(sql).rows[0]
    assert 0 < system == visible < tasks  # only system users are visible to the analyst
    tasks, visible, system = backends["site_manager_rr"].execute(sql).rows[0]
    assert visible == tasks and 0 < system < tasks


def test_commercial_values_are_masked_without_cost_access(backends) -> None:
    sql = (
        "SELECT COUNT(*), COUNT(value_usd), COUNT(lever_cost_usd), COUNT(gap_qty), "
        "COUNT(alternative_costs_usd) FROM snm.curated.replenishment_case"
    )
    cases, values, costs, gaps, alternatives = backends["ops_analyst_global"].execute(sql).rows[0]
    assert cases > 0 and values == costs == alternatives == 0 and gaps == cases
    terms = (
        "SELECT COUNT(*), COUNT(surcharge_pct_lt7), COUNT(min_expedite_days) "
        "FROM snm.raw_ext.supplier_condition"
    )
    vendors, surcharges, lead_times = backends["site_manager_rr"].execute(terms).rows[0]
    assert vendors == lead_times == 20 and surcharges == 0


@pytest.mark.parametrize(
    ("role", "sql"),
    [
        ("ops_analyst_global", "SELECT COUNT(*) FROM snm.eval.anomaly_ground_truth"),
        ("ops_analyst_global", "SELECT COUNT(*) FROM snm.raw_ext.workforce_roster"),
        ("site_manager_rr", "SELECT COUNT(*) FROM snm.raw_ext.autostore_port_status"),
        ("trade_compliance", "SELECT COUNT(*) FROM snm.raw_sap_ecc.mseg"),
        ("site_manager_cn", "CREATE TABLE snm.raw_sap_ecc.snm_write_probe (a INT)"),
        ("ops_analyst_global", "DELETE FROM snm.raw_sap_ecc.likp WHERE 1 = 0"),
    ],
)
def test_reads_outside_the_grants_and_all_writes_are_denied(backends, role, sql) -> None:
    with pytest.raises(BackendError, match=r"INSUFFICIENT_PERMISSIONS|PERMISSION_DENIED|does not have"):
        backends[role].execute(sql)


def test_tool_server_refuses_admin_and_foreign_credentials(monkeypatch) -> None:
    monkeypatch.delenv("DATABRICKS_CLIENT_ID", raising=False)
    monkeypatch.delenv("DATABRICKS_CLIENT_SECRET", raising=False)
    with pytest.raises(BackendError, match="workspace-admin"):
        LazyDatabricksBackend(ROLES["site_manager_rr"]).execute("SELECT 1")
    cn = ROLES["site_manager_cn"]
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", os.environ[cn.client_id_env])
    monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", os.environ[cn.client_secret_env])
    with pytest.raises(BackendError, match="do not belong to role site_manager_rr"):
        LazyDatabricksBackend(ROLES["site_manager_rr"]).execute("SELECT 1")
    assert LazyDatabricksBackend(cn).execute("SELECT 1").rows == [[1]]
