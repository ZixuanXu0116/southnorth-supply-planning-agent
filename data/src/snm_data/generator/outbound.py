"""Outbound flow: production-order picking lists (CO27) -> deliveries to the production supply area -> EWM
delivery orders -> waves -> pick tasks -> goods issue (311) -> receipt confirmation at the work center.

All intermediate timestamps are naive UTC ``datetime64[us]``. Site-local and ECC-system-local values
are derived only when rendering SAP fields. EWM data is extracted at ``profile.ewm_as_of`` (hourly
replication) and ECC data at ``profile.ecc_as_of`` (nightly extraction), so EWM is fresher than ECC.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass
from datetime import timedelta

import numpy as np
import polars as pl

from .calendar import fiscal_years_of
from .config import Profile
from .manufacturing import PSA_LGORT, STAGING_LEAD_DAYS, ProductionPlan
from .master import BOX_GROUPS, MATERIAL_GROUPS, shift_of
from .org import ECC_SYSTEM_TZ, MANDT, SITES
from .scenarios import (
    A4_PICK_FACTOR,
    A4_QUEUE_FACTOR,
    A4_SITE,
    A4_START,
    A4_WAVE_HOURS,
    A4_WAVE_TEMPLATE,
    A7_MATERIAL_GROUP,
    A7_START,
)
from .timeutil import MINUTE, dats, dec15, guids, local_to_utc, minutes, tims, utc_to_local, zfill

# Random streams besides the main outbound stream (component 10): the stages after the pick tasks draw
# from their own streams, so a change in one site's task times (scenario A4) does not shift the other
# sites' operators, system confirmations, holds, reversals, and transit noise.
OPERATOR_COMPONENT = 13  # operator assignment, one stream per site
GOODS_ISSUE_COMPONENT = 14  # staging, truck, holds, reversals, transit noise, POD confirmation
CONFIRMATION_COMPONENT = 15  # AutoStore ports and system confirmations
QUEUE_MEDIAN_MIN = {"RR": 20.0, "SZ": 10.0, "DG": 15.0}
PICK_MEDIAN_MIN = {"RR": 6.0, "SZ": 4.0, "DG": 5.0}
STAGE_MEDIAN_MIN = {"RR": 60.0, "SZ": 45.0, "DG": 50.0}
CANCEL_RATE = 0.02
REVERSAL_RATE = 0.008
SPLIT_RATE = 0.08
REJECTION_RATE = 0.01
SYSTEM_CONFIRM_RATE = 0.01
# Deliveries created before the site-local cut-off hour are planned for same-day goods issue.
GI_CUTOFF_HOUR = {"RR": 14, "SZ": 16, "DG": 15}
# Site-local departures of the shuttle that takes picked components to the production supply area; the
# goods issue is posted at departure. After the last departure the components leave the next wave day.
PSA_SHUTTLE_HOURS = (7, 9, 11, 13, 15, 17)
# Share of deliveries held (e.g. transport or credit hold), delaying goods issue by 1-3 days.
HOLD_RATE = 0.03
# Deviation of the actual arrival from the planned transit time, in days.
TRANSIT_NOISE_DAYS = np.array([-1, 0, 1, 2])
TRANSIT_NOISE_P = np.array([0.05, 0.75, 0.14, 0.06])


def _naive_utc(value) -> np.datetime64:
    return np.datetime64(value.replace(tzinfo=None), "us")


def _iso_weekday(days: np.ndarray) -> np.ndarray:
    """ISO weekday (1 = Monday) for datetime64[D] values; 1970-01-01 was a Thursday."""
    return (days.astype("int64") + 3) % 7 + 1


@dataclass
class OutboundResult:
    tables: dict[str, pl.DataFrame]
    facts: dict[str, pl.DataFrame]  # internal ground-truth frames used by tests and later eval builders


# --------------------------------------------------------------------------------------------------
# Orders
# --------------------------------------------------------------------------------------------------


def _orders(
    profile: Profile,
    rng: np.random.Generator,
    materials: pl.DataFrame,
    customers: pl.DataFrame,
    plan: ProductionPlan,
):
    """Outbound orders are the manufacturing department's picking lists (CO27, decision record 003): one per
    production order and issuing site, with the order's component lines at that site. Quantities and dates
    come from the plan; the time of day, the work center, and the delivery unit are drawn here."""
    ewm_as_of = _naive_utc(profile.ewm_as_of)
    first = profile.start - timedelta(days=1)
    group_codes = np.array([group.code for group in MATERIAL_GROUPS])
    catalog = materials.select("matnr", "group_index", "has_box", "box_size")
    units = plan.components.select("order_id", "site").unique().join(plan.orders, on="order_id")
    order_frames = []
    item_frames = []
    for code, site in SITES.items():
        subset = units.filter(pl.col("site") == code).sort("order_id")
        count = subset.height
        if count == 0:
            continue
        created_day = subset.get_column("created_day").to_numpy().astype(np.int64)
        day_values = np.datetime64(first) + created_day.astype("timedelta64[D]")
        hour = np.clip(rng.normal(11.0 if site.country == "US" else 13.0, 3.0, count), 6.0, 20.0)
        created = local_to_utc(day_values.astype("datetime64[us]") + minutes(hour * 60.0), site.tz)
        site_customers = customers.filter(pl.col("site") == code)
        weights = site_customers.get_column("size").to_numpy()
        customer = site_customers.get_column("kunnr").to_numpy()[
            rng.choice(site_customers.height, size=count, p=weights / weights.sum())
        ]
        start_day = subset.get_column("start_day").to_numpy().astype(np.int64)
        keep = created <= ewm_as_of
        frame = pl.DataFrame(
            {
                "site": code,
                "created_utc": created[keep],
                "kunnr": customer[keep],
                "rush": ((start_day - created_day) <= 4)[keep],
                "requested_offset_days": np.maximum(1, start_day - STAGING_LEAD_DAYS - created_day)[keep],
                "bstnk": [f"CO27-{value}" for value in subset.get_column("aufnr").to_numpy()[keep]],
                "plan_order_id": subset.get_column("order_id").to_numpy()[keep],
                "aufnr": subset.get_column("aufnr").to_numpy()[keep],
            }
        ).with_row_index("site_order_index")
        order_frames.append(frame)
        lines = (
            plan.components.filter(pl.col("site") == code)
            .join(
                frame.select("plan_order_id", "site_order_index"),
                left_on="order_id",
                right_on="plan_order_id",
            )
            .join(catalog, on="matnr")
            .sort(["site_order_index", "line"])
        )
        line_count = lines.height
        base_qty = lines.get_column("qty").to_numpy().astype(float)
        has_box = lines.get_column("has_box").to_numpy()
        box_size = np.maximum(lines.get_column("box_size").to_numpy().astype(float), 1.0)
        group_index = lines.get_column("group_index").to_numpy()
        use_box = (
            has_box
            & np.isin(group_codes[group_index], list(BOX_GROUPS))
            & (np.mod(base_qty, box_size) == 0)
            & (rng.random(line_count) < 0.5)
        )
        item_frames.append(
            pl.DataFrame(
                {
                    "site": code,
                    "site_order_index": lines.get_column("site_order_index").to_numpy().astype("uint32"),
                    "matnr": lines.get_column("matnr").to_numpy(),
                    "sales_qty": np.where(use_box, base_qty / box_size, base_qty),
                    "base_qty": base_qty,
                    "vrkme": np.where(use_box, "BOX", "EA"),
                    "umvkz": np.where(use_box, box_size, 1).astype(np.int64),
                    "rejected": np.zeros(line_count, dtype=bool),
                    "line_no": np.arange(line_count),
                    "rspos": lines.get_column("line").to_numpy().astype(np.int64),
                }
            )
        )
    orders = pl.concat(order_frames).sort(["created_utc", "site", "site_order_index"])
    orders = orders.with_row_index("order_id").with_columns(pl.col("aufnr").alias("vbeln_so"))
    items = pl.concat(item_frames).join(
        orders.select("site", "site_order_index", "order_id", "vbeln_so", "plan_order_id"),
        on=["site", "site_order_index"],
    )
    items = items.sort(["order_id", "line_no"]).with_columns(
        (pl.int_range(pl.len()).over("order_id") + 1).alias("item_seq")
    )
    items = items.with_columns(pl.col("rspos").cast(pl.String).str.zfill(6).alias("posnr_so"))
    return orders, _a7_sales_units(items, orders, materials)


def _a7_sales_units(items: pl.DataFrame, orders: pl.DataFrame, materials: pl.DataFrame) -> pl.DataFrame:
    """Scenario A7: the group is sold by the piece before the start date and in whole boxes from it.

    Only sales-unit fields change (no random draws); base-unit quantities stay as generated.
    """
    local_day = pl.lit(None, dtype=pl.Date)
    for code, site in SITES.items():
        local = pl.col("created_utc").dt.replace_time_zone("UTC").dt.convert_time_zone(site.tz).dt.date()
        local_day = pl.when(pl.col("site") == code).then(local).otherwise(local_day)
    group = materials.filter(pl.col("matkl") == A7_MATERIAL_GROUP).select("matnr", "has_box", "box_size")
    flagged = (
        items.join(orders.select("order_id", "created_utc"), on="order_id", how="left")
        .with_columns(local_day.alias("order_day"))
        .join(group, on="matnr", how="left")
    )
    in_group = pl.col("has_box").fill_null(False)
    before = in_group & (pl.col("order_day") < A7_START)
    whole_boxes = (
        in_group & (pl.col("order_day") >= A7_START) & ((pl.col("base_qty") % pl.col("box_size")) == 0)
    )
    flagged = flagged.with_columns(
        pl.when(before)
        .then(pl.col("base_qty"))
        .when(whole_boxes)
        .then(pl.col("base_qty") / pl.col("box_size"))
        .otherwise(pl.col("sales_qty"))
        .alias("sales_qty"),
        pl.when(before)
        .then(pl.lit("EA"))
        .when(whole_boxes)
        .then(pl.lit("BOX"))
        .otherwise(pl.col("vrkme"))
        .alias("vrkme"),
        pl.when(before)
        .then(pl.lit(1))
        .when(whole_boxes)
        .then(pl.col("box_size"))
        .otherwise(pl.col("umvkz"))
        .cast(items.schema["umvkz"])
        .alias("umvkz"),
    )
    return flagged.select(items.columns).sort(["order_id", "line_no"])


# --------------------------------------------------------------------------------------------------
# Deliveries
# --------------------------------------------------------------------------------------------------


def _deliveries(
    profile: Profile,
    rng: np.random.Generator,
    orders: pl.DataFrame,
    items: pl.DataFrame,
    customers: pl.DataFrame,
):
    ewm_as_of = _naive_utc(profile.ewm_as_of)
    live = items.filter(~pl.col("rejected"))
    item_counts = live.group_by("order_id").len().rename({"len": "n_items"})
    orders = orders.join(item_counts, on="order_id", how="inner").sort("order_id")
    split = (orders.get_column("n_items").to_numpy() >= 2) & (rng.random(orders.height) < SPLIT_RATE)
    orders = orders.with_columns(pl.Series("split", split))
    live = live.join(orders.select("order_id", "split"), on="order_id").sort(["order_id", "item_seq"])
    group = np.where(
        live.get_column("split").to_numpy() & (live.get_column("item_seq").to_numpy() > 1),
        rng.integers(0, 2, size=live.height),
        0,
    )
    live = live.with_columns(pl.Series("delivery_group", group))
    # Ensure split orders really have two groups: the last item moves to group 1 if none did.
    has_second = live.group_by("order_id").agg(pl.col("delivery_group").max().alias("max_group"))
    live = (
        live.join(has_second, on="order_id")
        .with_columns(
            pl.when(
                pl.col("split")
                & (pl.col("max_group") == 0)
                & (pl.col("item_seq") == pl.col("item_seq").max().over("order_id"))
            )
            .then(1)
            .otherwise(pl.col("delivery_group"))
            .alias("delivery_group")
        )
        .drop("max_group")
        .sort(["order_id", "item_seq"])
    )

    headers = (
        live.select("order_id", "delivery_group")
        .unique()
        .join(orders, on="order_id")
        .sort(["order_id", "delivery_group"])
    )
    count = headers.height
    rush = headers.get_column("rush").to_numpy()
    delay = np.where(
        rush,
        rng.uniform(5.0, 120.0, size=count),
        np.minimum(rng.lognormal(np.log(360.0), 0.8, size=count), 3 * 1440.0),
    )
    delay = delay + headers.get_column("delivery_group").to_numpy() * rng.uniform(
        2 * 1440.0, 6 * 1440.0, size=count
    )
    created = headers.get_column("created_utc").to_numpy() + minutes(delay)
    headers = headers.with_columns(pl.Series("dlv_created_utc", created)).filter(
        pl.col("dlv_created_utc") <= ewm_as_of
    )
    headers = (
        headers.sort(["dlv_created_utc", "order_id", "delivery_group"])
        .with_row_index("delivery_id")
        .with_columns(zfill(80_000_000 + np.arange(1, headers.height + 1), 10).alias("vbeln"))
    )
    headers = headers.join(customers.select("kunnr", "land1", "pod_required"), on="kunnr").sort("delivery_id")

    planned = []
    for code, site in SITES.items():
        subset = headers.filter(pl.col("site") == code)
        # The planned goods issue is the staging date of the production order (never before the next day
        # when the request arrives after the site's cut-off); the production supply area is on site.
        local = utc_to_local(subset.get_column("dlv_created_utc").to_numpy(), site.tz)
        local_day = local.astype("datetime64[D]")
        after_cutoff = (local - local_day.astype("datetime64[us]")) >= GI_CUTOFF_HOUR[code] * 60 * MINUTE
        order_day = utc_to_local(subset.get_column("created_utc").to_numpy(), site.tz).astype("datetime64[D]")
        requested = order_day + subset.get_column("requested_offset_days").to_numpy().astype("timedelta64[D]")
        earliest = local_day + (after_cutoff & ~subset.get_column("rush").to_numpy()).astype("timedelta64[D]")
        wadat = np.maximum(requested, earliest)
        if 7 not in site.wave_days:
            wadat = wadat + (_iso_weekday(wadat) == 7).astype("timedelta64[D]")
        transit = np.zeros(subset.height, dtype=np.int64)
        planned.append(
            subset.select("delivery_id").with_columns(
                pl.Series("planned_gi_date", wadat), pl.Series("transit_days", transit)
            )
        )
    headers = headers.join(pl.concat(planned), on="delivery_id").sort("delivery_id")
    dlv_items = live.join(
        headers.select("order_id", "delivery_group", "delivery_id", "vbeln"),
        on=["order_id", "delivery_group"],
    )
    dlv_items = (
        dlv_items.sort(["delivery_id", "item_seq"])
        .with_columns(
            ((pl.int_range(pl.len()).over("delivery_id") + 1) * 10)
            .cast(pl.String)
            .str.zfill(6)
            .alias("posnr")
        )
        .with_row_index("delivery_item_id")
    )
    return headers, dlv_items


# --------------------------------------------------------------------------------------------------
# Waves and warehouse tasks
# --------------------------------------------------------------------------------------------------


def _a4_policy(local_days: np.ndarray, site) -> np.ndarray:
    """Site-local days on which the twice-daily wave policy of scenario A4 applies."""
    if site.code != A4_SITE:
        return np.zeros(local_days.shape, dtype=bool)
    return local_days.astype("datetime64[D]") >= np.datetime64(A4_START)


def _wave_slots(created_utc: np.ndarray, site) -> np.ndarray:
    """First wave slot at least 15 minutes after delivery creation, honoring wave hours and days."""
    local = utc_to_local(created_utc + 15 * MINUTE, site.tz)
    day = local.astype("datetime64[D]")
    minute_of_day = (local - day.astype("datetime64[us]")) / MINUTE
    slot = np.ceil(minute_of_day / site.wave_minutes) * site.wave_minutes
    first = site.wave_first_hour * 60
    last = site.wave_last_hour * 60 + (60 - site.wave_minutes)
    slot = np.where(slot < first, first, slot)
    roll = slot > last
    day = np.where(roll, day + np.timedelta64(1, "D"), day)
    slot = np.where(roll, first, slot)
    for _ in range(3):
        closed = ~np.isin(_iso_weekday(day), site.wave_days)
        day = np.where(closed, day + np.timedelta64(1, "D"), day)
        slot = np.where(closed, first, slot)
    return local_to_utc(day.astype("datetime64[us]") + minutes(slot), site.tz)


def _a4_slots(slots_utc: np.ndarray, site) -> np.ndarray:
    """Scenario A4: the regular slot's wave under the twice-daily policy (the first of ``A4_WAVE_HOURS`` at
    or after it, else the next wave day's first), unchanged for other sites and before ``A4_START``.

    Mapping the regular slots instead of the creation times keeps the slot count, and with it the random
    draws of the other sites, identical to the data without the scenario.
    """
    local = utc_to_local(slots_utc, site.tz)
    day = local.astype("datetime64[D]")
    policy = _a4_policy(day, site)
    if not policy.any():
        return slots_utc
    minute_of_day = (local - day.astype("datetime64[us]")) / MINUTE
    fixed = np.array([hour * 60.0 for hour in A4_WAVE_HOURS])
    index = np.minimum(np.searchsorted(fixed, minute_of_day), fixed.size - 1)
    slot = np.where(minute_of_day <= fixed[-1], fixed[index], fixed[-1] + 1)
    roll = slot > fixed[-1]
    day = np.where(roll, day + np.timedelta64(1, "D"), day)
    slot = np.where(roll, fixed[0], slot)
    for _ in range(3):
        closed = ~np.isin(_iso_weekday(day), site.wave_days)
        day = np.where(closed, day + np.timedelta64(1, "D"), day)
        slot = np.where(closed, fixed[0], slot)
    mapped = local_to_utc(day.astype("datetime64[us]") + minutes(slot), site.tz)
    return np.where(policy, mapped, slots_utc)


def assign_operators(
    rng: np.random.Generator,
    roster: pl.DataFrame,
    site_code: str,
    lgnum: str,
    confirm_utc: np.ndarray,
    team_name: str = "OUTB",
) -> np.ndarray:
    """Pick an active operator on the matching shift (mostly from ``team_name``) for each confirmation."""
    site = SITES[site_code]
    local = utc_to_local(confirm_utc, site.tz)
    local_day = local.astype("datetime64[D]")
    hour = ((local - local_day.astype("datetime64[us]")) / np.timedelta64(3_600_000_000, "us")).astype(int)
    # Night shift hours after midnight belong to the shift that started the previous day.
    shifts = shift_of(site_code, hour)
    staff = roster.filter(pl.col("lgnum") == lgnum)
    uname = staff.get_column("sap_uname").to_numpy()
    team = staff.get_column("team").to_numpy()
    shift = staff.get_column("shift").to_numpy()
    first = staff.get_column("active_from").to_numpy().astype("datetime64[D]")
    last = staff.get_column("active_to").to_numpy().astype("datetime64[D]")
    last = np.where(np.isnat(last), np.datetime64("2100-01-01"), last)
    core = np.flatnonzero((team == team_name) & (last == np.datetime64("2100-01-01")))
    result = np.empty(confirm_utc.size, dtype=object)
    pending = np.arange(confirm_utc.size)
    want_team = rng.random(confirm_utc.size) < 0.8
    for _ in range(30):
        if pending.size == 0:
            break
        candidate = rng.integers(0, uname.size, size=pending.size)
        ok = (
            (shift[candidate] == shifts[pending])
            & (first[candidate] <= local_day[pending])
            & (last[candidate] >= local_day[pending])
            & ((team[candidate] == team_name) | ~want_team[pending])
        )
        result[pending[ok]] = uname[candidate[ok]]
        pending = pending[~ok]
    if pending.size:
        same_shift_core = {s: core[shift[core] == s] for s in np.unique(shift[core])}
        for index in pending:
            pool = same_shift_core.get(shifts[index], core)
            result[index] = uname[pool[rng.integers(pool.size)]]
    return result


def _tasks(
    profile: Profile,
    rng: np.random.Generator,
    headers: pl.DataFrame,
    dlv_items: pl.DataFrame,
    slot_table: pl.DataFrame,
    roster: pl.DataFrame,
    materials: pl.DataFrame,
):
    ewm_as_of = _naive_utc(profile.ewm_as_of)
    wave_frames = []
    task_frames = []
    for code, site in SITES.items():
        subset = headers.filter(pl.col("site") == code)
        if subset.height == 0:
            continue
        # Random draws are made per regular (pre-A4) slot and line, so the other sites' data does not
        # depend on the scenario: A4 then merges regular slots into twice-daily waves (a merged wave keeps
        # its first slot's jitter), and tasks whose wave is released after the extraction are dropped at
        # the end, after every draw.
        # Picking lists are released on the planned goods-issue day (the staging date of the production
        # order), never before the delivery exists.
        planned_start = local_to_utc(
            subset.get_column("planned_gi_date").to_numpy().astype("datetime64[us]"), site.tz
        )
        regular = _wave_slots(
            np.maximum(subset.get_column("dlv_created_utc").to_numpy(), planned_start), site
        )
        unique_regular = np.unique(regular)
        jitter = rng.uniform(0.0, 3.0, size=unique_regular.size)
        regular_frame = pl.DataFrame(
            {
                "regular_utc": unique_regular,
                "regular_released_utc": unique_regular + minutes(jitter),
                "slot_utc": _a4_slots(unique_regular, site),
                "jitter": jitter,
            }
        )
        slot_jitter = (
            regular_frame.group_by("slot_utc", maintain_order=True)
            .agg(pl.col("jitter").first())
            .sort("slot_utc")
        )
        unique_slots = slot_jitter.get_column("slot_utc").to_numpy()
        waves = pl.DataFrame(
            {
                "lgnum": site.lgnum,
                "slot_utc": unique_slots,
                "released_utc": unique_slots + minutes(slot_jitter.get_column("jitter").to_numpy()),
                "wave": zfill(1_000_000 + np.arange(1, unique_slots.size + 1), 10),
            }
        )
        wave_frames.append(
            waves.filter(pl.col("released_utc") <= ewm_as_of).with_columns(pl.lit(code).alias("site"))
        )
        assignments = (
            subset.select("delivery_id", "rush")
            .with_columns(pl.Series("regular_utc", regular))
            .join(regular_frame.select("regular_utc", "regular_released_utc", "slot_utc"), on="regular_utc")
            .filter(pl.col("regular_released_utc") <= ewm_as_of)
            .join(waves, on="slot_utc", how="inner")
            .drop("regular_utc", "regular_released_utc")
        )
        lines = (
            dlv_items.join(assignments, on="delivery_id", how="inner")
            .join(
                slot_table.filter(pl.col("lgnum") == site.lgnum).select("matnr", "lgtyp", "lgpla"),
                on="matnr",
                how="left",
            )
            .sort("delivery_item_id")
        )
        count = lines.height
        if count == 0:
            continue
        rush = lines.get_column("rush").to_numpy()
        lgtyp = lines.get_column("lgtyp").to_numpy()
        created = lines.get_column("released_utc").to_numpy() + minutes(rng.uniform(0.02, 1.0, size=count))
        # A4: fewer, larger waves queue tasks longer; batched picks are faster.
        policy = _a4_policy(utc_to_local(lines.get_column("slot_utc").to_numpy(), site.tz), site)
        queue_median = QUEUE_MEDIAN_MIN[code] * np.where(rush, 1 / 3, 1.0)
        queue_median = np.where(policy, queue_median * A4_QUEUE_FACTOR, queue_median)
        pick_median = np.full(count, PICK_MEDIAN_MIN[code])
        pick_median = np.where(policy, pick_median * A4_PICK_FACTOR, pick_median)
        pick_median = np.where(lgtyp == "0030", pick_median * 2.0, pick_median)
        pick_median = np.where(lgtyp == "AS01", 1.5, pick_median)
        queue = rng.lognormal(np.log(queue_median), 0.6)
        duration = rng.lognormal(np.log(pick_median), 0.5)
        cancelled = rng.random(count) < CANCEL_RATE
        cancel_time = created + minutes(queue)
        cancel_started = np.where(
            rng.random(count) < 0.4, created + minutes(queue * 0.5), np.datetime64("NaT")
        )
        second_created = cancel_time + minutes(rng.uniform(1.0, 10.0, size=count))
        second_queue = rng.lognormal(np.log(queue_median), 0.6)
        second_duration = rng.lognormal(np.log(pick_median), 0.5)
        final_created = np.where(cancelled, second_created, created)
        final_started = final_created + minutes(np.where(cancelled, second_queue, queue))
        final_confirmed = final_started + minutes(np.where(cancelled, second_duration, duration))

        base = lines.select(
            "delivery_item_id",
            "delivery_id",
            "matnr",
            "base_qty",
            "sales_qty",
            "vrkme",
            "wave",
            "lgtyp",
            "lgpla",
        ).with_columns(
            pl.lit(site.lgnum).alias("lgnum"),
            pl.lit(code).alias("site"),
            pl.Series("extracted", lines.get_column("released_utc").to_numpy() <= ewm_as_of),
        )
        primary = base.with_columns(
            pl.Series("created_utc", np.where(cancelled, created, final_created)),
            pl.Series("started_utc", np.where(cancelled, cancel_started, final_started)),
            pl.Series("confirmed_utc", np.where(cancelled, cancel_time, final_confirmed)),
            pl.Series("tostat", np.where(cancelled, "A", "C")),
            pl.Series("is_final", ~cancelled),
            pl.Series("procty", np.where(rush, "2020", "2010")),
        )
        replacements = base.filter(pl.Series(cancelled)).with_columns(
            pl.Series("created_utc", final_created[cancelled]),
            pl.Series("started_utc", final_started[cancelled]),
            pl.Series("confirmed_utc", final_confirmed[cancelled]),
            pl.lit("C").alias("tostat"),
            pl.lit(True).alias("is_final"),
            pl.Series("procty", np.where(rush[cancelled], "2020", "2010")),
        )
        task_frames.append(pl.concat([primary, replacements]))
    waves = pl.concat(wave_frames)
    tasks = pl.concat(task_frames)

    confirmed = tasks.get_column("confirmed_utc").to_numpy()
    by = np.empty(tasks.height, dtype=object)
    resource = np.empty(tasks.height, dtype=object)
    lgtyp = tasks.get_column("lgtyp").to_numpy()
    site_codes = tasks.get_column("site").to_numpy()
    for index, (code, site) in enumerate(SITES.items()):
        mask = site_codes == code
        if not mask.any():
            continue
        site_rng = np.random.default_rng([profile.seed, OPERATOR_COMPONENT, index])
        names = assign_operators(site_rng, roster, code, site.lgnum, confirmed[mask])
        by[mask] = names
        resource[mask] = [f"RF-{site.lgnum}-{zlib.crc32(name.encode()) % 40 + 1:03d}" for name in names]
    autostore = lgtyp == "AS01"
    by[autostore] = "AS-INTERFACE"
    confirmation_rng = np.random.default_rng([profile.seed, CONFIRMATION_COMPONENT])
    ports = confirmation_rng.integers(1, 13, size=int(autostore.sum()))
    resource[autostore] = [f"AS-PORT-{port:02d}" for port in ports]
    system = ~autostore & (confirmation_rng.random(tasks.height) < SYSTEM_CONFIRM_RATE)
    by[system] = "RFC-EWM"
    resource[system] = "RFC"
    tasks = tasks.with_columns(pl.Series("confirmed_by", by), pl.Series("prsrc", resource))
    tasks = (
        tasks.filter(pl.col("extracted"))
        .drop("extracted")
        .sort(["lgnum", "created_utc", "delivery_item_id", "tostat"])
        .with_columns(
            (pl.int_range(pl.len()).over("lgnum") + 100_000_000_001)
            .cast(pl.String)
            .str.zfill(12)
            .alias("tanum"),
            pl.col("delivery_id").rank("dense").over("lgnum").cast(pl.Int64).alias("who_seq"),
        )
        .with_columns((pl.col("who_seq") + 2_000_000_000).cast(pl.String).str.zfill(10).alias("who"))
    )
    return waves, tasks


# --------------------------------------------------------------------------------------------------
# Goods issue, POD, and status
# --------------------------------------------------------------------------------------------------


def _next_shuttle(ready_utc: np.ndarray, site) -> np.ndarray:
    """Departure of the first shuttle to the production supply area at or after ``ready_utc``: at the
    site-local ``PSA_SHUTTLE_HOURS`` on wave days, else the next wave day's first departure."""
    valid = ~np.isnat(ready_utc)
    ready = np.where(valid, ready_utc, np.datetime64("2000-01-01T00:00:00", "us"))
    local = utc_to_local(ready, site.tz)
    day = local.astype("datetime64[D]")
    minute_of_day = (local - day.astype("datetime64[us]")) / MINUTE
    fixed = np.array([hour * 60.0 for hour in PSA_SHUTTLE_HOURS])
    index = np.minimum(np.searchsorted(fixed, minute_of_day), fixed.size - 1)
    slot = np.where(minute_of_day <= fixed[-1], fixed[index], fixed[-1] + 1)
    roll = slot > fixed[-1]
    day = np.where(roll, day + np.timedelta64(1, "D"), day)
    slot = np.where(roll, fixed[0], slot)
    for _ in range(3):
        closed = ~np.isin(_iso_weekday(day), site.wave_days)
        day = np.where(closed, day + np.timedelta64(1, "D"), day)
        slot = np.where(closed, fixed[0], slot)
    departure = local_to_utc(day.astype("datetime64[us]") + minutes(slot), site.tz)
    return np.where(valid, departure, np.datetime64("NaT"))


