"""Ground-truth invariants of the synthetic data (evaluation layer L0).

These tests assert that the behaviors the generator is supposed to encode (time zones, freshness,
reversals, statuses, system users, number formats) actually hold in the generated tables.
"""

import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from snm_data.contracts import ContractRegistry
from snm_data.generator.calendar import fiscal_year_of
from snm_data.generator.config import PROFILES
from snm_data.generator.manufacturing import PSA_LGORT
from snm_data.generator.master import BOXABLE_NOUNS
from snm_data.generator.org import ECC_SYSTEM_TZ, SITE_BY_LGNUM, SITES, SYSTEM_USERS
from snm_data.generator.run import GenerationResult, generate

CONTRACTS = Path(__file__).resolve().parents[2] / "data_contracts"
PROFILE = PROFILES["test"]


@pytest.fixture(scope="module")
def result() -> GenerationResult:
    return generate(PROFILE, ContractRegistry.load(CONTRACTS), keep_facts=True)


@pytest.fixture(scope="module")
def t(result: GenerationResult):
    return result.tables


def _dec15(column: str) -> pl.Expr:
    return (
        pl.col(column)
        .cast(pl.Int64)
        .cast(pl.String)
        .str.strptime(pl.Datetime("us"), "%Y%m%d%H%M%S", strict=False)
    )


def _naive(value) -> pl.Expr:
    return pl.lit(value.replace(tzinfo=None)).cast(pl.Datetime("us"))


def test_no_contract_violations(result: GenerationResult) -> None:
    assert result.violations == []


def test_generation_is_deterministic(t) -> None:
    again = generate(PROFILE, ContractRegistry.load(CONTRACTS)).tables
    assert sorted(again) == sorted(t)
    for name, frame in t.items():
        assert frame.equals(again[name]), name


def _digest_in_subprocess(hash_seed: str) -> str:
    """Digest of all generated tables in a fresh interpreter with the given string-hash seed."""
    script = (
        "import hashlib\n"
        "from pathlib import Path\n"
        "from snm_data.contracts import ContractRegistry\n"
        "from snm_data.generator.config import PROFILES\n"
        "from snm_data.generator.run import generate\n"
        f"tables = generate(PROFILES['test'], ContractRegistry.load(Path({str(CONTRACTS)!r}))).tables\n"
        "digest = hashlib.sha256()\n"
        "for name in sorted(tables):\n"
        "    digest.update(name.encode()); digest.update(tables[name].write_csv().encode())\n"
        "print(digest.hexdigest())\n"
    )
    env = {**os.environ, "PYTHONHASHSEED": hash_seed}
    completed = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, env=env, check=True
    )
    return completed.stdout.strip()


def test_generation_is_deterministic_across_processes() -> None:
    # Set order and join row order can differ between interpreters; random draws must not depend on them.
    assert _digest_in_subprocess("1") == _digest_in_subprocess("2")


def test_every_table_is_populated(t) -> None:
    empty = [name for name, frame in t.items() if frame.height == 0]
    assert not empty


def test_warehouse_time_fields_match_site_time_zone(t) -> None:
    tasks = t["raw_sap_ewm.scwm_ordim_c"]
    for lgnum, site in SITE_BY_LGNUM.items():
        subset = tasks.filter(pl.col("lgnum") == lgnum).select(
            _dec15("confirmed_at")
            .dt.replace_time_zone("UTC")
            .dt.convert_time_zone(site.tz)
            .dt.replace_time_zone(None)
            .alias("expected"),
            _dec15("confirmed_at_wh").alias("actual"),
        )
        assert subset.height > 0
        assert subset.filter(pl.col("expected") != pl.col("actual")).height == 0, lgnum


def test_task_timestamps_are_ordered_and_extracted_before_cutoff(t) -> None:
    tasks = t["raw_sap_ewm.scwm_ordim_c"].with_columns(
        _dec15("created_at").alias("created"),
        _dec15("started_at").alias("started"),
        _dec15("confirmed_at").alias("confirmed"),
    )
    confirmed = tasks.filter(pl.col("tostat") == "C")
    assert confirmed.filter(pl.col("started").is_null()).height == 0
    assert (
        confirmed.filter(
            ~((pl.col("created") <= pl.col("started")) & (pl.col("started") <= pl.col("confirmed")))
        ).height
        == 0
    )
    assert tasks.filter(pl.col("confirmed") > _naive(PROFILE.ewm_as_of)).height == 0
    cancelled = tasks.filter(pl.col("tostat") == "A")
    assert cancelled.height > 0, "cancelled tasks are part of the task-count trap"


