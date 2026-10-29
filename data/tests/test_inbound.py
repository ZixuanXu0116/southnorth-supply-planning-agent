"""Invariants of the EWM inbound extract: deliveries, putaway tasks, PACI, and scenario A2."""

from datetime import datetime, time, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from snm_data.contracts import ContractRegistry
from snm_data.generator.config import PROFILES
from snm_data.generator.inbound import TYPE_BY_PACI, _destination
from snm_data.generator.org import SITES
from snm_data.generator.run import GenerationResult, generate
from snm_data.generator.scenarios import A2_MATERIAL_GROUP, A2_START, A2_WRONG_PACI
from snm_data.generator.timeutil import utc_to_local

CONTRACTS = Path(__file__).resolve().parents[2] / "data_contracts"
PROFILE = PROFILES["test"]


@pytest.fixture(scope="module")
def result() -> GenerationResult:
    return generate(PROFILE, ContractRegistry.load(CONTRACTS), keep_facts=True)


def _ts(column: str) -> pl.Expr:
    return pl.col(column).cast(pl.String).str.strptime(pl.Datetime("us"), "%Y%m%d%H%M%S")


@pytest.fixture(scope="module")
def putaways(result: GenerationResult) -> pl.DataFrame:
    tasks = result.tables["raw_sap_ewm.scwm_ordim_c"].filter(pl.col("procty") == "1011")
    items = result.tables["raw_sap_ewm.scdl_db_proci_i"]
    refdoc = result.tables["raw_sap_ewm.scdl_db_refdoc"].filter(pl.col("refdoccat") == "PO")
    return tasks.join(
        items.select(pl.col("docid").alias("rdocid"), pl.col("itemid").alias("ritmid"), "stock_category"),
        on=["rdocid", "ritmid"],
    ).join(refdoc.select(pl.col("docid").alias("rdocid"), pl.col("refdocno").alias("ebeln")), on="rdocid")


def test_no_contract_violations(result: GenerationResult) -> None:
    assert result.violations == []


def test_every_ewm_receipt_has_one_inbound_delivery(result: GenerationResult) -> None:
    receipts = result.tables["raw_sap_ecc.mseg"].filter(
        (pl.col("bwart") == "101")
        & (((pl.col("werks") == "US10") & (pl.col("lgort") == "1000")) | (pl.col("werks") == "CN10"))
    )
    refdoc = result.tables["raw_sap_ewm.scdl_db_refdoc"].filter(pl.col("refdoccat") == "PO")
    items = result.tables["raw_sap_ewm.scdl_db_proci_i"].join(refdoc, on=["mandt", "docid", "itemid"])
    assert items.height == receipts.height
    joined = receipts.join(items, left_on="ebeln", right_on="refdocno")
    assert joined.height == receipts.height
    assert (joined.get_column("menge").cast(pl.Float64) == joined.get_column("qty").cast(pl.Float64)).all()
    same_product = joined.get_column("productno").str.strip_chars_start("0") == joined.get_column(
        "matnr"
    ).str.strip_chars_start("0")
    assert same_product.all()


def test_each_item_is_put_away_once_after_its_delivery_was_created(
    result: GenerationResult, putaways: pl.DataFrame
) -> None:
    confirmed = putaways.filter(pl.col("tostat") == "C")
    per_item = confirmed.group_by("rdocid", "ritmid").len()
    assert (per_item.get_column("len") == 1).all()
    items = result.tables["raw_sap_ewm.scdl_db_proci_i"].height
    assert per_item.height >= 0.99 * items
    headers = result.tables["raw_sap_ewm.scdl_db_proch_i"].select(pl.col("docid").alias("rdocid"), "cretst")
    timed = putaways.join(headers, on="rdocid")
    assert timed.filter(_ts("created_at") < _ts("cretst")).height == 0
    assert timed.filter(_ts("confirmed_at") < _ts("created_at")).height == 0


def test_local_timestamps_match_utc(putaways: pl.DataFrame) -> None:
    for code, site in SITES.items():
        subset = putaways.filter(pl.col("lgnum") == site.lgnum)
        if subset.height == 0:
            continue
        utc = subset.select(_ts("confirmed_at")).to_series().to_numpy()
        local = subset.select(_ts("confirmed_at_wh")).to_series().to_numpy()
        assert (utc_to_local(utc, site.tz) == local).all(), code


