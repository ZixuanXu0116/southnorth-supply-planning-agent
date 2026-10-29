"""EWM inbound: inbound deliveries, putaway tasks, and warehouse product data (PACI).

Every purchase-order goods receipt (movement 101) into an EWM-managed storage location gets an inbound
delivery in EWM (created from the supplier's shipping notification) and a putaway task from the goods
receipt zone to a destination bin:

- Round Rock (US10/1000 -> RR01): destination by the putaway control indicator (PACI); receipts into
  quality inspection go to storage type 8000.
- Longgang (CN10/1000 and the bonded area CN10/1900 -> SZ01): destination by PACI; bonded receipts go to
  storage type BND1. AutoStore putaways (decanting at a port) are confirmed by AS-INTERFACE.

Dongguan receives only internal transfers (315); their EWM inbound documents are not part of this extract.
The goods receipt time is the ECC posting time (MKPF.CPUDT/CPUTM, America/Chicago) converted to UTC.
Scenario A2 (see ``scenarios``) changes the PACI of one material group at Round Rock.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass

import numpy as np
import polars as pl

from .config import Profile
from .org import ECC_SYSTEM_TZ, MANDT, SITES
from .outbound import assign_operators
from .scenarios import A2_INTENDED_PACI, A2_MATERIAL_GROUP, A2_START, A2_WRONG_PACI, ground_truth
from .timeutil import HOUR, dec15, guids, local_to_utc, minutes, utc_to_local, zfill

# Putaway control indicator per slotted storage type, and back.
PACI_BY_TYPE = {"0030": "CAGE", "0010": "HRCK", "0020": "SHLF", "AS01": "ASTR", "0050": "BULK"}
TYPE_BY_PACI = {paci: lgtyp for lgtyp, paci in PACI_BY_TYPE.items()}
# EWM-managed receiving storage locations and their site.
EWM_LOCATIONS = {("US10", "1000"): "RR", ("CN10", "1000"): "SZ", ("CN10", "1900"): "SZ"}
# Median minutes from task creation to start (queue) and from start to confirmation (execution).
QUEUE_MEDIAN_MIN = {
    ("RR", "0010"): 150.0,  # high rack: waits for the reach truck
    ("RR", "0020"): 40.0,
    ("RR", "0030"): 45.0,
    ("RR", "8000"): 30.0,
    ("SZ", "AS01"): 60.0,
    ("SZ", "0020"): 35.0,
    ("SZ", "0050"): 50.0,
    ("SZ", "BND1"): 30.0,
}
EXEC_MEDIAN_MIN = {
    ("RR", "0010"): 12.0,
    ("RR", "0020"): 6.0,
    ("RR", "0030"): 10.0,
    ("RR", "8000"): 5.0,
    ("SZ", "AS01"): 8.0,
    ("SZ", "0020"): 5.0,
    ("SZ", "0050"): 9.0,
    ("SZ", "BND1"): 6.0,
}
EXPEDITED_QUEUE_FACTOR = 0.25
CANCEL_RATE = 0.01
RR_WORK_START_HOUR = 6
RR_WORK_END_HOUR = 22


@dataclass
class InboundResult:
    tables: dict[str, pl.DataFrame]
    refdoc: pl.DataFrame  # rows to append to raw_sap_ewm.scdl_db_refdoc
    tasks: pl.DataFrame  # rows to append to raw_sap_ewm.scwm_ordim_c
    facts: dict[str, pl.DataFrame]


def _iso_weekday(days: np.ndarray) -> np.ndarray:
    return (days.astype("int64") + 3) % 7 + 1  # 1970-01-01 was a Thursday


def _round_rock_working_time(local: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Move site-local times outside Round Rock's shifts (Mon-Sat 06:00-22:00) to the next shift start."""
    result = local.copy()
    for _ in range(3):
        day = result.astype("datetime64[D]")
        hour = (result - day.astype("datetime64[us]")) / HOUR
        closed_day = _iso_weekday(day) == 7
        early = (hour < RR_WORK_START_HOUR) & ~closed_day
        late = (hour >= RR_WORK_END_HOUR) | closed_day
        if not (early | late).any():
            break
        target = np.where(late, day + np.timedelta64(1, "D"), day).astype("datetime64[us]")
        start = target + RR_WORK_START_HOUR * HOUR + minutes(rng.uniform(0.0, 45.0, size=result.size))
        result = np.where(early | late, start, result)
    return result


