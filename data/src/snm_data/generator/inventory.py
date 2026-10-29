"""Inventory: purchasing, receipts, quality inspection, bonded clearance, consignment, the pulls and moves
between the main warehouse and its overflow hub, current stock, and daily stock snapshots.

A daily simulation per plant decides every stock movement. Material documents are the single ledger:
``MARD`` (stock at ECC extraction) and ``raw_ext.inventory_snapshot_daily`` are both derived from the
same movements, so they reconcile by construction. Customer goods issues (601/602) come from the outbound
generator and are replayed here as demand.

Conventions (documented in the contracts and the knowledge base):
- Transfer postings (311, 313, 321, 411) have two items: the issuing side (SHKZG H) and the receiving side
  (SHKZG S, XAUTO = 'X'). 313 puts stock "in transfer" at the receiving storage location; 315 is a single
  receipt item that moves it from transfer to unrestricted stock.
- 101 goods receipts reference a purchase order; receipts into quality inspection carry INSMK = 'X';
  vendor consignment receipts carry SOBKZ = 'K' and are not valuated (DMBTR = 0).
- 561 posts opening balances on the day before the first fiscal year of the data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import numpy as np
import polars as pl

from .calendar import fiscal_years_of
from .config import Profile
from .manufacturing import COMPONENT as PRODUCTION_COMPONENT
from .manufacturing import PSA_LGORT, ProductionPlan, render
from .org import ECC_SYSTEM_TZ, MANDT, SITES
from .scenarios import A3_FACTOR, A3_MATERIAL_GROUP, A3_START
from .timeutil import dats, local_to_utc, minutes, tims, utc_to_local, zfill

# Round Rock's overflow hub (decision record 003): cheaper storage with no demand of its own. The storage
# locations 2002 and 2003 exist but carry no flows.
OVERFLOW_HUB = "2001"
HUB_RECEIPT_SHARE = 0.3  # share of supplier deliveries received at the overflow hub
MAIN_MAX_DAYS = 45  # maximum stock of the main warehouse above its minimum, in days of demand
REVIEW_WEEKDAY = 1  # Monday: logistics moves what the main warehouse holds above its maximum
HUB_OPENING_DAYS = 30  # opening stock of the overflow hub, in days of demand
QI_GROUPS = frozenset({"SP-GAS", "SP-SEN", "SP-RFP"})
QI_SHARE = 0.6
BONDED_SHARE = 0.5
CONSIGNMENT_SHARE = 0.4
HUB_SAFETY_DAYS = 14
SAFETY_FACTOR = 3.0
SAFETY_CAP_DAYS = 30  # the safety stock is at most this many days of demand
HUB_ORDER_DAYS = 30
INITIAL_COVER_DAYS = 45
SPOKE_MIN_DAYS, SPOKE_QTY_DAYS = 5, 14
CONSIGNMENT_TARGET_DAYS = 5

# Fictional suppliers: (name, country, city).
VENDORS = (
    ("Kestrel Vacuum Components", "US", "Fremont"),
    ("Harrow Precision Seals", "US", "Austin"),
    ("Blue Fen Electronics", "US", "Tempe"),
    ("Corvid Gas Systems", "US", "Chandler"),
    ("Tamsin Fastening Works", "US", "Dallas"),
    ("Oriel Motion Robotics", "JP", "Yokohama"),
    ("Sakuragawa Ceramics", "JP", "Nagoya"),
    ("Hoshino RF Devices", "JP", "Kawasaki"),
    ("Brennwald Vakuumtechnik", "DE", "Dresden"),
    ("Lindmark Sensorik", "DE", "Jena"),
    ("Falkenrath Dichtungen", "DE", "Stuttgart"),
    ("Jade Harbor Precision", "TW", "Hsinchu"),
    ("Formosa Quartz Works", "TW", "Taichung"),
    ("Hanul Process Parts", "KR", "Pyeongtaek"),
    ("Seoin Flow Control", "KR", "Suwon"),
    ("Pearl Delta Consumables", "CN", "Shenzhen"),
    ("Songshan Metal Parts", "CN", "Dongguan"),
    ("Qiantang Cleanroom Supply", "CN", "Hangzhou"),
    ("Straits Machining", "MY", "Penang"),
    ("Kinta Valley Electronics", "MY", "Ipoh"),
)


@dataclass
class InventoryResult:
    tables: dict[str, pl.DataFrame]
    facts: dict[str, pl.DataFrame] = field(default_factory=dict)


class Ledger:
    """Movement events collected during the simulation (one row per stock movement, before items)."""

    def __init__(self) -> None:
        self.parts: list[dict[str, np.ndarray]] = []

    def add(
        self, kind: str, day: int, minute: np.ndarray, material: np.ndarray, qty: np.ndarray, **extra
    ) -> None:
        count = material.size
        if count == 0:
            return
        part = {
            "kind": np.full(count, kind, dtype=object),
            "day": np.full(count, day, dtype=np.int32),
            "minute": np.asarray(minute, dtype=float).reshape(-1) * np.ones(count),
            "material": material.astype(np.int32),
            "qty": qty.astype(float),
        }
        for key, value in extra.items():
            part[key] = np.broadcast_to(np.asarray(value, dtype=object), (count,)).copy()
        self.parts.append(part)

    def frame(self) -> pl.DataFrame:
        columns = sorted({key for part in self.parts for key in part})
        data: dict[str, list] = {column: [] for column in columns}
        for part in self.parts:
            count = part["material"].size
            for column in columns:
                data[column].append(part.get(column, np.full(count, None, dtype=object)))
        merged = {column: np.concatenate(values) for column, values in data.items()}
        return pl.DataFrame(
            {
                "kind": merged["kind"].astype(str),
                "day": merged["day"].astype(np.int32),
                "minute": merged["minute"].astype(float),
                "material": merged["material"].astype(np.int32),
                "qty": merged["qty"].astype(float),
                **{
                    key: pl.Series(key, merged[key].tolist(), dtype=pl.String)
                    for key in columns
                    if key not in {"kind", "day", "minute", "material", "qty"}
                },
            }
        )


@dataclass
class PurchaseOrder:
    material: int
    created_day: int
    created_minute: float
    qty: float
    planned_day: int
    arrival_day: int
    target: str  # "U" unrestricted, "Q" quality inspection, "B" bonded, "K" consignment
    lgort: str
    expedited: bool = False


def _working(day: date, days: tuple[int, ...]) -> bool:
    return day.isoweekday() in days


def _next_working(offset: int, start: date, days: tuple[int, ...]) -> int:
    while not _working(start + timedelta(days=offset), days):
        offset += 1
    return offset


def _vendors(
    materials: pl.DataFrame, rng: np.random.Generator
) -> tuple[pl.DataFrame, np.ndarray, np.ndarray]:
    """Vendor master and each material's supplier (a vendor from its country of origin when possible)."""
    lfa1 = pl.DataFrame(
        {
            "mandt": MANDT,
            "lifnr": zfill(100_001 + np.arange(len(VENDORS)), 10),
            "name1": [name for name, _, _ in VENDORS],
            "land1": [country for _, country, _ in VENDORS],
            "ort01": [city for _, _, city in VENDORS],
        }
    )
    countries = np.array([country for _, country, _ in VENDORS])
    supplier = np.empty(materials.height, dtype=np.int64)
    for index, origin in enumerate(materials.get_column("herkl").to_list()):
        candidates = np.flatnonzero(countries == origin)
        if candidates.size == 0:
            candidates = np.arange(len(VENDORS))
        supplier[index] = rng.choice(candidates)
    late_probability = rng.uniform(0.03, 0.35, size=len(VENDORS))
    return lfa1, supplier, late_probability


