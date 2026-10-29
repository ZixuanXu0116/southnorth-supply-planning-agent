"""AutoStore controller logs for Longgang (``raw_ext.autostore_*``) and scenario A1.

The controller logs in site-local time (Asia/Shanghai) without an offset, unlike EWM (UTC).

- Bin presentations: a robot delivers a grid bin to a port; consecutive warehouse tasks on the same bin
  share one presentation (picks from ``AS01`` and decanting putaways into ``AS01``).
- Port status: OPEN and CLOSED intervals per port (planned night maintenance; the A1 fault).
- System status: short STOPPED intervals, which fall into quiet periods without presentations.

The EWM tasks are aligned with the logs: each AutoStore task gets the port of its presentation
(``PRSRC``). During scenario A1, AutoStore picks queue longer on the remaining ports; each delay stays
within the delivery's slack before its goods issue, so ECC is unaffected, and wave pick completion moves
with the tasks.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import polars as pl

from .config import Profile
from .org import SITES
from .scenarios import A1_END, A1_PORTS, A1_START
from .taskshift import advance_wave_completion, goods_issue_slack
from .timeutil import MINUTE, dec15, local_to_utc, minutes, utc_to_local, zfill

PORTS = tuple(f"AS-PORT-{index:02d}" for index in range(1, 13))
ROBOTS = 40
PRESENTATION_GAP = 3 * MINUTE  # a task this soon after the bin's last task uses the same presentation
MAINTENANCE_EVERY_DAYS = 56  # per port, staggered by four days
STOPS_PER_30_DAYS = 1.5
MIN_QUIET_GAP_MIN = 30.0
A1_EXTRA_QUEUE_MIN = (5.0, 40.0)
STOP_REASONS = ("ROBOT_FAULT", "GRID_OBSTRUCTION", "SOFTWARE", "POWER")


@dataclass
class AutoStoreResult:
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


def _closures(profile: Profile, rng: np.random.Generator, tz: str) -> pl.DataFrame:
    """CLOSED intervals per port in UTC: staggered night maintenance plus the A1 fault."""
    rows = []
    first = profile.start
    last = profile.ewm_as_of.date()
    for index, port in enumerate(PORTS):
        day = first + timedelta(days=4 * index + 3)
        while day <= last:
            if not (A1_START <= day <= A1_END and port in A1_PORTS):
                start = datetime.combine(day, datetime.min.time()) + timedelta(
                    hours=float(rng.uniform(2.0, 3.0))
                )
                rows.append(
                    (port, start, start + timedelta(hours=float(rng.uniform(2.0, 3.0))), "MAINTENANCE")
                )
            day += timedelta(days=MAINTENANCE_EVERY_DAYS)
        if port in A1_PORTS and first <= A1_END and last >= A1_START:
            start = datetime.combine(A1_START, datetime.min.time()) + timedelta(hours=6.5)
            end = datetime.combine(A1_END, datetime.min.time()) + timedelta(hours=21.0)
            rows.append((port, start, end, "FAULT"))
    frame = pl.DataFrame(
        rows, schema={"port_id": pl.String, "from_local": pl.Datetime("us"), "to_local": pl.Datetime("us"),
                      "reason": pl.String}, orient="row",
    )  # fmt: skip
    frame = frame.with_columns(pl.col("from_local").dt.truncate("1s"), pl.col("to_local").dt.truncate("1s"))
    return frame.with_columns(
        pl.Series("from_utc", local_to_utc(frame.get_column("from_local").to_numpy(), tz)),
        pl.Series("to_utc", local_to_utc(frame.get_column("to_local").to_numpy(), tz)),
    ).sort(["port_id", "from_utc"])


def _a1_delays(
    profile: Profile,
    rng: np.random.Generator,
    work: pl.DataFrame,
    outbound_tasks: pl.DataFrame,
    deliveries: pl.DataFrame,
    tz: str,
) -> pl.DataFrame:
    """Extra queue time for AutoStore picks created during A1, bounded by the delivery's slack."""
    ewm_as_of = np.datetime64(profile.ewm_as_of.replace(tzinfo=None), "us")
    picks = work.filter(
        (pl.col("vltyp") == "AS01") & pl.col("procty").is_in(["2010", "2020"]) & (pl.col("tostat") == "C")
    ).join(outbound_tasks.select("lgnum", "tanum", "delivery_id"), on=["lgnum", "tanum"], how="inner")
    local_day = pl.Series(utc_to_local(picks.get_column("created_utc").to_numpy(), tz)).dt.date()
    affected = picks.filter(local_day.is_between(A1_START, A1_END))
    if affected.height == 0:
        return pl.DataFrame(schema={"row": pl.UInt32, "delay": pl.Duration("us")})
    all_picks = work.filter(pl.col("procty").is_in(["2010", "2020"]) & (pl.col("tostat") == "C")).join(
        outbound_tasks.select("lgnum", "tanum", "delivery_id"), on=["lgnum", "tanum"], how="inner"
    )
    affected = goods_issue_slack(affected, all_picks, deliveries, ewm_as_of)
    slack = affected.get_column("slack_min").to_numpy()
    wanted = rng.uniform(*A1_EXTRA_QUEUE_MIN, size=affected.height)
    delay = minutes(np.minimum(wanted, slack))
    return affected.select("row").with_columns(pl.Series("delay", delay))


