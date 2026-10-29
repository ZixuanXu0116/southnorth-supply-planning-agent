"""Invariants of the AutoStore controller logs and scenario A1 (ports down at Longgang)."""

from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from snm_data.contracts import ContractRegistry
from snm_data.generator.autostore import PORTS
from snm_data.generator.config import Profile
from snm_data.generator.org import SITES
from snm_data.generator.run import GenerationResult, generate
from snm_data.generator.scenarios import A1_END, A1_PORTS, A1_START
from snm_data.generator.taskshift import GI_STAGING_MIN
from snm_data.generator.timeutil import utc_to_local

CONTRACTS = Path(__file__).resolve().parents[2] / "data_contracts"
# A short window around scenario A1 at test scale.
PROFILE = Profile(
    "a1",
    date(2026, 3, 16),
    datetime(2026, 4, 20, 14, 0, tzinfo=UTC),
    datetime(2026, 4, 20, 5, 0, tzinfo=UTC),
    0.01,
    150,
    20,
    8,
)
TZ = SITES["SZ"].tz


@pytest.fixture(scope="module")
def result() -> GenerationResult:
    return generate(PROFILE, ContractRegistry.load(CONTRACTS), keep_facts=True)


def _local(column: str) -> pl.Expr:
    """DEC15 UTC timestamp -> Longgang local datetime (null for 0)."""
    parsed = pl.col(column).cast(pl.String).str.strptime(pl.Datetime("us"), "%Y%m%d%H%M%S", strict=False)
    return parsed.dt.replace_time_zone("UTC").dt.convert_time_zone(TZ).dt.replace_time_zone(None)


@pytest.fixture(scope="module")
def autostore_tasks(result: GenerationResult) -> pl.DataFrame:
    tasks = result.tables["raw_sap_ewm.scwm_ordim_c"].filter(
        (pl.col("lgnum") == "SZ01") & ((pl.col("vltyp") == "AS01") | (pl.col("nltyp") == "AS01"))
    )
    return tasks.with_columns(
        _local("started_at").alias("started_local"),
        _local("confirmed_at").alias("confirmed_local"),
        _local("created_at").alias("created_local"),
    )


def _overlaps(left: pl.DataFrame, right: pl.DataFrame, on: str | None) -> int:
    pairs = left.join(right, on=on, how="inner") if on else left.join(right, how="cross")
    return pairs.filter(
        (pl.col("start") < pl.col("end_right")) & (pl.col("end") > pl.col("start_right"))
    ).height


def test_no_contract_violations(result: GenerationResult) -> None:
    assert result.violations == []


def test_presentations_cover_every_started_autostore_task(
    result: GenerationResult, autostore_tasks: pl.DataFrame
) -> None:
    presentations = result.tables["raw_ext.autostore_bin_presentation"]
    started = autostore_tasks.filter(pl.col("started_at") > 0)
    assert presentations.get_column("task_count").sum() == started.height
    assert (presentations.get_column("closed_at") > presentations.get_column("presented_at")).all()
    assert set(presentations.get_column("port_id").unique()) <= set(PORTS)
    # A bin is never at two ports at once.
    spans = presentations.select(
        "bin_id", pl.col("presented_at").alias("start"), pl.col("closed_at").alias("end")
    )
    same_bin = spans.with_row_index("id").join(spans.with_row_index("id"), on="bin_id", suffix="_right")
    assert same_bin.filter(
        (pl.col("id") != pl.col("id_right"))
        & (pl.col("start") < pl.col("end_right"))
        & (pl.col("end") > pl.col("start_right"))
    ).is_empty()


def test_nothing_happens_at_closed_ports_or_during_stops(
    result: GenerationResult, autostore_tasks: pl.DataFrame
) -> None:
    presentations = result.tables["raw_ext.autostore_bin_presentation"].select(
        "port_id", pl.col("presented_at").alias("start"), pl.col("closed_at").alias("end")
    )
    status = result.tables["raw_ext.autostore_port_status"]
    closed = status.filter(pl.col("status") == "CLOSED").select(
        "port_id", pl.col("valid_from").alias("start"), pl.col("valid_to").alias("end")
    )
    assert _overlaps(presentations, closed, on="port_id") == 0
    stops = result.tables["raw_ext.autostore_system_status"].select(
        pl.col("started_at").alias("start"), pl.col("ended_at").alias("end")
    )
    assert stops.height > 0
    assert _overlaps(presentations.drop("port_id"), stops, on=None) == 0
    # Port timelines are complete: consecutive intervals touch, and only the last one is open-ended.
    for (port,), timeline in status.sort("valid_from").group_by("port_id", maintain_order=True):
        ends = timeline.get_column("valid_to").to_list()
        assert ends[-1] is None and None not in ends[:-1], port
        assert timeline.get_column("valid_from").to_list()[1:] == ends[:-1], port