def _demand_matrix(
    events: pl.DataFrame, items: pl.DataFrame, site: str, index: dict[str, int], first: date, n_days: int
) -> np.ndarray:
    """Net customer goods-issue quantity per site-local posting day and material (602 counts negative)."""
    matrix = np.zeros((n_days, len(index)))
    subset = events.filter(pl.col("site") == site)
    if subset.height == 0:
        return matrix
    local_day = utc_to_local(subset.get_column("event_utc").to_numpy(), SITES[site].tz).astype(
        "datetime64[D]"
    )
    subset = subset.with_columns(pl.Series("local_day", local_day))
    lines = subset.join(items.select("delivery_id", "matnr", "base_qty"), on="delivery_id")
    sign = np.where(lines.get_column("kind").to_numpy() == "REV", -1.0, 1.0)
    day_index = (lines.get_column("local_day").to_numpy() - np.datetime64(first)).astype(int)
    if day_index.size and day_index.min() < 1:
        raise AssertionError("goods issue before the simulated period")
    material_index = np.array([index[m] for m in lines.get_column("matnr").to_list()], dtype=np.int64)
    np.add.at(matrix, (day_index, material_index), sign * lines.get_column("base_qty").to_numpy())
    return matrix


def _staged_days(events: pl.DataFrame, items: pl.DataFrame, first: date) -> pl.DataFrame:
    """The site-local day on which each production-order line was staged by its delivery's goods issue
    (the last goods-issue event of the delivery; a reversal without a repost leaves the line unstaged)."""
    if events.height == 0:
        return pl.DataFrame(schema={"plan_order_id": pl.Int64, "rspos": pl.Int64, "staged_day": pl.Int64})
    frames = []
    for code, site in SITES.items():
        subset = events.filter(pl.col("site") == code)
        if subset.height == 0:
            continue
        local_day = utc_to_local(subset.get_column("event_utc").to_numpy(), site.tz).astype("datetime64[D]")
        frames.append(subset.with_columns(pl.Series("local_day", local_day)))
    last = (
        pl.concat(frames)
        .sort(["delivery_id", "event_utc"])
        .group_by("delivery_id")
        .agg(pl.col("kind").last().alias("last_kind"), pl.col("local_day").last().alias("local_day"))
        .filter(pl.col("last_kind") != "REV")
    )
    return (
        items.select("delivery_id", "plan_order_id", "rspos")
        .join(last, on="delivery_id")
        .with_columns(
            (pl.col("local_day") - pl.lit(np.datetime64(first)))
            .dt.total_days()
            .cast(pl.Int64)
            .alias("staged_day"),
            pl.col("plan_order_id").cast(pl.Int64),
            pl.col("rspos").cast(pl.Int64),
        )
        .select("plan_order_id", "rspos", "staged_day")
    )


