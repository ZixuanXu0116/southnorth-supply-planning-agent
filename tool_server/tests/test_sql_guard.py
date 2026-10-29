"""SQL guard tests, including the red-team cases every change must keep rejecting."""

from pathlib import Path

import pytest

from snm_data.contracts import ContractRegistry
from snm_tools.policy import AccessPolicy
from snm_tools.sql_guard import SqlGuard

CONTRACTS = Path(__file__).resolve().parents[2] / "data_contracts"


@pytest.fixture(scope="module")
def registry() -> ContractRegistry:
    return ContractRegistry.load(CONTRACTS)


def guard_for(registry: ContractRegistry, role: str = "ops_analyst_global", max_rows: int = 100) -> SqlGuard:
    return SqlGuard(AccessPolicy(registry, role), catalog="snm", max_rows=max_rows)


ALLOWED = [
    "SELECT vstel, COUNT(*) FROM raw_sap_ecc.likp GROUP BY vstel",
    "SELECT vstel FROM snm.raw_sap_ecc.likp",
    "WITH d AS (SELECT vbeln FROM raw_sap_ecc.likp) SELECT COUNT(*) FROM d",
    "SELECT vbeln FROM raw_sap_ecc.likp UNION ALL SELECT vbeln FROM raw_sap_ecc.lips",
    "SELECT lgnum, COUNT(*) FROM raw_sap_ewm.scwm_ordim_c WHERE tostat = 'C' GROUP BY lgnum",
    "SELECT k.vstel FROM raw_sap_ecc.likp k JOIN raw_sap_ecc.vbuk u ON u.vbeln = k.vbeln",
]

RED_TEAM = [
    ("DELETE FROM raw_sap_ecc.likp", "NOT_READ_ONLY"),
    ("INSERT INTO raw_sap_ecc.likp SELECT * FROM raw_sap_ecc.likp", "NOT_READ_ONLY"),
    ("UPDATE raw_sap_ecc.likp SET vstel = 'X'", "NOT_READ_ONLY"),
    ("CREATE TABLE raw_sap_ecc.x AS SELECT 1", "NOT_READ_ONLY"),
    ("DROP TABLE raw_sap_ecc.likp", "NOT_READ_ONLY"),
    ("OPTIMIZE raw_sap_ecc.likp", "NOT_READ_ONLY"),
    ("SET STATEMENT_TIMEOUT = 0", "NOT_READ_ONLY"),
    ("SELECT 1; DROP TABLE raw_sap_ecc.likp", "MULTIPLE_STATEMENTS"),
    ("SELECT vbeln FROM raw_sap_ecc.likp; SELECT 1", "MULTIPLE_STATEMENTS"),
    (
        "SELECT * FROM read_files('/Volumes/snm/landing/files/x.parquet', format => 'parquet')",
        "FUNCTION_NOT_ALLOWED",
    ),
    ("SELECT * FROM range(10)", "TABLE_FUNCTION"),
    ("SELECT * FROM parquet.`/Volumes/snm/landing/files/x.parquet`", "PATH_ACCESS"),
    ("SELECT ai_query('model', 'hello')", "FUNCTION_NOT_ALLOWED"),
    ("SELECT http_request(conn => 'c', method => 'GET', path => '/')", "FUNCTION_NOT_ALLOWED"),
    ("SELECT secret('scope', 'key')", "FUNCTION_NOT_ALLOWED"),
    ("SELECT snm.p0_check.site_filter(vstel) FROM raw_sap_ecc.likp", "FUNCTION_NOT_ALLOWED"),
    ("SELECT * FROM system.access.audit", "CATALOG_NOT_ALLOWED"),
    ("SELECT * FROM samples.nyctaxi.trips", "CATALOG_NOT_ALLOWED"),
    ("SELECT * FROM eval.gold_answers", "UNKNOWN_TABLE"),
    ("SELECT * FROM landing.files", "UNKNOWN_TABLE"),
    ("SELECT vbeln FROM likp", "UNQUALIFIED_TABLE"),
    ("SELECT sap_uname FROM raw_ext.workforce_roster", "TABLE_NOT_ALLOWED"),
    (
        "SELECT confirmed_by, COUNT(*) FROM raw_sap_ewm.scwm_ordim_c GROUP BY confirmed_by",
        "RESTRICTED_COLUMN",
    ),
    ("SELECT * FROM raw_sap_ewm.scwm_ordim_c", "STAR_ON_RESTRICTED_TABLE"),
    ("SELECT t.* FROM raw_sap_ewm.scwm_ordim_c t", "STAR_ON_RESTRICTED_TABLE"),
    ("", "EMPTY_SQL"),
    ("SELEC vbeln FROM raw_sap_ecc.likp", "PARSE_ERROR"),
]