def _presentations(work: pl.DataFrame, rng: np.random.Generator) -> pl.DataFrame:
    started = work.filter(pl.col("started_utc").is_not_null()).with_columns(
        pl.when(pl.col("vltyp") == "AS01").then(pl.col("vlpla")).otherwise(pl.col("nlpla")).alias("bin_id"),
        pl.when(pl.col("vltyp") == "AS01").then(pl.lit("PICK")).otherwise(pl.lit("DECANT")).alias("kind"),
    )
    started = started.sort(["bin_id", "started_utc", "row"]).with_columns(
        pl.col("confirmed_utc").cum_max().shift(1).over("bin_id").alias("previous_end")
    )
    started = started.with_columns(
        (
            pl.col("previous_end").is_null()
            | (pl.col("started_utc") > pl.col("previous_end") + PRESENTATION_GAP)
        )
        .cum_sum()
        .alias("group")
    )
    groups = (
        started.group_by("group")
        .agg(
            pl.col("bin_id").first(),
            pl.col("started_utc").min().alias("first_start"),
            pl.col("confirmed_utc").max().alias("last_end"),
            pl.len().alias("task_count"),
            pl.col("kind").n_unique().alias("kinds"),
            pl.col("kind").first(),
            pl.col("row"),
        )
        .sort(["first_start", "bin_id"])
    )
    count = groups.height
    presented = groups.get_column("first_start").to_numpy() - minutes(rng.uniform(10.0, 45.0, count) / 60.0)
    closed = groups.get_column("last_end").to_numpy() + minutes(rng.uniform(3.0, 20.0, count) / 60.0)
    purpose = np.where(
        groups.get_column("kinds").to_numpy() > 1, "MIXED", groups.get_column("kind").to_numpy()
    )
    return groups.with_columns(
        pl.Series("presented_utc", presented),
        pl.Series("closed_utc", closed),
        pl.Series("purpose", purpose),
        pl.Series("robot_id", [f"R-{robot:03d}" for robot in rng.integers(1, ROBOTS + 1, count)]),
    )


def _assign_ports(
    presentations: pl.DataFrame, closures: pl.DataFrame, rng: np.random.Generator
) -> np.ndarray:
    """Greedy: a random port that is open and free at presentation time, else the one free first."""
    closed = {
        port: (group.get_column("from_utc").to_numpy(), group.get_column("to_utc").to_numpy())
        for (port,), group in closures.group_by("port_id")
    }
    free_at = np.full(len(PORTS), np.datetime64("1970-01-01T00:00:00", "us"))
    starts = presentations.get_column("presented_utc").to_numpy()
    ends = presentations.get_column("closed_utc").to_numpy()
    chosen = np.empty(presentations.height, dtype=object)
    order = rng.random((presentations.height, len(PORTS)))
    for index in range(presentations.height):
        start, end = starts[index], ends[index]
        is_open = np.array(
            [
                not (port in closed and ((closed[port][0] < end) & (closed[port][1] > start)).any())
                for port in PORTS
            ]
        )
        free = is_open & (free_at <= start)
        pool = np.flatnonzero(free) if free.any() else np.flatnonzero(is_open)
        # A free open port at random; if all are busy, the one that frees up first.
        pick = pool[np.argmin(order[index, pool])] if free.any() else pool[np.argmin(free_at[pool])]
        chosen[index] = PORTS[pick]
        free_at[pick] = max(free_at[pick], end)
    return chosen