class PlantSimulation:
    """Daily stock simulation for one plant (US10: main warehouse and overflow hub; CN10: bonded, consignment,
    Dongguan overflow hub with two-step transfers)."""

    def __init__(
        self,
        profile: Profile,
        werks: str,
        materials: pl.DataFrame,
        stprs: np.ndarray,
        supplier: np.ndarray,
        late_probability: np.ndarray,
        hub_demand: np.ndarray,
        first: date,
        rng: np.random.Generator,
        spoke_demand: np.ndarray | None = None,
        production: pl.DataFrame | None = None,
    ):
        self.profile = profile
        self.werks = werks
        self.site = "RR" if werks == "US10" else "SZ"
        self.tz = SITES[self.site].tz
        self.materials = materials
        self.n = materials.height
        self.stprs = stprs
        self.supplier = supplier
        self.late_probability = late_probability
        self.first = first
        self.rng = rng
        self.hub_demand = hub_demand
        self.n_days = hub_demand.shape[0]
        self.ledger = Ledger()
        self.orders: list[PurchaseOrder] = []
        self.snapshots: list[pl.DataFrame] = []
        groups = materials.get_column("matkl").to_numpy()
        self.qi = np.isin(groups, list(QI_GROUPS))
        self.plifz = materials.get_column("plifz").to_numpy().astype(int)
        horizon = max(self.n_days - 1, 1)
        self.hub_rate = np.clip(hub_demand[1:].sum(axis=0), 0, None) / horizon

        cn = werks == "CN10"
        self.spoke = cn
        self.spoke_demand = spoke_demand if spoke_demand is not None else np.zeros_like(hub_demand)
        self.dg = materials.get_column("in_dg").to_numpy() if cn else np.zeros(self.n, dtype=bool)
        self.bonded = (
            (materials.get_column("herkl").to_numpy() != "CN") if cn else np.zeros(self.n, dtype=bool)
        )
        self.consignment = (
            (groups == "SP-CON") & (rng.random(self.n) < CONSIGNMENT_SHARE)
            if cn
            else np.zeros(self.n, dtype=bool)
        )
        self.spoke_rate = np.clip(self.spoke_demand[1:].sum(axis=0), 0, None) / horizon
        spoke_sigma = np.clip(self.spoke_demand[1:], 0, None).std(axis=0)
        # The hub issues whole picking-list lines: its reorder point keeps one largest issue on top of the
        # usual cover, and a replenishment brings at least one largest issue.
        spoke_peak = np.clip(self.spoke_demand[1:], 0, None).max(axis=0)
        self.spoke_min = np.where(
            self.dg,
            np.maximum(1, np.ceil(self.spoke_rate * SPOKE_MIN_DAYS + 4.0 * spoke_sigma + spoke_peak)),
            0,
        )
        self.spoke_qty = np.where(
            self.dg, np.maximum(1, np.ceil(np.maximum(self.spoke_rate * SPOKE_QTY_DAYS, spoke_peak))), 0
        )
        # A3: from A3_START the replenishment parameters of one group at the spoke are set far too low.
        self.a3_day = (A3_START - first).days
        lowered = self.dg & (groups == A3_MATERIAL_GROUP)
        self.spoke_min_low = np.where(
            lowered, np.maximum(1, np.ceil(self.spoke_min * A3_FACTOR)), self.spoke_min
        )
        self.spoke_qty_low = np.where(
            lowered, np.maximum(1, np.ceil(self.spoke_qty * A3_FACTOR)), self.spoke_qty
        )

        # Round Rock: the overflow hub holds what the main warehouse does not need right away.
        self.overflow = werks == "US10"
        self.hub_used = np.zeros(self.n, dtype=bool)  # materials that ever had stock at the overflow hub

        # Production supply: component lines of the plant's production orders (manufacturing.py).
        lines = production if production is not None else pl.DataFrame(
            schema={"order_id": pl.Int64, "line": pl.Int64, "material": pl.Int64, "qty": pl.Float64,
                    "due_day": pl.Int64, "stage_day": pl.Int64, "staged_day": pl.Int64, "aufnr": pl.String}
        )  # fmt: skip
        self.p_order = lines.get_column("order_id").to_numpy().astype(np.int64)
        self.p_material = lines.get_column("material").to_numpy().astype(np.int64)
        self.p_qty = lines.get_column("qty").to_numpy().astype(float)
        self.p_due = lines.get_column("due_day").to_numpy().astype(np.int64)
        self.p_stage = lines.get_column("stage_day").to_numpy().astype(np.int64)
        self.p_staged = lines.get_column("staged_day").to_numpy().astype(np.int64)
        self.p_aufnr = lines.get_column("aufnr").to_numpy().astype(object)
        self.p_remaining = self.p_qty.copy()
        self.order_started: dict[int, int] = {}  # order -> start day
        self.order_started_minute: dict[int, float] = {}  # order -> site-local minute of the start
        self.P = np.zeros(self.n)
        # The plant's demand is what leaves its warehouses for the production supply area (the goods issues
        # of the picking lists, replayed from the deliveries); the production orders' reservations are the
        # same demand seen from manufacturing and are not added again.
        total_rate = self.hub_rate + self.spoke_rate
        self.total_rate = total_rate
        # Demand is lumpy (whole picking-list lines), so the safety stock covers its variability over the
        # protection period, capped at SAFETY_CAP_DAYS of demand so that a short history does not inflate it.
        daily = np.clip(hub_demand[1:] + self.spoke_demand[1:], 0, None)
        sigma = daily.std(axis=0)
        protection = self.plifz + HUB_SAFETY_DAYS
        safety = np.minimum(SAFETY_FACTOR * sigma * np.sqrt(protection), total_rate * SAFETY_CAP_DAYS)
        # Min-max of the main warehouse (MARC.EISBE and MABST): the minimum is the safety stock plus
        # HUB_SAFETY_DAYS of demand; the reorder point of the plant adds the lead-time demand; the maximum
        # adds MAIN_MAX_DAYS of demand to the minimum (at least five units). Below the minimum the main
        # warehouse pulls from the overflow hub, above the maximum it moves stock out.
        # The minimum also keeps one largest daily issue on hand: picking-list lines are whole lots.
        peak = daily.max(axis=0) if daily.shape[0] else np.zeros(self.n)
        self.main_min = np.ceil(np.maximum(total_rate * HUB_SAFETY_DAYS + safety, peak))
        self.reorder_point = np.ceil(self.main_min + total_rate * self.plifz)
        self.order_qty = np.maximum(1, np.ceil(total_rate * HUB_ORDER_DAYS))
        self.main_max = np.maximum(np.ceil(self.main_min + total_rate * MAIN_MAX_DAYS), 5.0)

        # State: own unrestricted U, quality Q, bonded B, consignment K, overflow hub H, spoke U/T, supply
        # area P.
        self.U = np.zeros(self.n)
        self.Q = np.zeros(self.n)
        self.B = np.zeros(self.n)
        self.K = np.zeros(self.n)
        self.DU = np.zeros(self.n)
        self.DT = np.zeros(self.n)
        self.H = np.zeros(self.n)
        self.on_order = np.zeros(self.n)
        self.arrivals: dict[int, list[int]] = {}
        self.qi_releases: dict[int, list[tuple[np.ndarray, np.ndarray]]] = {}
        self.clearances: dict[int, list[tuple[np.ndarray, np.ndarray]]] = {}
        self.transit: dict[int, list[tuple[np.ndarray, np.ndarray]]] = {}

    # -- helpers ------------------------------------------------------------------------------

    def _minutes(self, count: int, low: float, high: float) -> np.ndarray:
        return self.rng.uniform(low * 60, high * 60, size=count)

    def _day(self, offset: int) -> date:
        return self.first + timedelta(days=offset)

    def _receipt_days(self) -> tuple[int, ...]:
        return (1, 2, 3, 4, 5) if self.site == "RR" else (1, 2, 3, 4, 5, 6)

    def _place_order(self, material: int, day: int, qty: float, expedited: bool = False) -> PurchaseOrder:
        vendor = self.supplier[material]
        planned = day if expedited else day + int(self.plifz[material])
        actual = planned
        if not expedited:
            draw = self.rng.random()
            if draw < self.late_probability[vendor]:
                actual += int(self.rng.integers(1, 11))
            elif draw > 0.9:
                actual -= int(self.rng.integers(1, 4))
            actual = _next_working(max(actual, day + 1), self.first, self._receipt_days())
            planned = _next_working(planned, self.first, self._receipt_days())
        if expedited:
            target, lgort = "U", "1000"  # emergency buys go straight to own unrestricted stock
        elif self.consignment[material]:
            target, lgort = "K", "1000"
        elif self.bonded[material] and self.rng.random() < BONDED_SHARE:
            target, lgort = "B", "1900"
        elif self.qi[material] and self.rng.random() < QI_SHARE:
            target, lgort = "Q", "1000"
        elif self.overflow and self.rng.random() < HUB_RECEIPT_SHARE:
            target, lgort = "U", OVERFLOW_HUB
        else:
            target, lgort = "U", "1000"
        order = PurchaseOrder(
            material=material,
            created_day=day,
            created_minute=float(
                self.rng.uniform(6 * 60, 6.3 * 60) if expedited else self.rng.uniform(9 * 60, 16 * 60)
            ),
            qty=float(qty),
            planned_day=planned,
            arrival_day=actual,
            target=target,
            lgort=lgort,
            expedited=expedited,
        )
        self.orders.append(order)
        self.on_order[material] += qty
        if not expedited:
            self.arrivals.setdefault(actual, []).append(len(self.orders) - 1)
        return order

    def _receive(self, order_id: int, day: int, minute: float | None = None) -> None:
        order = self.orders[order_id]
        material = np.array([order.material])
        qty = np.array([order.qty])
        stamp = minute if minute is not None else float(self.rng.uniform(7 * 60, 10 * 60))
        self.ledger.add(
            "101", day, stamp, material, qty, po=str(order_id), lgort=order.lgort, target=order.target
        )
        self.on_order[order.material] -= order.qty
        if order.target == "U" and order.lgort == OVERFLOW_HUB:
            self.H[order.material] += order.qty
            self.hub_used[order.material] = True
        elif order.target == "U":
            self.U[order.material] += order.qty
        elif order.target == "Q":
            self.Q[order.material] += order.qty
            release = _next_working(day + int(self.rng.integers(1, 5)), self.first, self._receipt_days())
            self.qi_releases.setdefault(release, []).append((material, qty))
        elif order.target == "B":
            self.B[order.material] += order.qty
            clearance = _next_working(day + int(self.rng.integers(2, 8)), self.first, self._receipt_days())
            self.clearances.setdefault(clearance, []).append((material, qty))
        else:
            self.K[order.material] += order.qty

    def spoke_min_at(self, d: int) -> np.ndarray:
        """Reorder point per material at the spoke on day ``d`` (MARD.LMINB of storage location 3000)."""
        return self.spoke_min_low if d >= self.a3_day else self.spoke_min

    def spoke_qty_at(self, d: int) -> np.ndarray:
        """Replenishment quantity per material at the spoke on day ``d`` (MARD.LBSTF of location 3000)."""
        return self.spoke_qty_low if d >= self.a3_day else self.spoke_qty

    def _expedite(self, day: int, shortage: np.ndarray) -> None:
        """Emergency purchase order received the same morning (document type ZEX)."""
        for material in np.flatnonzero(shortage > 1e-9):
            qty = float(np.ceil(shortage[material] + self.order_qty[material] / 2))
            order = self._place_order(int(material), day, qty, expedited=True)
            self._receive(len(self.orders) - 1, day, minute=order.created_minute + 20)

    # -- simulation ---------------------------------------------------------------------------

    def opening_balances(self) -> None:
        opening = np.maximum(np.ceil(self.total_rate * INITIAL_COVER_DAYS), self.reorder_point)
        opening = np.where(opening > 0, opening, self.rng.integers(1, 6, self.n))
        opening = np.where(self.consignment, 0, opening)
        consignment_opening = np.where(self.consignment, np.ceil(self.total_rate * INITIAL_COVER_DAYS) + 1, 0)
        if self.overflow:
            # The main warehouse opens somewhere between its minimum and its maximum; the overflow hub holds a
            # month of demand on top.
            level = self.rng.uniform(0.3, 1.0, self.n)
            main_opening = np.ceil(self.main_min + level * (self.main_max - self.main_min))
            hub_opening = np.ceil(self.total_rate * HUB_OPENING_DAYS)
        else:
            main_opening, hub_opening = opening, np.zeros(self.n)
        rows = np.flatnonzero(main_opening > 0)
        self.ledger.add(
            "561", 0, self._minutes(rows.size, 6, 7), rows, main_opening[rows], lgort="1000", target="U"
        )
        self.U += main_opening
        rows = np.flatnonzero(hub_opening > 0)
        if rows.size:
            self.ledger.add(
                "561",
                0,
                self._minutes(rows.size, 6, 7),
                rows,
                hub_opening[rows],
                lgort=OVERFLOW_HUB,
                target="U",
            )
            self.H += hub_opening
            self.hub_used[rows] = True
        rows = np.flatnonzero(consignment_opening > 0)
        self.ledger.add(
            "561",
            0,
            self._minutes(rows.size, 6, 7),
            rows,
            consignment_opening[rows],
            lgort="1000",
            target="K",
        )
        self.K += consignment_opening
        if self.spoke:
            dg_open = np.where(self.dg, self.spoke_min_at(0) + self.spoke_qty_at(0), 0)
            rows = np.flatnonzero(dg_open > 0)
            self.ledger.add(
                "561", 0, self._minutes(rows.size, 6, 7), rows, dg_open[rows], lgort="3000", target="U"
            )
            self.DU += dg_open

    def step(self, d: int) -> None:
        day = self._day(d)
        weekday = day.isoweekday()
        # Receipts from purchase orders, QI releases, bonded clearances, spoke arrivals (morning).
        for order_id in self.arrivals.pop(d, []):
            self._receive(order_id, d)
        for material, qty in self.qi_releases.pop(d, []):
            moved = np.minimum(qty, self.Q[material])
            self.ledger.add("321", d, self._minutes(material.size, 9, 11), material, moved, lgort="1000")
            self.Q[material] -= moved
            self.U[material] += moved
        for material, qty in self.clearances.pop(d, []):
            self.ledger.add(
                "311", d, self._minutes(material.size, 10, 12), material, qty, lgort="1900", umlgo="1000"
            )
            self.B[material] -= qty
            self.U[material] += qty
        for material, qty in self.transit.pop(d, []):
            self.ledger.add(
                "315", d, self._minutes(material.size, 8, 10), material, qty, lgort="3000", umlgo="1000"
            )
            self.DT[material] -= qty
            self.DU[material] += qty

        # Vendor consignment: withdraw to own stock when own stock runs low (07:30).
        if self.consignment.any():
            target = np.ceil(self.total_rate * CONSIGNMENT_TARGET_DAYS) + self.hub_demand[d].clip(0)
            need = np.where(self.consignment, np.clip(2 * target - self.U, 0, None), 0)
            need = np.where(target > self.U, np.minimum(need, self.K), 0)
            rows = np.flatnonzero(need > 0)
            self.ledger.add("411", d, np.full(rows.size, 7.5 * 60), rows, need[rows], lgort="1000")
            self.K[rows] -= need[rows]
            self.U[rows] += need[rows]

        # Replenishment transfers (11:00-15:00): Longgang -> Dongguan (two-step).
        if self.spoke and weekday in SITES["DG"].wave_days:
            position = self.DU + self.DT
            want = np.where(self.dg & (position < self.spoke_min_at(d)), self.spoke_qty_at(d), 0)
            ship = np.minimum(want, np.clip(self.U, 0, None))
            rows = np.flatnonzero(ship > 0)
            self.ledger.add(
                "313", d, self._minutes(rows.size, 11, 15), rows, ship[rows], lgort="1000", umlgo="3000"
            )
            self.U[rows] -= ship[rows]
            self.DT[rows] += ship[rows]
            if rows.size:
                arrival = _next_working(d + int(self.rng.integers(1, 3)), self.first, SITES["DG"].wave_days)
                self.transit.setdefault(arrival, []).append((rows, ship[rows]))
        # Round Rock: when the main warehouse is below its minimum, logistics pulls from the overflow hub
        # up to the maximum (working days, 09:00-10:00).
        if self.overflow and weekday <= 5:
            want = np.where(self.main_min > self.U, self.main_max - self.U, 0.0)
            self._pull(d, want, "PULL", (9.0, 10.0))

        # Demand: the goods issues to the production supply area (replayed from the deliveries), from the
        # Dongguan overflow hub for its share of the lines and from the main warehouse for the rest.
        if self.spoke:
            need = self.spoke_demand[d]
            shortage = np.clip(need - self.DU, 0, None)
            if shortage.any():
                hub_short = np.clip(shortage - self.U, 0, None)
                if hub_short.any():
                    self._expedite(d, hub_short)
                rows = np.flatnonzero(shortage > 0)
                qty = shortage[rows]
                emergency = {"trigger": "EMERGENCY"}
                self.ledger.add(
                    "313", d, np.full(rows.size, 6.4 * 60), rows, qty, lgort="1000", umlgo="3000", **emergency
                )
                self.ledger.add(
                    "315", d, np.full(rows.size, 6.8 * 60), rows, qty, lgort="3000", umlgo="1000", **emergency
                )
                self.U[rows] -= qty
                self.DU[rows] += qty
            self.DU -= need
            self.P += need
        need = self.hub_demand[d]
        shortage = np.clip(need - self.U, 0, None)
        if shortage.any() and self.overflow:
            # An emergency pull from the overflow hub covers the day's issues before any emergency buy.
            shortage = self._pull(d, shortage, "EMERGENCY", (6.4, 6.6))
        if shortage.any():
            self._expedite(d, shortage)
        self.U -= need
        self.P += need

        # Production orders start (and consume their staged components) on working days.
        if weekday <= 5 and self.p_order.size:
            self._consume(d)

        # Round Rock: on the weekly review day logistics moves what the main warehouse holds above its
        # maximum to the overflow hub (14:00-16:00).
        if self.overflow and weekday == REVIEW_WEEKDAY:
            excess = np.clip(self.U - self.main_max, 0, None)
            rows = np.flatnonzero(excess > 0)
            if rows.size:
                self.ledger.add(
                    "311", d, self._minutes(rows.size, 14, 16), rows, excess[rows], lgort="1000",
                    umlgo=OVERFLOW_HUB, trigger="MOVE",
                )  # fmt: skip
                self.U[rows] -= excess[rows]
                self.H[rows] += excess[rows]
                self.hub_used[rows] = True

        # Purchase orders (working days): MRP orders when the inventory position of the plant (main
        # warehouse and overflow hub together), net of the production-order reservations due within the
        # lead time, falls below the reorder point; the order covers the shortfall on top of the lot.
        if weekday <= 5:
            reserved = self._reservations_within_lead_time(d)
            position = self.U + self.H + self.Q + self.B + self.K + self.on_order - reserved
            rows = np.flatnonzero((self.total_rate > 0) & (position < self.reorder_point))
            for material in rows:
                shortfall = float(self.reorder_point[material] - position[material])
                self._place_order(int(material), d, float(self.order_qty[material]) + max(shortfall, 0.0))

    def _reservations_within_lead_time(self, d: int) -> np.ndarray:
        """Open component reservations (not yet consumed) due within each material's planned delivery time:
        the requirements MRP sees when it decides on a purchase order."""
        reserved = np.zeros(self.n)
        if not self.p_order.size:
            return reserved
        open_line = np.array([int(order) not in self.order_started for order in self.p_order])
        within = open_line & (self.p_due <= d + self.plifz[self.p_material])
        np.add.at(reserved, self.p_material[within], self.p_qty[within])
        return reserved

    def _pull(self, d: int, want: np.ndarray, trigger: str, hours: tuple[float, float]) -> np.ndarray:
        """Move ``want`` from the overflow hub into the main warehouse as far as the hub holds it (one-step
        311 posted by logistics); returns what the hub could not cover."""
        ship = np.minimum(want, np.clip(self.H, 0, None))
        rows = np.flatnonzero(ship > 1e-9)
        if rows.size:
            self.ledger.add(
                "311", d, self._minutes(rows.size, *hours), rows, ship[rows], lgort=OVERFLOW_HUB,
                umlgo="1000", trigger=trigger,
            )  # fmt: skip
            self.H[rows] -= ship[rows]
            self.U[rows] += ship[rows]
        return want - ship

    def _consume(self, d: int) -> None:
        """Orders whose start date has come and whose component lines were all staged by their deliveries
        (outbound goods issues replayed into the production supply area) start today and consume them."""
        candidates = np.unique(self.p_order[(self.p_due <= d)])
        consumed_rows, consumed_qty, consumed_orders, consumed_minutes = [], [], [], []
        for order in candidates:
            if int(order) in self.order_started:
                continue
            lines = np.flatnonzero(self.p_order == order)
            staged = self.p_staged[lines]
            if ((staged < 0) | (staged > d)).any():
                continue
            # One goods movement per order start: its lines share the posting time, so an extraction in
            # the middle of the day sees either the whole withdrawal or none of it.
            minute = float(self._minutes(1, 10, 16)[0])
            self.order_started[int(order)] = d
            self.order_started_minute[int(order)] = minute
            for line in lines:
                material = int(self.p_material[line])
                self.P[material] -= self.p_qty[line]
                consumed_rows.append(material)
                consumed_qty.append(self.p_qty[line])
                consumed_orders.append(self.p_aufnr[line])
                consumed_minutes.append(minute)
        if consumed_rows:
            rows = np.array(consumed_rows)
            self.ledger.add(
                "261", d, np.array(consumed_minutes), rows, np.array(consumed_qty),
                lgort=PSA_LGORT, aufnr=np.array(consumed_orders, dtype=object),
            )  # fmt: skip

    def started_before(self, cutoff: np.datetime64) -> dict[int, int]:
        """Orders whose start (the withdrawal of their components) was posted by ``cutoff`` (UTC)."""
        started = {}
        for order, day in self.order_started.items():
            local = (
                np.datetime64(self.first, "us")
                + np.timedelta64(day, "D")
                + minutes(self.order_started_minute[order])
            )
            if local_to_utc(np.array([local]), self.tz)[0] <= cutoff:
                started[order] = day
        return started

    def snapshot(self, d: int) -> None:
        day = self._day(d)
        parts = [
            ("1000", "UNRESTRICTED", self.U),
            ("1000", "QUALITY", self.Q),
            ("1000", "CONSIGNMENT", self.K),
            (PSA_LGORT, "UNRESTRICTED", self.P),
        ]
        if self.spoke:
            parts += [
                ("1900", "UNRESTRICTED", self.B),
                ("3000", "UNRESTRICTED", self.DU),
                ("3000", "IN_TRANSFER", self.DT),
            ]
        if self.overflow:
            parts.append((OVERFLOW_HUB, "UNRESTRICTED", self.H))
        frames = []
        for lgort, category, values in parts:
            rows = np.flatnonzero(values > 1e-9)
            if rows.size:
                frames.append(
                    pl.DataFrame(
                        {
                            "snapshot_date": day,
                            "lgort": lgort,
                            "material": rows.astype(np.int32),
                            "stock_category": category,
                            "quantity": values[rows].round(3),
                        }
                    )
                )
        if frames:
            self.snapshots.append(pl.concat(frames))

    def check_non_negative(self) -> None:
        for name, values in (
            ("U", self.U),
            ("Q", self.Q),
            ("B", self.B),
            ("K", self.K),
            ("DU", self.DU),
            ("DT", self.DT),
            ("P", self.P),
            ("H", self.H),
        ):
            if (values < -1e-6).any():
                raise AssertionError(f"{self.werks}: negative {name} stock")


