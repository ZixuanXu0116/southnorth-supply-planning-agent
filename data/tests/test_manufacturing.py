"""Manufacturing demand (generator 0.7): orders, reservations, staging, consumption, planned orders."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import polars as pl
import pytest

from snm_data.contracts import ContractRegistry
from snm_data.generator.config import PROFILES
from snm_data.generator.manufacturing import BLANK_DATE, PLANNED_WEEKS, PSA_LGORT
from snm_data.generator.run import GenerationResult, generate

CONTRACTS = Path(__file__).resolve().parents[2] / "data_contracts"
PROFILE = PROFILES["test"]


@pytest.fixture(scope="module")
def result() -> GenerationResult:
    return generate(PROFILE, ContractRegistry.load(CONTRACTS), keep_facts=True)


@pytest.fixture(scope="module")
def t(result: GenerationResult):
    return result.tables


def test_production_orders_have_items_and_reservations(t) -> None:
    afko, afpo, resb = t["raw_sap_ecc.afko"], t["raw_sap_ecc.afpo"], t["raw_sap_ecc.resb"]
    assert afko.height > 0
    assert sorted(afpo.get_column("aufnr").to_list()) == sorted(afko.get_column("aufnr").to_list())
    orders = resb.filter(pl.col("bdart") == "SB")
    assert set(orders.get_column("aufnr").to_list()) == set(afko.get_column("aufnr").to_list())
    assert set(orders.get_column("rsnum").to_list()) == set(afko.get_column("rsnum").to_list())
    assert (resb.get_column("lgort") == PSA_LGORT).all()
    # Products are finished goods produced in house; components are spare parts.
    marc = t["raw_sap_ecc.marc"]
    products = marc.join(afpo.select("matnr", pl.col("pwerk").alias("werks")).unique(), on=["matnr", "werks"])
    assert (products.get_column("beskz") == "E").all()
    mara = t["raw_sap_ecc.mara"]
    components = mara.join(resb.select("matnr").unique(), on="matnr")
    assert "FERT" not in components.get_column("mtart").to_list()


def test_orders_start_when_every_component_is_staged_and_consumed(t) -> None:
    afko, resb, mseg = t["raw_sap_ecc.afko"], t["raw_sap_ecc.resb"], t["raw_sap_ecc.mseg"]
    consumption = mseg.filter(pl.col("bwart") == "261")
    assert (consumption.get_column("lgort") == PSA_LGORT).all()
    assert (consumption.get_column("aufnr") != "").all()
    consumed = consumption.group_by("aufnr").agg(pl.col("menge").cast(pl.Float64).sum().alias("consumed"))
    reservations = (
        resb.filter(pl.col("bdart") == "SB")
        .group_by("aufnr")
        .agg(
            pl.col("bdmng").cast(pl.Float64).sum().alias("required"),
            pl.col("enmng").cast(pl.Float64).sum().alias("withdrawn"),
            (pl.col("kzear") == "X").all().alias("final"),
        )
        .join(consumed, on="aufnr", how="left")
        .with_columns(pl.col("consumed").fill_null(0.0))
        .join(afko.select("aufnr", "gstrp", "gstri", "getri"), on="aufnr")
    )
    started = reservations.filter(pl.col("gstri") != BLANK_DATE)
    waiting = reservations.filter(pl.col("gstri") == BLANK_DATE)
    # Withdrawn equals consumed equals required for started orders; nothing for waiting orders.
    assert (started.get_column("withdrawn") == started.get_column("required")).all()
    assert (started.get_column("consumed") == started.get_column("required")).all()
    assert started.get_column("final").all()
    assert (waiting.get_column("withdrawn") == 0).all() and (waiting.get_column("consumed") == 0).all()
    assert (started.get_column("gstri") >= started.get_column("gstrp")).all()
    # An order finishes after it starts, never after the extraction.
    finished = started.filter(pl.col("getri") != BLANK_DATE)
    assert (finished.get_column("getri") > finished.get_column("gstri")).all()
    assert finished.get_column("getri").max() <= PROFILE.ecc_as_of.date().strftime("%Y%m%d")


def test_staging_moves_components_from_the_warehouse_and_the_psa_is_extended(t) -> None:
    mseg = t["raw_sap_ecc.mseg"]
    staging = mseg.filter(
        (pl.col("bwart") == "311") & (pl.col("umlgo") == PSA_LGORT) & (pl.col("shkzg") == "H")
    )
    assert staging.height > 0
    # The main warehouse issues to the production supply area; at CN10 the Dongguan overflow hub issues a
    # share of the lines directly. Every issue is the picking list of a production order.
    assert set(staging.filter(pl.col("werks") == "US10").get_column("lgort")) == {"1000"}
    assert set(staging.filter(pl.col("werks") == "CN10").get_column("lgort")) == {"1000", "3000"}
    assert (staging.get_column("aufnr") != "").all()
    assert (staging.get_column("vbeln_im") != "").all()
    mard = t["raw_sap_ecc.mard"].filter(pl.col("lgort") == PSA_LGORT)
    staged_materials = set(staging.select("werks", "matnr").unique().rows())
    assert staged_materials <= set(mard.select("werks", "matnr").rows())
    assert t["raw_sap_ecc.t001l"].filter(pl.col("lgort") == PSA_LGORT).height == 2


def test_planned_orders_cover_weeks_two_to_thirteen_with_dependent_requirements(t) -> None:
    plaf, resb = t["raw_sap_ecc.plaf"], t["raw_sap_ecc.resb"]
    as_of = PROFILE.ecc_as_of.date()
    starts = plaf.get_column("psttr").str.strptime(pl.Date, "%Y%m%d")
    weeks = ((starts - pl.Series([as_of] * plaf.height)).dt.total_days() // 7).to_list()
    assert plaf.height > 0 and set(weeks) <= set(PLANNED_WEEKS)
    dependent = resb.filter(pl.col("bdart") == "AR")
    assert set(dependent.get_column("rsnum").to_list()) == set(plaf.get_column("rsnum").to_list())
    assert (dependent.get_column("enmng").cast(pl.Float64) == 0).all()
    assert (dependent.get_column("aufnr") == "").all() and (dependent.get_column("plnum") != "").all()
    assert (dependent.get_column("bdter").str.strptime(pl.Date, "%Y%m%d") > as_of + timedelta(days=7)).all()


def test_backlog_is_visible_as_open_reservations(result: GenerationResult) -> None:
    resb = result.tables["raw_sap_ecc.resb"].filter(pl.col("bdart") == "SB")
    as_of = PROFILE.ecc_as_of.date().strftime("%Y%m%d")
    overdue = resb.filter((pl.col("bdter") < as_of) & (pl.col("kzear") != "X"))
    # Overdue open reservations are backlog; those staged in full have started, so none is both.
    assert (overdue.get_column("enmng").cast(pl.Float64) == 0).all()
