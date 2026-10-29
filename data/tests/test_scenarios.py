"""Injected scenarios: A6 (duplicated replication day), A7 (sales unit change), and the B-class scenarios
A3 (overflow hub parameters too low) and A4 (wave policy change)."""

from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl
import pytest

from snm_data.contracts import ContractRegistry
from snm_data.generator.artifacts import DUPLICATE_TANUM_START
from snm_data.generator.config import Profile
from snm_data.generator.org import SITES
from snm_data.generator.run import GenerationResult, generate
from snm_data.generator.scenarios import (
    A3_MATERIAL_GROUP,
    A3_START,
    A4_START,
    A4_WAVE_HOURS,
    A4_WAVE_TEMPLATE,
    A6_DATE,
    A6_LGNUM,
    A7_MATERIAL_GROUP,
    A7_START,
)

CONTRACTS = Path(__file__).resolve().parents[2] / "data_contracts"
# A window at test scale that covers scenarios A6 and A7.
PROFILE = Profile(
    "a6a7",
    date(2025, 12, 15),
    datetime(2026, 6, 15, 14, 0, tzinfo=UTC),
    datetime(2026, 6, 15, 5, 0, tzinfo=UTC),
    0.01,
    150,
    20,
    8,
)
# A window at test scale that covers scenarios A3 and A4 (and ends at the standard extraction).
PROFILE_B = Profile(
    "a3a5",
    date(2026, 5, 4),
    datetime(2026, 9, 21, 14, 0, tzinfo=UTC),
    datetime(2026, 9, 21, 5, 0, tzinfo=UTC),
    0.01,
    150,
    20,
    8,
)


@pytest.fixture(scope="module")
def result() -> GenerationResult:
    return generate(PROFILE, ContractRegistry.load(CONTRACTS), keep_facts=True)


def test_no_contract_violations(result: GenerationResult) -> None:
    assert result.violations == []
    truth = result.tables["eval.anomaly_ground_truth"]
    assert {"A6", "A7", "A8a", "A8b"} <= set(truth.get_column("scenario_id"))


def test_a6_duplicates_one_day_of_tasks(result: GenerationResult) -> None:
    tasks = result.tables["raw_sap_ewm.scwm_ordim_c"]
    assert tasks.select("lgnum", "tanum").is_duplicated().sum() == 0
    copies = tasks.filter(pl.col("tanum").cast(pl.Int64) >= DUPLICATE_TANUM_START)
    assert copies.height > 0
    assert (copies.get_column("lgnum") == A6_LGNUM).all()
    days = copies.get_column("confirmed_at_wh").cast(pl.String).str.slice(0, 8).unique().to_list()
    assert days == [A6_DATE.strftime("%Y%m%d")]
    originals = tasks.filter(
        (pl.col("lgnum") == A6_LGNUM)
        & (pl.col("tanum").cast(pl.Int64) < DUPLICATE_TANUM_START)
        & (pl.col("confirmed_at_wh").cast(pl.String).str.slice(0, 8) == days[0])
    )
    content = [column for column in tasks.columns if column != "tanum"]
    assert copies.select(content).sort(content).equals(originals.select(content).sort(content))


def test_a7_seals_switch_to_whole_boxes(result: GenerationResult) -> None:
    group = result.facts["materials"].filter((pl.col("matkl") == A7_MATERIAL_GROUP) & pl.col("has_box"))
    items = (
        result.facts["delivery_items"]
        .join(group.select("matnr", "box_size"), on="matnr")
        .join(result.facts["deliveries"].select("delivery_id", "site", "created_utc"), on="delivery_id")
    )
    local = pl.col("created_utc").dt.replace_time_zone("UTC")
    day = pl.lit(None, dtype=pl.Date)
    for code, site in SITES.items():
        site_day = local.dt.convert_time_zone(site.tz).dt.date()
        day = pl.when(pl.col("site") == code).then(site_day).otherwise(day)
    items = items.with_columns(day.alias("order_day"))
    before = items.filter(pl.col("order_day") < A7_START)
    after = items.filter(pl.col("order_day") >= A7_START)
    assert before.height > 0 and after.height > 0
    assert (before.get_column("vrkme") == "EA").all() and (before.get_column("umvkz") == 1).all()
    whole = after.filter(pl.col("base_qty") % pl.col("box_size") == 0)
    assert whole.height > 0 and (whole.get_column("vrkme") == "BOX").all()
    assert (whole.get_column("umvkz") == whole.get_column("box_size")).all()
    assert (after.filter(pl.col("vrkme") == "BOX").height) == whole.height