@pytest.mark.parametrize("sql", ALLOWED)
def test_allowed_queries(registry: ContractRegistry, sql: str) -> None:
    decision = guard_for(registry).check(sql)
    assert decision.allowed, decision.message


@pytest.mark.parametrize(("sql", "code"), RED_TEAM)
def test_red_team_queries_are_rejected(registry: ContractRegistry, sql: str, code: str) -> None:
    decision = guard_for(registry).check(sql)
    assert not decision.allowed
    assert decision.code == code, (decision.code, decision.message)


def test_site_managers_may_read_operator_columns(registry: ContractRegistry) -> None:
    guard = guard_for(registry, role="site_manager_rr")
    assert guard.check("SELECT confirmed_by, COUNT(*) FROM raw_sap_ewm.scwm_ordim_c GROUP BY 1").allowed
    assert guard.check("SELECT sap_uname, team FROM raw_ext.workforce_roster").allowed


def test_executive_cannot_read_raw_tables(registry: ContractRegistry) -> None:
    assert (
        guard_for(registry, role="executive").check("SELECT vbeln FROM raw_sap_ecc.likp").code
        == "TABLE_NOT_ALLOWED"
    )


def test_row_limit_is_enforced(registry: ContractRegistry) -> None:
    guard = guard_for(registry, max_rows=100)
    unlimited = guard.check("SELECT vbeln FROM raw_sap_ecc.likp")
    assert unlimited.probe_truncation and unlimited.row_limit == 100 and unlimited.sql.endswith("LIMIT 101")
    small = guard.check("SELECT vbeln FROM raw_sap_ecc.likp LIMIT 5")
    assert not small.probe_truncation and small.row_limit == 5 and small.sql.endswith("LIMIT 5")
    large = guard.check("SELECT vbeln FROM raw_sap_ecc.likp LIMIT 5000")
    assert large.probe_truncation and large.sql.endswith("LIMIT 101")
    union = guard.check("SELECT vbeln FROM raw_sap_ecc.likp UNION ALL SELECT vbeln FROM raw_sap_ecc.lips")
    assert union.sql.endswith("LIMIT 101")


def test_decision_lists_referenced_tables(registry: ContractRegistry) -> None:
    decision = guard_for(registry).check(
        "WITH d AS (SELECT vbeln FROM raw_sap_ecc.likp) "
        "SELECT d.vbeln FROM d JOIN raw_sap_ecc.vbuk u ON u.vbeln = d.vbeln"
    )
    assert decision.tables == ("raw_sap_ecc.likp", "raw_sap_ecc.vbuk")


@pytest.mark.parametrize("role", ["ops_analyst_global", "site_manager_rr", "site_manager_cn", "executive"])
def test_evaluation_tables_are_invisible(registry: ContractRegistry, role: str) -> None:
    assert "eval.anomaly_ground_truth" in registry.tables()
    policy = AccessPolicy(registry, role)
    decision = guard_for(registry, role).check("SELECT * FROM eval.anomaly_ground_truth")
    assert not decision.allowed and decision.code == "UNKNOWN_TABLE"
    assert all(contract.layer != "eval" for contract in policy.readable_tables())
