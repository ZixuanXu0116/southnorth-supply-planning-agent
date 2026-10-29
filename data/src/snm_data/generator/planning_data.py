"""Planning master data and purchasing history (generator 0.6.1).

Three additive tables for the replenishment engine (PROPOSAL.md section 4):

* ``raw_ext.supplier_condition``: per vendor, the expedite terms (minimum expedite lead time, surcharge tiers
  by days gained, expedited freight), the strategic flag, and whether the vendor is onboarded to the
  supplier portal. Surcharges are commercial data.
* ``raw_ext.cost_parameter``: the cost model of the planning evaluation (line-down cost per criticality
  class, holding rate, transfer and order costs). Synthetic parameters, not a statement about any real cost.
* ``raw_ext.purchasing_interaction``: the purchasing team's recorded exchanges with vendors about purchase
  orders (expedite requests on late orders, emergency buys, confirmation inquiries): rounds, days to
  resolution, days gained, surcharge paid, and the pseudonymized buyer. This is the human baseline against
  which the planner agent's handling of tail cases is measured.

All draws come from the component's own random stream, so every other table stays byte-identical.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import polars as pl

from .config import Profile

COMPONENT = 80
STRATEGIC_VENDORS = 3
PORTAL_SHARE = 0.7
# Surcharge tiers (percent of line value) by days gained: under 7, 7 to 13, 14 or more (PROPOSAL section 14).
BASE_SURCHARGES = (10.0, 15.0, 25.0)
LATE_THRESHOLD_DAYS = 3
INQUIRY_SHARE = 0.15
BUYERS = ("BUY0001", "BUY0002", "BUY0003", "BUY0004")

PER_PART_DAY = "USD per part per day"
COST_PARAMETERS = (
    ("line_down_cost_a", 5000.00, PER_PART_DAY, "Line-down cost of a criticality A part per day short."),
    ("line_down_cost_b", 1000.00, PER_PART_DAY, "Line-down cost of a criticality B part per day short."),
    ("line_down_cost_c", 200.00, PER_PART_DAY, "Line-down cost of a criticality C part per day short."),
    ("holding_rate_annual", 0.20, "fraction per year", "Annual holding cost as a share of valuation price."),
    ("transfer_cost_fixed", 40.00, "USD per transfer", "Fixed cost of one transfer from a hub."),
    ("transfer_cost_per_unit", 0.50, "USD per unit", "Variable cost per unit transferred."),
    ("order_cost", 75.00, "USD per purchase order", "Processing cost of one new purchase order."),
    ("expedite_freight_default", 150.00, "USD per shipment", "Expedited freight for vendors without terms."),
)


@dataclass
class PlanningDataResult:
    tables: dict[str, pl.DataFrame]
    facts: dict[str, pl.DataFrame] = field(default_factory=dict)


def _round_half(values: np.ndarray) -> np.ndarray:
    return np.round(values * 2) / 2


def generate_supplier_conditions(
    profile: Profile, lfa1: pl.DataFrame, orders: pl.DataFrame, rng: np.random.Generator
) -> pl.DataFrame:
    """Expedite terms per vendor; the three vendors with the largest order value are strategic."""
    vendors = lfa1.get_column("lifnr").to_list()
    spend = (
        orders.with_columns((pl.col("qty") * pl.col("stprs")).alias("value"))
        .group_by("lifnr")
        .agg(pl.col("value").sum())
    )
    spend_by_vendor = dict(
        zip(spend.get_column("lifnr").to_list(), spend.get_column("value").to_list(), strict=True)
    )
    ranked = sorted(vendors, key=lambda v: (-spend_by_vendor.get(v, 0.0), v))
    strategic = {v for v in ranked[:STRATEGIC_VENDORS]}
    n = len(vendors)
    factor = rng.uniform(0.7, 1.3, size=n)
    tiers = [_round_half(base * factor) for base in BASE_SURCHARGES]
    min_days = rng.integers(3, 8, size=n)
    freight = np.round(rng.uniform(80, 250, size=n), -1)
    onboarded = rng.random(n) < PORTAL_SHARE
    return pl.DataFrame(
        {
            "lifnr": vendors,
            "strategic": [v in strategic for v in vendors],
            "min_expedite_days": min_days,
            "surcharge_pct_lt7": tiers[0],
            "surcharge_pct_7_13": tiers[1],
            "surcharge_pct_ge14": tiers[2],
            "expedite_freight_usd": freight,
            "portal_onboarded": [
                bool(flag) and vendor not in strategic
                for flag, vendor in zip(onboarded, vendors, strict=True)
            ],
            "valid_from": [profile.start] * n,
        }
    ).sort("lifnr")


def generate_cost_parameters() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "parameter": [row[0] for row in COST_PARAMETERS],
            "value": [row[1] for row in COST_PARAMETERS],
            "unit": [row[2] for row in COST_PARAMETERS],
            "description": [row[3] for row in COST_PARAMETERS],
        }
    )


def _surcharge_for(days_gained: int, condition: dict[str, float]) -> float:
    if days_gained >= 14:
        return condition["surcharge_pct_ge14"]
    if days_gained >= 7:
        return condition["surcharge_pct_7_13"]
    return condition["surcharge_pct_lt7"]


def generate_purchasing_interactions(
    profile: Profile,
    orders: pl.DataFrame,
    ledger: pl.DataFrame,
    conditions: pl.DataFrame,
    rng: np.random.Generator,
) -> pl.DataFrame:
    """Interaction records derived from the purchase orders that existed at the extraction.

    Late standard orders (first receipt three or more days after the agreed date, or not received three
    days after it) get an expedite request; emergency buys (ZEX) get an emergency-buy record; a share of
    the other orders gets a confirmation inquiry. Effort per vendor is latent: strategic vendors answer
    fast in few rounds, tail vendors take more rounds and days.
    """
    as_of = profile.ecc_as_of.date()
    receipts = (
        ledger.filter(pl.col("kind") == "101")
        .group_by("ebeln")
        .agg(pl.col("local_ts").min().dt.date().alias("first_receipt"))
    )
    frame = (
        orders.select(
            "ebeln",
            "lifnr",
            "werks",
            pl.col("planned"),
            pl.col("created_local").dt.date().alias("created"),
            "expedited",
        )
        .join(receipts, on="ebeln", how="left")
        .sort("ebeln")
    )
    vendors = conditions.get_column("lifnr").to_list()
    strategic = dict(zip(vendors, conditions.get_column("strategic").to_list(), strict=True))
    condition_rows = {row["lifnr"]: row for row in conditions.to_dicts()}
    effort = {
        vendor: (rng.uniform(0.2, 0.45) if strategic[vendor] else rng.uniform(0.4, 1.0)) for vendor in vendors
    }
    rows: list[dict] = []
    for record in frame.iter_rows(named=True):
        planned: date = record["planned"]
        received: date | None = record["first_receipt"]
        lateness = (received - planned).days if received else (as_of - planned).days
        if record["expedited"]:
            kind = "emergency_buy"
        elif lateness >= LATE_THRESHOLD_DAYS:
            kind = "expedite_request"
        elif rng.random() < INQUIRY_SHARE:
            kind = "date_inquiry"
        else:
            continue
        vendor = record["lifnr"]
        e = effort[vendor]
        if kind == "emergency_buy":
            opened = record["created"]
            rounds = 1 + int(rng.poisson(0.4))
            days = int(rng.integers(0, 2))
            days_gained = int(rng.integers(5, 21))
            surcharge = float(condition_rows[vendor]["surcharge_pct_ge14"])
            outcome = "expedited"
        elif kind == "expedite_request":
            opened = max(record["created"], planned - timedelta(days=3))
            rounds = 1 + int(rng.poisson(3.0 * e))
            days = int(np.ceil(rounds * rng.uniform(0.5, 2.5) * (0.5 + e)))
            if rng.random() < 0.55:
                days_gained = int(min(max(lateness, 1), 1 + rng.integers(1, 15)))
                surcharge = _surcharge_for(days_gained, condition_rows[vendor]) if rng.random() < 0.6 else 0.0
                outcome = "expedited"
            else:
                days_gained, surcharge, outcome = 0, 0.0, "no_change"
        else:
            opened = max(record["created"], planned - timedelta(days=int(rng.integers(5, 15))))
            rounds = 1 + int(rng.poisson(0.8 * e))
            days = int(rng.integers(1, 4))
            days_gained, surcharge, outcome = 0, 0.0, "confirmed"
        closed = opened + timedelta(days=days)
        rows.append(
            {
                "ebeln": record["ebeln"],
                "lifnr": vendor,
                "werks": record["werks"],
                "kind": kind,
                "opened_at": opened,
                "closed_at": closed if closed <= as_of else None,
                "rounds": rounds,
                "days_to_resolution": days if closed <= as_of else None,
                "days_gained": days_gained,
                "surcharge_pct": surcharge,
                "outcome": outcome if closed <= as_of else "open",
                "handled_by": BUYERS[int(rng.integers(len(BUYERS)))],
            }
        )
    if not rows:
        return pl.DataFrame(
            schema={
                "interaction_id": pl.String, "ebeln": pl.String, "ebelp": pl.String, "lifnr": pl.String,
                "werks": pl.String, "kind": pl.String, "opened_at": pl.Date, "closed_at": pl.Date,
                "rounds": pl.Int32, "days_to_resolution": pl.Int32, "days_gained": pl.Int32,
                "surcharge_pct": pl.Float64, "outcome": pl.String, "handled_by": pl.String,
            }
        )  # fmt: skip
    interactions = pl.DataFrame(rows).sort(["opened_at", "ebeln"]).with_row_index("seq")
    return interactions.select(
        ("PI" + (pl.col("seq") + 1).cast(pl.String).str.zfill(8)).alias("interaction_id"),
        "ebeln",
        pl.lit("00010").alias("ebelp"),
        "lifnr",
        "werks",
        "kind",
        "opened_at",
        "closed_at",
        pl.col("rounds").cast(pl.Int32),
        pl.col("days_to_resolution").cast(pl.Int32),
        pl.col("days_gained").cast(pl.Int32),
        "surcharge_pct",
        "outcome",
        "handled_by",
    )


def generate_planning_data(
    profile: Profile, lfa1: pl.DataFrame, orders: pl.DataFrame, ledger: pl.DataFrame
) -> PlanningDataResult:
    rng = np.random.default_rng([profile.seed, COMPONENT])
    conditions = generate_supplier_conditions(profile, lfa1, orders, rng)
    interactions = generate_purchasing_interactions(profile, orders, ledger, conditions, rng)
    return PlanningDataResult(
        tables={
            "raw_ext.supplier_condition": conditions,
            "raw_ext.cost_parameter": generate_cost_parameters(),
            "raw_ext.purchasing_interaction": interactions,
        }
    )