# --------------------------------------------------------------------------------------------------
# Driver: simulate both plants, then render material documents, purchase orders, stock tables.
# --------------------------------------------------------------------------------------------------

USNAM = {
    "561": "MIGRATION",
    "101": "RFC-EWM",
    "321": "RFC-EWM",
    "313": "WF-BATCH",
    "315": "RFC-EWM",
    "411": "WF-BATCH",
    "261": "WF-BATCH",
}
VGART = {
    "561": "WA",
    "101": "WE",
    "321": "WQ",
    "311": "WA",
    "313": "WA",
    "315": "WA",
    "411": "WA",
    "261": "WA",
}
GR_KINDS = ("561", "101")


def _plant_materials(materials: pl.DataFrame, valuation: pl.DataFrame, werks: str) -> pl.DataFrame:
    flag = "in_us10" if werks == "US10" else "in_cn10"
    prices = valuation.filter(pl.col("bwkey") == werks).select("matnr", pl.col("stprs").cast(pl.Float64))
    return materials.filter(pl.col(flag)).join(prices, on="matnr").sort("matnr")


def _simulate(
    profile: Profile,
    werks: str,
    plant: pl.DataFrame,
    supplier: np.ndarray,
    late_probability: np.ndarray,
    events: pl.DataFrame,
    items: pl.DataFrame,
    first: date,
    n_days: int,
    cutoff: np.datetime64,
    rng: np.random.Generator,
    plan: ProductionPlan | None = None,
) -> PlantSimulation:
    index = {matnr: i for i, matnr in enumerate(plant.get_column("matnr").to_list())}
    hub_site = "RR" if werks == "US10" else "SZ"
    hub = _demand_matrix(events, items, hub_site, index, first, n_days)
    spoke = _demand_matrix(events, items, "DG", index, first, n_days) if werks == "CN10" else None
    production = None
    if plan is not None and plan.components.height:
        lines = (
            plan.components.filter(pl.col("werks") == werks)
            .join(plan.orders.select("order_id", "aufnr"), on="order_id")
            .join(
                _staged_days(events, items, first),
                left_on=["order_id", "line"],
                right_on=["plan_order_id", "rspos"],
                how="left",
            )
            .with_columns(pl.col("staged_day").fill_null(-1))
        )
        production = lines.with_columns(
            pl.col("matnr").replace_strict(index, return_dtype=pl.Int64).alias("material")
        ).select("order_id", "line", "material", "qty", "due_day", "stage_day", "staged_day", "aufnr")
    simulation = PlantSimulation(
        profile, werks, plant, plant.get_column("stprs").to_numpy(), supplier, late_probability, hub,
        first, rng, spoke_demand=spoke, production=production,
    )  # fmt: skip
    midnights = local_to_utc(
        np.array(
            [np.datetime64(first + timedelta(days=d + 1)) for d in range(n_days)], dtype="datetime64[us]"
        ),
        simulation.tz,
    )
    simulation.opening_balances()
    if midnights[0] <= cutoff:
        simulation.snapshot(0)
    for d in range(1, n_days):
        simulation.step(d)
        simulation.check_non_negative()
        if midnights[d] <= cutoff:
            simulation.snapshot(d)
    return simulation