def _receipts(mseg: pl.DataFrame, mkpf: pl.DataFrame, ekko: pl.DataFrame, eket: pl.DataFrame) -> pl.DataFrame:
    locations = pl.DataFrame(
        {
            "werks": [werks for werks, _ in EWM_LOCATIONS],
            "lgort": [lgort for _, lgort in EWM_LOCATIONS],
            "site": list(EWM_LOCATIONS.values()),
        }
    )
    return (
        mseg.filter((pl.col("bwart") == "101") & (pl.col("shkzg") == "S"))
        .join(locations, on=["werks", "lgort"], how="inner")
        .join(mkpf.select("mblnr", "mjahr", "cpudt", "cputm"), on=["mblnr", "mjahr"], how="inner")
        .join(ekko.select("ebeln", "bsart", "aedat"), on="ebeln", how="inner")
        .join(eket.select("ebeln", "ebelp", "eindt"), on=["ebeln", "ebelp"], how="inner")
        .with_columns(
            pl.concat_str("cpudt", "cputm").str.strptime(pl.Datetime("us"), "%Y%m%d%H%M%S").alias("gr_ecc"),
            pl.col("aedat").str.strptime(pl.Datetime("us"), "%Y%m%d").alias("aedat_local"),
        )
        .sort(["gr_ecc", "mblnr", "zeile"])
    )


