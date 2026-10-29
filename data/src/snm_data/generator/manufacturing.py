"""Manufacturing demand (generator 0.7): production orders, component reservations, planned orders.

Each hub plant has an assembly building modeled as the production supply storage location ``2100``
(IM-managed, no EWM). Finished products (material type FERT) are built from spare-part components with a
fixed bill of materials. Production orders are released a few days before their start; their component
reservations (RESB, requirement type SB) are due at the start date at ``2100``. The inventory simulation
stages the components from the hub (``311`` from ``1000`` to ``2100``) two days before the start, as far as
stock allows, and consumes them (``261`` against the order) on the first working day on which every component
is staged. Reservations whose start date has passed without full staging are the manufacturing backlog.

Planned orders (PLAF) are the MRP output at the extraction: one per product and week for weeks 2 to 13 ahead,
with dependent requirements (RESB, requirement type AR) for their components. They are the forecast of the
replenishment engine; firm demand comes from the released production orders.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import polars as pl

from .config import Profile
from .master import BOX_GROUPS
from .org import MANDT
from .scenarios import A7_MATERIAL_GROUP
from .timeutil import zfill

COMPONENT = 70
PSA_LGORT = "2100"
BLANK_DATE = "00000000"
PLANTS = ("US10", "CN10")
BOM_SIZE = (3, 8)
USAGE = (1, 4)
ORDER_QTY = (1, 3)
DURATION_DAYS = (2, 10)
STAGING_LEAD_DAYS = 2
RELEASE_LEAD_DAYS = (3, 10)
# First start day of the schedule: every order is created, released, and staged inside the simulated period.
FIRST_START_OFFSET = STAGING_LEAD_DAYS + 2
# Share of a CN10 line issued from the Dongguan overflow hub instead of the Longgang main warehouse.
DONGGUAN_ISSUE_SHARE = 0.3
PLANNED_WEEKS = range(2, 14)
SCHEDULE_EXTRA_DAYS = 14


@dataclass
class ProductionPlan:
    """The production schedule before simulation: BOMs, orders, and their component lines."""

    bom: pl.DataFrame  # product (matnr), component (matnr), usage
    orders: (
        pl.DataFrame
    )  # order_id, aufnr, werks, product, qty, created_day, release_day, start_day, duration
    components: pl.DataFrame  # order_id, line, werks, matnr, qty, due_day, stage_day


def _working(day: date) -> bool:
    return day.isoweekday() <= 5


def _next_working(day: date) -> date:
    while not _working(day):
        day += timedelta(days=1)
    return day


def _bom_line(rng: np.random.Generator, product: str, component: dict) -> dict:
    """One bill-of-material line; boxed consumables are used in whole boxes."""
    usage = int(rng.integers(USAGE[0], USAGE[1] + 1))
    if component["has_box"] and component["matkl"] in BOX_GROUPS:
        usage = int(component["box_size"]) * int(rng.integers(1, 3))
    return {"product": product, "component": component["matnr"], "usage": usage}


def production_plan(profile: Profile, materials: pl.DataFrame) -> ProductionPlan:
    """The plan for the whole generated period: from the day before the profile's start to the last local
    day of the ECC extraction, on the manufacturing random stream."""
    from zoneinfo import ZoneInfo

    from .org import SITES

    first = profile.start - timedelta(days=1)
    last = max(profile.ecc_as_of.astimezone(ZoneInfo(site.tz)).date() for site in SITES.values())
    n_days = (last - first).days + 1
    return schedule(profile, materials, first, n_days, np.random.default_rng([profile.seed, COMPONENT]))


def schedule(
    profile: Profile, materials: pl.DataFrame, first: date, n_days: int, rng: np.random.Generator
) -> ProductionPlan:
    """Bills of materials and the production orders of the generated period (plus two weeks ahead).

    The manufacturing department is the warehouse's only customer (decision record 003), so the orders are
    sized to the site's warehouse volume: the component lines per working day match ``BASE_LINES_PER_DAY``
    times the profile's scale and the seasonal demand multipliers. Each component line names the warehouse
    that issues it: the plant's main warehouse, or for CN10 the Dongguan overflow hub for part of the lines.
    """
    from .calendar import demand_multipliers
    from .config import BASE_LINES_PER_DAY

    products = materials.filter(pl.col("mtart") == "FERT").sort("matnr")
    spares = materials.filter(pl.col("mtart") != "FERT").sort("matnr")
    bom_rows, order_rows, line_rows = [], [], []
    for product in products.iter_rows(named=True):
        flags = [flag for flag in ("in_us10", "in_cn10") if product[flag]]
        pool = spares
        for flag in flags:
            pool = pool.filter(pl.col(flag))
        if pool.height == 0:
            continue
        size = min(int(rng.integers(BOM_SIZE[0], BOM_SIZE[1] + 1)), pool.height)
        weights = pool.get_column("popularity").to_numpy()
        chosen = set(rng.choice(pool.height, size=size, replace=False, p=weights / weights.sum()).tolist())
        # Every product uses at least one boxed consumable in whole boxes: seals when the plant stocks boxed
        # seals (the group of scenario A7), else another boxed group.
        group = pool.get_column("matkl").to_numpy()
        boxed = pool.get_column("has_box").to_numpy() & np.isin(group, list(BOX_GROUPS))
        preferred = boxed & (group == A7_MATERIAL_GROUP)
        wanted = preferred if preferred.any() else boxed
        if wanted.any() and not any(wanted[index] for index in chosen):
            wanted_weights = np.where(wanted, weights, 0.0)
            chosen.add(int(rng.choice(pool.height, p=wanted_weights / wanted_weights.sum())))
        for index in sorted(chosen):
            bom_rows.append(_bom_line(rng, product["matnr"], pool.row(index, named=True)))
    # A spares warehouse for the company's own manufacturing: every spare part stocked at a plant is used
    # by at least one product made there, so uncovered spares join a random product's bill of material.
    flags = ("in_us10", "in_cn10")
    for flag in flags:
        plant_products = products.filter(pl.col(flag)).select("matnr", *flags).rows(named=True)
        used = {
            row["component"] for row in bom_rows if row["product"] in {p["matnr"] for p in plant_products}
        }
        for spare in spares.filter(pl.col(flag) & ~pl.col("matnr").is_in(used)).iter_rows(named=True):
            # A component must be stocked at every plant that makes the product.
            fitting = [p for p in plant_products if all(spare[f] for f in flags if p[f])]
            if fitting:
                bom_rows.append(_bom_line(rng, fitting[int(rng.integers(len(fitting)))]["matnr"], spare))
    bom = pl.DataFrame(
        bom_rows, schema={"product": pl.String, "component": pl.String, "usage": pl.Int64}
    ).sort(["product", "component"])
    components_of: dict[str, list[tuple[str, int]]] = {}
    for row in bom.iter_rows(named=True):
        components_of.setdefault(row["product"], []).append((row["component"], row["usage"]))

    in_dg = set(materials.filter(pl.col("in_dg")).get_column("matnr").to_list())
    order_id = 0
    plants = (("US10", "in_us10", ("RR",), "US"), ("CN10", "in_cn10", ("SZ", "DG"), "CN"))
    for werks, flag, sites, country in plants:
        plant_products = [
            row for row in products.iter_rows(named=True) if row[flag] and row["matnr"] in components_of
        ]
        if not plant_products:
            continue
        mean_lines = sum(len(components_of[p["matnr"]]) for p in plant_products) / len(plant_products)
        target_lines = sum(BASE_LINES_PER_DAY[site] for site in sites) * profile.scale
        days = [first + timedelta(days=offset) for offset in range(1, n_days + SCHEDULE_EXTRA_DAYS)]
        multipliers = demand_multipliers(days, country, profile.start)
        # Working days carry the week's volume (the multipliers are per calendar day).
        base_rate = target_lines / mean_lines * 7.0 / 5.0
        for offset, start, multiplier in zip(
            range(1, n_days + SCHEDULE_EXTRA_DAYS), days, multipliers, strict=True
        ):
            if offset < FIRST_START_OFFSET or not _working(start):
                continue
            for _ in range(int(rng.poisson(base_rate * float(multiplier)))):
                product = plant_products[int(rng.integers(len(plant_products)))]
                qty = int(rng.integers(ORDER_QTY[0], ORDER_QTY[1] + 1))
                lead = int(rng.integers(RELEASE_LEAD_DAYS[0], RELEASE_LEAD_DAYS[1] + 1))
                duration = int(rng.integers(DURATION_DAYS[0], DURATION_DAYS[1] + 1))
                order_id += 1
                order_rows.append(
                    {
                        "order_id": order_id,
                        "werks": werks,
                        "product": product["matnr"],
                        "qty": qty,
                        "created_day": max(1, offset - lead),
                        "release_day": max(1, offset - lead) + 1,
                        "start_day": offset,
                        "duration": duration,
                    }
                )
                for line, (component, usage) in enumerate(components_of[product["matnr"]], start=1):
                    site = sites[0]
                    if werks == "CN10" and component in in_dg and rng.random() < DONGGUAN_ISSUE_SHARE:
                        site = "DG"
                    line_rows.append(
                        {
                            "order_id": order_id,
                            "line": line,
                            "werks": werks,
                            "site": site,
                            "matnr": component,
                            "qty": float(qty * usage),
                            "due_day": offset,
                            "stage_day": offset - STAGING_LEAD_DAYS,
                        }
                    )
    orders = pl.DataFrame(
        order_rows,
        schema={"order_id": pl.Int64, "werks": pl.String, "product": pl.String, "qty": pl.Int64,
                "created_day": pl.Int64, "release_day": pl.Int64, "start_day": pl.Int64,
                "duration": pl.Int64},
    )  # fmt: skip
    numbers = 60_000_000_000 + orders.get_column("order_id").to_numpy()
    orders = orders.with_columns(zfill(numbers, 12).alias("aufnr"))
    components = pl.DataFrame(
        line_rows,
        schema={"order_id": pl.Int64, "line": pl.Int64, "werks": pl.String, "site": pl.String,
                "matnr": pl.String, "qty": pl.Float64, "due_day": pl.Int64, "stage_day": pl.Int64},
    )  # fmt: skip
    return ProductionPlan(bom=bom, orders=orders, components=components)


def render(
    plan: ProductionPlan,
    started: dict[int, int],
    profile: Profile,
    first: date,
    rng: np.random.Generator,
) -> dict[str, pl.DataFrame]:
    """AFKO, AFPO, RESB (order reservations and planned-order dependent requirements), and PLAF."""
    as_of = profile.ecc_as_of.date()
    as_of_offset = (as_of - first).days
    visible = plan.orders.filter(pl.col("created_day") <= as_of_offset).sort("order_id")

    def day_of(offset) -> str:
        return (first + timedelta(days=int(offset))).strftime("%Y%m%d")

    afko_rows, afpo_rows, resb_rows = [], [], []
    for order in visible.iter_rows(named=True):
        start_day = started.get(order["order_id"])
        finish_day = start_day + order["duration"] if start_day is not None else None
        if finish_day is not None and finish_day > as_of_offset:
            finish_day = None
        rsnum = zfill(np.array([100_000_000 + order["order_id"]]), 10)[0]
        afko_rows.append(
            {
                "mandt": MANDT,
                "aufnr": order["aufnr"],
                "rsnum": rsnum,
                "plnbez": order["product"],
                "gamng": float(order["qty"]),
                "gmein": "EA",
                "erdat": day_of(order["created_day"]),
                "ftrmi": day_of(order["release_day"]),
                "gstrp": day_of(order["start_day"]),
                "gltrp": day_of(order["start_day"] + order["duration"]),
                "gstri": day_of(start_day) if start_day is not None else BLANK_DATE,
                "getri": day_of(finish_day) if finish_day is not None else BLANK_DATE,
            }
        )
        afpo_rows.append(
            {
                "mandt": MANDT,
                "aufnr": order["aufnr"],
                "posnr": "0001",
                "matnr": order["product"],
                "psmng": float(order["qty"]),
                "wemng": float(order["qty"]) if finish_day is not None else 0.0,
                "meins": "EA",
                "pwerk": order["werks"],
                "dwerk": order["werks"],
                "ltrmi": day_of(finish_day) if finish_day is not None else BLANK_DATE,
            }
        )
        lines = plan.components.filter(pl.col("order_id") == order["order_id"]).sort("line")
        for line in lines.iter_rows(named=True):
            consumed = start_day is not None
            resb_rows.append(
                {
                    "mandt": MANDT,
                    "rsnum": rsnum,
                    "rspos": zfill(np.array([line["line"]]), 4)[0],
                    "bdart": "SB",
                    "xloek": "",
                    "kzear": "X" if consumed else "",
                    "matnr": line["matnr"],
                    "werks": line["werks"],
                    "lgort": PSA_LGORT,
                    "bdter": day_of(line["due_day"]),
                    "bdmng": line["qty"],
                    "meins": "EA",
                    "enmng": line["qty"] if consumed else 0.0,
                    "aufnr": order["aufnr"],
                    "plnum": "",
                    "xwaok": "X",
                }
            )

    # Planned orders: the MRP run at the extraction covers weeks 2 to 13 per product and plant.
    plaf_rows = []
    products = plan.orders.select("werks", "product").unique().sort(["werks", "product"])
    history_days = max(as_of_offset, 1)
    plnum = 200_000_000
    for werks, product in products.rows():
        history = plan.orders.filter(
            (pl.col("werks") == werks)
            & (pl.col("product") == product)
            & (pl.col("start_day") <= as_of_offset)
        )
        weekly_qty = float(history.get_column("qty").sum()) / history_days * 7.0 if history.height else 1.0
        bom_lines = plan.bom.filter(pl.col("product") == product).sort("component")
        for week in PLANNED_WEEKS:
            qty = int(rng.poisson(max(weekly_qty, 0.2)))
            if qty == 0:
                continue
            plnum += 1
            number = zfill(np.array([plnum]), 10)[0]
            start = _next_working(as_of + timedelta(days=7 * week))
            duration = int(rng.integers(DURATION_DAYS[0], DURATION_DAYS[1] + 1))
            rsnum = zfill(np.array([300_000_000 + plnum - 200_000_000]), 10)[0]
            plaf_rows.append(
                {
                    "mandt": MANDT,
                    "plnum": number,
                    "matnr": product,
                    "plwrk": werks,
                    "gsmng": float(qty),
                    "meins": "EA",
                    "psttr": start.strftime("%Y%m%d"),
                    "pedtr": (start + timedelta(days=duration)).strftime("%Y%m%d"),
                    "paart": "LA",
                    "dispo": "M01",
                    "rsnum": rsnum,
                }
            )
            bom_pairs = bom_lines.select("component", "usage").rows()
            for line, (component, usage) in enumerate(bom_pairs, start=1):
                resb_rows.append(
                    {
                        "mandt": MANDT,
                        "rsnum": rsnum,
                        "rspos": zfill(np.array([line]), 4)[0],
                        "bdart": "AR",
                        "xloek": "",
                        "kzear": "",
                        "matnr": component,
                        "werks": werks,
                        "lgort": PSA_LGORT,
                        "bdter": start.strftime("%Y%m%d"),
                        "bdmng": float(qty * usage),
                        "meins": "EA",
                        "enmng": 0.0,
                        "aufnr": "",
                        "plnum": number,
                        "xwaok": "",
                    }
                )
    schemas = {
        "afko": {"mandt": pl.String, "aufnr": pl.String, "rsnum": pl.String, "plnbez": pl.String,
                 "gamng": pl.Float64, "gmein": pl.String, "erdat": pl.String, "ftrmi": pl.String,
                 "gstrp": pl.String, "gltrp": pl.String, "gstri": pl.String, "getri": pl.String},
        "afpo": {"mandt": pl.String, "aufnr": pl.String, "posnr": pl.String, "matnr": pl.String,
                 "psmng": pl.Float64, "wemng": pl.Float64, "meins": pl.String, "pwerk": pl.String,
                 "dwerk": pl.String, "ltrmi": pl.String},
        "resb": {"mandt": pl.String, "rsnum": pl.String, "rspos": pl.String, "bdart": pl.String,
                 "xloek": pl.String, "kzear": pl.String, "matnr": pl.String, "werks": pl.String,
                 "lgort": pl.String, "bdter": pl.String, "bdmng": pl.Float64, "meins": pl.String,
                 "enmng": pl.Float64, "aufnr": pl.String, "plnum": pl.String, "xwaok": pl.String},
        "plaf": {"mandt": pl.String, "plnum": pl.String, "matnr": pl.String, "plwrk": pl.String,
                 "gsmng": pl.Float64, "meins": pl.String, "psttr": pl.String, "pedtr": pl.String,
                 "paart": pl.String, "dispo": pl.String, "rsnum": pl.String},
    }  # fmt: skip
    return {
        "raw_sap_ecc.afko": pl.DataFrame(afko_rows, schema=schemas["afko"]).sort("aufnr"),
        "raw_sap_ecc.afpo": pl.DataFrame(afpo_rows, schema=schemas["afpo"]).sort(["aufnr", "posnr"]),
        "raw_sap_ecc.resb": pl.DataFrame(resb_rows, schema=schemas["resb"]).sort(["rsnum", "rspos"]),
        "raw_sap_ecc.plaf": pl.DataFrame(plaf_rows, schema=schemas["plaf"]).sort("plnum"),
    }