def test_destination_follows_stock_type_and_paci(result: GenerationResult, putaways: pl.DataFrame) -> None:
    rr = putaways.filter(pl.col("lgnum") == "RR01")
    assert (rr.filter(pl.col("stock_category") == "Q2").get_column("nltyp") == "8000").all()
    bonded = result.tables["raw_sap_ecc.mseg"].filter(
        (pl.col("bwart") == "101") & (pl.col("lgort") == "1900")
    )
    bonded_tasks = putaways.filter(pl.col("tostat") == "C").join(
        bonded.select("ebeln").unique(), on="ebeln", how="semi"
    )
    assert (bonded_tasks.get_column("nltyp") == "BND1").all()
    assert (
        bonded_tasks.height == putaways.filter((pl.col("tostat") == "C") & (pl.col("nltyp") == "BND1")).height
    )
    autostore = putaways.filter(pl.col("nltyp") == "AS01")
    assert (autostore.get_column("confirmed_by") == "AS-INTERFACE").all()
    # Every other destination is the storage type of the PACI in force at receipt.
    facts = result.facts["inbound_receipts"]
    regular = facts.filter(~pl.col("dest_type").is_in(["8000", "BND1"]))
    assert (regular.get_column("dest_type") == regular.get_column("paci").replace_strict(TYPE_BY_PACI)).all()


def test_round_rock_putaways_start_within_shifts(putaways: pl.DataFrame) -> None:
    started = putaways.filter((pl.col("lgnum") == "RR01") & (pl.col("started_at") > 0))
    local = utc_to_local(started.select(_ts("started_at")).to_series().to_numpy(), SITES["RR"].tz)
    day = local.astype("datetime64[D]")
    hour = (local - day.astype("datetime64[us]")) / np.timedelta64(1, "h")
    weekday = (day.astype("int64") + 3) % 7 + 1
    assert ((hour >= 6) & (hour < 22) & (weekday <= 6)).all()


def test_scenario_a2_misroutes_one_material_group(result: GenerationResult) -> None:
    facts = result.facts["inbound_receipts"]
    affected = facts.filter(
        (pl.col("site") == "RR") & (pl.col("matkl") == A2_MATERIAL_GROUP) & (pl.col("dest_type") != "8000")
    )
    after = affected.filter(pl.col("gr_local").dt.date() >= A2_START)
    assert (after.get_column("paci") == A2_WRONG_PACI).all()
    assert (after.get_column("dest_type") == "0010").all()
    assert (affected.filter(pl.col("gr_local").dt.date() < A2_START).get_column("dest_type") != "0010").all()
    config = result.tables["raw_sap_ewm.sapapo_matlwh"]
    keys = result.facts["product_keys"].select("matid", pl.col("matnr_ecc").alias("matnr"))
    group = result.facts["materials"].filter(pl.col("matkl") == A2_MATERIAL_GROUP).select("matnr")
    rr_group = config.filter(pl.col("lgnum") == "RR01").join(keys, on="matid").join(group, on="matnr")
    assert rr_group.height > 0 and (rr_group.get_column("put_stra") == A2_WRONG_PACI).all()
    truth = result.tables["eval.anomaly_ground_truth"]
    assert truth.filter(pl.col("scenario_id") == "A2").height == 1


def test_a2_routing_switches_on_the_start_date() -> None:
    day = datetime.combine(A2_START, time(9))
    receipts = pl.DataFrame(
        {
            "site": ["RR", "RR", "RR", "RR", "SZ"],
            "werks": ["US10", "US10", "US10", "US10", "CN10"],
            "lgort": ["1000", "1000", "1000", "1000", "1000"],
            "matnr": ["M1", "M1", "M2", "M1", "M1"],
            "insmk": ["", "", "", "X", ""],
            "gr_local": [day - timedelta(days=1), day, day, day, day],
        }
    )
    materials = pl.DataFrame({"matnr": ["M1", "M2"], "matkl": [A2_MATERIAL_GROUP, "SP-VAC"]})
    slots = pl.DataFrame(
        {"lgnum": ["RR01", "RR01", "SZ01"], "matnr": ["M1", "M2", "M1"], "lgtyp": ["0020", "0020", "0020"],
         "lgpla": ["SH-01-01-A", "SH-01-02-A", "SH-01-01-A"]}
    )  # fmt: skip
    routed, config = _destination(receipts, materials, slots)
    assert routed.get_column("dest_type").to_list() == ["0020", "0010", "0020", "8000", "0020"]
    rr = config.filter(pl.col("lgnum") == "RR01").sort("matnr")
    assert rr.get_column("put_stra").to_list() == [A2_WRONG_PACI, "SHLF"]
