"""Engine inputs: the frames the projection needs, derived from the raw tables.

The same Polars derivation serves two sources: the generator's tables (tests, offline runs) and the raw
tables read from Databricks (``from_databricks``, column-projected and date-filtered SQL). Dates are
site-local calendar dates; quantities are in base units; prices are in USD.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import numpy as np
import polars as pl

from .demand import sba
from .params import PlanningParams

DATS = "%Y%m%d"
CUSTOMER_ISSUE = ("601", "602")
PSA_LGORT = "2100"  # production supply area (staging target)


def _dats(column: str) -> pl.Expr:
    return pl.col(column).str.strptime(pl.Date, DATS, strict=False)


def _material(column: str = "matnr") -> pl.Expr:
    return pl.col(column).str.strip_chars_start("0").alias("material")


@dataclass
class PlanningInputs:
    """Frames keyed by (material, plant); ``material`` is the number without leading zeros."""

    as_of: date
    # material, plant, criticality, safety_stock, max_stock, planned_delivery_days, price_usd, description
    materials: pl.DataFrame
    # material, plant, storage_location, available_qty, in_transit_qty, reorder_point
    stock: pl.DataFrame
    # ebeln, ebelp, material, plant, vendor, open_qty, agreed_date, expected_date, unit_price_usd
    open_orders: pl.DataFrame
    # material, plant, requirement_date, qty, source (delivery | sales_order | production)
    firm_demand: pl.DataFrame
    # material, plant, requirement_date, qty (dependent requirements of planned orders: the MRP forecast)
    planned_demand: pl.DataFrame
    # material, plant, storage_location, daily_rate (trailing-window customer demand per day)
    demand_rate: pl.DataFrame
    # material, plant, vendor (the vendor of the most recent purchase order)
    material_vendor: pl.DataFrame
    # vendor, name, country, spend_usd_12m, po_count_12m, on_time_rate_12m, median_lateness_days,
    # strategic, portal_onboarded, min_expedite_days, surcharge_pct_lt7, surcharge_pct_7_13,
    # surcharge_pct_ge14, expedite_freight_usd, median_rounds, median_days_to_resolution
    vendors: pl.DataFrame
    # parameter -> value
    cost_parameters: dict[str, float]
    # material, plant, week, qty: weekly outflow to production over the ``sba_weeks`` weeks before the as-of
    # day (week 0 the oldest); the demand model's history (record 002). None: no model, no risk.
    demand_weekly: pl.DataFrame | None = None

    @classmethod
    def from_tables(
        cls, tables: dict[str, pl.DataFrame], as_of: date, params: PlanningParams
    ) -> PlanningInputs:
        """Derive every input from raw frames keyed by ``schema.table`` (generator output or SQL pulls)."""
        t = {name.split(".")[-1]: frame for name, frame in tables.items()}
        materials = _materials(t["marc"], t["mbew"], t["makt"], params)
        stock = _stock(t["mard"])
        receipts = _receipts(t["mseg"])
        vendors, lateness = _vendors(t, receipts, as_of, params)
        open_orders = _open_orders(t["ekko"], t["ekpo"], t["eket"], lateness, as_of, params)
        resb = t.get("resb")
        weekly = _demand_weekly(t["mseg"], as_of, params)
        demand_rate = (
            _sba_rate(weekly, params)
            if params.forecast_model == "sba"
            else _demand_rate(t["mseg"], as_of, params)
        )
        demand = [_production_demand(resb)]
        if params.include_customer_demand:
            demand.append(_firm_demand(t["likp"], t["lips"], t["vbak"], t["vbap"], as_of))
        return cls(
            as_of=as_of,
            materials=materials,
            stock=stock,
            open_orders=open_orders,
            firm_demand=pl.concat(demand).sort(["plant", "material", "requirement_date", "source"]),
            planned_demand=_planned_demand(resb),
            demand_rate=demand_rate,
            material_vendor=_material_vendor(t["ekko"], t["ekpo"]),
            vendors=vendors,
            cost_parameters=_cost_parameters(t.get("cost_parameter"), params),
            demand_weekly=weekly,
        )

    @classmethod
    def from_databricks(cls, settings: Any, as_of: date, params: PlanningParams) -> PlanningInputs:
        """Read the raw tables the engine needs (projected and filtered) and derive the inputs."""
        from ..databricks import sql_connection

        catalog = settings.catalog
        since = (as_of - timedelta(days=params.lateness_window_days + 30)).strftime(DATS)
        demand_days = max(params.forecast_window_days - 1, 7 * params.sba_weeks)
        demand_since = (as_of - timedelta(days=demand_days)).strftime(DATS)
        plants = ", ".join(f"'{plant}'" for plant in params.plants)
        queries = {
            "raw_sap_ecc.marc": (
                "SELECT matnr, werks, eisbe, mabst, plifz, maabc, beskz FROM {c}.raw_sap_ecc.marc "
                f"WHERE werks IN ({plants})"
            ),
            "raw_sap_ecc.mbew": "SELECT matnr, bwkey, stprs FROM {c}.raw_sap_ecc.mbew",
            "raw_sap_ecc.makt": "SELECT matnr, maktx FROM {c}.raw_sap_ecc.makt",
            "raw_sap_ecc.mard": (
                "SELECT matnr, werks, lgort, labst, umlme, lminb FROM {c}.raw_sap_ecc.mard "
                f"WHERE werks IN ({plants})"
            ),
            "raw_sap_ecc.ekko": "SELECT ebeln, lifnr, bsart, waers, aedat FROM {c}.raw_sap_ecc.ekko",
            "raw_sap_ecc.ekpo": (
                "SELECT ebeln, ebelp, matnr, werks, menge, netpr, peinh FROM {c}.raw_sap_ecc.ekpo"
            ),
            "raw_sap_ecc.eket": "SELECT ebeln, ebelp, eindt, menge, wemng FROM {c}.raw_sap_ecc.eket",
            "raw_sap_ecc.mseg": (
                "SELECT bwart, shkzg, matnr, werks, lgort, umlgo, menge, budat_mkpf, ebeln "
                "FROM {c}.raw_sap_ecc.mseg "
                f"WHERE werks IN ({plants}) AND ((bwart = '101' AND budat_mkpf >= '{since}') "
                f"OR (bwart IN ('601', '602') AND budat_mkpf >= '{demand_since}') "
                f"OR (bwart IN ('311', '313') AND shkzg = 'H' AND budat_mkpf >= '{demand_since}') "
                f"OR (bwart = '312' AND shkzg = 'S' AND budat_mkpf >= '{demand_since}'))"
            ),
            "raw_sap_ecc.lfa1": "SELECT lifnr, name1, land1 FROM {c}.raw_sap_ecc.lfa1",
            "raw_sap_ecc.resb": (
                "SELECT bdart, matnr, werks, bdter, bdmng, enmng FROM {c}.raw_sap_ecc.resb "
                f"WHERE xloek = '' AND werks IN ({plants})"
            ),
            "raw_ext.supplier_condition": "SELECT * FROM {c}.raw_ext.supplier_condition",
            "raw_ext.cost_parameter": "SELECT parameter, value FROM {c}.raw_ext.cost_parameter",
            "raw_ext.purchasing_interaction": (
                "SELECT lifnr, kind, rounds, days_to_resolution, closed_at "
                "FROM {c}.raw_ext.purchasing_interaction"
            ),
        }
        if params.include_customer_demand:
            queries.update(
                {
                    "raw_sap_ecc.likp": "SELECT vbeln, wadat, wadat_ist FROM {c}.raw_sap_ecc.likp",
                    "raw_sap_ecc.lips": (
                        "SELECT vbeln, matnr, werks, lgmng, vgbel, vgpos FROM {c}.raw_sap_ecc.lips"
                    ),
                    "raw_sap_ecc.vbak": "SELECT vbeln, vdatu FROM {c}.raw_sap_ecc.vbak",
                    "raw_sap_ecc.vbap": (
                        "SELECT vbeln, posnr, matnr, werks, kwmeng, umvkz, umvkn, abgru "
                        "FROM {c}.raw_sap_ecc.vbap"
                    ),
                }
            )
        tables: dict[str, pl.DataFrame] = {}
        with sql_connection(settings) as connection, connection.cursor() as cursor:
            for name, query in queries.items():
                cursor.execute(query.format(c=f"`{catalog}`"))
                columns = [item[0] for item in cursor.description]
                tables[name] = pl.DataFrame(cursor.fetchall(), schema=columns, orient="row")
        return cls.from_tables(_normalize(tables), as_of, params)


def _normalize(tables: dict[str, pl.DataFrame]) -> dict[str, pl.DataFrame]:
    """Databricks returns decimals as Decimal objects; the derivation works in floats."""
    out = {}
    for name, frame in tables.items():
        casts = [pl.col(c).cast(pl.Float64) for c, dtype in frame.schema.items() if dtype == pl.Decimal]
        out[name] = frame.with_columns(casts) if casts else frame
    return out


def _materials(
    marc: pl.DataFrame, mbew: pl.DataFrame, makt: pl.DataFrame, params: PlanningParams
) -> pl.DataFrame:
    price = mbew.select(
        "matnr",
        pl.col("bwkey").alias("werks"),
        pl.when(pl.col("bwkey") == "CN10")
        .then(pl.col("stprs").cast(pl.Float64) / params.cny_per_usd)
        .otherwise(pl.col("stprs").cast(pl.Float64))
        .alias("price_usd"),
    )
    if "beskz" in marc.columns:  # in-house produced materials (finished products) are not purchased
        marc = marc.filter(pl.col("beskz") != "E")
    marc = marc.filter(pl.col("werks").is_in(list(params.plants)))
    return (
        marc.join(price, on=["matnr", "werks"], how="left")
        .join(makt.select("matnr", "maktx").unique(subset=["matnr"]), on="matnr", how="left")
        .select(
            _material(),
            pl.col("werks").alias("plant"),
            pl.col("maabc").alias("criticality"),
            pl.col("eisbe").cast(pl.Float64).alias("safety_stock"),
            pl.col("mabst").cast(pl.Float64).alias("max_stock"),
            pl.col("plifz").cast(pl.Int64).alias("planned_delivery_days"),
            pl.col("price_usd").fill_null(0.0).round(2),
            pl.col("maktx").fill_null("").alias("description"),
        )
        .sort(["plant", "material"])
    )


def _stock(mard: pl.DataFrame) -> pl.DataFrame:
    return mard.select(
        _material(),
        pl.col("werks").alias("plant"),
        pl.col("lgort").alias("storage_location"),
        pl.col("labst").cast(pl.Float64).alias("available_qty"),
        pl.col("umlme").cast(pl.Float64).alias("in_transit_qty"),
        pl.col("lminb").cast(pl.Float64).alias("reorder_point"),
    ).sort(["plant", "material", "storage_location"])


def _receipts(mseg: pl.DataFrame) -> pl.DataFrame:
    """First goods receipt date per purchase order."""
    return (
        mseg.filter((pl.col("bwart") == "101") & (pl.col("ebeln") != ""))
        .group_by("ebeln")
        .agg(_dats("budat_mkpf").min().alias("first_receipt_date"))
    )


def _vendors(
    t: dict[str, pl.DataFrame], receipts: pl.DataFrame, as_of: date, params: PlanningParams
) -> tuple[pl.DataFrame, pl.DataFrame]:
    window_start = as_of - timedelta(days=params.lateness_window_days)
    ekko, ekpo, eket = t["ekko"], t["ekpo"], t["eket"]
    orders = (
        ekko.select("ebeln", "lifnr", "bsart", "waers", _dats("aedat").alias("created"))
        .join(ekpo.select("ebeln", "ebelp", "menge", "netpr", "peinh"), on="ebeln")
        .join(eket.select("ebeln", "ebelp", _dats("eindt").alias("agreed_date")), on=["ebeln", "ebelp"])
        .join(receipts, on="ebeln", how="left")
        .filter(pl.col("created") >= window_start)
        .with_columns(
            (
                pl.col("menge").cast(pl.Float64)
                * pl.col("netpr").cast(pl.Float64)
                / pl.col("peinh").cast(pl.Float64).clip(lower_bound=1.0)
                / pl.when(pl.col("waers") == "CNY").then(params.cny_per_usd).otherwise(1.0)
            ).alias("value_usd"),
            (pl.col("first_receipt_date") - pl.col("agreed_date")).dt.total_days().alias("lateness"),
        )
    )
    standard = orders.filter(pl.col("bsart") == "NB")
    received = standard.filter(pl.col("first_receipt_date").is_not_null())
    reliability = received.group_by("lifnr").agg(
        (pl.col("lateness") <= 1).mean().alias("on_time_rate_12m"),
        pl.col("lateness").median().alias("median_lateness_days"),
    )
    spend = orders.group_by("lifnr").agg(
        pl.col("value_usd").sum().alias("spend_usd_12m"), pl.col("ebeln").n_unique().alias("po_count_12m")
    )
    # Effort is measured on expedite requests, the exchanges the intake agent would take over; emergency
    # buys and confirmation inquiries close in a message or two whatever the vendor.
    interactions = t.get("purchasing_interaction")
    if interactions is not None and "kind" in interactions.columns:
        interactions = interactions.filter(pl.col("kind") == "expedite_request")
    effort = (
        interactions.filter(pl.col("closed_at").is_not_null())
        .group_by("lifnr")
        .agg(
            pl.col("rounds").cast(pl.Float64).median().alias("median_rounds"),
            pl.col("days_to_resolution").cast(pl.Float64).median().alias("median_days_to_resolution"),
        )
        if interactions is not None and interactions.height
        else pl.DataFrame(
            schema={"lifnr": pl.String, "median_rounds": pl.Float64, "median_days_to_resolution": pl.Float64}
        )
    )
    conditions = t.get("supplier_condition")
    if conditions is None:
        conditions = pl.DataFrame(schema={"lifnr": pl.String})
    vendors = (
        t["lfa1"]
        .select("lifnr", pl.col("name1").alias("name"), pl.col("land1").alias("country"))
        .join(spend, on="lifnr", how="left")
        .join(reliability, on="lifnr", how="left")
        .join(effort, on="lifnr", how="left")
        .join(conditions, on="lifnr", how="left")
        .with_columns(
            pl.col("spend_usd_12m").fill_null(0.0).round(2),
            pl.col("po_count_12m").fill_null(0).cast(pl.Int64),
            pl.col("median_lateness_days").fill_null(0.0).clip(lower_bound=0.0).cast(pl.Float64),
        )
        .rename({"lifnr": "vendor"})
    )
    for column, default in (
        ("strategic", False),
        ("portal_onboarded", False),
        ("min_expedite_days", None),
        ("surcharge_pct_lt7", params.surcharge_default["lt7"]),
        ("surcharge_pct_7_13", params.surcharge_default["7_13"]),
        ("surcharge_pct_ge14", params.surcharge_default["ge14"]),
        ("expedite_freight_usd", params.expedite_freight_default),
    ):
        if column not in vendors.columns:
            vendors = vendors.with_columns(pl.lit(default).alias(column))
        elif default is not None:
            vendors = vendors.with_columns(pl.col(column).fill_null(default))
    numeric = ["surcharge_pct_lt7", "surcharge_pct_7_13", "surcharge_pct_ge14", "expedite_freight_usd"]
    vendors = vendors.with_columns([pl.col(c).cast(pl.Float64) for c in numeric])
    lateness = vendors.select("vendor", "median_lateness_days")
    return vendors.sort("vendor"), lateness


def _open_orders(
    ekko: pl.DataFrame,
    ekpo: pl.DataFrame,
    eket: pl.DataFrame,
    lateness: pl.DataFrame,
    as_of: date,
    params: PlanningParams,
) -> pl.DataFrame:
    orders = (
        eket.select("ebeln", "ebelp", _dats("eindt").alias("agreed_date"), "menge", "wemng")
        .with_columns((pl.col("menge").cast(pl.Float64) - pl.col("wemng").cast(pl.Float64)).alias("open_qty"))
        .filter(pl.col("open_qty") > 0)
        .join(ekpo.select("ebeln", "ebelp", "matnr", "werks", "netpr", "peinh"), on=["ebeln", "ebelp"])
        .join(ekko.select("ebeln", "lifnr", "bsart", "waers"), on="ebeln")
        .filter(pl.col("bsart") == "NB")
        .join(lateness, left_on="lifnr", right_on="vendor", how="left")
    )
    adjustment = pl.col("median_lateness_days").fill_null(0.0).clip(upper_bound=params.lateness_cap_days)
    expected = pl.col("agreed_date") + pl.duration(days=adjustment.cast(pl.Int64))
    floor = pl.lit(as_of + timedelta(days=1))
    return orders.select(
        "ebeln",
        "ebelp",
        _material(),
        pl.col("werks").alias("plant"),
        pl.col("lifnr").alias("vendor"),
        pl.col("open_qty"),
        "agreed_date",
        pl.when(expected < floor).then(floor).otherwise(expected).alias("expected_date"),
        (
            pl.col("netpr").cast(pl.Float64)
            / pl.col("peinh").cast(pl.Float64).clip(lower_bound=1.0)
            / pl.when(pl.col("waers") == "CNY").then(params.cny_per_usd).otherwise(1.0)
        )
        .round(2)
        .alias("unit_price_usd"),
    ).sort(["plant", "material", "expected_date", "ebeln"])


def _firm_demand(
    likp: pl.DataFrame, lips: pl.DataFrame, vbak: pl.DataFrame, vbap: pl.DataFrame, as_of: date
) -> pl.DataFrame:
    """Open outbound deliveries (planned goods issue) and sales-order lines without a delivery."""
    open_deliveries = (
        likp.filter(pl.col("wadat_ist").is_null() | (pl.col("wadat_ist") == ""))
        .select("vbeln", _dats("wadat").alias("requirement_date"))
        .join(lips.select("vbeln", "matnr", "werks", "lgmng"), on="vbeln")
        .select(
            _material(),
            pl.col("werks").alias("plant"),
            "requirement_date",
            pl.col("lgmng").cast(pl.Float64).alias("qty"),
            pl.lit("delivery").alias("source"),
        )
    )
    delivered = (
        lips.group_by(["vgbel", "vgpos"])
        .agg(pl.col("lgmng").cast(pl.Float64).sum().alias("delivered_qty"))
        .rename({"vgbel": "vbeln", "vgpos": "posnr"})
    )
    open_lines = (
        vbap.filter(pl.col("abgru") == "")
        .join(vbak.select("vbeln", _dats("vdatu").alias("requirement_date")), on="vbeln")
        .join(delivered, on=["vbeln", "posnr"], how="left")
        .with_columns(
            (
                pl.col("kwmeng").cast(pl.Float64)
                * pl.col("umvkz").cast(pl.Float64).clip(lower_bound=1.0)
                / pl.col("umvkn").cast(pl.Float64).clip(lower_bound=1.0)
                - pl.col("delivered_qty").fill_null(0.0)
            ).alias("qty")
        )
        .filter(pl.col("qty") > 0)
        .select(
            _material(),
            pl.col("werks").alias("plant"),
            "requirement_date",
            "qty",
            pl.lit("sales_order").alias("source"),
        )
    )
    return (
        pl.concat([open_deliveries, open_lines])
        .filter(pl.col("requirement_date").is_not_null())
        .sort(["plant", "material", "requirement_date", "source"])
    )


_DEMAND_SCHEMA = {
    "material": pl.String,
    "plant": pl.String,
    "requirement_date": pl.Date,
    "qty": pl.Float64,
    "source": pl.String,
}


def _reservations(resb: pl.DataFrame | None, bdart: str) -> pl.DataFrame:
    if resb is None or resb.height == 0:
        return pl.DataFrame(schema=_DEMAND_SCHEMA)
    frame = resb
    if "xloek" in frame.columns:
        frame = frame.filter(pl.col("xloek") == "")
    return (
        frame.filter(pl.col("bdart") == bdart)
        .with_columns((pl.col("bdmng").cast(pl.Float64) - pl.col("enmng").cast(pl.Float64)).alias("qty"))
        .filter(pl.col("qty") > 0)
        .select(
            _material(),
            pl.col("werks").alias("plant"),
            _dats("bdter").alias("requirement_date"),
            "qty",
            pl.lit("production" if bdart == "SB" else "planned_order").alias("source"),
        )
        .filter(pl.col("requirement_date").is_not_null())
        .sort(["plant", "material", "requirement_date"])
    )


def _production_demand(resb: pl.DataFrame | None) -> pl.DataFrame:
    """Open component reservations of released production orders (requirement type SB): firm demand."""
    return _reservations(resb, "SB")


def _planned_demand(resb: pl.DataFrame | None) -> pl.DataFrame:
    """Dependent requirements of planned orders (requirement type AR): the MRP forecast."""
    return _reservations(resb, "AR")


def manufacturing_outflow(params: PlanningParams) -> pl.Expr:
    """Issues from a main warehouse to the production supply area (staging, 311 issuing items) and their
    reversals (312 receiving items back at the warehouse): what the warehouse delivered to the manufacturing
    department. Quantities are signed with ``outflow_sign``."""
    condition = pl.lit(False)
    for plant in params.plants:
        for main in params.main_locations.get(plant, ()):
            condition = condition | ((pl.col("werks") == plant) & (pl.col("lgort") == main))
    issue = pl.col("bwart").is_in(["311", "313"]) & (pl.col("shkzg") == "H")
    reversal = (pl.col("bwart") == "312") & (pl.col("shkzg") == "S")
    return (issue | reversal) & (pl.col("umlgo") == PSA_LGORT) & condition


def outflow_sign() -> pl.Expr:
    """Signed quantity of an outflow row: reversals (312, 602) count negative."""
    return (
        pl.when(pl.col("bwart").is_in(["312", "602"]))
        .then(-pl.col("menge").cast(pl.Float64))
        .otherwise(pl.col("menge").cast(pl.Float64))
    )


def _demand_rate(mseg: pl.DataFrame, as_of: date, params: PlanningParams) -> pl.DataFrame:
    """Trailing daily demand per material and storage location: staging to the production supply area
    (plus customer goods issues net of reversals when customer demand is in scope)."""
    start = (as_of - timedelta(days=params.forecast_window_days - 1)).strftime(DATS)
    end = as_of.strftime(DATS)
    selector = manufacturing_outflow(params)
    if params.include_customer_demand:
        selector = selector | pl.col("bwart").is_in(list(CUSTOMER_ISSUE))
    issues = mseg.filter(
        selector
        & pl.col("werks").is_in(list(params.plants))
        & (pl.col("budat_mkpf") >= start)
        & (pl.col("budat_mkpf") <= end)
    )
    return (
        issues.with_columns(outflow_sign().alias("signed_qty"))
        .group_by(["matnr", "werks", "lgort"])
        .agg(
            (pl.col("signed_qty").sum() / params.forecast_window_days)
            .clip(lower_bound=0.0)
            .alias("daily_rate")
        )
        .select(
            _material(),
            pl.col("werks").alias("plant"),
            pl.col("lgort").alias("storage_location"),
            "daily_rate",
        )
        .sort(["plant", "material", "storage_location"])
    )


def _demand_weekly(mseg: pl.DataFrame, as_of: date, params: PlanningParams) -> pl.DataFrame:
    """Weekly outflow per material and plant over the ``sba_weeks`` weeks before the as-of day (week 0 the
    oldest): staging from a main warehouse to the production supply area net of reversals, plus customer goods
    issues at a main warehouse when customer demand is in scope (the backtest's realized outflow)."""
    start = as_of - timedelta(days=7 * params.sba_weeks)
    selector = manufacturing_outflow(params)
    if params.include_customer_demand:
        at_main = pl.lit(False)
        for plant in params.plants:
            for main in params.main_locations.get(plant, ()):
                at_main = at_main | ((pl.col("werks") == plant) & (pl.col("lgort") == main))
        selector = selector | (pl.col("bwart").is_in(["601", "602"]) & at_main)
    rows = mseg.filter(
        selector
        & pl.col("werks").is_in(list(params.plants))
        & (pl.col("budat_mkpf") >= start.strftime(DATS))
        & (pl.col("budat_mkpf") < as_of.strftime(DATS))
    )
    return (
        rows.with_columns(
            outflow_sign().alias("signed_qty"),
            ((pl.col("budat_mkpf").str.strptime(pl.Date, "%Y%m%d") - pl.lit(start)).dt.total_days() // 7)
            .cast(pl.Int64)
            .alias("week"),
        )
        .group_by(["matnr", "werks", "week"])
        .agg(pl.col("signed_qty").sum().alias("qty"))
        .select(_material(), pl.col("werks").alias("plant"), "week", "qty")
        .sort(["plant", "material", "week"])
    )


def weekly_series(weekly: pl.DataFrame | None, material: str, plant: str, weeks: int) -> np.ndarray | None:
    """One material's weekly outflow as an array (week 0 the oldest; negative weeks clipped to zero), or
    None when the inputs carry no history."""
    if weekly is None:
        return None
    series = np.zeros(weeks)
    rows = weekly.filter((pl.col("material") == material) & (pl.col("plant") == plant))
    for week, qty in rows.select("week", "qty").iter_rows():
        if 0 <= week < weeks:
            series[week] += qty
    return np.clip(series, 0.0, None)


def _sba_rate(weekly: pl.DataFrame, params: PlanningParams) -> pl.DataFrame:
    """Daily forecast rate per material and plant from the SBA model of its weekly outflow, keyed to the
    plant's first main warehouse (the engine sums the rates of a plant's main warehouses)."""
    rows = []
    for material, plant in (
        weekly.select("material", "plant").unique().sort(["plant", "material"]).iter_rows()
    ):
        series = weekly_series(weekly, material, plant, params.sba_weeks)
        rate, _ = sba(series, params.sba_alpha)
        rows.append(
            {
                "material": material,
                "plant": plant,
                "storage_location": params.main_locations[plant][0],
                "daily_rate": rate / 7,
            }
        )
    schema = {
        "material": pl.String,
        "plant": pl.String,
        "storage_location": pl.String,
        "daily_rate": pl.Float64,
    }
    return pl.DataFrame(rows, schema=schema)


def _material_vendor(ekko: pl.DataFrame, ekpo: pl.DataFrame) -> pl.DataFrame:
    return (
        ekpo.select("ebeln", "matnr", "werks")
        .join(ekko.select("ebeln", "lifnr", "aedat"), on="ebeln")
        .sort(["aedat", "ebeln"], descending=[True, True])
        .unique(subset=["matnr", "werks"], keep="first", maintain_order=True)
        .select(_material(), pl.col("werks").alias("plant"), pl.col("lifnr").alias("vendor"))
        .sort(["plant", "material"])
    )


def cost_parameters(frame: pl.DataFrame | None, params: PlanningParams) -> dict[str, float]:
    """Cost model values from ``raw_ext.cost_parameter`` (fallbacks from the parameters)."""
    return _cost_parameters(frame, params)


def _cost_parameters(frame: pl.DataFrame | None, params: PlanningParams) -> dict[str, float]:
    values = {
        "line_down_cost_a": params.line_down_cost["A"],
        "line_down_cost_b": params.line_down_cost["B"],
        "line_down_cost_c": params.line_down_cost["C"],
        "holding_rate_annual": params.holding_rate_annual,
        "transfer_cost_fixed": params.transfer_cost_fixed,
        "transfer_cost_per_unit": params.transfer_cost_per_unit,
        "order_cost": params.order_cost,
        "expedite_freight_default": params.expedite_freight_default,
    }
    if frame is not None and frame.height:
        values.update(
            {
                str(name): float(value)
                for name, value in zip(frame.get_column("parameter"), frame.get_column("value"), strict=True)
            }
        )
    return values
