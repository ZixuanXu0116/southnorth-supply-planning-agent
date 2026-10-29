from pathlib import Path

import pytest

from snm_data import governance as g
from snm_data.contracts import Contract, ContractRegistry
from snm_data.semantic import build_statements, load_metric_views, metric_view_yaml, views_in_order

CONTRACTS = Path(__file__).resolve().parents[2] / "data_contracts"


@pytest.fixture(scope="module")
def registry() -> ContractRegistry:
    return ContractRegistry.load(CONTRACTS)


def test_curated_views_are_not_generated_and_deploy_in_dependency_order(registry) -> None:
    assert "curated.fct_outbound_delivery" not in registry.generated()
    assert "raw_sap_ecc.likp" in registry.generated() and "eval.anomaly_ground_truth" in registry.generated()
    order = [view.table for view in views_in_order(registry)]
    assert order.index("curated.dim_date") < order.index("curated.fct_outbound_delivery")
    statements = build_statements(registry, load_metric_views(), "snm")
    assert statements[0].startswith("CREATE SCHEMA IF NOT EXISTS `snm`.`curated`")
    assert "FROM `snm`.raw_sap_ecc.likp k" in next(s for s in statements if "fct_outbound_delivery" in s)
    assert "{catalog}" not in "".join(statements)


def test_metric_views_deploy_as_yaml_with_the_catalog(registry) -> None:
    views = {view.name: view for view in load_metric_views()}
    assert views["mv_outbound_due"].source_table == "curated.fct_outbound_delivery"
    text = metric_view_yaml(views["mv_outbound_due"], "snm")
    assert "source: snm.curated.fct_outbound_delivery" in text and "filter: is_due_before_cutoff" in text
    assert g.validate(registry, views.values()) == []


def test_metric_views_get_grants_for_their_roles(registry) -> None:
    grantees = {role.name: f"app-{role.name}" for role in g.provisioned_roles()}
    grants = g.desired_grants(registry, "snm", grantees, load_metric_views())
    assert ("app-site_manager_rr", "TABLE", "snm.semantic.mv_outbound_due", "SELECT") in grants
    assert ("app-site_manager_rr", "SCHEMA", "snm.semantic", "USE SCHEMA") in grants
    assert not any(securable.startswith("snm.semantic") for grantee, _, securable, _ in grants
                   if grantee == "app-trade_compliance")  # fmt: skip
    # Internal curated views get no grants, and views carry no filters or masks of their own; the engine's
    # curated tables are granted like any table.
    assert not any(securable == "snm.curated.fct_outbound_delivery" for _, _, securable, _ in grants)
    assert ("app-ops_analyst_global", "TABLE", "snm.curated.replenishment_case", "SELECT") in grants
    assert g.table_policy_statements(registry["curated.fct_outbound_delivery"], "snm") == []


def test_views_cannot_widen_access_or_read_hidden_tables(registry) -> None:
    views = load_metric_views()
    wide = views[0].model_copy(update={"access": ["trade_compliance"]})
    assert any("granted to trade_compliance" in problem for problem in g.validate(registry, [wide]))
    leak = Contract.model_validate(
        {
            **registry["curated.dim_date"].model_dump(),
            "table": "curated.leak",
            "depends_on": ["eval.anomaly_ground_truth"],
            "access": [],
        }
    )
    extended = ContractRegistry([*registry, leak])
    assert "curated.leak: reads the hidden table eval.anomaly_ground_truth" in g.validate(extended)
    bases = g.base_tables("curated.fct_outbound_delivery", registry)
    assert {"raw_sap_ecc.likp", "raw_sap_ecc.t009b"} <= bases


def test_metric_views_keep_commercial_columns_out_until_cost_roles_exist(registry) -> None:
    from snm_data.semantic import MetricField

    tasks = next(view for view in load_metric_views() if view.name == "mv_supplier_delivery")
    priced = tasks.model_copy(
        update={
            "definition": tasks.definition.model_copy(
                update={
                    "source": "{catalog}.raw_ext.supplier_condition",
                    "measures": [
                        MetricField(name="freight", expr="SUM(expedite_freight_usd)", comment="Freight.")
                    ],
                }
            )
        }
    )
    assert any("uses a commercial column" in problem for problem in g.validate(registry, [priced]))


def test_restricted_dimensions_follow_restricted_source_columns(registry) -> None:
    views = {view.name: view for view in load_metric_views()}
    tasks = views["mv_warehouse_tasks"]
    assert tasks.restricted_dimensions == ["operator"] and tasks.dimension("operator") is not None
    assert g.validate(registry, [tasks]) == []
    # An operator dimension that is not restricted would leak identities to every role of the view.
    exposed = tasks.model_copy(update={"restricted_dimensions": []})
    assert any("exposes a restricted column" in problem for problem in g.validate(registry, [exposed]))
    # A restricted dimension must be over a restricted column, or the restriction is meaningless.
    spurious = tasks.model_copy(update={"restricted_dimensions": ["operator", "shift"]})
    assert any("shift: restricted dimension" in problem for problem in g.validate(registry, [spurious]))
    # Restricted dimensions must exist.
    with pytest.raises(ValueError, match="restricted_dimensions must name dimensions"):
        payload = {**tasks.model_dump(), "restricted_dimensions": ["confirmed_by"]}
        _load_one(payload, "mv_warehouse_tasks")


def _load_one(payload: dict, name: str):
    import tempfile

    import yaml

    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / f"{name}.yaml"
        path.write_text(yaml.safe_dump(payload), encoding="utf-8")
        return load_metric_views(Path(folder))


def test_view_contracts_must_declare_dependencies_and_no_site_key(registry) -> None:
    base = registry["curated.dim_date"].model_dump()
    with pytest.raises(ValueError, match="depends_on"):
        Contract.model_validate({**base, "depends_on": []})
    with pytest.raises(ValueError, match="inherit row filters"):
        Contract.model_validate({**base, "site_key": {"column": "fiscal_year", "kind": "plant"}})