def test_scenario_a1_closes_two_ports_and_slows_autostore_picks(
    result: GenerationResult, autostore_tasks: pl.DataFrame
) -> None:
    faults = result.tables["raw_ext.autostore_port_status"].filter(pl.col("reason") == "FAULT")
    assert sorted(faults.get_column("port_id").to_list()) == sorted(A1_PORTS)
    assert (faults.get_column("valid_from").dt.date() == A1_START).all()
    assert (faults.get_column("valid_to").dt.date() == A1_END).all()
    picks = autostore_tasks.filter((pl.col("vltyp") == "AS01") & (pl.col("tostat") == "C"))
    in_window = picks.filter(pl.col("created_local").dt.date().is_between(A1_START, A1_END))
    assert in_window.height > 0
    fault_start, fault_end = faults.get_column("valid_from").min(), faults.get_column("valid_to").max()
    during_fault = autostore_tasks.filter(pl.col("started_local").is_between(fault_start, fault_end))
    assert during_fault.height > 0
    assert not set(during_fault.get_column("prsrc").unique()) & set(A1_PORTS)
    cycle = ((pl.col("confirmed_local") - pl.col("created_local")).dt.total_seconds() / 60).median()
    before = picks.filter(pl.col("created_local").dt.date() < A1_START).select(cycle).item()
    during = in_window.select(cycle).item()
    assert during > 1.5 * before
    truth = result.tables["eval.anomaly_ground_truth"]
    assert truth.filter(pl.col("scenario_id") == "A1").height == 1


def test_a1_delays_keep_picks_before_goods_issue_and_waves_consistent(result: GenerationResult) -> None:
    delays = result.facts["autostore_delays"]
    assert delays.height > 0 and (delays.get_column("delay").dt.total_microseconds() >= 0).all()
    # Only picks the scenario moved are capped: an unmoved pick may sit closer to its goods issue when the
    # outbound model staged the delivery quickly.
    moved = delays.filter(pl.col("delay").dt.total_microseconds() > 0)
    assert moved.height > 0
    tasks = result.tables["raw_sap_ewm.scwm_ordim_c"].with_row_index("row")
    delayed = tasks.join(moved, on="row").join(
        result.facts["tasks"].select("lgnum", "tanum", "delivery_id"), on=["lgnum", "tanum"]
    )
    pgi = delayed.join(result.facts["deliveries"].select("delivery_id", "pgi_utc"), on="delivery_id").select(
        pl.col("confirmed_at")
        .cast(pl.String)
        .str.strptime(pl.Datetime("us"), "%Y%m%d%H%M%S")
        .alias("picked"),
        (pl.col("pgi_utc") - pl.duration(minutes=GI_STAGING_MIN)).alias("limit"),
    )
    assert pgi.filter(pl.col("limit").is_not_null() & (pl.col("picked") > pl.col("limit"))).is_empty()
    waves = result.tables["raw_sap_ewm.scwm_wavehdr"].filter(pl.col("pick_comp_dt") > 0)
    last_pick = (
        tasks.filter((pl.col("tostat") == "C") & (pl.col("trart") == "2"))
        .group_by("lgnum", "wave")
        .agg(pl.col("confirmed_at").max().alias("last"))
    )
    joined = waves.join(last_pick, on=["lgnum", "wave"])
    assert (joined.get_column("pick_comp_dt") >= joined.get_column("last")).all()
    loaded = joined.filter(pl.col("load_comp_dt") > 0)
    assert (loaded.get_column("load_comp_dt") >= loaded.get_column("pick_comp_dt")).all()


def test_controller_uses_local_time(result: GenerationResult, autostore_tasks: pl.DataFrame) -> None:
    presentations = result.tables["raw_ext.autostore_bin_presentation"]
    started = autostore_tasks.filter(pl.col("started_at") > 0)
    first_task = started.get_column("started_local").min()
    first_presentation = presentations.get_column("presented_at").min()
    # The first presentation precedes the first task start by seconds, not by the eight-hour UTC offset.
    assert abs((first_task - first_presentation).total_seconds()) < 3600
    utc = utc_to_local(np.array([np.datetime64("2026-04-01T00:00:00", "us")]), TZ)[0]
    assert str(utc).startswith("2026-04-01T08:00")