def test_sales_and_base_quantities_stay_consistent(result: GenerationResult) -> None:
    rows = result.tables["raw_sap_ecc.lips"]
    sales_qty = rows.get_column("lfimg").cast(pl.Float64)
    converted = sales_qty * rows.get_column("umvkz") / rows.get_column("umvkn")
    assert (converted - rows.get_column("lgmng").cast(pl.Float64)).abs().max() < 1e-6
    boxes = rows.filter(pl.col("vrkme") == "BOX")
    assert boxes.height > 0 and (boxes.get_column("umvkz") > 1).all()
    mseg = result.tables["raw_sap_ecc.mseg"].filter(pl.col("erfme") == "BOX")
    assert mseg.height > 0 and (mseg.get_column("erfmg") < mseg.get_column("menge")).all()
    tasks = result.tables["raw_sap_ewm.scwm_ordim_c"].filter(pl.col("altme") == "BOX")
    assert tasks.height > 0 and (tasks.get_column("vsola") < tasks.get_column("vsolm")).all()


# --------------------------------------------------------------------------------------------------
# Scenarios A3 and A4 (B-class: ECC documents change)
# --------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def result_b() -> GenerationResult:
    return generate(PROFILE_B, ContractRegistry.load(CONTRACTS), keep_facts=True)


def test_b_scenarios_are_in_the_ground_truth(result_b: GenerationResult) -> None:
    assert result_b.violations == []
    truth = result_b.tables["eval.anomaly_ground_truth"]
    assert {"A3", "A4"} <= set(truth.get_column("scenario_id"))
    assert (
        truth.filter(pl.col("scenario_id").is_in(["A3", "A4"])).get_column("scenario_type") == "real"
    ).all()


def test_round_rock_moves_stock_above_the_maximum_to_the_overflow_hub(result_b: GenerationResult) -> None:
    ledger = result_b.facts["inventory_ledger"].filter(
        (pl.col("kind") == "311") & (pl.col("werks") == "US10")
    )
    moves = ledger.filter(pl.col("trigger") == "MOVE")
    pulls = ledger.filter(pl.col("trigger").is_in(["PULL", "EMERGENCY"]))
    assert moves.height > 0 and pulls.height > 0
    assert set(moves.select("lgort", "umlgo").unique().rows()) == {("1000", "2001")}
    assert set(pulls.select("lgort", "umlgo").unique().rows()) == {("2001", "1000")}
    # Supplier deliveries arrive at either location.
    receipts = result_b.tables["raw_sap_ecc.mseg"].filter(
        (pl.col("bwart") == "101") & (pl.col("werks") == "US10")
    )
    assert {"1000", "2001"} <= set(receipts.get_column("lgort"))