def _goods_issue(
    profile: Profile,
    headers: pl.DataFrame,
    tasks: pl.DataFrame,
    dlv_items: pl.DataFrame,
    materials: pl.DataFrame,
):
    rng = np.random.default_rng([profile.seed, GOODS_ISSUE_COMPONENT])
    ewm_as_of = _naive_utc(profile.ewm_as_of)
    ecc_as_of = _naive_utc(profile.ecc_as_of)
    finals = tasks.filter(pl.col("is_final"))
    item_done = finals.select("delivery_item_id", "delivery_id", pl.col("confirmed_utc").alias("picked_utc"))
    per_delivery = (
        dlv_items.group_by("delivery_id")
        .len()
        .rename({"len": "n_dlv_items"})
        .join(
            item_done.group_by("delivery_id").agg(
                pl.len().alias("n_task_items"), pl.col("picked_utc").max().alias("pick_complete_utc")
            ),
            on="delivery_id",
            how="left",
        )
    )
    frame = headers.join(per_delivery, on="delivery_id", how="left").sort("delivery_id")
    count = frame.height
    stage_median = np.array([STAGE_MEDIAN_MIN[code] for code in frame.get_column("site").to_numpy()])
    complete = frame.get_column("pick_complete_utc").to_numpy()
    fully_picked = (
        frame.get_column("n_task_items").fill_null(0).to_numpy() == frame.get_column("n_dlv_items").to_numpy()
    )
    fully_picked &= ~np.isnat(complete) & (complete <= ewm_as_of)
    ready = complete + minutes(rng.lognormal(np.log(stage_median), 0.5))
    # The picked components leave with the next shuttle to the production supply area; a few deliveries
    # are held for one to three days (a discrepancy found at staging).
    loading = rng.uniform(0.0, 20.0, size=count)
    held = rng.random(count) < HOLD_RATE
    hold_delay = np.where(held, rng.uniform(1440.0, 3 * 1440.0, size=count), 0.0)
    departure = np.full(count, np.datetime64("NaT"), dtype="datetime64[us]")
    sites = frame.get_column("site").to_numpy()
    for code, site in SITES.items():
        mask = sites == code
        departure[mask] = _next_shuttle(ready[mask] + minutes(hold_delay[mask]), site) + minutes(
            loading[mask]
        )
    pgi = np.where(fully_picked, departure, np.datetime64("NaT"))
    reversed_ = (~np.isnat(pgi)) & (rng.random(count) < REVERSAL_RATE)
    reversal = np.where(reversed_, pgi + minutes(rng.uniform(60.0, 2880.0, size=count)), np.datetime64("NaT"))
    repost = np.where(
        reversed_, reversal + minutes(rng.uniform(60.0, 1440.0, size=count)), np.datetime64("NaT")
    )
    final_pgi = np.where(reversed_, repost, pgi)
    noise_days = rng.choice(TRANSIT_NOISE_DAYS, size=count, p=TRANSIT_NOISE_P)
    transit = frame.get_column("transit_days").to_numpy()
    # The receipt at the production supply area is confirmed by the work center within hours (the proof of
    # delivery of an internal delivery); transit is zero, the noise keeps a few confirmations on later days.
    delay = (transit + np.maximum(noise_days, 0)).astype("timedelta64[D]").astype("timedelta64[us]")
    arrival = final_pgi + delay
    pod_confirm = arrival + minutes(rng.uniform(0.0, 8 * 60.0, size=count))
    frame = frame.with_columns(
        pl.Series("pgi_utc", pgi),
        pl.Series("reversal_utc", reversal),
        pl.Series("repost_utc", repost),
        pl.Series("final_pgi_utc", final_pgi),
        pl.Series("held", held),
        pl.Series("pod_utc", arrival),
        pl.Series("pod_confirmed_utc", pod_confirm),
    )

    events = []
    for kind, column in (("GI", "pgi_utc"), ("REV", "reversal_utc"), ("REPOST", "repost_utc")):
        subset = frame.filter(pl.col(column).is_not_null() & (pl.col(column) <= ecc_as_of))
        events.append(
            subset.select("delivery_id", "vbeln", "site", pl.col(column).alias("event_utc")).with_columns(
                pl.lit(kind).alias("kind")
            )
        )
    events = (
        pl.concat(events)
        .sort(["event_utc", "delivery_id", "kind"])
        .with_row_index("doc_seq")
        .with_columns((pl.col("doc_seq") + 4_900_000_001).cast(pl.String).str.zfill(10).alias("mblnr"))
    )
    return frame, events


