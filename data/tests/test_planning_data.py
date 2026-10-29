"""Planning master data and purchasing history (generator 0.6.1)."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from snm_data.contracts import ContractRegistry
from snm_data.generator.config import PROFILES
from snm_data.generator.planning_data import COST_PARAMETERS, STRATEGIC_VENDORS, generate_cost_parameters
from snm_data.generator.run import GenerationResult, generate

CONTRACTS = Path(__file__).resolve().parents[2] / "data_contracts"


@pytest.fixture(scope="module")
def result() -> GenerationResult:
    return generate(PROFILES["test"], ContractRegistry.load(CONTRACTS), keep_facts=True)


def test_supplier_conditions_cover_every_vendor(result: GenerationResult) -> None:
    conditions = result.tables["raw_ext.supplier_condition"]
    vendors = result.tables["raw_sap_ecc.lfa1"].get_column("lifnr")
    assert sorted(conditions.get_column("lifnr").to_list()) == sorted(vendors.to_list())
    assert conditions.get_column("strategic").sum() == STRATEGIC_VENDORS
    # Strategic vendors are handled by people, so they are never onboarded to the portal.
    assert not conditions.filter(pl.col("strategic")).get_column("portal_onboarded").any()
    tiers = conditions.select("surcharge_pct_lt7", "surcharge_pct_7_13", "surcharge_pct_ge14")
    assert (tiers.get_column("surcharge_pct_lt7") < tiers.get_column("surcharge_pct_ge14")).all()
    assert conditions.get_column("min_expedite_days").min() >= 3


def test_strategic_vendors_are_the_largest_by_order_value(result: GenerationResult) -> None:
    orders = result.facts["purchase_orders"]
    spend = (
        orders.with_columns((pl.col("qty") * pl.col("stprs")).alias("value"))
        .group_by("lifnr")
        .agg(pl.col("value").sum())
        .sort(["value", "lifnr"], descending=[True, False])
    )
    strategic = result.tables["raw_ext.supplier_condition"].filter(pl.col("strategic")).get_column("lifnr")
    assert set(strategic.to_list()) == set(spend.head(STRATEGIC_VENDORS).get_column("lifnr").to_list())


def test_cost_parameters_match_the_proposal_defaults() -> None:
    frame = generate_cost_parameters()
    values = dict(zip(frame.get_column("parameter"), frame.get_column("value"), strict=True))
    assert values["line_down_cost_a"] == 5000.0 and values["line_down_cost_c"] == 200.0
    assert values["holding_rate_annual"] == 0.2 and values["order_cost"] == 75.0
    assert len(COST_PARAMETERS) == frame.height == 8


def test_purchasing_interactions_follow_the_purchase_orders(result: GenerationResult) -> None:
    interactions = result.tables["raw_ext.purchasing_interaction"]
    ekko = result.tables["raw_sap_ecc.ekko"]
    assert interactions.height > 0
    assert interactions.get_column("ebeln").is_in(ekko.get_column("ebeln").to_list()).all()
    # Every emergency buy (ZEX) has its record; late standard orders get an expedite request.
    zex = ekko.filter(pl.col("bsart") == "ZEX").get_column("ebeln")
    buys = interactions.filter(pl.col("kind") == "emergency_buy").get_column("ebeln")
    assert set(buys.to_list()) == set(zex.to_list())
    closed = interactions.filter(pl.col("closed_at").is_not_null())
    assert (closed.get_column("closed_at") >= closed.get_column("opened_at")).all()
    assert (closed.get_column("outcome") != "open").all()
    assert (interactions.filter(pl.col("closed_at").is_null()).get_column("outcome") == "open").all()
    assert interactions.get_column("rounds").min() >= 1
    # Surcharges are paid only when days were gained.
    paid = interactions.filter(pl.col("surcharge_pct") > 0)
    assert (paid.get_column("days_gained") > 0).all()


def test_marc_carries_criticality_and_maximum_stock(result: GenerationResult) -> None:
    marc = result.tables["raw_sap_ecc.marc"]
    for werks in ("US10", "CN10"):
        shares = marc.filter(pl.col("werks") == werks).get_column("maabc").value_counts(normalize=True)
        share = dict(zip(shares.get_column("maabc"), shares.get_column("proportion"), strict=True))
        # A covers 70 % of the value with a minority of the items.
        assert share.get("A", 0) < 0.5 and share.get("C", 0) > 0.2
    assert (marc.get_column("mabst") >= marc.get_column("eisbe")).all()
