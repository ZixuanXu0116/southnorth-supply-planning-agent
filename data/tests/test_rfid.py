"""Invariants of the LTS RFID scans at Round Rock and the late confirmation of emergency picks."""

from pathlib import Path

import polars as pl
import pytest

from snm_data.contracts import ContractRegistry
from snm_data.generator.config import PROFILES
from snm_data.generator.run import GenerationResult, generate
from snm_data.generator.taskshift import GI_STAGING_MIN

CONTRACTS = Path(__file__).resolve().parents[2] / "data_contracts"
PROFILE = PROFILES["test"]


@pytest.fixture(scope="module")
def result() -> GenerationResult:
    return generate(PROFILE, ContractRegistry.load(CONTRACTS), keep_facts=True)


def _utc(column: str) -> pl.Expr:
    return pl.col(column).cast(pl.String).str.strptime(pl.Datetime("us"), "%Y%m%d%H%M%S")


@pytest.fixture(scope="module")
def orders(result: GenerationResult) -> pl.DataFrame:
    """Round Rock pick warehouse orders with their EWM times and RFID scans (naive UTC)."""
    tasks = result.tables["raw_sap_ewm.scwm_ordim_c"].filter(
        (pl.col("lgnum") == "RR01") & pl.col("procty").is_in(["2010", "2020"]) & (pl.col("tostat") == "C")
    )
    per_order = tasks.group_by("who").agg(
        pl.col("procty").first(),
        _utc("created_at").min().alias("created"),
        _utc("confirmed_at").max().alias("last"),
    )
    scans = result.tables["raw_ext.lts_rfid_scan"].with_columns(
        pl.col("scanned_at").dt.replace_time_zone(None).alias("at")
    )
    exits = scans.filter(pl.col("zone") != "DOCK").select("who", pl.col("at").alias("exit"))
    dock = scans.filter(pl.col("zone") == "DOCK").select("who", pl.col("at").alias("dock"))
    return per_order.join(exits, on="who", how="left").join(dock, on="who", how="left")


def test_no_contract_violations(result: GenerationResult) -> None:
    assert result.violations == []


def test_one_tote_per_warehouse_order(result: GenerationResult, orders: pl.DataFrame) -> None:
    scans = result.tables["raw_ext.lts_rfid_scan"]
    assert scans.get_column("scanned_at").dtype == pl.Datetime("us", "UTC")
    per_zone = scans.with_columns((pl.col("zone") == "DOCK").alias("dock")).group_by("who", "dock").len()
    assert (per_zone.get_column("len") == 1).all()
    assert orders.get_column("exit").null_count() == 0
    tags = scans.group_by("who").agg(pl.col("tag_id").n_unique())
    assert (tags.get_column("tag_id") == 1).all()


def test_scans_follow_the_flow(orders: pl.DataFrame) -> None:
    assert orders.filter(pl.col("exit") < pl.col("created")).is_empty()
    assert orders.filter(pl.col("exit") > pl.col("last")).is_empty()
    assert orders.filter(pl.col("dock").is_not_null() & (pl.col("dock") < pl.col("exit"))).is_empty()


def test_only_emergency_picks_are_confirmed_late(result: GenerationResult, orders: pl.DataFrame) -> None:
    lag = orders.with_columns(((pl.col("last") - pl.col("exit")).dt.total_seconds() / 60).alias("lag"))
    assert lag.filter((pl.col("procty") == "2010") & (pl.col("lag") > 2)).is_empty()
    late = result.facts["rfid_late_confirmations"]
    rows = result.tables["raw_sap_ewm.scwm_ordim_c"].with_row_index("row").join(late, on="row")
    assert (rows.get_column("procty") == "2020").all() and (rows.get_column("lgnum") == "RR01").all()


def test_late_confirmations_stay_before_goods_issue(result: GenerationResult) -> None:
    moved = (
        result.tables["raw_sap_ewm.scwm_ordim_c"]
        .with_row_index("row")
        .join(result.facts["rfid_late_confirmations"], on="row")
    )
    picks = moved.join(result.facts["tasks"].select("lgnum", "tanum", "delivery_id"), on=["lgnum", "tanum"])
    shipped = picks.join(
        result.facts["deliveries"].select("delivery_id", "pgi_utc"), on="delivery_id"
    ).filter(pl.col("pgi_utc").is_not_null() & (pl.col("procty") == "2020") & (pl.col("tostat") == "C"))
    too_late = shipped.filter(_utc("confirmed_at") > pl.col("pgi_utc") - pl.duration(minutes=GI_STAGING_MIN))
    assert too_late.is_empty()
