"""Injected scenarios (DATA_MODEL.md section 6): shared definitions and their ground truth.

Generators read the parameters below; ``ground_truth`` renders ``eval.anomaly_ground_truth``, which the
agent can never read.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import polars as pl

from .config import Profile


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    scenario_type: str  # real | data_artifact | definition_artifact | expected_seasonality
    site: str
    lgnum: str
    start: date
    end: date | None
    scope: str
    root_cause: str
    expected_signals: str


# A2: wrong putaway control indicator (PACI) for one material group at Round Rock. Putaways for the group go
# to the high rack instead of shelving, and wait for a reach truck. Only the inbound side is modeled: picks
# keep their slotted source bins because outbound tasks are generated before receipts.
A2_START = date(2026, 6, 29)
A2_MATERIAL_GROUP = "SP-FST"
A2_INTENDED_PACI = "SHLF"
A2_WRONG_PACI = "HRCK"

# A1: two AutoStore ports out of service for five days at Longgang. Picks from the grid queue longer on the
# remaining ports. Delays stay within each delivery's slack before its goods issue, so ECC is unaffected.
A1_START = date(2026, 3, 30)
A1_END = date(2026, 4, 3)
A1_PORTS = ("AS-PORT-03", "AS-PORT-07")

# A3: the replenishment parameters of one material group at the Dongguan spoke (storage location 3000:
# reorder point MARD.LMINB and replenishment quantity MARD.LBSTF) were set to 10 % of their intended values
# by a mass change. Scheduled transfers start only when the spoke is empty and bring a day of demand, so
# the spoke runs dry and emergency transfers on the demand day replace the scheduled ones.
A3_START = date(2026, 6, 15)
A3_MATERIAL_GROUP = "SP-GAS"
A3_FACTOR = 0.1

# A4: Round Rock releases picking waves twice a day (10:00 and 16:00 site-local) instead of hourly. Fewer,
# larger waves queue tasks longer, but batched picks are faster (a trade-off, not a fault).
A4_START = date(2026, 7, 13)
A4_SITE = "RR"
A4_WAVE_HOURS = (10, 16)
A4_WAVE_TEMPLATE = "RR-2XDAY"
A4_QUEUE_FACTOR = 2.0
A4_PICK_FACTOR = 0.85

# A6: EWM replication delivered one day of Dongguan warehouse tasks twice (new task numbers, same content).
A6_DATE = date(2026, 1, 14)
A6_LGNUM = "DG01"

# A7: seals move in whole boxes from this date: delivery lines whose base quantity is a whole number of boxes
# use the unit BOX; before it, the group was issued by the piece. Base-unit quantities are unchanged.
A7_START = date(2026, 5, 4)
A7_MATERIAL_GROUP = "SP-SEL"

# A8: expected seasonality already built into demand (see calendar.py); not anomalies.
A8_CNY_START = date(2026, 2, 15)
A8_CNY_END = date(2026, 2, 28)

SCENARIOS = (
    Scenario(
        scenario_id="A1",
        scenario_type="real",
        site="Longgang",
        lgnum="SZ01",
        start=A1_START,
        end=A1_END,
        scope=f"AutoStore ports {' and '.join(A1_PORTS)} at Longgang",
        root_cause="Two AutoStore ports out of service (hardware fault) for five days",
        expected_signals="Port status CLOSED for the two ports and no bin presentations or tasks at them; "
        "queue and cycle time of AutoStore picks at SZ01 rise during the window",
    ),
    Scenario(
        scenario_id="A2",
        scenario_type="real",
        site="Round Rock",
        lgnum="RR01",
        start=A2_START,
        end=None,
        scope=f"Products of material group {A2_MATERIAL_GROUP} at warehouse RR01 whose intended PACI is "
        f"{A2_INTENDED_PACI}",
        root_cause=f"Putaway control indicator changed from {A2_INTENDED_PACI} to {A2_WRONG_PACI} in the "
        "warehouse product master",
        expected_signals="Putaway tasks for the group go to storage type 0010 (high rack) instead of 0020 "
        "(shelving); their dock-to-stock time rises from under an hour to several hours",
    ),
    Scenario(
        scenario_id="A3",
        scenario_type="real",
        site="Dongguan",
        lgnum="DG01",
        start=A3_START,
        end=None,
        scope=f"Products of material group {A3_MATERIAL_GROUP} stocked at the Dongguan spoke (plant CN10, "
        "storage location 3000)",
        root_cause=f"Reorder point (MARD.LMINB) and replenishment quantity (MARD.LBSTF) of the group at "
        f"storage location 3000 set to about {A3_FACTOR:.0%} of their intended values by a mass change",
        expected_signals="Scheduled hub-to-spoke transfers (313 posted by WF-BATCH) of the group become "
        "small and near-daily; emergency transfers of the group (313 posted by RFC-EWM on the demand day) "
        "rise sharply and become the majority; spoke stock of the group is near zero; the hub, other "
        "groups, and customer shipments are unaffected",
    ),
    Scenario(
        scenario_id="A4",
        scenario_type="real",
        site="Round Rock",
        lgnum="RR01",
        start=A4_START,
        end=None,
        scope="All outbound waves and pick tasks at warehouse RR01",
        root_cause=f"Wave release policy changed from hourly to twice daily ({A4_WAVE_HOURS[0]:02d}:00 and "
        f"{A4_WAVE_HOURS[1]:02d}:00 site-local; wave template {A4_WAVE_TEMPLATE})",
        expected_signals="Wave count drops to two per day and waves become much larger; task queue time "
        "(creation to start) roughly doubles; pick duration per "
        "task falls by about 15 % (batched picks). A trade-off, not a fault: throughput per labor hour "
        "improves",
    ),
    Scenario(
        scenario_id="A6",
        scenario_type="data_artifact",
        site="Dongguan",
        lgnum=A6_LGNUM,
        start=A6_DATE,
        end=A6_DATE,
        scope=f"All warehouse tasks confirmed at {A6_LGNUM} on {A6_DATE.isoformat()} (site-local date)",
        root_cause="EWM replication delivered the day's tasks twice, with new task numbers",
        expected_signals="Task count for the day doubles; each duplicate repeats the reference document "
        "item, quantities, and timestamps of an original task; ECC shipments show no spike",
    ),
    Scenario(
        scenario_id="A7",
        scenario_type="definition_artifact",
        site="All sites",
        lgnum="",
        start=A7_START,
        end=None,
        scope=f"Delivery lines of material group {A7_MATERIAL_GROUP} products with a BOX unit",
        root_cause="Delivery unit changed from EA to BOX for lines in whole boxes",
        expected_signals="Delivery-unit quantities (LIPS.LFIMG, MSEG.ERFMG, task VSOLA) of the "
        "group drop sharply; base-unit quantities (LIPS.LGMNG, MSEG.MENGE, task VSOLM) continue unchanged",
    ),
    Scenario(
        scenario_id="A8a",
        scenario_type="expected_seasonality",
        site="Longgang and Dongguan",
        lgnum="",
        start=A8_CNY_START,
        end=A8_CNY_END,
        scope="Chinese New Year 2026 at the Chinese sites",
        root_cause="Holiday shutdown and ramp-up",
        expected_signals="Outbound volume near zero for a week, then about 60 % for five days; "
        "not an anomaly",
    ),
    Scenario(
        scenario_id="A8b",
        scenario_type="expected_seasonality",
        site="All sites",
        lgnum="",
        start=date(2024, 10, 28),
        end=None,
        scope="The last ten days of every fiscal quarter",
        root_cause="Quarter-end shipping push",
        expected_signals="Outbound volume about 25 % above the surrounding weeks; not an anomaly",
    ),
)


def ground_truth(profile: Profile) -> pl.DataFrame:
    """Scenarios whose window overlaps the generated period."""
    active = [
        scenario
        for scenario in SCENARIOS
        if scenario.start <= profile.ecc_as_of.date()
        and (scenario.end is None or scenario.end >= profile.start)
    ]
    return pl.DataFrame(
        {
            "scenario_id": [s.scenario_id for s in active],
            "scenario_type": [s.scenario_type for s in active],
            "site": [s.site for s in active],
            "lgnum": [s.lgnum for s in active],
            "start_date": [s.start for s in active],
            "end_date": pl.Series([s.end for s in active], dtype=pl.Date),
            "scope": [s.scope for s in active],
            "root_cause": [s.root_cause for s in active],
            "expected_signals": [s.expected_signals for s in active],
        }
    )