def _destination(
    receipts: pl.DataFrame, materials: pl.DataFrame, slots: pl.DataFrame
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """PACI in force at each receipt and the destination storage type; also the PACI at extraction."""
    lgnum = pl.col("site").replace_strict({code: site.lgnum for code, site in SITES.items()})
    receipts = receipts.with_columns(lgnum.alias("lgnum")).join(
        materials.select("matnr", "matkl"), on="matnr", how="left"
    )
    slotted = slots.select(
        "lgnum", "matnr", pl.col("lgtyp").alias("slot_type"), pl.col("lgpla").alias("slot_bin")
    )
    receipts = receipts.join(slotted, on=["lgnum", "matnr"], how="left").with_columns(
        pl.col("slot_type").fill_null("0020")
    )
    intended = pl.col("slot_type").replace_strict(PACI_BY_TYPE)
    a2 = (
        (pl.col("site") == "RR")
        & (pl.col("matkl") == A2_MATERIAL_GROUP)
        & (intended == A2_INTENDED_PACI)
        & (pl.col("gr_local").dt.date() >= A2_START)
    )
    receipts = receipts.with_columns(
        pl.when(a2).then(pl.lit(A2_WRONG_PACI)).otherwise(intended).alias("paci")
    )
    dest = (
        pl.when((pl.col("site") == "RR") & (pl.col("insmk") == "X"))
        .then(pl.lit("8000"))
        .when(pl.col("lgort") == "1900")
        .then(pl.lit("BND1"))
        .otherwise(pl.col("paci").replace_strict(TYPE_BY_PACI))
    )
    receipts = receipts.with_columns(dest.alias("dest_type"))

    # Configuration at extraction: the intended PACI, except where scenario A2 is in force.
    config = slots.join(materials.select("matnr", "matkl"), on="matnr", how="left").with_columns(
        pl.col("lgtyp").replace_strict(PACI_BY_TYPE).alias("put_stra")
    )
    config = config.with_columns(
        pl.when(
            (pl.col("lgnum") == "RR01")
            & (pl.col("matkl") == A2_MATERIAL_GROUP)
            & (pl.col("put_stra") == A2_INTENDED_PACI)
        )
        .then(pl.lit(A2_WRONG_PACI))
        .otherwise(pl.col("put_stra"))
        .alias("put_stra")
    )
    return receipts, config


def _destination_bins(receipts: pl.DataFrame, bins: pl.DataFrame, rng: np.random.Generator) -> np.ndarray:
    """The slotted bin when the destination type is the slotted type, otherwise a bin of that type."""
    result = receipts.get_column("slot_bin").to_numpy().astype(object)
    use_slot = (receipts.get_column("dest_type") == receipts.get_column("slot_type")).to_numpy() & (
        receipts.get_column("slot_bin").is_not_null().to_numpy()
    )
    lgnums = receipts.get_column("lgnum").to_numpy()
    types = receipts.get_column("dest_type").to_numpy()
    for lgnum, lgtyp in sorted({(a, b) for a, b in zip(lgnums, types, strict=True)}):  # stable draw order
        mask = (lgnums == lgnum) & (types == lgtyp) & ~use_slot
        if not mask.any():
            continue
        names = (
            bins.filter((pl.col("lgnum") == lgnum) & (pl.col("lgtyp") == lgtyp))
            .get_column("lgpla")
            .to_numpy()
        )
        result[mask] = rng.choice(names, size=int(mask.sum()))
    return result.astype(str)


def _operators(
    rng: np.random.Generator, roster: pl.DataFrame, code: str, lgnum: str, confirmed: np.ndarray
) -> np.ndarray:
    staff = roster.filter((pl.col("lgnum") == lgnum) & pl.col("active_to").is_null())
    team = "INB" if (staff.get_column("team") == "INB").any() else "OUTB"
    return assign_operators(rng, roster, code, lgnum, confirmed, team_name=team)


def generate_inbound(
    profile: Profile,
    materials: pl.DataFrame,
    slots: pl.DataFrame,
    bins: pl.DataFrame,
    product_keys: pl.DataFrame,
    roster: pl.DataFrame,
    mseg: pl.DataFrame,
    mkpf: pl.DataFrame,
    ekko: pl.DataFrame,
    eket: pl.DataFrame,
) -> InboundResult:
    rng = np.random.default_rng([profile.seed, 30])
    ewm_as_of = np.datetime64(profile.ewm_as_of.replace(tzinfo=None), "us")

    receipts = _receipts(mseg, mkpf, ekko, eket)
    gr_utc = local_to_utc(receipts.get_column("gr_ecc").to_numpy(), ECC_SYSTEM_TZ)
    site_codes = receipts.get_column("site").to_numpy()
    gr_local = gr_utc.copy()
    for code, site in SITES.items():
        mask = site_codes == code
        if mask.any():
            gr_local[mask] = utc_to_local(gr_utc[mask], site.tz)
    receipts = receipts.with_columns(pl.Series("gr_utc", gr_utc), pl.Series("gr_local", gr_local))
    receipts, config = _destination(receipts, materials, slots)
    receipts = receipts.sort(["gr_ecc", "mblnr", "zeile"])  # joins do not keep row order
    receipts = receipts.with_columns(pl.Series("dest_bin", _destination_bins(receipts, bins, rng)))
    count = receipts.height

    # ---- inbound deliveries: from the shipping notification (expedited buys: minutes before receipt) -----
    expedited = (receipts.get_column("bsart") == "ZEX").to_numpy()
    lead = np.where(expedited, rng.uniform(5.0, 15.0, count), rng.uniform(12 * 60.0, 96 * 60.0, count))
    created = gr_utc - minutes(lead)
    po_created = np.empty(count, dtype="datetime64[us]")
    aedat_local = receipts.get_column("aedat_local").to_numpy() + 17 * HOUR
    for code, site in SITES.items():
        mask = site_codes == code
        if mask.any():
            po_created[mask] = local_to_utc(aedat_local[mask], site.tz)
    created = np.where(expedited, created, np.maximum(created, np.minimum(po_created + HOUR, gr_utc)))
    receipts = receipts.with_columns(pl.Series("cretst_utc", created)).sort(["cretst_utc", "mblnr"])
    receipts = (
        receipts.with_columns(
            pl.Series("docid", guids(rng, count)),
            pl.Series("itemid", guids(rng, count)),
            zfill(1_800_000_000 + np.arange(1, count + 1), 10).alias("docno"),
        )
        .join(
            product_keys.select(
                pl.col("matnr_ecc").alias("matnr"), pl.col("matnr").alias("productno"), "matid"
            ),
            on="matnr",
            how="inner",
        )
        .sort(["cretst_utc", "mblnr"])
    )
    proch = receipts.select(
        pl.lit(MANDT).alias("mandt"),
        "docid",
        "docno",
        pl.lit("PDI").alias("doccat"),
        pl.when(pl.col("bsart") == "ZEX").then(pl.lit("INBX")).otherwise(pl.lit("INB")).alias("doctype"),
        "lgnum",
        pl.col("eindt").alias("deldate"),
        pl.Series("cretst", dec15(receipts.get_column("cretst_utc").to_numpy())),
        pl.lit("RFC-EWM").alias("creusr"),
    )
    proci = receipts.select(
        pl.lit(MANDT).alias("mandt"),
        "docid",
        "itemid",
        pl.lit("0000000010").alias("itemno"),
        "productno",
        pl.col("menge").alias("qty"),
        pl.lit("EA").alias("uom"),
        pl.when(pl.col("insmk") == "X").then(pl.lit("Q2")).otherwise(pl.lit("F2")).alias("stock_category"),
        pl.when(pl.col("sobkz") == "K").then(pl.col("lifnr")).otherwise(pl.col("werks")).alias("owner"),
    )
    refdoc = receipts.select(
        pl.lit(MANDT).alias("mandt"),
        "docid",
        "itemid",
        pl.lit("PO").alias("refdoccat"),
        pl.col("ebeln").alias("refdocno"),
        pl.col("ebelp").alias("refitemno"),
    )

    # ---- putaway tasks: created at goods receipt; queue, execution, and 1 % cancellations ------------------
    tasks = _putaway_tasks(receipts, rng, roster, ewm_as_of)
    matlwh = config.join(product_keys.select(pl.col("matnr_ecc").alias("matnr"), "matid"), on="matnr").select(
        pl.lit(MANDT).alias("mandt"), "lgnum", "matid", "put_stra", pl.lit("FIFO").alias("rem_stra")
    )
    tables = {
        "raw_sap_ewm.scdl_db_proch_i": proch,
        "raw_sap_ewm.scdl_db_proci_i": proci,
        "raw_sap_ewm.sapapo_matlwh": matlwh.sort(["lgnum", "matid"]),
        "eval.anomaly_ground_truth": ground_truth(profile),
    }
    facts = {
        "inbound_receipts": receipts.select(
            "mblnr", "matnr", "matkl", "site", "lgnum", "gr_utc", "gr_local", "paci", "dest_type", "dest_bin"
        )
    }
    return InboundResult(tables=tables, refdoc=refdoc, tasks=tasks, facts=facts)


def _putaway_tasks(
    receipts: pl.DataFrame, rng: np.random.Generator, roster: pl.DataFrame, ewm_as_of: np.datetime64
) -> pl.DataFrame:
    count = receipts.height
    site_codes = receipts.get_column("site").to_numpy()
    dest_types = receipts.get_column("dest_type").to_numpy()
    keys = list(zip(site_codes, dest_types, strict=True))
    queue_median = np.array([QUEUE_MEDIAN_MIN[key] for key in keys])
    queue_median = np.where(
        (receipts.get_column("bsart") == "ZEX").to_numpy(),
        queue_median * EXPEDITED_QUEUE_FACTOR,
        queue_median,
    )
    exec_median = np.array([EXEC_MEDIAN_MIN[key] for key in keys])

    gr_utc = receipts.get_column("gr_utc").to_numpy()
    created = gr_utc + minutes(rng.uniform(0.5, 3.0, count))
    cancelled = rng.random(count) < CANCEL_RATE

    def schedule(created_at: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        start = created_at + minutes(rng.lognormal(np.log(queue_median), 0.6))
        rr = site_codes == "RR"
        if rr.any():
            tz = SITES["RR"].tz
            start[rr] = local_to_utc(_round_rock_working_time(utc_to_local(start[rr], tz), rng), tz)
        return start, start + minutes(rng.lognormal(np.log(exec_median), 0.45))

    started, confirmed = schedule(created)
    cancel_time = created + minutes(rng.lognormal(np.log(queue_median), 0.6) * 0.5)
    cancel_started = np.where(
        rng.random(count) < 0.4, cancel_time - minutes(rng.uniform(1.0, 5.0, count)), np.datetime64("NaT")
    )
    cancel_started = np.where(cancel_started < created, created, cancel_started)
    second_created = cancel_time + minutes(rng.uniform(1.0, 10.0, count))
    second_started, second_confirmed = schedule(second_created)

    base = receipts.select(
        "site", "lgnum", "matid", "menge", "dest_type", "dest_bin", "docid", "itemid", "gr_utc"
    ).with_columns(pl.Series("vlpla", [f"GR-ZONE-{index:02d}" for index in rng.integers(1, 3, count)]))
    primary = base.with_columns(
        pl.Series("created_utc", created),
        pl.Series("started_utc", np.where(cancelled, cancel_started, started)),
        pl.Series("confirmed_utc", np.where(cancelled, cancel_time, confirmed)),
        pl.Series("tostat", np.where(cancelled, "A", "C")),
    )
    replacements = base.filter(pl.Series(cancelled)).with_columns(
        pl.Series("created_utc", second_created[cancelled]),
        pl.Series("started_utc", second_started[cancelled]),
        pl.Series("confirmed_utc", second_confirmed[cancelled]),
        pl.lit("C").alias("tostat"),
    )
    tasks = pl.concat([primary, replacements]).filter(pl.col("confirmed_utc") <= ewm_as_of)

    confirmed_at = tasks.get_column("confirmed_utc").to_numpy()
    by = np.empty(tasks.height, dtype=object)
    resource = np.empty(tasks.height, dtype=object)
    codes = tasks.get_column("site").to_numpy()
    for code, site in SITES.items():
        mask = codes == code
        if not mask.any():
            continue
        names = _operators(rng, roster, code, site.lgnum, confirmed_at[mask])
        by[mask] = names
        resource[mask] = [f"RF-{site.lgnum}-{zlib.crc32(name.encode()) % 40 + 1:03d}" for name in names]
    autostore = (tasks.get_column("dest_type") == "AS01").to_numpy()
    by[autostore] = "AS-INTERFACE"
    resource[autostore] = [f"AS-PORT-{port:02d}" for port in rng.integers(1, 13, size=int(autostore.sum()))]
    tasks = tasks.with_columns(pl.Series("confirmed_by", by), pl.Series("prsrc", resource))

    rows = []
    for code, site in SITES.items():
        subset = tasks.filter(pl.col("site") == code)
        if subset.height == 0:
            continue
        created_at = subset.get_column("created_utc").to_numpy()
        confirmed_at = subset.get_column("confirmed_utc").to_numpy()
        rows.append(
            subset.with_columns(
                pl.Series("created_at", dec15(created_at)),
                pl.Series("created_at_wh", dec15(utc_to_local(created_at, site.tz))),
                pl.Series("started_at", dec15(subset.get_column("started_utc").to_numpy())),
                pl.Series("confirmed_at", dec15(confirmed_at)),
                pl.Series("confirmed_at_wh", dec15(utc_to_local(confirmed_at, site.tz))),
            )
        )
    tasks = (
        pl.concat(rows)
        .sort(["lgnum", "created_utc", "docid", "tostat"])
        .with_columns(pl.int_range(pl.len()).over("lgnum").alias("seq"))
        .with_columns(
            (pl.col("seq") + 300_000_000_001).cast(pl.String).str.zfill(12).alias("tanum"),
            (pl.col("seq") + 3_000_000_001).cast(pl.String).str.zfill(10).alias("who"),
        )
    )
    return tasks.select(
        pl.lit(MANDT).alias("mandt"),
        "lgnum",
        "tanum",
        pl.lit("0001").alias("tapos"),
        "tostat",
        pl.lit("1011").alias("procty"),
        pl.lit("1").alias("trart"),
        "matid",
        pl.col("menge").alias("vsolm"),
        pl.lit("EA").alias("meins"),
        pl.col("menge").alias("vsola"),
        pl.lit("EA").alias("altme"),
        pl.lit("9010").alias("vltyp"),
        "vlpla",
        pl.col("dest_type").alias("nltyp"),
        pl.col("dest_bin").alias("nlpla"),
        "who",
        pl.lit("0000000000").alias("wave"),  # NUMC initial value: no wave
        pl.lit("PDI").alias("rdoccat"),
        pl.col("docid").alias("rdocid"),
        pl.col("itemid").alias("ritmid"),
        pl.lit("WF-BATCH").alias("created_by"),
        "created_at",
        "created_at_wh",
        "started_at",
        "confirmed_at",
        "confirmed_at_wh",
        "confirmed_by",
        "prsrc",
    )
