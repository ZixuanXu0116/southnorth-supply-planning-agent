"""LTS RFID scans at Round Rock (``raw_ext.lts_rfid_scan``) and late confirmation of emergency picks.

Each warehouse order at Round Rock is picked into one tote with an RFID tag. Readers log the tote when it
leaves the storage area after its last pick (the secure-cage exit when that pick was from storage type 0030,
a pick-area exit otherwise) and when it passes a dock door at truck loading. LTS is a cloud service and logs
UTC. The scan links to EWM through the warehouse order (WHO).

Emergency picks (process type 2020) are often confirmed in EWM only after the rush: a share of them is
confirmed 15-90 minutes after the physical pick, capped before the delivery's goods issue. The exit scan keeps
the physical pick time; EWM ``CONFIRMED_AT`` shows the late confirmation.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

from .config import Profile
from .org import SITES
from .taskshift import advance_wave_completion, goods_issue_slack
from .timeutil import dec15, minutes, utc_to_local, zfill

LATE_SHARE = 0.4
LATE_MINUTES = (15.0, 90.0)
EXIT_BEFORE_CONFIRM_S = (0.0, 90.0)  # a normal pick is confirmed at the exit, seconds after the scan
LOADING_BEFORE_GI_MIN = (2.0, 15.0)
PICK_EXIT_READERS = ("RR-PICK-EXIT-01", "RR-PICK-EXIT-02", "RR-PICK-EXIT-03")
CAGE_READER = "RR-CAGE-EXIT"
DOCK_READERS = ("RR-DOCK-01", "RR-DOCK-02")


@dataclass
class RfidResult:
    tables: dict[str, pl.DataFrame]
    ordim_c: pl.DataFrame
    wavehdr: pl.DataFrame
    facts: dict[str, pl.DataFrame]


def _parse_dec15(column: str) -> pl.Expr:
    return (
        pl.when(pl.col(column) > 0)
        .then(pl.col(column).cast(pl.String).str.strptime(pl.Datetime("us"), "%Y%m%d%H%M%S"))
        .otherwise(None)
    )


def generate_rfid(
    profile: Profile,
    ordim_c: pl.DataFrame,
    wavehdr: pl.DataFrame,
    outbound_tasks: pl.DataFrame,
    deliveries: pl.DataFrame,
) -> RfidResult:
    rng = np.random.default_rng([profile.seed, 50])
    site = SITES["RR"]
    ewm_as_of = np.datetime64(profile.ewm_as_of.replace(tzinfo=None), "us")
    ordim_c = ordim_c.with_row_index("row")
    picks = (
        ordim_c.filter(
            (pl.col("lgnum") == site.lgnum)
            & pl.col("procty").is_in(["2010", "2020"])
            & (pl.col("tostat") == "C")
        )
        .with_columns(
            _parse_dec15("started_at").alias("started_utc"),
            _parse_dec15("confirmed_at").alias("confirmed_utc"),
        )
        .join(outbound_tasks.select("lgnum", "tanum", "delivery_id"), on=["lgnum", "tanum"], how="inner")
        .sort("row")
    )
    count = picks.height

    # Physical pick: the tote leaves the storage area just before the (on-time) confirmation.
    confirmed = picks.get_column("confirmed_utc").to_numpy()
    started = picks.get_column("started_utc").to_numpy()
    exit_scan = confirmed - minutes(rng.uniform(*EXIT_BEFORE_CONFIRM_S, count) / 60.0)
    exit_scan = np.where(~np.isnat(started) & (exit_scan < started), started, exit_scan)

    # Late confirmation of emergency picks, within the delivery's slack before goods issue.
    emergency = picks.filter(pl.col("procty") == "2020")
    late = emergency.filter(pl.Series(rng.random(emergency.height) < LATE_SHARE)).select("row", "delivery_id")
    late = goods_issue_slack(late, picks.select("delivery_id", "confirmed_utc"), deliveries, ewm_as_of)
    wanted = rng.uniform(*LATE_MINUTES, size=late.height)
    late = late.select("row").with_columns(
        pl.Series("delay", minutes(np.minimum(wanted, late.get_column("slack_min").to_numpy())))
    )
    moved = (
        picks.select("row", "confirmed_utc")
        .join(late, on="row")
        .with_columns((pl.col("confirmed_utc") + pl.col("delay")).alias("confirmed_utc"))
    )
    new_confirmed = moved.get_column("confirmed_utc").to_numpy()
    moved = moved.with_columns(
        pl.Series("new_confirmed_at", dec15(new_confirmed)),
        pl.Series("new_confirmed_at_wh", dec15(utc_to_local(new_confirmed, site.tz))),
    )
    ordim_c = ordim_c.join(
        moved.select("row", "new_confirmed_at", "new_confirmed_at_wh"), on="row", how="left"
    )
    ordim_c = ordim_c.with_columns(
        pl.coalesce("new_confirmed_at", "confirmed_at").alias("confirmed_at"),
        pl.coalesce("new_confirmed_at_wh", "confirmed_at_wh").alias("confirmed_at_wh"),
    )
    wavehdr = advance_wave_completion(ordim_c, wavehdr, late)
    ordim_c = ordim_c.sort("row").drop("row", "new_confirmed_at", "new_confirmed_at_wh")

    # One tote per warehouse order: it leaves the storage area after its last physical pick and passes a dock
    # door at truck loading (shipped deliveries only).
    totes = (
        picks.select("who", "delivery_id", "vltyp")
        .with_columns(pl.Series("exit_utc", exit_scan))
        .group_by("who")
        .agg(
            pl.col("exit_utc").max(),
            pl.col("vltyp").sort_by("exit_utc").last().alias("last_type"),
            pl.col("delivery_id").first(),
        )
        .sort("who")
    )
    count = totes.height
    tags = [f"E280{rng.bytes(10).hex().upper()}" for _ in range(count)]
    cage = (totes.get_column("last_type") == "0030").to_numpy()
    exits = totes.select("who").with_columns(
        pl.Series("tag_id", tags),
        pl.Series("reader_id", np.where(cage, CAGE_READER, rng.choice(PICK_EXIT_READERS, size=count))),
        pl.Series("zone", np.where(cage, "CAGE_EXIT", "PICK_EXIT")),
        pl.Series("scanned_utc", totes.get_column("exit_utc").to_numpy()),
    )
    loading = (
        totes.select("who", "delivery_id")
        .with_columns(pl.Series("tag_id", tags))
        .join(deliveries.select("delivery_id", "pgi_utc"), on="delivery_id", how="left")
        .filter(pl.col("pgi_utc").is_not_null() & (pl.col("pgi_utc") <= ewm_as_of))
        .sort("who")
    )
    dock = loading.select("who", "tag_id").with_columns(
        pl.Series("reader_id", rng.choice(DOCK_READERS, size=loading.height)),
        pl.lit("DOCK").alias("zone"),
        pl.Series(
            "scanned_utc",
            loading.get_column("pgi_utc").to_numpy()
            - minutes(rng.uniform(*LOADING_BEFORE_GI_MIN, loading.height)),
        ),
    )
    scans = (
        pl.concat([exits, dock.select(exits.columns)])
        .with_columns(pl.col("scanned_utc").dt.truncate("1s"))
        .sort(["scanned_utc", "tag_id", "zone"])
    )
    scans = scans.select(
        zfill(np.arange(1, scans.height + 1), 12).alias("scan_id"),
        "reader_id",
        "zone",
        "tag_id",
        "who",
        pl.col("scanned_utc").dt.replace_time_zone("UTC").alias("scanned_at"),
    )
    facts = {"rfid_late_confirmations": late, "rfid_totes": totes}
    return RfidResult(tables={"raw_ext.lts_rfid_scan": scans}, ordim_c=ordim_c, wavehdr=wavehdr, facts=facts)
