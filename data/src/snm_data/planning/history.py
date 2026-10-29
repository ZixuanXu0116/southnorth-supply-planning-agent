"""Engine replay against the generated history (evaluation layer L12, PROPOSAL §10.2).

``tables_as_of`` rebuilds the engine's raw inputs as the nightly extraction at the start of day ``as_of``
would have shown them: material documents, deliveries, orders, and interactions before that day; purchase
order receipts and reservation withdrawals recomputed up to it; storage-location stock from the daily
snapshot of the previous day. ``replay`` runs the engine on a series of past days and scores its shortage
cases against what happened next in the simulation: the emergency purchase orders (``ZEX``) the plant had
to place because the hub would otherwise have run out.

Approximations (documented in the report): reorder points (MARD LMINB) are today's values; planned orders
exist only at the extraction, so past forecasts use the trailing demand rate without planned-order
requirements; sales-order rejections are today's.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from .engine import plan
from .inputs import DATS, PlanningInputs
from .params import PlanningParams

HISTORY_TABLES = ("raw_ext.inventory_snapshot_daily", "raw_sap_ecc.afko")


def _before(column: str, day: date) -> pl.Expr:
    return pl.col(column) < day.strftime(DATS)


def tables_as_of(tables: dict[str, pl.DataFrame], as_of: date) -> dict[str, pl.DataFrame]:
    """The raw input tables as extracted at the start of ``as_of`` (see the module docstring)."""
    t = dict(tables)
    cutoff = as_of.strftime(DATS)
    mseg = t["raw_sap_ecc.mseg"].filter(pl.col("budat_mkpf") < cutoff)
    t["raw_sap_ecc.mseg"] = mseg

    # Purchase orders created before the day; received quantity up to it.
    ekko = t["raw_sap_ecc.ekko"].filter(_before("aedat", as_of))
    t["raw_sap_ecc.ekko"] = ekko
    t["raw_sap_ecc.ekpo"] = t["raw_sap_ecc.ekpo"].join(ekko.select("ebeln"), on="ebeln")
    received = (
        mseg.filter((pl.col("bwart") == "101") & (pl.col("ebeln") != ""))
        .group_by(["ebeln", "ebelp"])
        .agg(pl.col("menge").cast(pl.Float64).sum().alias("received"))
    )
    t["raw_sap_ecc.eket"] = (
        t["raw_sap_ecc.eket"]
        .join(ekko.select("ebeln"), on="ebeln")
        .join(received, on=["ebeln", "ebelp"], how="left")
        .with_columns(pl.col("received").fill_null(0.0).alias("wemng"))
        .drop("received")
    )

    # Deliveries created before the day; goods issued on or after it are still open.
    likp = t["raw_sap_ecc.likp"].filter(_before("erdat", as_of))
    t["raw_sap_ecc.likp"] = likp.with_columns(
        pl.when(pl.col("wadat_ist") >= cutoff)
        .then(pl.lit(""))
        .otherwise(pl.col("wadat_ist"))
        .alias("wadat_ist")
    )
    t["raw_sap_ecc.lips"] = t["raw_sap_ecc.lips"].join(likp.select("vbeln"), on="vbeln")
    if "raw_sap_ecc.vbak" in t:  # legacy customer orders (generator 0.7 and earlier)
        vbak = t["raw_sap_ecc.vbak"].filter(_before("erdat", as_of))
        t["raw_sap_ecc.vbak"] = vbak
        t["raw_sap_ecc.vbap"] = t["raw_sap_ecc.vbap"].join(vbak.select("vbeln"), on="vbeln")

    # Production reservations of orders created before the day; withdrawals up to it; no planned orders.
    resb = t.get("raw_sap_ecc.resb")
    afko = t.get("raw_sap_ecc.afko")
    if resb is not None and afko is not None:
        orders = afko.filter(_before("erdat", as_of)).select("aufnr")
        withdrawn = (
            mseg.filter(pl.col("bwart") == "261")
            .group_by(["aufnr", "matnr"])
            .agg(pl.col("menge").cast(pl.Float64).sum().alias("withdrawn"))
        )
        t["raw_sap_ecc.resb"] = (
            resb.filter(pl.col("bdart") == "SB")
            .join(orders, on="aufnr")
            .join(withdrawn, on=["aufnr", "matnr"], how="left")
            .with_columns(pl.col("withdrawn").fill_null(0.0).alias("enmng"))
            .drop("withdrawn")
        )

    interactions = t.get("raw_ext.purchasing_interaction")
    if interactions is not None:
        day = pl.lit(as_of)
        t["raw_ext.purchasing_interaction"] = interactions.filter(
            pl.col("opened_at").cast(pl.Date) < day
        ).with_columns(
            pl.when(pl.col("closed_at").cast(pl.Date) < day)
            .then(pl.col("closed_at"))
            .otherwise(None)
            .alias("closed_at")
        )

    # Stock at the end of the previous day from the daily snapshots; reorder points from today's MARD.
    snapshots = t.get("raw_ext.inventory_snapshot_daily")
    if snapshots is not None:
        previous = as_of - timedelta(days=1)
        stock = (
            snapshots.filter(pl.col("snapshot_date") == previous)
            .group_by(["werks", "lgort", "matnr"])
            .agg(
                pl.col("quantity")
                .filter(pl.col("stock_category") == "UNRESTRICTED")
                .sum()
                .cast(pl.Float64)
                .alias("labst"),
                pl.col("quantity")
                .filter(pl.col("stock_category") == "IN_TRANSFER")
                .sum()
                .cast(pl.Float64)
                .alias("umlme"),
            )
        )
        mard = t["raw_sap_ecc.mard"]
        t["raw_sap_ecc.mard"] = (
            mard.drop("labst", "umlme")
            .join(stock, on=["werks", "lgort", "matnr"], how="left")
            .with_columns(pl.col("labst").fill_null(0.0), pl.col("umlme").fill_null(0.0))
        )
    return t


def realized_shortages(tables: dict[str, pl.DataFrame], start: date, days: int) -> pl.DataFrame:
    """Material and plant pairs with an emergency purchase order (ZEX) created in [start, start + days)."""
    end = start + timedelta(days=days)
    ekko = tables["raw_sap_ecc.ekko"].filter(
        (pl.col("bsart") == "ZEX")
        & (pl.col("aedat") >= start.strftime(DATS))
        & (pl.col("aedat") < end.strftime(DATS))
    )
    return (
        tables["raw_sap_ecc.ekpo"]
        .join(ekko.select("ebeln", "aedat"), on="ebeln")
        .group_by(["matnr", "werks"])
        .agg(pl.col("aedat").min().alias("first_emergency"))
        .select(
            pl.col("matnr").str.strip_chars_start("0").alias("material"),
            pl.col("werks").alias("plant"),
            pl.col("first_emergency").str.strptime(pl.Date, "%Y%m%d").alias("first_emergency"),
        )
    )


@dataclass
class ReplayDay:
    as_of: date
    predicted: int  # shortage cases dated inside the window
    realized: int  # emergency purchase orders inside the window (material and plant pairs)
    hits: int  # realized shortages with a shortage case dated inside the window
    warned: int  # realized shortages with only a warning case
    late: int  # realized shortages whose shortage case was dated after the window
    flagged: int  # realized shortages with a shortage or a warning case dated inside the window
    # Days between the as-of date and the emergency, for hits.
    lead_days: list[int] = field(default_factory=list)
    # Trigger date minus emergency date, for every realized shortage with a shortage case.
    timing_errors: list[int] = field(default_factory=list)

    @property
    def precision(self) -> float:
        return self.hits / self.predicted if self.predicted else 0.0

    @property
    def recall(self) -> float:
        return self.hits / self.realized if self.realized else 0.0


def replay(
    tables: dict[str, pl.DataFrame], days: list[date], horizon_days: int, params: PlanningParams | None = None
) -> list[ReplayDay]:
    """Run the engine on each past day and score its shortage cases against the next ``horizon_days``.

    Planned orders exist only at the extraction, so on past days the trailing staging rate (netted against
    the released orders' reservations) stands in for them."""
    params = params or PlanningParams()
    out = []
    empty = pl.DataFrame(schema={"material": pl.String, "plant": pl.String, "trigger_date": pl.Date})
    for day in days:
        inputs = PlanningInputs.from_tables(tables_as_of(tables, day), day, params)
        cases = plan(inputs, params).replenishment_case
        window_end = day + timedelta(days=horizon_days)
        if cases.height:
            shortages = cases.filter(pl.col("condition") == "shortage").select(
                "material", "plant", "trigger_date"
            )
            warnings = cases.filter(
                (pl.col("condition") == "warning") & (pl.col("trigger_date") < window_end)
            )
        else:
            shortages, warnings = empty, empty
        predicted = shortages.filter(pl.col("trigger_date") < window_end)
        realized = realized_shortages(tables, day, horizon_days).filter(
            pl.col("plant").is_in(list(params.plants))
        )
        keys = ["material", "plant"]
        hits = realized.join(predicted, on=keys)
        late = realized.join(shortages, on=keys).join(predicted, on=keys, how="anti")
        warned = realized.join(warnings.select(keys), on=keys).join(shortages, on=keys, how="anti")
        timing = realized.join(shortages, on=keys)
        at_risk = pl.concat([predicted.select(keys), warnings.select(keys)]).unique()
        flagged = realized.join(at_risk, on=keys)
        out.append(
            ReplayDay(
                as_of=day,
                predicted=predicted.height,
                realized=realized.height,
                hits=hits.height,
                warned=warned.height,
                late=late.height,
                flagged=flagged.height,
                lead_days=[(d - day).days for d in hits.get_column("first_emergency").to_list()],
                timing_errors=[
                    (row["trigger_date"] - row["first_emergency"]).days
                    for row in timing.iter_rows(named=True)
                ],
            )
        )
    return out


def load_tables(profile_dir: Path, names: tuple[str, ...]) -> dict[str, pl.DataFrame]:
    tables = {}
    for name in names:
        schema, table = name.split(".")
        path = profile_dir / schema / f"{table}.parquet"
        if path.exists():
            tables[name] = pl.read_parquet(path)
    return tables
