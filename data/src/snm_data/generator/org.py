"""Fictional organizational structure of SouthNorth Solutions (sites, plants, warehouses)."""

from __future__ import annotations

from dataclasses import dataclass

MANDT = "100"
ECC_SYSTEM_TZ = "America/Chicago"
LANGUAGE = "E"

SYSTEM_USERS = ("WF-BATCH", "AS-INTERFACE", "RFC-EWM")


@dataclass(frozen=True)
class Site:
    """A shipping site: one EWM warehouse attached to a plant / storage location."""

    code: str
    name: str
    werks: str
    lgort: str
    lgnum: str
    vstel: str
    vkorg: str
    tz: str
    sap_tz: str
    country: str
    waers: str
    uname_prefix: str
    shifts: tuple[str, ...]
    wave_template: str
    wave_minutes: int
    wave_first_hour: int
    wave_last_hour: int
    wave_days: tuple[int, ...]  # ISO weekdays with waves (1 = Monday)


SITES: dict[str, Site] = {
    "RR": Site(
        code="RR",
        name="Round Rock Hub",
        werks="US10",
        lgort="1000",
        lgnum="RR01",
        vstel="U100",
        vkorg="1000",
        tz="America/Chicago",
        sap_tz="CST",
        country="US",
        waers="USD",
        uname_prefix="RR",
        shifts=("A", "B"),
        wave_template="RR-HOURLY",
        wave_minutes=60,
        wave_first_hour=6,
        wave_last_hour=21,
        wave_days=(1, 2, 3, 4, 5, 6),
    ),
    "SZ": Site(
        code="SZ",
        name="Longgang Hub",
        werks="CN10",
        lgort="1000",
        lgnum="SZ01",
        vstel="C100",
        vkorg="2000",
        tz="Asia/Shanghai",
        sap_tz="UTC+8",
        country="CN",
        waers="CNY",
        uname_prefix="SZ",
        shifts=("A", "B", "C"),
        wave_template="SZ-30MIN",
        wave_minutes=30,
        wave_first_hour=0,
        wave_last_hour=23,
        wave_days=(1, 2, 3, 4, 5, 6, 7),
    ),
    "DG": Site(
        code="DG",
        name="Dongguan Spoke",
        werks="CN10",
        lgort="3000",
        lgnum="DG01",
        vstel="C300",
        vkorg="2000",
        tz="Asia/Shanghai",
        sap_tz="UTC+8",
        country="CN",
        waers="CNY",
        uname_prefix="DG",
        shifts=("A", "B"),
        wave_template="DG-HOURLY",
        wave_minutes=60,
        wave_first_hour=7,
        wave_last_hour=22,
        wave_days=(1, 2, 3, 4, 5, 6),
    ),
}

# Shift boundaries in site-local hours: shift -> (start_hour, end_hour); end may exceed 24.
SHIFT_HOURS = {
    ("RR", "A"): (6, 14),
    ("RR", "B"): (14, 22),
    ("SZ", "A"): (7, 15),
    ("SZ", "B"): (15, 23),
    ("SZ", "C"): (23, 31),
    ("DG", "A"): (7, 15),
    ("DG", "B"): (15, 23),
}

PLANTS = [
    # werks, name1, ort01, land1, regio, fabkl
    ("US10", "SouthNorth Round Rock", "Round Rock", "US", "TX", "US"),
    ("CN10", "SouthNorth Shenzhen", "Shenzhen", "CN", "44", "CN"),
]

STORAGE_LOCATIONS = [
    # werks, lgort, lgobe, lgnum (None = inventory-managed only)
    ("US10", "1000", "RR HUB", "RR01"),
    ("US10", "2001", "PFLUGERVILLE SAT", None),
    ("US10", "2002", "GEORGETOWN SAT", None),
    ("US10", "2003", "HUTTO SAT", None),
    ("US10", "2100", "RR ASSEMBLY PSA", None),
    ("CN10", "1000", "LONGGANG HUB", "SZ01"),
    ("CN10", "1900", "LONGGANG BONDED", "SZ01"),
    ("CN10", "2100", "LONGGANG PSA", None),
    ("CN10", "3000", "DONGGUAN SPOKE", "DG01"),
]


@dataclass(frozen=True)
class StorageType:
    lgnum: str
    lgtyp: str
    description: str
    st_role: str
    put_rule: str
    bin_prefix: str
    full_bins: int
    lptyp: str
    max_weight: float


STORAGE_TYPES = [
    StorageType("RR01", "0010", "High Rack", "A", "1", "HR", 800, "PAL1", 1000.0),
    StorageType("RR01", "0020", "Shelving", "A", "2", "SH", 1500, "SHF1", 60.0),
    StorageType("RR01", "0030", "Secure Cage", "A", "1", "CG", 200, "CAG1", 40.0),
    StorageType("RR01", "8000", "Quality Inspection", "A", "2", "QI", 40, "SHF1", 60.0),
    StorageType("RR01", "9010", "GR Zone", "D", "2", "GR-ZONE", 2, "ZONE", 5000.0),
    StorageType("RR01", "9020", "GI Staging", "D", "2", "GI-STAGE", 4, "ZONE", 5000.0),
    StorageType("SZ01", "AS01", "AutoStore Grid", "A", "2", "ASB", 20000, "ASBN", 30.0),
    StorageType("SZ01", "0020", "Shelving", "A", "2", "SH", 4000, "SHF1", 60.0),
    StorageType("SZ01", "0050", "Bulk", "A", "2", "BK", 600, "PAL1", 1500.0),
    StorageType("SZ01", "BND1", "Bonded Storage", "A", "2", "BD", 300, "SHF1", 60.0),
    StorageType("SZ01", "9010", "GR Zone", "D", "2", "GR-ZONE", 2, "ZONE", 5000.0),
    StorageType("SZ01", "9020", "GI Staging", "D", "2", "GI-STAGE", 4, "ZONE", 5000.0),
    StorageType("DG01", "0020", "Shelving", "A", "2", "SH", 2500, "SHF1", 60.0),
    StorageType("DG01", "0050", "Bulk", "A", "2", "BK", 400, "PAL1", 1500.0),
    StorageType("DG01", "9010", "GR Zone", "D", "2", "GR-ZONE", 2, "ZONE", 5000.0),
    StorageType("DG01", "9020", "GI Staging", "D", "2", "GI-STAGE", 4, "ZONE", 5000.0),
]

SITE_BY_LGNUM = {site.lgnum: site for site in SITES.values()}