def plant_parameters(simulations: dict[str, PlantSimulation], marc: pl.DataFrame) -> pl.DataFrame:
    """MARC with the min-max of the main warehouse from the simulation: EISBE (minimum), MABST (maximum), and
    MINBE (reorder point of the plant) for materials planned by reorder point."""
    frames = []
    for werks, simulation in simulations.items():
        frames.append(
            pl.DataFrame(
                {
                    "werks": werks,
                    "matnr": simulation.materials.get_column("matnr").to_numpy(),
                    "sim_eisbe": simulation.main_min,
                    "sim_mabst": simulation.main_max,
                    "sim_minbe": simulation.reorder_point,
                }
            )
        )
    parameters = pl.concat(frames)
    return (
        marc.join(parameters, on=["werks", "matnr"], how="left")
        .with_columns(
            pl.coalesce("sim_eisbe", "eisbe").cast(marc.schema["eisbe"]).alias("eisbe"),
            pl.coalesce("sim_mabst", "mabst").cast(marc.schema["mabst"]).alias("mabst"),
            pl.when(pl.col("dismm") == "VB")
            .then(pl.coalesce("sim_minbe", "minbe"))
            .otherwise(pl.col("minbe"))
            .cast(marc.schema["minbe"])
            .alias("minbe"),
        )
        .drop("sim_eisbe", "sim_mabst", "sim_minbe")
    )