def _material_documents(
    events: pl.DataFrame, dlv_items: pl.DataFrame, headers: pl.DataFrame, valuation: pl.DataFrame
):
    frames = []
    for code, site in SITES.items():
        subset = events.filter(pl.col("site") == code)
        if subset.height == 0:
            continue
        event_utc = subset.get_column("event_utc").to_numpy()
        local = utc_to_local(event_utc, site.tz)
        system = utc_to_local(event_utc, ECC_SYSTEM_TZ)
        frames.append(
            subset.with_columns(
                dats(local).alias("budat"),
                dats(system).alias("cpudt"),
                tims(system).alias("cputm"),
                pl.Series("mjahr", fiscal_years_of(local.astype("datetime64[D]")).astype(str)),
                pl.lit(site.werks).alias("werks"),
                pl.lit(site.lgort).alias("lgort"),
                pl.lit(site.waers).alias("waers"),
            )
        )
    docs = pl.concat(frames).sort("doc_seq")
    mkpf = docs.select(
        pl.lit(MANDT).alias("mandt"),
        "mblnr",
        "mjahr",
        pl.lit("WL").alias("vgart"),
        pl.lit("WL").alias("blart"),
        pl.col("budat").alias("bldat"),
        "budat",
        "cpudt",
        "cputm",
        pl.lit("RFC-EWM").alias("usnam"),
        pl.col("vbeln").alias("xblnr"),
        pl.col("vbeln").alias("le_vbeln"),
    )
    lines = (
        docs.join(
            dlv_items.select("delivery_id", "posnr", "matnr", "base_qty", "sales_qty", "vrkme"),
            on="delivery_id",
        )
        .join(headers.select("delivery_id", "kunnr"), on="delivery_id")
        .join(
            valuation.select(pl.col("matnr"), pl.col("bwkey").alias("werks"), "stprs"),
            on=["matnr", "werks"],
            how="left",
        )
        .sort(["doc_seq", "posnr"])
        .with_columns(
            # The item number of the issuing item; the automatic receiving item follows it.
            (pl.int_range(pl.len()).over("doc_seq") * 2 + 1).cast(pl.String).str.zfill(4).alias("zeile")
        )
    )
    original = lines.filter(pl.col("kind") == "GI").select(
        "delivery_id",
        "posnr",
        pl.col("mblnr").alias("smbln"),
        pl.col("mjahr").alias("sjahr"),
        pl.col("zeile").alias("smblp"),
    )
    lines = lines.join(original, on=["delivery_id", "posnr"], how="left").with_columns(
        pl.when(pl.col("kind") == "REV").then(pl.col("smbln")).otherwise(pl.lit("")).alias("smbln"),
        pl.when(pl.col("kind") == "REV").then(pl.col("sjahr")).otherwise(pl.lit("0000")).alias("sjahr"),
        pl.when(pl.col("kind") == "REV").then(pl.col("smblp")).otherwise(pl.lit("0000")).alias("smblp"),
    )
    lines = lines.join(headers.select("delivery_id", "aufnr"), on="delivery_id").sort(["doc_seq", "posnr"])
    reversal = pl.col("kind") == "REV"
    common = [
        pl.lit(MANDT).alias("mandt"),
        "mblnr",
        "mjahr",
        pl.when(reversal).then(pl.lit("312")).otherwise(pl.lit("311")).alias("bwart"),
        "matnr",
        "werks",
        pl.lit("").alias("sobkz"),
        pl.lit("").alias("insmk"),
        pl.col("werks").alias("umwrk"),
        pl.col("base_qty").alias("menge"),
        pl.lit("EA").alias("meins"),
        pl.col("sales_qty").alias("erfmg"),
        pl.col("vrkme").alias("erfme"),
        (pl.col("base_qty") * pl.col("stprs")).round(2).alias("dmbtr"),
        "waers",
        pl.lit("").alias("lifnr"),
        pl.lit("").alias("ebeln"),
        pl.lit("00000").alias("ebelp"),
        pl.lit("").alias("kostl"),
        "aufnr",
        "kunnr",
        pl.col("vbeln").alias("vbeln_im"),
        pl.col("posnr").alias("vbelp_im"),
        "smbln",
        "sjahr",
        "smblp",
        pl.col("budat").alias("budat_mkpf"),
        pl.col("cpudt").alias("cpudt_mkpf"),
        pl.col("cputm").alias("cputm_mkpf"),
    ]
    next_item = (pl.col("zeile").cast(pl.Int64) + 1).cast(pl.String).str.zfill(4)
    # Issuing item: the warehouse (or the overflow hub) for a goods issue, the production supply area for a
    # reversal; the receiving item is the automatic counterpart. A reversal item references the item of the
    # original document that it reverses.
    issuing = lines.with_columns(
        pl.lit("H").alias("shkzg"),
        pl.when(reversal).then(pl.lit(PSA_LGORT)).otherwise(pl.col("lgort")).alias("lgort"),
        pl.when(reversal).then(pl.col("lgort")).otherwise(pl.lit(PSA_LGORT)).alias("umlgo"),
        pl.lit("").alias("xauto"),
    )
    receiving = lines.with_columns(
        next_item.alias("zeile"),
        pl.when(reversal).then(next_item.alias("smblp")).otherwise(pl.col("smblp")).alias("smblp"),
        pl.lit("S").alias("shkzg"),
        pl.when(reversal).then(pl.col("lgort")).otherwise(pl.lit(PSA_LGORT)).alias("lgort"),
        pl.when(reversal).then(pl.lit(PSA_LGORT)).otherwise(pl.col("lgort")).alias("umlgo"),
        pl.lit("X").alias("xauto"),
    )
    columns = [
        *common[:3], pl.col("zeile"), *common[3:4], pl.col("shkzg"), *common[4:6], pl.col("lgort"),
        *common[6:8], pl.col("xauto"), *common[8:9], pl.col("umlgo"), *common[9:],
    ]  # fmt: skip
    mseg = pl.concat([issuing.select(columns), receiving.select(columns)]).sort(["mblnr", "zeile"])
    return mkpf, mseg