def test_ecc_is_staler_than_ewm(t) -> None:
    """Nightly ECC vs hourly EWM: EWM knows deliveries that the ECC extract does not contain yet."""
    ecc_deliveries = t["raw_sap_ecc.likp"].select(pl.col("vbeln").alias("refdocno"))
    ewm_only = t["raw_sap_ewm.scdl_db_refdoc"].join(ecc_deliveries, on="refdocno", how="anti")
    assert ewm_only.height > 0
    entry = t["raw_sap_ecc.mkpf"].select(
        (pl.col("cpudt") + pl.col("cputm"))
        .str.strptime(pl.Datetime("us"), "%Y%m%d%H%M%S")
        .dt.replace_time_zone(ECC_SYSTEM_TZ)
        .dt.convert_time_zone("UTC")
        .dt.replace_time_zone(None)
        .alias("entry")
    )
    assert entry.filter(pl.col("entry") > _naive(PROFILE.ecc_as_of)).height == 0


def test_goods_issue_status_matches_delivery_header(t) -> None:
    joined = t["raw_sap_ecc.likp"].join(t["raw_sap_ecc.vbuk"], on=["mandt", "vbeln"])
    posted = joined.filter(pl.col("wadat_ist") != "00000000")
    assert posted.filter(pl.col("wbstk") != "C").height == 0
    assert joined.filter((pl.col("wadat_ist") == "00000000") & (pl.col("wbstk") == "C")).height == 0


def test_picked_deliveries_ship_on_or_after_the_planned_day(t) -> None:
    joined = t["raw_sap_ecc.likp"].join(t["raw_sap_ecc.vbuk"], on=["mandt", "vbeln"])
    settled = (PROFILE.ecc_as_of.date() - timedelta(days=5)).strftime("%Y%m%d")
    old = joined.filter(pl.col("wadat") <= settled)
    # Regression: items of split orders were once never goods-issued.
    assert old.filter((pl.col("kostk") == "C") & (pl.col("wbstk") != "C")).height == 0
    shipped = old.filter(pl.col("wadat_ist") != "00000000")
    assert shipped.filter(pl.col("wadat_ist") < pl.col("wadat")).height == 0
    on_time = shipped.filter(pl.col("wadat_ist") <= pl.col("wadat")).height / old.height
    assert 0.85 <= on_time <= 0.995


def test_pod_date_is_receipt_and_mostly_on_time(t) -> None:
    confirmed = t["raw_sap_ecc.likp"].filter(pl.col("podat") != "00000000")
    assert confirmed.filter(pl.col("podat") < pl.col("wadat_ist")).height == 0
    on_time = confirmed.filter(pl.col("podat") <= pl.col("lfdat")).height / confirmed.height
    assert 0.65 <= on_time <= 0.95


def test_only_commodity_parts_have_a_box_unit(t) -> None:
    boxed = t["raw_sap_ecc.marm"].filter(pl.col("meinh") == "BOX").join(t["raw_sap_ecc.makt"], on="matnr")
    assert boxed.height > 0
    nouns = boxed.get_column("maktx").to_list()
    assert all(any(name.startswith(noun) for noun in BOXABLE_NOUNS) for name in nouns)


def test_reversals_net_out_and_reference_originals(t) -> None:
    mseg = t["raw_sap_ecc.mseg"]
    reversals = mseg.filter(pl.col("bwart") == "312")
    assert reversals.height > 0
    originals = mseg.filter(pl.col("bwart") == "311").select(
        pl.col("mblnr").alias("smbln"),
        pl.col("mjahr").alias("sjahr"),
        pl.col("zeile").alias("smblp"),
        pl.col("matnr").alias("orig_matnr"),
        pl.col("menge").alias("orig_menge"),
        pl.col("lgort").alias("orig_lgort"),
        pl.col("umlgo").alias("orig_umlgo"),
    )
    linked = reversals.join(originals, on=["smbln", "sjahr", "smblp"], how="left")
    assert linked.filter(pl.col("orig_matnr").is_null()).height == 0
    assert (
        linked.filter(
            (pl.col("orig_matnr") != pl.col("matnr")) | (pl.col("orig_menge") != pl.col("menge"))
        ).height
        == 0
    )
    # A reversal mirrors the route of the item it reverses (the production supply area gives back).
    mirrored = (pl.col("lgort") == pl.col("orig_umlgo")) & (pl.col("umlgo") == pl.col("orig_lgort"))
    assert linked.filter(~mirrored).height == 0
    # Net quantity issued from the warehouse per posted delivery item equals the delivered base quantity.
    warehouse_side = mseg.filter(pl.col("bwart").is_in(["311", "312"]) & (pl.col("lgort") != PSA_LGORT))
    net = warehouse_side.group_by("vbeln_im", "vbelp_im").agg(
        pl.when(pl.col("shkzg") == "H").then(pl.col("menge")).otherwise(-pl.col("menge")).sum().alias("net")
    )
    posted_items = t["raw_sap_ecc.lips"].join(
        t["raw_sap_ecc.vbup"].filter(pl.col("wbsta") == "C"), on=["mandt", "vbeln", "posnr"]
    )
    check = posted_items.join(net, left_on=["vbeln", "posnr"], right_on=["vbeln_im", "vbelp_im"], how="left")
    assert check.filter(pl.col("net").is_null() | (pl.col("net") != pl.col("lgmng"))).height == 0