def _material_frame(simulation: PlantSimulation) -> pl.DataFrame:
    return (
        simulation.materials.select("matnr", "stprs")
        .with_row_index("material")
        .with_columns(
            pl.col("material").cast(pl.Int32), pl.Series("vendor", simulation.supplier.astype(np.int64))
        )
    )


def _ledger_frame(simulation: PlantSimulation, cutoff: np.datetime64) -> pl.DataFrame:
    ledger = simulation.ledger.frame()
    local = (
        np.datetime64(simulation.first, "us")
        + ledger.get_column("day").to_numpy().astype("timedelta64[D]").astype("timedelta64[us]")
        + minutes(ledger.get_column("minute").to_numpy())
    )
    utc = local_to_utc(local, simulation.tz)
    for optional in ("po", "lgort", "umlgo", "target", "trigger", "aufnr"):
        if optional not in ledger.columns:
            ledger = ledger.with_columns(pl.lit(None, dtype=pl.String).alias(optional))
    return (
        ledger.with_columns(
            pl.Series("local_ts", local), pl.Series("event_utc", utc), pl.lit(simulation.werks).alias("werks")
        )
        .join(_material_frame(simulation), on="material", how="left")
        .filter(pl.col("event_utc") <= cutoff)
    )


ITEM_COLUMNS = (
    "doc", "zeile", "bwart", "shkzg", "matnr", "werks", "lgort", "sobkz", "insmk", "xauto", "umwrk", "umlgo",
    "menge", "dmbtr", "lifnr", "ebeln", "ebelp", "kostl", "aufnr",
)  # fmt: skip


def _items(ledger: pl.DataFrame) -> pl.DataFrame:
    """Material document items per movement, following the transfer-posting conventions above."""
    blank = pl.lit("")
    value = (pl.col("qty") * pl.col("stprs")).round(2)
    consignment = pl.col("target") == "K"

    def item(frame: pl.DataFrame, zeile: str, shkzg: str, **columns: pl.Expr) -> pl.DataFrame:
        defaults = {
            "sobkz": blank, "insmk": blank, "xauto": blank, "umwrk": blank, "umlgo": blank,
            "lifnr": blank, "ebeln": blank, "ebelp": pl.lit("00000"), "kostl": blank, "aufnr": blank,
            "dmbtr": value, "lgort": pl.col("lgort"),
        }  # fmt: skip
        defaults.update(columns)
        return frame.select(
            "doc",
            pl.lit(zeile).alias("zeile"),
            pl.col("kind").alias("bwart"),
            pl.lit(shkzg).alias("shkzg"),
            "matnr",
            "werks",
            *(expr.alias(name) for name, expr in defaults.items()),
            pl.col("qty").alias("menge"),
        )

    kind = pl.col("kind")
    parts = [
        item(ledger.filter(kind.is_in(list(GR_KINDS))), "0001", "S",
             sobkz=pl.when(consignment).then(pl.lit("K")).otherwise(blank),
             insmk=pl.when(pl.col("target") == "Q").then(pl.lit("X")).otherwise(blank),
             lifnr=pl.when((kind == "101") | consignment).then(pl.col("lifnr")).otherwise(blank),
             ebeln=pl.col("ebeln").fill_null(""),
             ebelp=pl.when(kind == "101").then(pl.lit("00010")).otherwise(pl.lit("00000")),
             dmbtr=pl.when(consignment).then(pl.lit(0.0)).otherwise(value)),
        item(ledger.filter(kind == "321"), "0001", "H", insmk=pl.lit("X")),
        item(ledger.filter(kind == "321"), "0002", "S", xauto=pl.lit("X")),
        item(ledger.filter(kind.is_in(["311", "313"])), "0001", "H",
             umwrk=pl.col("werks"), umlgo=pl.col("umlgo")),
        item(ledger.filter(kind.is_in(["311", "313"])), "0002", "S", lgort=pl.col("umlgo"), xauto=pl.lit("X"),
             umwrk=pl.col("werks"), umlgo=pl.col("lgort")),
        item(ledger.filter(kind == "315"), "0001", "S", umwrk=pl.col("werks"), umlgo=pl.col("umlgo")),
        item(ledger.filter(kind == "411"), "0001", "H", sobkz=pl.lit("K"), lifnr=pl.col("lifnr"),
             dmbtr=pl.lit(0.0)),
        item(ledger.filter(kind == "411"), "0002", "S", xauto=pl.lit("X")),
        item(ledger.filter(kind == "261"), "0001", "H", aufnr=pl.col("aufnr").fill_null("")),
    ]  # fmt: skip
    return pl.concat(parts).select(ITEM_COLUMNS).sort(["doc", "zeile"])