def generate_outbound(
    profile: Profile,
    materials: pl.DataFrame,
    customers: pl.DataFrame,
    slot_table: pl.DataFrame,
    product_keys: pl.DataFrame,
    roster: pl.DataFrame,
    valuation: pl.DataFrame,
    marm: pl.DataFrame,
    plan: ProductionPlan,
) -> OutboundResult:
    rng = np.random.default_rng([profile.seed, 10])
    ewm_as_of = _naive_utc(profile.ewm_as_of)
    ecc_as_of = _naive_utc(profile.ecc_as_of)

    orders, order_items = _orders(profile, rng, materials, customers, plan)
    headers, dlv_items = _deliveries(profile, rng, orders, order_items, customers)
    waves, tasks = _tasks(profile, rng, headers, dlv_items, slot_table, roster, materials)
    frame, events = _goods_issue(profile, headers, tasks, dlv_items, materials)
    mkpf, mseg = _material_documents(events, dlv_items, headers, valuation)

    site_frame = pl.DataFrame(
        {
            "site": list(SITES),
            "werks": [s.werks for s in SITES.values()],
            "lgort": [s.lgort for s in SITES.values()],
            "vstel": [s.vstel for s in SITES.values()],
            "vkorg": [s.vkorg for s in SITES.values()],
            "lgnum": [s.lgnum for s in SITES.values()],
            "tzonis": [s.sap_tz for s in SITES.values()],
        }
    )

    # ---- Deliveries (ECC extract, status as of ECC extraction) ---------------------------------
    delivered = frame.filter(pl.col("dlv_created_utc") <= ecc_as_of)
    final_pgi = delivered.get_column("final_pgi_utc").to_numpy()
    gi_posted = ~np.isnat(final_pgi) & (final_pgi <= ecc_as_of)
    pod_time = delivered.get_column("pod_utc").to_numpy()
    pod_confirmed = (
        delivered.get_column("pod_required").to_numpy()
        & gi_posted
        & (delivered.get_column("pod_confirmed_utc").to_numpy() <= ecc_as_of)
    )
    header_rows = []
    for code, site in SITES.items():
        mask = delivered.get_column("site").to_numpy() == code
        subset = delivered.filter(pl.Series(mask))
        created_system = utc_to_local(subset.get_column("dlv_created_utc").to_numpy(), ECC_SYSTEM_TZ)
        wadat = subset.get_column("planned_gi_date").to_numpy().astype("datetime64[D]")
        lfdat = wadat + subset.get_column("transit_days").to_numpy().astype("timedelta64[D]")
        gi_local = np.where(gi_posted[mask], utc_to_local(final_pgi[mask], site.tz), np.datetime64("NaT"))
        pod_local = np.where(pod_confirmed[mask], utc_to_local(pod_time[mask], site.tz), np.datetime64("NaT"))
        header_rows.append(
            subset.select("delivery_id").with_columns(
                dats(created_system).alias("erdat"),
                tims(created_system).alias("erzet"),
                dats(wadat).alias("wadat"),
                dats(np.where(gi_posted[mask], gi_local, np.datetime64("NaT"))).alias("wadat_ist"),
                dats(wadat).alias("kodat"),
                dats(lfdat).alias("lfdat"),
                dats(pod_local).alias("podat"),
                tims(pod_local).alias("potim"),
            )
        )
    delivered = (
        delivered.join(pl.concat(header_rows), on="delivery_id")
        .with_columns(
            (pl.col("final_pgi_utc").is_not_null() & (pl.col("final_pgi_utc") <= ecc_as_of)).alias(
                "gi_posted"
            ),
            (
                pl.col("pod_required")
                & pl.col("final_pgi_utc").is_not_null()
                & (pl.col("final_pgi_utc") <= ecc_as_of)
                & (pl.col("pod_confirmed_utc") <= ecc_as_of)
            ).alias("pod_confirmed"),
        )
        .sort("delivery_id")
    )

    lips_base = dlv_items.join(
        delivered.select("delivery_id", "vbeln", "site", "pod_required"), on=["delivery_id", "vbeln"]
    )
    gross = materials.select("matnr", (pl.col("weight_kg") * 1.1).alias("brgew"))
    weights = (
        lips_base.join(gross, on="matnr")
        .group_by("delivery_id")
        .agg((pl.col("base_qty") * pl.col("brgew")).sum().round(3).alias("btgew"))
    )
    likp = (
        delivered.join(site_frame, on="site")
        .join(weights, on="delivery_id")
        .sort("delivery_id")
        .select(
            pl.lit(MANDT).alias("mandt"),
            "vbeln",
            pl.lit("ZPSA").alias("lfart"),
            "vstel",
            "vkorg",
            "kunnr",
            pl.col("kunnr").alias("kunag"),
            "erdat",
            "erzet",
            "wadat",
            "wadat_ist",
            "kodat",
            "lfdat",
            "podat",
            "potim",
            "lgnum",
            "tzonis",
            "btgew",
            pl.lit("KG").alias("gewei"),
        )
    )
    lips = (
        lips_base.join(site_frame, on="site")
        .sort(["delivery_id", "posnr"])
        .select(
            pl.lit(MANDT).alias("mandt"),
            "vbeln",
            "posnr",
            pl.lit("TAN").alias("pstyv"),
            "matnr",
            "werks",
            "lgort",
            pl.col("sales_qty").alias("lfimg"),
            "vrkme",
            pl.col("base_qty").alias("lgmng"),
            pl.lit("EA").alias("meins"),
            "umvkz",
            pl.lit(1).alias("umvkn"),
            pl.col("vbeln_so").alias("vgbel"),
            pl.col("posnr_so").alias("vgpos"),
            pl.when(pl.col("pod_required")).then(pl.lit("A")).otherwise(pl.lit("")).alias("podrel"),
        )
    )

    picked = tasks.filter(pl.col("is_final") & (pl.col("confirmed_utc") <= ecc_as_of)).select(
        "delivery_item_id"
    )
    item_status = (
        lips_base.join(delivered.select("delivery_id", "gi_posted", "pod_confirmed"), on="delivery_id")
        .join(picked.with_columns(pl.lit(True).alias("picked")), on="delivery_item_id", how="left")
        .with_columns(pl.col("picked").fill_null(False))
        .with_columns(
            pl.when(pl.col("picked")).then(pl.lit("C")).otherwise(pl.lit("A")).alias("kosta"),
            pl.when(pl.col("gi_posted")).then(pl.lit("C")).otherwise(pl.lit("A")).alias("wbsta"),
            pl.when(~pl.col("pod_required"))
            .then(pl.lit(""))
            .when(pl.col("pod_confirmed"))
            .then(pl.lit("C"))
            .otherwise(pl.lit("A"))
            .alias("pdsta"),
        )
        .with_columns(
            pl.when((pl.col("wbsta") == "C") & pl.col("pdsta").is_in(["", "C"]))
            .then(pl.lit("C"))
            .when((pl.col("kosta") == "C") | (pl.col("wbsta") == "C"))
            .then(pl.lit("B"))
            .otherwise(pl.lit("A"))
            .alias("gbsta")
        )
        .sort(["delivery_id", "posnr"])
    )
    vbup = item_status.select(
        pl.lit(MANDT).alias("mandt"), "vbeln", "posnr", "gbsta", "kosta", "wbsta", "pdsta"
    )

    def rollup(column: str) -> pl.Expr:
        return (
            pl.when(pl.col(column).eq("C").all())
            .then(pl.lit("C"))
            .when(pl.col(column).eq("A").all())
            .then(pl.lit("A"))
            .otherwise(pl.lit("B"))
        )

    vbuk = (
        item_status.group_by("delivery_id", "vbeln")
        .agg(
            rollup("gbsta").alias("gbstk"),
            rollup("kosta").alias("kostk"),
            rollup("wbsta").alias("wbstk"),
            pl.when(pl.col("pdsta").eq("").all())
            .then(pl.lit(""))
            .when(pl.col("pdsta").is_in(["", "C"]).all())
            .then(pl.lit("C"))
            .otherwise(pl.lit("A"))
            .alias("pdstk"),
        )
        .sort("delivery_id")
        .select(pl.lit(MANDT).alias("mandt"), "vbeln", "gbstk", "kostk", "wbstk", "pdstk")
    )

    # ---- EWM outbound delivery orders (replicated within minutes of ECC creation) ---------------
    replication = headers.get_column("dlv_created_utc").to_numpy() + minutes(
        rng.uniform(1.0, 5.0, size=headers.height)
    )
    odo = (
        headers.with_columns(pl.Series("cretst_utc", replication))
        .filter(pl.col("cretst_utc") <= ewm_as_of)
        .sort(["cretst_utc", "delivery_id"])
    )
    odo = odo.with_columns(
        pl.Series("docid", guids(rng, odo.height)),
        zfill(5_000_000_000 + np.arange(1, odo.height + 1), 10).alias("docno"),
    ).join(site_frame.select("site", "lgnum"), on="site")
    odo_items = dlv_items.join(odo.select("delivery_id", "docid"), on="delivery_id").sort(
        ["delivery_id", "posnr"]
    )
    odo_items = odo_items.with_columns(
        pl.Series("itemid", guids(rng, odo_items.height)),
        pl.col("posnr").str.zfill(10).alias("itemno"),
    ).join(
        product_keys.select(pl.col("matnr_ecc").alias("matnr"), pl.col("matnr").alias("productno")),
        on="matnr",
    )
    proch = odo.select(
        pl.lit(MANDT).alias("mandt"),
        "docid",
        "docno",
        pl.lit("PDO").alias("doccat"),
        pl.lit("OUTB").alias("doctype"),
        "lgnum",
        pl.Series("cretst", dec15(odo.get_column("cretst_utc").to_numpy())),
        pl.lit("RFC-EWM").alias("creusr"),
    )
    proci = odo_items.sort(["delivery_id", "posnr"]).select(
        pl.lit(MANDT).alias("mandt"),
        "docid",
        "itemid",
        "itemno",
        "productno",
        pl.col("base_qty").alias("qty"),
        pl.lit("EA").alias("uom"),
    )
    refdoc = odo_items.sort(["delivery_id", "posnr"]).select(
        pl.lit(MANDT).alias("mandt"),
        "docid",
        "itemid",
        pl.lit("ERP").alias("refdoccat"),
        pl.col("vbeln").alias("refdocno"),
        pl.col("posnr").alias("refitemno"),
    )

    # ---- EWM warehouse tasks and waves (EWM extract) --------------------------------------------
    tasks_ewm = (
        tasks.filter(pl.col("confirmed_utc") <= ewm_as_of)
        .join(odo_items.select("delivery_item_id", "docid", "itemid"), on="delivery_item_id")
        .join(product_keys.select(pl.col("matnr_ecc").alias("matnr"), "matid"), on="matnr")
    )
    task_rows = []
    for code, site in SITES.items():
        subset = tasks_ewm.filter(pl.col("site") == code)
        created = subset.get_column("created_utc").to_numpy()
        confirmed_at = subset.get_column("confirmed_utc").to_numpy()
        task_rows.append(
            subset.with_columns(
                pl.Series("created_at", dec15(created)),
                pl.Series("created_at_wh", dec15(utc_to_local(created, site.tz))),
                pl.Series("started_at", dec15(subset.get_column("started_utc").to_numpy())),
                pl.Series("confirmed_at", dec15(confirmed_at)),
                pl.Series("confirmed_at_wh", dec15(utc_to_local(confirmed_at, site.tz))),
            )
        )
    stage_bins = rng.integers(1, 5, size=tasks_ewm.height)
    ordim_c = (
        pl.concat(task_rows)
        .sort(["lgnum", "tanum"])
        .with_columns(pl.Series("nlpla", [f"GI-STAGE-{index:02d}" for index in stage_bins]))
        .select(
            pl.lit(MANDT).alias("mandt"),
            "lgnum",
            "tanum",
            pl.lit("0001").alias("tapos"),
            "tostat",
            "procty",
            pl.lit("2").alias("trart"),
            "matid",
            pl.col("base_qty").alias("vsolm"),
            pl.lit("EA").alias("meins"),
            pl.col("sales_qty").alias("vsola"),
            pl.col("vrkme").alias("altme"),
            pl.col("lgtyp").alias("vltyp"),
            pl.col("lgpla").alias("vlpla"),
            pl.lit("9020").alias("nltyp"),
            "nlpla",
            "who",
            "wave",
            pl.lit("PDO").alias("rdoccat"),
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
    )

    wave_members = (
        tasks.filter(pl.col("is_final"))
        .group_by("lgnum", "wave")
        .agg(
            pl.col("confirmed_utc").max().alias("last_pick"),
            pl.col("delivery_id").unique().alias("deliveries"),
        )
    )
    delivery_pgi = frame.select("delivery_id", "pgi_utc")
    wave_load = (
        tasks.filter(pl.col("is_final"))
        .select("lgnum", "wave", "delivery_id")
        .unique()
        .join(delivery_pgi, on="delivery_id")
        .group_by("lgnum", "wave")
        .agg(pl.col("pgi_utc").max().alias("last_pgi"), pl.col("pgi_utc").is_null().any().alias("open_pgi"))
    )
    wave_frame = waves.join(wave_members.drop("deliveries"), on=["lgnum", "wave"], how="left").join(
        wave_load, on=["lgnum", "wave"], how="left"
    )
    pick_done = wave_frame.get_column("last_pick").to_numpy()
    pick_done = np.where(~np.isnat(pick_done) & (pick_done <= ewm_as_of), pick_done, np.datetime64("NaT"))
    load_done = wave_frame.get_column("last_pgi").to_numpy()
    open_pgi = wave_frame.get_column("open_pgi").fill_null(True).to_numpy()
    load_done = np.where(
        ~open_pgi & ~np.isnat(load_done) & (load_done <= ewm_as_of), load_done, np.datetime64("NaT")
    )
    wave_rows = []
    wave_frame = wave_frame.with_columns(pl.Series("pick_done", pick_done), pl.Series("load_done", load_done))
    for code, site in SITES.items():
        subset = wave_frame.filter(pl.col("site") == code)
        released = subset.get_column("released_utc").to_numpy()
        policy = _a4_policy(utc_to_local(subset.get_column("slot_utc").to_numpy(), site.tz), site)
        wave_rows.append(
            subset.with_columns(
                pl.Series("rls_dt", dec15(subset.get_column("slot_utc").to_numpy())),
                pl.Series("released_at", dec15(released)),
                pl.Series("released_at_wh", dec15(utc_to_local(released, site.tz))),
                pl.Series("cutoff_dt", dec15(released + 240 * MINUTE)),
                pl.Series("pick_comp_dt", dec15(subset.get_column("pick_done").to_numpy())),
                pl.Series("load_comp_dt", dec15(subset.get_column("load_done").to_numpy())),
                pl.Series("tmplt", np.where(policy, A4_WAVE_TEMPLATE, site.wave_template)),
            )
        )
    wavehdr = (
        pl.concat(wave_rows)
        .sort(["lgnum", "wave"])
        .select(
            pl.lit(MANDT).alias("mandt"),
            "lgnum",
            "wave",
            pl.lit("OUTB").alias("wave_type"),
            "tmplt",
            pl.when(pl.col("load_comp_dt") > 0).then(pl.lit("C")).otherwise(pl.lit("R")).alias("status"),
            "rls_dt",
            "released_at",
            "released_at_wh",
            pl.lit("WF-BATCH").alias("released_by"),
            "cutoff_dt",
            "pick_comp_dt",
            "load_comp_dt",
        )
    )

    tables = {
        "raw_sap_ecc.likp": likp,
        "raw_sap_ecc.lips": lips,
        "raw_sap_ecc.vbuk": vbuk,
        "raw_sap_ecc.vbup": vbup,
        "raw_sap_ecc.mkpf": mkpf,
        "raw_sap_ecc.mseg": mseg,
        "raw_sap_ewm.scdl_db_proch_o": proch,
        "raw_sap_ewm.scdl_db_proci_o": proci,
        "raw_sap_ewm.scdl_db_refdoc": refdoc,
        "raw_sap_ewm.scwm_wavehdr": wavehdr,
        "raw_sap_ewm.scwm_ordim_c": ordim_c,
    }
    facts = {"deliveries": frame, "tasks": tasks, "events": events, "delivery_items": dlv_items}
    return OutboundResult(tables=tables, facts=facts)