def test_material_document_year_is_fiscal_year_of_posting_date(t) -> None:
    mkpf = t["raw_sap_ecc.mkpf"]
    for row in mkpf.select("mjahr", "budat").unique().iter_rows(named=True):
        budat = date(int(row["budat"][:4]), int(row["budat"][4:6]), int(row["budat"][6:]))
        assert int(row["mjahr"]) == fiscal_year_of(budat)


def test_pod_only_for_relevant_items(t) -> None:
    relevant = t["raw_sap_ecc.lips"].group_by("vbeln").agg((pl.col("podrel") == "A").any().alias("relevant"))
    headers = (
        t["raw_sap_ecc.likp"].join(relevant, on="vbeln").join(t["raw_sap_ecc.vbuk"], on=["mandt", "vbeln"])
    )
    assert headers.filter((pl.col("podat") != "00000000") & ~pl.col("relevant")).height == 0
    assert headers.filter(~pl.col("relevant") & (pl.col("pdstk") != "")).height == 0
    assert headers.filter((pl.col("pdstk") == "C") & (pl.col("podat") == "00000000")).height == 0


def test_system_users_and_autostore_confirmations(t) -> None:
    tasks = t["raw_sap_ewm.scwm_ordim_c"]
    roster = set(t["raw_ext.workforce_roster"].get_column("sap_uname").to_list())
    people = set(tasks.get_column("confirmed_by").to_list()) - set(SYSTEM_USERS)
    assert people <= roster
    assert not set(SYSTEM_USERS) & roster
    autostore = tasks.filter(pl.col("vltyp") == "AS01")
    assert autostore.height > 0 and set(autostore.get_column("confirmed_by").to_list()) == {"AS-INTERFACE"}
    # AS-INTERFACE confirms only AutoStore tasks: picks from AS01 and putaways (decanting) into AS01.
    at_ports = (pl.col("vltyp") == "AS01") | (pl.col("nltyp") == "AS01")
    assert tasks.filter((pl.col("confirmed_by") == "AS-INTERFACE") & ~at_ports).height == 0


def test_operators_confirm_only_in_their_home_warehouse(t) -> None:
    roster = t["raw_ext.workforce_roster"].select(
        pl.col("sap_uname").alias("confirmed_by"), pl.col("lgnum").alias("home")
    )
    joined = t["raw_sap_ewm.scwm_ordim_c"].join(roster.unique(), on="confirmed_by")
    assert joined.filter(pl.col("home") != pl.col("lgnum")).height == 0


def test_product_numbers_differ_in_padding_between_ecc_and_ewm(t) -> None:
    keys = t["raw_sap_ewm.sapapo_matkey"]
    assert keys.filter(pl.col("matnr").str.len_chars() != 40).height == 0
    mara = t["raw_sap_ecc.mara"].select("matnr")
    assert keys.join(mara, on="matnr", how="inner").height == 0, "padding must differ (join trap)"
    stripped = keys.select(pl.col("matnr").str.slice(22).alias("matnr"))
    assert stripped.join(mara, on="matnr", how="anti").height == 0


def test_delivery_items_follow_their_production_order_components(t) -> None:
    # A delivery to the production supply area is the CO27 picking list of one production order: every
    # line references the order (LIPS-VGBEL = AUFNR) and its component reservation line (VGPOS = RSPOS).
    lips = t["raw_sap_ecc.lips"]
    assert (lips.get_column("vgpos").str.len_chars() == 6).all()
    joined = lips.join(
        t["raw_sap_ecc.resb"]
        .filter(pl.col("aufnr") != "")
        .select(
            pl.col("aufnr").alias("vgbel"),
            pl.col("rspos").str.zfill(6).alias("vgpos"),
            pl.col("matnr").alias("component"),
            pl.col("werks").alias("order_plant"),
        ),
        on=["vgbel", "vgpos"],
    )
    assert joined.height == lips.height
    assert joined.filter(pl.col("matnr") != pl.col("component")).height == 0
    assert joined.filter(pl.col("werks") != pl.col("order_plant")).height == 0
    orders = t["raw_sap_ecc.afko"].select(pl.col("aufnr").alias("vgbel"))
    assert joined.join(orders, on="vgbel").height == lips.height


def test_currencies_differ_by_plant(t) -> None:
    pairs = set(t["raw_sap_ecc.mseg"].select("werks", "waers").unique().iter_rows())
    assert pairs <= {("US10", "USD"), ("CN10", "CNY")}


def test_all_sites_ship(t) -> None:
    shipped = set(t["raw_sap_ecc.likp"].get_column("vstel").unique().to_list())
    assert shipped == {site.vstel for site in SITES.values()}