def _render(
    profile: Profile,
    first: date,
    cutoff: np.datetime64,
    lfa1: pl.DataFrame,
    simulations: dict[str, PlantSimulation],
    ledger: pl.DataFrame,
    snapshots: pl.DataFrame,
    outbound_mkpf: pl.DataFrame,
    outbound_mseg: pl.DataFrame,
) -> InventoryResult:
    tz = {werks: simulation.tz for werks, simulation in simulations.items()}
    lifnr_by_vendor = lfa1.get_column("lifnr").to_list()

    # Purchase orders created before the extraction.
    order_rows = []
    for werks, simulation in simulations.items():
        for order_id, order in enumerate(simulation.orders):
            order_rows.append(
                {
                    "werks": werks,
                    "po": str(order_id),
                    "material": order.material,
                    "created_local": datetime.combine(
                        first + timedelta(days=order.created_day), datetime.min.time()
                    )
                    + timedelta(minutes=order.created_minute),
                    "planned": first + timedelta(days=order.planned_day),
                    "qty": order.qty,
                    "target": order.target,
                    "lgort": order.lgort,
                    "expedited": order.expedited,
                }
            )
    orders = pl.DataFrame(order_rows)
    created_utc = np.concatenate(
        [
            local_to_utc(
                orders.filter(pl.col("werks") == werks)
                .get_column("created_local")
                .to_numpy()
                .astype("datetime64[us]"),
                tz[werks],
            )
            for werks in ("US10", "CN10")
        ]
    )
    orders = pl.concat([orders.filter(pl.col("werks") == werks) for werks in ("US10", "CN10")]).with_columns(
        pl.Series("created_utc", created_utc)
    )
    orders = (
        orders.filter(pl.col("created_utc") <= cutoff)
        .sort(["created_utc", "werks", "material", "po"])
        .with_row_index("po_seq")
        .with_columns(
            zfill(4_500_000_001 + np.arange(orders.filter(pl.col("created_utc") <= cutoff).height), 10).alias(
                "ebeln"
            )
        )
    )
    materials = pl.concat(
        [_material_frame(sim).with_columns(pl.lit(w).alias("werks")) for w, sim in simulations.items()]
    )
    orders = orders.join(materials, on=["werks", "material"], how="left").with_columns(
        pl.col("vendor")
        .replace_strict(dict(enumerate(lifnr_by_vendor)), return_dtype=pl.String)
        .alias("lifnr")
    )

    # Ledger: attach PO numbers and vendor ids, then number documents by type and time.
    ledger = (
        ledger.with_columns(pl.col("po").fill_null(""))
        .join(orders.select("werks", "po", "ebeln"), on=["werks", "po"], how="left")
        .with_columns(
            pl.col("vendor")
            .replace_strict(dict(enumerate(lifnr_by_vendor)), return_dtype=pl.String)
            .alias("lifnr"),
            pl.col("umlgo").fill_null(""),
            pl.col("target").fill_null(""),
            pl.col("trigger").fill_null(""),
        )
        .sort(["event_utc", "werks", "kind", "matnr", "lgort"])
    )
    is_gr = ledger.get_column("kind").is_in(list(GR_KINDS)).to_numpy()
    numbers = np.empty(ledger.height, dtype=np.int64)
    numbers[is_gr] = 5_000_000_001 + np.arange(int(is_gr.sum()))
    numbers[~is_gr] = 4_910_000_001 + np.arange(int((~is_gr).sum()))
    ledger = ledger.with_columns(zfill(numbers, 10).alias("doc"))

    # Headers (MKPF): posting date is site-local; entry date/time in ECC system time.
    local = ledger.get_column("local_ts").to_numpy()
    system = utc_to_local(ledger.get_column("event_utc").to_numpy(), ECC_SYSTEM_TZ)
    werks = ledger.get_column("werks").to_numpy()
    waers = np.where(werks == "US10", "USD", "CNY")
    headers = ledger.select(
        pl.lit(MANDT).alias("mandt"),
        pl.col("doc").alias("mblnr"),
        pl.Series("mjahr", fiscal_years_of(local.astype("datetime64[D]")).astype(str)),
        pl.col("kind").replace_strict(VGART).alias("vgart"),
        pl.when(pl.col("kind") == "101").then(pl.lit("WE")).otherwise(pl.lit("WA")).alias("blart"),
        dats(local).alias("bldat"),
        dats(local).alias("budat"),
        dats(system).alias("cpudt"),
        tims(system).alias("cputm"),
        pl.when((pl.col("kind") == "311") & (pl.col("lgort") == "1900"))
        .then(pl.lit("RFC-EWM"))
        .when(pl.col("kind").is_in(["311", "313"]) & (pl.col("trigger") == "EMERGENCY"))
        .then(pl.lit("RFC-EWM"))
        .when(pl.col("kind") == "311")
        .then(pl.lit("WF-BATCH"))
        .otherwise(pl.col("kind").replace_strict(USNAM, default="WF-BATCH"))
        .alias("usnam"),
        pl.col("ebeln").fill_null("").alias("xblnr"),
        pl.lit("").alias("le_vbeln"),
    )
    items = _items(ledger.with_columns(pl.Series("waers", waers)))
    mseg = items.join(
        headers.select("mblnr", "mjahr", "budat", "cpudt", "cputm"), left_on="doc", right_on="mblnr"
    )
    mseg = mseg.select(
        pl.lit(MANDT).alias("mandt"),
        pl.col("doc").alias("mblnr"),
        "mjahr",
        "zeile",
        "bwart",
        "shkzg",
        "matnr",
        "werks",
        "lgort",
        "sobkz",
        "insmk",
        "xauto",
        "umwrk",
        "umlgo",
        "menge",
        pl.lit("EA").alias("meins"),
        pl.col("menge").alias("erfmg"),
        pl.lit("EA").alias("erfme"),
        "dmbtr",
        pl.when(pl.col("werks") == "US10").then(pl.lit("USD")).otherwise(pl.lit("CNY")).alias("waers"),
        "lifnr",
        "ebeln",
        "ebelp",
        "kostl",
        "aufnr",
        pl.lit("").alias("kunnr"),
        pl.lit("").alias("vbeln_im"),
        pl.lit("000000").alias("vbelp_im"),
        pl.lit("").alias("smbln"),
        pl.lit("0000").alias("sjahr"),
        pl.lit("0000").alias("smblp"),
        pl.col("budat").alias("budat_mkpf"),
        pl.col("cpudt").alias("cpudt_mkpf"),
        pl.col("cputm").alias("cputm_mkpf"),
    )

    # Purchase orders (EKKO/EKPO/EKET) with received quantities up to the extraction.
    received = (
        ledger.filter(pl.col("kind") == "101").group_by("ebeln").agg(pl.col("qty").sum().alias("wemng"))
    )
    orders = orders.join(received, on="ebeln", how="left").with_columns(pl.col("wemng").fill_null(0.0))
    created_local = orders.get_column("created_local").to_numpy().astype("datetime64[us]")
    ekko = orders.select(
        pl.lit(MANDT).alias("mandt"),
        "ebeln",
        pl.when(pl.col("werks") == "US10").then(pl.lit("1000")).otherwise(pl.lit("2000")).alias("bukrs"),
        pl.lit("F").alias("bstyp"),
        pl.when(pl.col("expedited")).then(pl.lit("ZEX")).otherwise(pl.lit("NB")).alias("bsart"),
        "lifnr",
        pl.when(pl.col("werks") == "US10").then(pl.lit("1000")).otherwise(pl.lit("2000")).alias("ekorg"),
        pl.when(pl.col("werks") == "US10").then(pl.lit("USD")).otherwise(pl.lit("CNY")).alias("waers"),
        dats(created_local).alias("aedat"),
        pl.lit("WF-BATCH").alias("ernam"),
    )
    price_noise = np.random.default_rng([profile.seed, 21]).uniform(0.95, 1.05, orders.height)
    ekpo = orders.select(
        pl.lit(MANDT).alias("mandt"),
        "ebeln",
        pl.lit("00010").alias("ebelp"),
        "matnr",
        "werks",
        "lgort",
        pl.col("qty").alias("menge"),
        pl.lit("EA").alias("meins"),
        (pl.col("stprs") * pl.Series(price_noise)).round(2).alias("netpr"),
        pl.lit(1).alias("peinh"),
        pl.when(pl.col("target") == "K").then(pl.lit("2")).otherwise(pl.lit("0")).alias("pstyp"),
        pl.when(pl.col("wemng") >= pl.col("qty")).then(pl.lit("X")).otherwise(pl.lit("")).alias("elikz"),
    )
    eket = orders.select(
        pl.lit(MANDT).alias("mandt"),
        "ebeln",
        pl.lit("00010").alias("ebelp"),
        pl.lit("0001").alias("etenr"),
        pl.col("planned").dt.strftime("%Y%m%d").alias("eindt"),
        pl.col("qty").alias("menge"),
        "wemng",
    )

    # Stock at extraction (MARD, MKOL) from the ledger; storage-location extensions per plant.
    mseg = pl.concat([outbound_mseg, mseg.select(outbound_mseg.columns)]).sort(["mblnr", "zeile"])
    mkpf = pl.concat([outbound_mkpf, headers.select(outbound_mkpf.columns)]).sort("mblnr")
    signed = pl.when(pl.col("shkzg") == "S").then(pl.col("menge")).otherwise(-pl.col("menge"))
    own = mseg.filter(pl.col("sobkz") == "")
    stock = (
        own.with_columns(
            pl.when((pl.col("bwart") == "313") & (pl.col("shkzg") == "S"))
            .then(pl.lit("T"))
            .when(pl.col("insmk") == "X")
            .then(pl.lit("Q"))
            .otherwise(pl.lit("U"))
            .alias("category"),
            # 315 receives into unrestricted from stock in transfer at the same storage location.
            signed.alias("delta"),
        )
        .group_by("werks", "lgort", "matnr", "category")
        .agg(pl.col("delta").sum())
    )
    transfer_out = (
        mseg.filter(pl.col("bwart") == "315")
        .group_by("werks", "lgort", "matnr")
        .agg((-pl.col("menge").sum()).alias("delta"))
        .with_columns(pl.lit("T").alias("category"))
    )
    stock = (
        pl.concat([stock, transfer_out.select(stock.columns)])
        .group_by("werks", "lgort", "matnr", "category")
        .agg(pl.col("delta").sum())
    )
    wide = stock.pivot(
        on="category", index=["werks", "lgort", "matnr"], values="delta", aggregate_function="sum"
    )
    for category in ("U", "Q", "T"):
        if category not in wide.columns:
            wide = wide.with_columns(pl.lit(0.0).alias(category))
    extensions = []
    for werks_code, simulation in simulations.items():
        matnr = simulation.materials.get_column("matnr").to_numpy()
        extensions.append(
            pl.DataFrame(
                {
                    "werks": werks_code,
                    "lgort": "1000",
                    "matnr": matnr,
                    "diskz": "",
                    "lminb": 0.0,
                    "lbstf": 0.0,
                }
            )
        )
        components = np.unique(simulation.p_material)
        if components.size:
            extensions.append(
                pl.DataFrame(
                    {
                        "werks": werks_code,
                        "lgort": PSA_LGORT,
                        "matnr": matnr[components],
                        "diskz": "",
                        "lminb": 0.0,
                        "lbstf": 0.0,
                    }
                )
            )
        if simulation.spoke:
            extensions.append(
                pl.DataFrame(
                    {
                        "werks": werks_code,
                        "lgort": "1900",
                        "matnr": matnr[simulation.bonded],
                        "diskz": "",
                        "lminb": 0.0,
                        "lbstf": 0.0,
                    }
                )
            )
            extensions.append(
                pl.DataFrame(
                    {"werks": werks_code, "lgort": "3000", "matnr": matnr[simulation.dg], "diskz": "2",
                     "lminb": simulation.spoke_min_at(simulation.n_days - 1)[simulation.dg],
                     "lbstf": simulation.spoke_qty_at(simulation.n_days - 1)[simulation.dg]}
                )
            )  # fmt: skip
        if simulation.overflow:
            extensions.append(
                pl.DataFrame(
                    {
                        "werks": werks_code,
                        "lgort": OVERFLOW_HUB,
                        "matnr": matnr[simulation.hub_used],
                        "diskz": "",
                        "lminb": 0.0,
                        "lbstf": 0.0,
                    }
                )
            )
    extended = pl.concat([frame.cast({"lminb": pl.Float64, "lbstf": pl.Float64}) for frame in extensions])
    mard = (
        extended.join(wide, on=["werks", "lgort", "matnr"], how="left")
        .with_columns(pl.col(c).fill_null(0.0).round(3) for c in ("U", "Q", "T"))
        .sort(["matnr", "werks", "lgort"])
        .select(
            pl.lit(MANDT).alias("mandt"),
            "matnr",
            "werks",
            "lgort",
            pl.col("U").alias("labst"),
            pl.col("Q").alias("insme"),
            pl.lit(0.0).alias("speme"),
            pl.col("T").alias("umlme"),
            "diskz",
            "lminb",
            "lbstf",
        )
    )
    consignment = (
        mseg.filter(pl.col("sobkz") == "K")
        .group_by("matnr", "werks", "lgort", "lifnr")
        .agg(signed.sum().round(3).alias("slabs"))
        .sort(["matnr", "werks", "lgort", "lifnr"])
    )
    mkol = consignment.select(
        pl.lit(MANDT).alias("mandt"), "matnr", "werks", "lgort", pl.lit("K").alias("sobkz"), "lifnr", "slabs"
    )

    snapshot = (
        snapshots.with_columns(
            pl.when(pl.col("stock_category") == "CONSIGNMENT")
            .then(pl.lit(0.0))
            .otherwise((pl.col("quantity") * pl.col("stprs")).round(2))
            .alias("stock_value"),
            pl.when(pl.col("werks") == "US10").then(pl.lit("USD")).otherwise(pl.lit("CNY")).alias("waers"),
        )
        .select(
            "snapshot_date",
            "werks",
            "lgort",
            "matnr",
            "stock_category",
            "quantity",
            pl.lit("EA").alias("meins"),
            "stock_value",
            "waers",
        )
        .sort(["snapshot_date", "werks", "lgort", "matnr", "stock_category"])
    )
    tables = {
        "raw_sap_ecc.mkpf": mkpf,
        "raw_sap_ecc.mseg": mseg,
        "raw_sap_ecc.lfa1": lfa1,
        "raw_sap_ecc.ekko": ekko,
        "raw_sap_ecc.ekpo": ekpo,
        "raw_sap_ecc.eket": eket,
        "raw_sap_ecc.mard": mard,
        "raw_sap_ecc.mkol": mkol,
        "raw_ext.inventory_snapshot_daily": snapshot,
    }
    facts = {"inventory_ledger": ledger, "purchase_orders": orders}
    return InventoryResult(tables=tables, facts=facts)


