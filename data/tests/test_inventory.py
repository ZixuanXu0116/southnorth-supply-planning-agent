"""Ground-truth invariants of the inventory simulation (evaluation layer L0).

The material documents are the ledger; MARD and the daily snapshots must reconcile with it exactly.
"""

from datetime import timedelta
from pathlib import Path

import polars as pl
import pytest

from snm_data.contracts import ContractRegistry
from snm_data.generator.config import PROFILES
from snm_data.generator.run import GenerationResult, generate
from snm_data.generator.scenarios import A3_MATERIAL_GROUP

CONTRACTS = Path(__file__).resolve().parents[2] / "data_contracts"
PROFILE = PROFILES["test"]
TRANSFERS = ("311", "313", "321", "411")


@pytest.fixture(scope="module")
def result() -> GenerationResult:
    return generate(PROFILE, ContractRegistry.load(CONTRACTS), keep_facts=True)


@pytest.fixture(scope="module")
def t(result: GenerationResult):
    return result.tables


def stock_from_ledger(mseg: pl.DataFrame, last_budat: str | None = None) -> pl.DataFrame:
    """Stock by storage location, material, and category, rebuilt from material document items."""
    items = mseg if last_budat is None else mseg.filter(pl.col("budat_mkpf") <= last_budat)
    signed = (
        pl.when(pl.col("shkzg") == "S").then(pl.col("menge")).otherwise(-pl.col("menge")).cast(pl.Float64)
    )
    category = (
        pl.when(pl.col("sobkz") == "K")
        .then(pl.lit("CONSIGNMENT"))
        .when((pl.col("bwart") == "313") & (pl.col("shkzg") == "S"))
        .then(pl.lit("IN_TRANSFER"))
        .when(pl.col("insmk") == "X")
        .then(pl.lit("QUALITY"))
        .otherwise(pl.lit("UNRESTRICTED"))
    )
    moves = items.select(
        "werks", "lgort", "matnr", category.alias("stock_category"), signed.alias("quantity")
    )
    placed = items.filter(pl.col("bwart") == "315").select(
        "werks", "lgort", "matnr", pl.lit("IN_TRANSFER").alias("stock_category"),
        (-pl.col("menge").cast(pl.Float64)).alias("quantity"),
    )  # fmt: skip
    return (
        pl.concat([moves, placed])
        .group_by("werks", "lgort", "matnr", "stock_category")
        .agg(pl.col("quantity").sum().round(3))
        .filter(pl.col("quantity").abs() > 1e-9)
        .sort("werks", "lgort", "matnr", "stock_category")
    )


def test_snapshots_reconcile_with_the_ledger_every_day(t) -> None:
    snapshots = t["raw_ext.inventory_snapshot_daily"]
    mseg = t["raw_sap_ecc.mseg"]
    assert snapshots.filter(pl.col("quantity") <= 0).height == 0
    for day in snapshots.get_column("snapshot_date").unique().sort().to_list():
        expected = stock_from_ledger(mseg, day.strftime("%Y%m%d"))
        actual = (
            snapshots.filter(pl.col("snapshot_date") == day)
            .select("werks", "lgort", "matnr", "stock_category", pl.col("quantity").cast(pl.Float64).round(3))
            .sort("werks", "lgort", "matnr", "stock_category")
        )
        assert actual.equals(expected), day


def test_mard_and_mkol_equal_the_ledger_at_extraction(t) -> None:
    stock = stock_from_ledger(t["raw_sap_ecc.mseg"])
    mard = t["raw_sap_ecc.mard"].with_columns(pl.col(c).cast(pl.Float64) for c in ("labst", "insme", "umlme"))
    for column, category in (("labst", "UNRESTRICTED"), ("insme", "QUALITY"), ("umlme", "IN_TRANSFER")):
        expected = stock.filter(pl.col("stock_category") == category).select(
            "werks", "lgort", "matnr", "quantity"
        )
        joined = mard.join(expected, on=["werks", "lgort", "matnr"], how="left").with_columns(
            pl.col("quantity").fill_null(0.0)
        )
        assert joined.filter((pl.col(column) - pl.col("quantity")).abs() > 1e-6).height == 0, column
        # Every storage location with stock is extended in MARD.
        assert expected.join(mard, on=["werks", "lgort", "matnr"], how="anti").height == 0
    consignment = stock.filter(pl.col("stock_category") == "CONSIGNMENT").get_column("quantity").sum()
    assert abs(t["raw_sap_ecc.mkol"].get_column("slabs").cast(pl.Float64).sum() - consignment) < 1e-6
    assert (mard.select(pl.min_horizontal("labst", "insme", "umlme")).to_series() >= 0).all()