def _stops(profile: Profile, presentations: pl.DataFrame, rng: np.random.Generator, tz: str) -> pl.DataFrame:
    """Short system stops, placed in quiet gaps between presentations."""
    starts = presentations.get_column("presented_utc").to_numpy()
    ends = presentations.get_column("closed_utc").to_numpy()
    order = np.argsort(starts)
    starts, ends = starts[order], np.maximum.accumulate(ends[order])
    gap_start, gap_end = ends[:-1], starts[1:]
    gap_minutes = (gap_end - gap_start) / MINUTE
    candidates = np.flatnonzero(gap_minutes >= MIN_QUIET_GAP_MIN)
    days = (profile.ewm_as_of.date() - profile.start).days
    wanted = min(candidates.size, round(days * STOPS_PER_30_DAYS / 30))
    chosen = np.sort(rng.choice(candidates, size=wanted, replace=False)) if wanted else np.array([], int)
    room = gap_minutes[chosen] - 10.0
    duration = np.minimum(rng.uniform(5.0, 45.0, chosen.size), room)
    offset = 5.0 + rng.uniform(0.0, 1.0, chosen.size) * (room - duration)
    stopped = gap_start[chosen] + minutes(offset)
    restarted = stopped + minutes(duration)
    return pl.DataFrame(
        {
            "event_id": zfill(np.arange(1, chosen.size + 1), 8),
            "status": "STOPPED",
            "started_at": pl.Series(utc_to_local(stopped, tz), dtype=pl.Datetime("us")).dt.truncate("1s"),
            "ended_at": pl.Series(utc_to_local(restarted, tz), dtype=pl.Datetime("us")).dt.truncate("1s"),
            "reason": rng.choice(STOP_REASONS, size=chosen.size, p=[0.5, 0.25, 0.15, 0.1]),
        }
    )


def _port_status(profile: Profile, closures: pl.DataFrame, tz: str) -> pl.DataFrame:
    """Complete OPEN/CLOSED timeline per port in local time; the current interval has no end."""
    begin = datetime.combine(profile.start, datetime.min.time())
    rows = []
    for port in PORTS:
        cursor = begin
        for row in closures.filter(pl.col("port_id") == port).iter_rows(named=True):
            rows.append((port, "OPEN", cursor, row["from_local"], ""))
            rows.append((port, "CLOSED", row["from_local"], row["to_local"], row["reason"]))
            cursor = row["to_local"]
        rows.append((port, "OPEN", cursor, None, ""))
    frame = pl.DataFrame(
        rows, schema={"port_id": pl.String, "status": pl.String, "valid_from": pl.Datetime("us"),
                      "valid_to": pl.Datetime("us"), "reason": pl.String}, orient="row",
    )  # fmt: skip
    as_of_local = utc_to_local(np.array([np.datetime64(profile.ewm_as_of.replace(tzinfo=None), "us")]), tz)[0]
    frame = frame.filter(pl.col("valid_from") <= as_of_local)
    return frame.with_columns(
        pl.when(pl.col("valid_to") > as_of_local).then(None).otherwise(pl.col("valid_to")).alias("valid_to")
    )