def generate_inventory(
    profile: Profile,
    materials: pl.DataFrame,
    valuation: pl.DataFrame,
    events: pl.DataFrame,
    items: pl.DataFrame,
    outbound_mkpf: pl.DataFrame,
    outbound_mseg: pl.DataFrame,
    plan: ProductionPlan,
    marc: pl.DataFrame,
) -> InventoryResult:
    """Simulate stock for both plants; returns inventory tables, MKPF/MSEG merged with outbound, and MARC
    with the main warehouse's min-max."""
    rng = np.random.default_rng([profile.seed, 20])
    cutoff = np.datetime64(profile.ecc_as_of.replace(tzinfo=None), "us")
    first = profile.start - timedelta(days=1)
    last = max(
        profile.ecc_as_of.astimezone(__import__("zoneinfo").ZoneInfo(SITES[s].tz)).date() for s in SITES
    )
    n_days = (last - first).days + 1
    lfa1, supplier_all, late_probability = _vendors(materials, rng)
    supplier_by_matnr = dict(zip(materials.get_column("matnr").to_list(), supplier_all.tolist(), strict=True))
    production_rng = np.random.default_rng([profile.seed, PRODUCTION_COMPONENT, 1])

    ledgers, snapshots, simulations = [], [], {}
    for werks in ("US10", "CN10"):
        plant = _plant_materials(materials, valuation, werks)
        supplier = np.array([supplier_by_matnr[m] for m in plant.get_column("matnr").to_list()])
        simulation = _simulate(
            profile, werks, plant, supplier, late_probability, events, items, first, n_days, cutoff, rng,
            plan,
        )  # fmt: skip
        simulations[werks] = simulation
        ledgers.append(_ledger_frame(simulation, cutoff))
        if simulation.snapshots:
            snapshots.append(
                pl.concat(simulation.snapshots)
                .join(_material_frame(simulation).select("material", "matnr", "stprs"), on="material")
                .with_columns(pl.lit(werks).alias("werks"))
            )
    ledger = pl.concat(ledgers, how="diagonal_relaxed")
    result = _render(
        profile, first, cutoff, lfa1, simulations, ledger, pl.concat(snapshots), outbound_mkpf, outbound_mseg
    )
    result.tables["raw_sap_ecc.marc"] = plant_parameters(simulations, marc)
    started = {
        order: day for sim in simulations.values() for order, day in sim.started_before(cutoff).items()
    }
    result.tables.update(render(plan, started, profile, first, production_rng))
    result.facts["production_plan"] = plan.components.join(plan.orders, on="order_id", how="left")
    return result