def test_a3_spoke_parameters_too_low_cause_emergency_transfers(result_b: GenerationResult) -> None:
    tables = result_b.tables
    group = result_b.facts["materials"].filter(pl.col("matkl") == A3_MATERIAL_GROUP).get_column("matnr")
    in_group = pl.col("matnr").is_in(group.implode())
    spoke = tables["raw_sap_ecc.mard"].filter(pl.col("lgort") == "3000")
    assert spoke.filter(in_group).height > 0
    # The group's reorder point and replenishment quantity are a fraction of the other groups' values.
    for column in ("lminb", "lbstf"):
        lowered = spoke.filter(in_group).get_column(column).cast(pl.Float64).mean()
        assert lowered < 0.2 * spoke.filter(~in_group).get_column(column).cast(pl.Float64).mean()
    mseg = tables["raw_sap_ecc.mseg"].join(
        tables["raw_sap_ecc.mkpf"].select("mblnr", "mjahr", "usnam"), on=["mblnr", "mjahr"]
    )
    issues = (
        mseg.filter((pl.col("bwart") == "313") & (pl.col("shkzg") == "H") & (pl.col("werks") == "CN10"))
        .with_columns(
            in_group.alias("in_group"), (pl.col("budat_mkpf") >= A3_START.strftime("%Y%m%d")).alias("after")
        )
        .group_by("in_group", "after")
        .agg(pl.len().alias("docs"), (pl.col("usnam") == "RFC-EWM").mean().alias("emergency"))
    )

    def cell(in_group: bool, after: bool, column: str) -> float:
        subset = issues.filter((pl.col("in_group") == in_group) & (pl.col("after") == after))
        return subset.item(0, column) if subset.height else 0.0

    weeks_before = (A3_START - PROFILE_B.start).days / 7
    weeks_after = (PROFILE_B.ecc_as_of.date() - A3_START).days / 7
    assert cell(True, True, "emergency") > cell(True, False, "emergency") + 0.25
    assert cell(True, True, "docs") / weeks_after > cell(True, False, "docs") / weeks_before
    assert cell(False, True, "emergency") < 0.2  # other groups stay normal
    snapshots = tables["raw_ext.inventory_snapshot_daily"].filter((pl.col("lgort") == "3000") & in_group)
    before = (
        snapshots.filter(pl.col("snapshot_date") < A3_START).get_column("quantity").cast(pl.Float64).mean()
    )
    after = (
        snapshots.filter(pl.col("snapshot_date") >= A3_START).get_column("quantity").cast(pl.Float64).mean()
    )
    assert after < 0.6 * before


def test_a4_round_rock_waves_twice_a_day(result_b: GenerationResult) -> None:
    tables = result_b.tables
    waves = tables["raw_sap_ewm.scwm_wavehdr"].with_columns(
        pl.col("released_at_wh").cast(pl.String).str.slice(0, 8).alias("day"),
        pl.col("released_at_wh").cast(pl.String).str.slice(8, 2).cast(pl.Int32).alias("hour"),
    )
    start = A4_START.strftime("%Y%m%d")
    rr = waves.filter(pl.col("lgnum") == "RR01")
    before, after = rr.filter(pl.col("day") < start), rr.filter(pl.col("day") >= start)
    assert before.height > 0 and after.height > 0
    assert set(before.get_column("tmplt")) == {"RR-HOURLY"} and set(after.get_column("tmplt")) == {
        A4_WAVE_TEMPLATE
    }
    assert set(after.get_column("hour")) <= set(A4_WAVE_HOURS)
    assert (after.group_by("day").len().get_column("len") <= len(A4_WAVE_HOURS)).all()
    assert not set(before.get_column("hour")) <= set(A4_WAVE_HOURS)
    assert set(waves.filter(pl.col("lgnum") != "RR01").get_column("tmplt")) == {"SZ-30MIN", "DG-HOURLY"}
    tasks = tables["raw_sap_ewm.scwm_ordim_c"].filter((pl.col("lgnum") == "RR01") & (pl.col("tostat") == "C"))

    def stamp(column: str) -> pl.Expr:
        return pl.col(column).cast(pl.String).str.strptime(pl.Datetime("us"), "%Y%m%d%H%M%S")

    tasks = tasks.with_columns(
        (stamp("started_at") - stamp("created_at")).dt.total_seconds().alias("queue"),
        (stamp("confirmed_at") - stamp("started_at")).dt.total_seconds().alias("pick"),
        (pl.col("created_at_wh").cast(pl.String).str.slice(0, 8) >= start).alias("after"),
    )
    medians = tasks.group_by("after").agg(pl.col("queue").median(), pl.col("pick").median())
    queue = {row["after"]: row["queue"] for row in medians.rows(named=True)}
    pick = {row["after"]: row["pick"] for row in medians.rows(named=True)}
    assert queue[True] > 1.5 * queue[False]
    assert pick[True] < pick[False]