def test_transfer_postings_have_balanced_item_pairs(t) -> None:
    # A transfer posting is an issuing item followed by its automatic receiving item; a goods issue to the
    # production supply area posts one pair per delivery line in one document.
    items = t["raw_sap_ecc.mseg"].filter(pl.col("bwart").is_in(TRANSFERS))
    pair = ((pl.col("zeile").cast(pl.Int64) - 1) // 2).alias("pair")
    pairs = (
        items.with_columns(pair)
        .group_by("mblnr", "pair")
        .agg(
            pl.len().alias("n"),
            pl.col("shkzg").sort_by("zeile").alias("shkzg"),
            pl.col("xauto").sort_by("zeile").alias("xauto"),
            pl.col("menge").n_unique().alias("quantities"),
            pl.col("matnr").n_unique().alias("materials"),
        )
    )
    assert (pairs.get_column("n") == 2).all()
    assert (pairs.get_column("quantities") == 1).all() and (pairs.get_column("materials") == 1).all()
    assert all(value == ["H", "S"] for value in pairs.get_column("shkzg").to_list())
    assert all(value == ["", "X"] for value in pairs.get_column("xauto").to_list())


def test_movement_types_are_used_where_they_belong(t) -> None:
    mseg = t["raw_sap_ecc.mseg"]
    assert mseg.filter(pl.col("bwart") == "201").height == 0  # no consumption to cost centers any more
    two_step = mseg.filter(pl.col("bwart").is_in(["313", "315"]))
    assert set(two_step.get_column("werks").unique()) == {"CN10"}
    issue = mseg.filter((pl.col("bwart") == "313") & (pl.col("shkzg") == "H"))
    assert set(issue.get_column("lgort").unique()) == {"1000"}
    assert set(issue.get_column("umlgo").unique()) == {"3000"}
    one_step = mseg.filter((pl.col("bwart") == "311") & (pl.col("shkzg") == "H"))
    routes = set(
        one_step.select(pl.concat_str("werks", "lgort", "umlgo", separator="/")).to_series().to_list()
    )
    assert routes <= {
        "US10/1000/2001", "US10/2001/1000", "US10/1000/2100", "CN10/1900/1000", "CN10/1000/2100",
        "CN10/3000/2100",
    }  # fmt: skip
    opening = t["raw_sap_ecc.mkpf"].filter(pl.col("usnam") == "MIGRATION")
    assert set(opening.get_column("budat").unique()) == {
        (PROFILE.start - timedelta(days=1)).strftime("%Y%m%d")
    }


def test_goods_receipts_match_purchase_orders(t) -> None:
    receipts = t["raw_sap_ecc.mseg"].filter(pl.col("bwart") == "101")
    assert (receipts.get_column("ebeln") != "").all() and (receipts.get_column("lifnr") != "").all()
    received = receipts.group_by("ebeln").agg(pl.col("menge").cast(pl.Float64).sum().alias("received"))
    eket = t["raw_sap_ecc.eket"].with_columns(pl.col("wemng").cast(pl.Float64))
    joined = eket.join(received, on="ebeln", how="left").with_columns(pl.col("received").fill_null(0.0))
    assert joined.filter((pl.col("wemng") - pl.col("received")).abs() > 1e-6).height == 0
    consignment = receipts.filter(pl.col("sobkz") == "K")
    assert (consignment.get_column("dmbtr").cast(pl.Float64) == 0).all()
    items = t["raw_sap_ecc.ekpo"].join(consignment.select("ebeln").unique(), on="ebeln")
    assert set(items.get_column("pstyp").unique()) <= {"2"}


def test_emergency_replenishment_is_the_exception(result: GenerationResult) -> None:
    ekko = result.tables["raw_sap_ecc.ekko"]
    assert ekko.filter(pl.col("bsart") == "ZEX").height <= 0.05 * ekko.height
    # Outside the group whose hub parameters scenario A3 breaks, emergency transfers stay the exception.
    groups = result.facts["materials"].select("matnr", "matkl")
    ledger = result.facts["inventory_ledger"].join(groups, on="matnr")
    transfers = ledger.filter((pl.col("kind") == "313") & (pl.col("matkl") != A3_MATERIAL_GROUP))
    emergency = transfers.filter(pl.col("trigger") == "EMERGENCY")
    assert transfers.height > 0 and emergency.height <= 0.2 * transfers.height


def test_main_warehouse_pulls_from_and_moves_to_the_overflow_hub(result: GenerationResult) -> None:
    """Round Rock (decision record 003): below its minimum the main warehouse pulls from the overflow hub
    (an emergency pull on the demand day), above its maximum it moves stock out on the Monday review; MARC
    carries the minimum and maximum the simulation used."""
    tables = result.tables
    ledger = result.facts["inventory_ledger"].filter((pl.col("kind") == "311") & (pl.col("werks") == "US10"))
    hub = ledger.filter((pl.col("lgort") == "2001") | (pl.col("umlgo") == "2001"))
    assert set(hub.get_column("trigger").unique()) <= {"PULL", "EMERGENCY", "MOVE"}
    pulls = hub.filter(pl.col("trigger").is_in(["PULL", "EMERGENCY"]))
    moves = hub.filter(pl.col("trigger") == "MOVE")
    assert pulls.height > 0  # moves need a receipt above the maximum: test_scenarios checks them on 20 weeks
    assert set(pulls.select("lgort", "umlgo").unique().rows()) == {("2001", "1000")}
    assert set(moves.select("lgort", "umlgo").unique().rows()) <= {("1000", "2001")}
    weekdays = {(PROFILE.start + timedelta(days=int(d) - 1)).isoweekday() for d in moves.get_column("day")}
    assert weekdays <= {1}
    # Posted by logistics (WF-BATCH); an emergency pull by the interface user on the demand day.
    docs = (
        tables["raw_sap_ecc.mseg"]
        .filter((pl.col("bwart") == "311") & (pl.col("werks") == "US10") & (pl.col("shkzg") == "H"))
        .join(tables["raw_sap_ecc.mkpf"].select("mblnr", "mjahr", "usnam"), on=["mblnr", "mjahr"])
    )
    assert set(docs.filter(pl.col("lgort") == "2001").get_column("usnam")) <= {"WF-BATCH", "RFC-EWM"}
    assert set(docs.filter(pl.col("umlgo") == "2001").get_column("usnam")) <= {"WF-BATCH"}
    marc = tables["raw_sap_ecc.marc"].filter(pl.col("werks") == "US10")
    assert (marc.get_column("mabst").cast(pl.Float64) >= marc.get_column("eisbe").cast(pl.Float64)).all()
    assert (marc.get_column("mabst").cast(pl.Float64) >= 5).all()
    # The main warehouse never ends a review day above its maximum.
    main = (
        (pl.col("werks") == "US10")
        & (pl.col("lgort") == "1000")
        & (pl.col("stock_category") == "UNRESTRICTED")
    )
    snapshot = (
        tables["raw_ext.inventory_snapshot_daily"]
        .filter(main & (pl.col("snapshot_date").dt.weekday() == 1))
        .join(marc.select("matnr", pl.col("mabst").cast(pl.Float64)), on="matnr")
    )
    assert (snapshot.get_column("quantity").cast(pl.Float64) <= snapshot.get_column("mabst")).all()


def test_snapshots_stop_at_the_extraction(t) -> None:
    last = t["raw_ext.inventory_snapshot_daily"].get_column("snapshot_date").max()
    assert last == PROFILE.ecc_as_of.date() - timedelta(days=1)