def generate_autostore(
    profile: Profile,
    ordim_c: pl.DataFrame,
    wavehdr: pl.DataFrame,
    outbound_tasks: pl.DataFrame,
    deliveries: pl.DataFrame,
) -> AutoStoreResult:
    rng = np.random.default_rng([profile.seed, 40])
    site = SITES["SZ"]
    tz = site.tz
    ordim_c = ordim_c.with_row_index("row")
    autostore = (pl.col("lgnum") == site.lgnum) & ((pl.col("vltyp") == "AS01") | (pl.col("nltyp") == "AS01"))
    work = ordim_c.filter(autostore).with_columns(
        _parse_dec15("created_at").alias("created_utc"),
        _parse_dec15("started_at").alias("started_utc"),
        _parse_dec15("confirmed_at").alias("confirmed_utc"),
    )

    # Scenario A1: longer queues for AutoStore picks while two ports are down.
    delays = _a1_delays(profile, rng, work, outbound_tasks, deliveries, tz)
    work = (
        work.join(delays, on="row", how="left")
        .with_columns(pl.col("delay").fill_null(pl.duration(microseconds=0)))
        .with_columns(
            (pl.col("started_utc") + pl.col("delay")).alias("started_utc"),
            (pl.col("confirmed_utc") + pl.col("delay")).alias("confirmed_utc"),
        )
    )

    closures = _closures(profile, rng, tz)
    presentations = _presentations(work, rng)
    presentations = presentations.with_columns(
        pl.Series("port_id", _assign_ports(presentations, closures, rng))
    )
    task_port = presentations.select("row", "port_id").explode("row")
    never_started = work.filter(pl.col("started_utc").is_null()).select("row").sort("row")
    fallback = never_started.with_columns(
        pl.Series("port_id", [PORTS[index] for index in rng.integers(0, len(PORTS), never_started.height)])
    )
    ports = pl.concat([task_port, fallback])

    # Write the aligned timestamps and ports back into the task table.
    changed = work.select("row", "delay", "started_utc", "confirmed_utc").join(ports, on="row")
    confirmed = changed.get_column("confirmed_utc").to_numpy()
    started = changed.get_column("started_utc").to_numpy()
    changed = changed.with_columns(
        pl.Series("new_started_at", dec15(started)),
        pl.Series("new_confirmed_at", dec15(confirmed)),
        pl.Series("new_confirmed_at_wh", dec15(utc_to_local(confirmed, tz))),
    )
    ordim_c = ordim_c.join(
        changed.select("row", "port_id", "new_started_at", "new_confirmed_at", "new_confirmed_at_wh"),
        on="row",
        how="left",
    ).with_columns(
        pl.coalesce("port_id", "prsrc").alias("prsrc"),
        pl.coalesce("new_started_at", "started_at").alias("started_at"),
        pl.coalesce("new_confirmed_at", "confirmed_at").alias("confirmed_at"),
        pl.coalesce("new_confirmed_at_wh", "confirmed_at_wh").alias("confirmed_at_wh"),
    )

    # Waves with delayed picks complete later (still before their goods issues).
    wavehdr = advance_wave_completion(ordim_c, wavehdr, delays)
    ordim_c = ordim_c.sort("row").drop(
        "row", "port_id", "new_started_at", "new_confirmed_at", "new_confirmed_at_wh"
    )

    presentation_table = presentations.sort(["presented_utc", "bin_id"]).with_columns(
        zfill(np.arange(1, presentations.height + 1), 12).alias("presentation_id")
    )
    presentation_table = presentation_table.select(
        "presentation_id",
        "port_id",
        "bin_id",
        "robot_id",
        "purpose",
        pl.Series(
            "presented_at", utc_to_local(presentation_table.get_column("presented_utc").to_numpy(), tz)
        ).dt.truncate("1s"),
        pl.Series(
            "closed_at", utc_to_local(presentation_table.get_column("closed_utc").to_numpy(), tz)
        ).dt.truncate("1s"),
        pl.col("task_count").cast(pl.Int32),
    )
    tables = {
        "raw_ext.autostore_bin_presentation": presentation_table,
        "raw_ext.autostore_port_status": _port_status(profile, closures, tz),
        "raw_ext.autostore_system_status": _stops(profile, presentations, rng, tz),
    }
    facts = {"autostore_delays": delays, "autostore_closures": closures}
    return AutoStoreResult(tables=tables, ordim_c=ordim_c, wavehdr=wavehdr, facts=facts)
