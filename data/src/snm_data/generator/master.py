"""Master data: organization, fiscal calendar, materials, customers, storage layout, and workforce."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import polars as pl

from .calendar import FISCAL_YEAR_VARIANT, fiscal_periods
from .config import Profile
from .org import LANGUAGE, MANDT, PLANTS, SHIFT_HOURS, SITES, STORAGE_LOCATIONS, STORAGE_TYPES
from .timeutil import guids, zfill

CNY_PER_USD = 7.1


@dataclass(frozen=True)
class MaterialGroup:
    code: str
    share: float
    nouns: tuple[str, ...]
    specs: tuple[str, ...]
    weight_kg: float
    price_usd: float
    hts: str
    box_share: float
    base_qty: tuple[int, int]


# Only small commodity parts are also sold by the box; instruments and assemblies are sold by the piece.
# Material groups whose boxed materials move in whole boxes (sales unit BOX) part of the time.
BOX_GROUPS = frozenset({"SP-CON", "SP-SEL", "SP-FST", "SP-GAS"})
BOXABLE_NOUNS = frozenset(
    {
        "FITTING, VCR", "FILTER, GAS LINE", "O-RING KIT", "WIPES, CLEANROOM", "GLOVES, NITRILE",
        "SWAB, CLEANROOM", "O-RING, FFKM", "GASKET, METAL", "SEAL, LIP", "SCREW, SHCS", "WASHER, FLAT",
        "NUT, HEX", "STANDOFF", "DOWEL PIN",
    }
)  # fmt: skip

MATERIAL_GROUPS = (
    MaterialGroup(
        "SP-VAC",
        0.12,
        ("VALVE, GATE", "VALVE, PENDULUM", "PUMP, TURBO", "GAUGE, MANOMETER", "BELLOWS ASSY"),
        ("200MM", "300MM", "ISO-K 160", "KF40", "CF63"),
        6.0,
        3500.0,
        "8414100000",
        0.0,
        (1, 2),
    ),
    MaterialGroup(
        "SP-RFP",
        0.08,
        ("GENERATOR, RF", "MATCH NETWORK, RF", "CABLE, RF", "CAPACITOR, VAC VARIABLE"),
        ("13.56MHZ", "2MHZ", "3KW", "5KW", "N-TYPE"),
        8.0,
        6000.0,
        "8504409580",
        0.0,
        (1, 2),
    ),
    MaterialGroup(
        "SP-WHD",
        0.10,
        ("ROBOT BLADE", "END EFFECTOR", "ALIGNER ASSY", "LIFT PIN SET", "WAFER SENSOR"),
        ("300MM", "200MM", "CERAMIC", "VESPEL", "QUARTZ"),
        3.0,
        2500.0,
        "8486400000",
        0.0,
        (1, 3),
    ),
    MaterialGroup(
        "SP-GAS",
        0.12,
        ("MFC, MASS FLOW", "REGULATOR, GAS", "FILTER, GAS LINE", "VALVE, PNEUMATIC", "FITTING, VCR"),
        ("1/4IN", "3/8IN", "500SCCM", "2SLM", "SS316L"),
        1.5,
        900.0,
        "8481809050",
        0.2,
        (1, 6),
    ),
    MaterialGroup(
        "SP-CHK",
        0.12,
        ("SHIELD KIT, CHAMBER", "LINER, CHAMBER", "SHOWERHEAD", "EDGE RING", "FOCUS RING"),
        ("300MM", "200MM", "ALUMINA", "Y2O3", "SIC"),
        5.0,
        4000.0,
        "8486900000",
        0.0,
        (1, 4),
    ),
    MaterialGroup(
        "SP-SEN",
        0.08,
        ("SENSOR, PRESSURE", "SENSOR, TEMPERATURE", "THERMOCOUPLE", "ENDPOINT DETECTOR"),
        ("TYPE K", "0-10 TORR", "RTD PT100", "OPTICAL"),
        0.4,
        700.0,
        "9026208000",
        0.0,
        (1, 4),
    ),
    MaterialGroup(
        "SP-CON",
        0.12,
        ("O-RING KIT", "WIPES, CLEANROOM", "GLOVES, NITRILE", "LUBRICANT, VACUUM", "SWAB, CLEANROOM"),
        ("CLASS 10", "SIZE M", "SIZE L", "PFPE", "9IN"),
        0.3,
        40.0,
        "3926909985",
        0.8,
        (2, 30),
    ),
    MaterialGroup(
        "SP-ELE",
        0.10,
        ("PCB ASSY, CONTROLLER", "POWER SUPPLY", "CABLE HARNESS", "INTERLOCK MODULE", "I/O BOARD"),
        ("24VDC", "48VDC", "REV C", "REV D", "ETHERCAT"),
        1.2,
        1500.0,
        "8537109170",
        0.0,
        (1, 3),
    ),
    MaterialGroup(
        "SP-SEL",
        0.08,
        ("O-RING, FFKM", "GASKET, METAL", "SEAL, LIP", "SEAL KIT"),
        ("AS568-236", "CF40", "KF25", "VITON"),
        0.05,
        120.0,
        "4016935020",
        0.6,
        (2, 20),
    ),
    MaterialGroup(
        "SP-FST",
        0.08,
        ("SCREW, SHCS", "WASHER, FLAT", "NUT, HEX", "STANDOFF", "DOWEL PIN"),
        ("M4X10", "M6X20", "1/4-20", "SS316", "TI GR5"),
        0.02,
        5.0,
        "7318152065",
        0.9,
        (5, 100),
    ),
)
ORIGIN_COUNTRIES = ("US", "CN", "JP", "DE", "TW", "KR", "MY")


def _rng(profile: Profile, component: int) -> np.random.Generator:
    return np.random.default_rng([profile.seed, component])


# --------------------------------------------------------------------------------------------------
# Organization and calendar
# --------------------------------------------------------------------------------------------------


def organization_tables() -> dict[str, pl.DataFrame]:
    t001w = pl.DataFrame(
        {
            "mandt": MANDT,
            "werks": [plant[0] for plant in PLANTS],
            "name1": [plant[1] for plant in PLANTS],
            "ort01": [plant[2] for plant in PLANTS],
            "land1": [plant[3] for plant in PLANTS],
            "regio": [plant[4] for plant in PLANTS],
            "bwkey": [plant[0] for plant in PLANTS],
            "fabkl": [plant[5] for plant in PLANTS],
        }
    )
    t001l = pl.DataFrame(
        {
            "mandt": MANDT,
            "werks": [row[0] for row in STORAGE_LOCATIONS],
            "lgort": [row[1] for row in STORAGE_LOCATIONS],
            "lgobe": [row[2] for row in STORAGE_LOCATIONS],
        }
    )
    assigned = [row for row in STORAGE_LOCATIONS if row[3] is not None]
    t320 = pl.DataFrame(
        {
            "mandt": MANDT,
            "werks": [row[0] for row in assigned],
            "lgort": [row[1] for row in assigned],
            "lgnum": [row[3] for row in assigned],
        }
    )
    return {"raw_sap_ecc.t001w": t001w, "raw_sap_ecc.t001l": t001l, "raw_sap_ecc.t320": t320}


def fiscal_calendar_tables(first_fiscal_year: int, last_fiscal_year: int) -> dict[str, pl.DataFrame]:
    t009 = pl.DataFrame(
        {
            "mandt": [MANDT],
            "periv": [FISCAL_YEAR_VARIANT],
            "xkale": [""],
            "xjabh": ["X"],
            "anzbp": ["012"],
            "anzsp": ["04"],
        }
    )
    rows = []

    def entry(day: date, period: int, fiscal_year: int) -> dict[str, str]:
        return {
            "mandt": MANDT,
            "periv": FISCAL_YEAR_VARIANT,
            "bdatj": f"{day.year:04d}",
            "bumon": f"{day.month:02d}",
            "butag": f"{day.day:02d}",
            "poper": f"{period:03d}",
            "reljr": "+1" if fiscal_year > day.year else "0",
        }

    for fiscal_year in range(first_fiscal_year, last_fiscal_year + 1):
        for period, first, last in fiscal_periods(fiscal_year):
            # Entries are maintained per calendar year, so a period spanning New Year is split at Dec 31.
            if first.year != last.year:
                rows.append(entry(date(first.year, 12, 31), period, fiscal_year))
            rows.append(entry(last, period, fiscal_year))
    return {"raw_sap_ecc.t009": t009, "raw_sap_ecc.t009b": pl.DataFrame(rows)}


# --------------------------------------------------------------------------------------------------
# Materials
# --------------------------------------------------------------------------------------------------


def generate_materials(profile: Profile) -> pl.DataFrame:
    """Internal material attribute table used by all downstream generators."""
    rng = _rng(profile, 1)
    count = profile.n_materials
    shares = np.array([group.share for group in MATERIAL_GROUPS])
    group_index = rng.choice(len(MATERIAL_GROUPS), size=count, p=shares / shares.sum())
    weight = np.empty(count)
    price = np.empty(count)
    descriptions = []
    mtart = []
    has_box = np.zeros(count, dtype=bool)
    for index in range(count):
        group = MATERIAL_GROUPS[group_index[index]]
        weight[index] = group.weight_kg * rng.lognormal(0.0, 0.6)
        price[index] = max(1.0, group.price_usd * rng.lognormal(0.0, 0.7))
        noun = group.nouns[rng.integers(len(group.nouns))]
        spec = group.specs[rng.integers(len(group.specs))]
        descriptions.append(f"{noun}, {spec}"[:40])
        if group.code == "SP-CON" and rng.random() < 0.5:
            mtart.append("HAWA")
        elif ("ASSY" in noun or "KIT" in noun) and rng.random() < 0.3:
            mtart.append("FERT")
        else:
            mtart.append("ERSA")
        has_box[index] = (rng.random() < group.box_share) and noun in BOXABLE_NOUNS
    numbers = 40_000_000 + rng.permutation(np.arange(1, count * 3 + 1))[:count]
    in_us = rng.random(count) < 0.6
    in_cn = rng.random(count) < 0.8
    in_cn |= ~(in_us | in_cn)
    in_dg = in_cn & (rng.random(count) < 0.5)
    serialized = (price > 5000) & (rng.random(count) < 0.7)
    created_days = rng.integers(0, (profile.start - date(2015, 1, 1)).days - 30, size=count)
    popularity = rng.pareto(1.2, size=count) + 0.05
    return pl.DataFrame(
        {
            "matnr": zfill(numbers, 18),
            "matkl": [MATERIAL_GROUPS[i].code for i in group_index],
            "group_index": group_index,
            "maktx": descriptions,
            "mtart": mtart,
            "weight_kg": weight.round(3),
            "volume_dm3": (weight / rng.uniform(0.3, 2.5, size=count) + 0.05).round(3),
            "price_usd": price.round(2),
            "has_box": has_box,
            "box_size": rng.choice([10, 20, 25, 50, 100], size=count),
            "serialized": serialized,
            "in_us10": in_us,
            "in_cn10": in_cn,
            "in_dg": in_dg,
            "ersda": [date(2015, 1, 1) + timedelta(days=int(offset)) for offset in created_days],
            "herkl": rng.choice(ORIGIN_COUNTRIES, size=count),
            "plifz": rng.integers(5, 61, size=count),
            "popularity": popularity,
        }
    )


def material_tables(materials: pl.DataFrame, profile: Profile) -> dict[str, pl.DataFrame]:
    rng = _rng(profile, 2)
    count = materials.height
    mara = materials.select(
        pl.lit(MANDT).alias("mandt"),
        "matnr",
        pl.col("ersda").dt.strftime("%Y%m%d").alias("ersda"),
        "mtart",
        "matkl",
        pl.lit("EA").alias("meins"),
        (pl.col("weight_kg") * 1.1).round(3).alias("brgew"),
        pl.col("weight_kg").alias("ntgew"),
        pl.lit("KG").alias("gewei"),
        pl.col("volume_dm3").alias("volum"),
        pl.lit("CD3").alias("voleh"),
        pl.lit("").alias("lvorm"),
    )
    makt = materials.select(pl.lit(MANDT).alias("mandt"), "matnr", pl.lit(LANGUAGE).alias("spras"), "maktx")
    edge_cm = (materials.get_column("volume_dm3").to_numpy() ** (1 / 3)) * 10
    base_units = pl.DataFrame(
        {
            "mandt": MANDT,
            "matnr": materials.get_column("matnr"),
            "meinh": "EA",
            "umrez": 1,
            "umren": 1,
            "laeng": (edge_cm * rng.uniform(1.0, 1.6, count)).round(1),
            "breit": edge_cm.round(1),
            "hoehe": (edge_cm * rng.uniform(0.5, 1.0, count)).round(1),
            "meabm": "CM",
            "brgew": (materials.get_column("weight_kg") * 1.1).round(3),
            "gewei": "KG",
        }
    )
    boxed = materials.filter(pl.col("has_box"))
    box_edge = (boxed.get_column("volume_dm3").to_numpy() * boxed.get_column("box_size").to_numpy()) ** (
        1 / 3
    ) * 10
    box_units = pl.DataFrame(
        {
            "mandt": MANDT,
            "matnr": boxed.get_column("matnr"),
            "meinh": "BOX",
            "umrez": boxed.get_column("box_size"),
            "umren": 1,
            "laeng": (box_edge * 1.2).round(1),
            "breit": box_edge.round(1),
            "hoehe": (box_edge * 0.8).round(1),
            "meabm": "CM",
            "brgew": (boxed.get_column("weight_kg") * boxed.get_column("box_size") * 1.15).round(3),
            "gewei": "KG",
        }
    )
    marm = pl.concat([base_units, box_units.cast(base_units.schema)]).sort(["matnr", "meinh"])

    plant_rows = []
    for werks, flag in (("US10", "in_us10"), ("CN10", "in_cn10")):
        subset = materials.filter(pl.col(flag))
        size = subset.height
        reorder = rng.random(size) < 0.2
        base_demand = rng.uniform(1, 40, size)
        eisbe = np.round(base_demand * rng.uniform(0.0, 1.5, size))
        # ABC by annual consumption value (price times popularity): A covers the top 70 % of value.
        value = subset.get_column("price_usd").to_numpy() * subset.get_column("popularity").to_numpy()
        order = np.argsort(-value, kind="stable")
        share = np.cumsum(value[order]) / value.sum()
        maabc = np.empty(size, dtype="<U1")
        maabc[order] = np.where(share <= 0.7, "A", np.where(share <= 0.9, "B", "C"))
        plant_rows.append(
            pl.DataFrame(
                {
                    "mandt": MANDT,
                    "matnr": subset.get_column("matnr"),
                    "werks": werks,
                    "dismm": np.where(reorder, "VB", "PD"),
                    "minbe": np.where(reorder, np.round(base_demand * 2), 0.0),
                    "eisbe": eisbe,
                    "beskz": np.where(subset.get_column("mtart").to_numpy() == "FERT", "E", "F"),
                    "plifz": subset.get_column("plifz"),
                    "stawn": [MATERIAL_GROUPS[i].hts for i in subset.get_column("group_index")],
                    "sernp": np.where(subset.get_column("serialized").to_numpy(), "ZSN1", ""),
                    "herkl": subset.get_column("herkl"),
                    "lvorm": "",
                    "maabc": maabc,
                    "mabst": np.round(base_demand * 60 + eisbe),
                }
            )
        )
    marc = pl.concat(plant_rows)

    valuation_rows = []
    for werks, flag, factor in (("US10", "in_us10", 1.0), ("CN10", "in_cn10", CNY_PER_USD)):
        subset = materials.filter(pl.col(flag))
        noise = rng.uniform(1.0, 1.05, subset.height)
        valuation_rows.append(
            pl.DataFrame(
                {
                    "mandt": MANDT,
                    "matnr": subset.get_column("matnr"),
                    "bwkey": werks,
                    "vprsv": "S",
                    "stprs": (subset.get_column("price_usd").to_numpy() * factor * noise).round(2),
                    "peinh": 1,
                }
            )
        )
    mbew = pl.concat(valuation_rows)
    return {
        "raw_sap_ecc.mara": mara,
        "raw_sap_ecc.makt": makt,
        "raw_sap_ecc.marm": marm,
        "raw_sap_ecc.marc": marc,
        "raw_sap_ecc.mbew": mbew,
    }


# --------------------------------------------------------------------------------------------------
# Storage layout, product keys, and slotting
# --------------------------------------------------------------------------------------------------


def storage_tables(profile: Profile) -> tuple[dict[str, pl.DataFrame], pl.DataFrame]:
    """Storage types, descriptions, and bins. Returns the raw tables and an internal bin list."""
    rng = _rng(profile, 3)
    t301 = pl.DataFrame(
        {
            "mandt": MANDT,
            "lgnum": [st.lgnum for st in STORAGE_TYPES],
            "lgtyp": [st.lgtyp for st in STORAGE_TYPES],
            "st_role": [st.st_role for st in STORAGE_TYPES],
            "put_rule": [st.put_rule for st in STORAGE_TYPES],
        }
    )
    t301t = pl.DataFrame(
        {
            "mandt": MANDT,
            "spras": LANGUAGE,
            "lgnum": [st.lgnum for st in STORAGE_TYPES],
            "lgtyp": [st.lgtyp for st in STORAGE_TYPES],
            "ltypt": [st.description for st in STORAGE_TYPES],
        }
    )
    frames = []
    for st in STORAGE_TYPES:
        if st.st_role == "D":
            count = st.full_bins
            names = [f"{st.bin_prefix}-{index:02d}" for index in range(1, count + 1)]
            aisle = stack = level = [""] * count
        else:
            count = max(20, round(st.full_bins * max(profile.scale, 0.02)))
            if st.lgtyp == "AS01":
                names = [f"{st.bin_prefix}-{index:06d}" for index in range(1, count + 1)]
                aisle = stack = level = [""] * count
            else:
                aisle_n = np.arange(count) // 40 + 1
                stack_n = (np.arange(count) // 4) % 10 + 1
                level_n = np.arange(count) % 4
                aisle = [f"{a:02d}" for a in aisle_n]
                stack = [f"{s:02d}" for s in stack_n]
                level = ["ABCD"[lvl] for lvl in level_n]
                names = [
                    f"{st.bin_prefix}-{a}-{s}-{lvl}" for a, s, lvl in zip(aisle, stack, level, strict=True)
                ]
        blocked = rng.random(count) < 0.01
        frames.append(
            pl.DataFrame(
                {
                    "mandt": MANDT,
                    "lgnum": st.lgnum,
                    "lgpla": names,
                    "lgtyp": st.lgtyp,
                    "lgber": "0001",
                    "lptyp": st.lptyp,
                    "aisle": aisle,
                    "stack": stack,
                    "lvl_v": level,
                    "kzler": np.where(rng.random(count) < 0.15, "X", ""),
                    "skzue": np.where(blocked, "X", ""),
                    "skzua": "",
                    "max_weight": st.max_weight,
                }
            )
        )
    lagp = pl.concat(frames)
    tables = {"raw_sap_ewm.scwm_t301": t301, "raw_sap_ewm.scwm_t301t": t301t, "raw_sap_ewm.scwm_lagp": lagp}
    return tables, lagp.select("lgnum", "lgpla", "lgtyp")


def product_keys(materials: pl.DataFrame, profile: Profile) -> pl.DataFrame:
    """EWM product GUIDs and 40-character product numbers (internal + raw table)."""
    rng = _rng(profile, 4)
    return materials.select(
        pl.lit(MANDT).alias("mandt"),
        pl.Series("matid", guids(rng, materials.height)),
        pl.col("matnr").str.zfill(40).alias("matnr"),
        pl.col("matnr").alias("matnr_ecc"),
    )


def slotting(materials: pl.DataFrame, bins: pl.DataFrame, profile: Profile) -> pl.DataFrame:
    """Primary source storage type and bin per warehouse and material."""
    rng = _rng(profile, 5)
    rows = []
    for lgnum, flag in (("RR01", "in_us10"), ("SZ01", "in_cn10"), ("DG01", "in_dg")):
        subset = materials.filter(pl.col(flag))
        weight = subset.get_column("weight_kg").to_numpy()
        price = subset.get_column("price_usd").to_numpy()
        serialized = subset.get_column("serialized").to_numpy()
        if lgnum == "RR01":
            lgtyp = np.where(serialized | (price > 10_000), "0030", np.where(weight > 15, "0010", "0020"))
        elif lgnum == "SZ01":
            small = (weight <= 5) & (rng.random(subset.height) < 0.7)
            lgtyp = np.where(small, "AS01", np.where(weight > 15, "0050", "0020"))
        else:
            lgtyp = np.where(weight > 15, "0050", "0020")
        chosen_bins = np.empty(subset.height, dtype=object)
        for storage_type in np.unique(lgtyp):
            candidates = bins.filter((pl.col("lgnum") == lgnum) & (pl.col("lgtyp") == storage_type))
            names = candidates.get_column("lgpla").to_numpy()
            mask = lgtyp == storage_type
            chosen_bins[mask] = rng.choice(names, size=int(mask.sum()))
        rows.append(
            pl.DataFrame(
                {
                    "lgnum": lgnum,
                    "matnr": subset.get_column("matnr"),
                    "lgtyp": lgtyp,
                    "lgpla": chosen_bins.astype(str),
                }
            )
        )
    return pl.concat(rows)


# --------------------------------------------------------------------------------------------------
# Customers
# --------------------------------------------------------------------------------------------------

_NAME_PREFIXES = (
    "Aldermoor",
    "Brightwater",
    "Cedarline",
    "Driftpeak",
    "Emberfield",
    "Foxglove",
    "Harborview",
    "Ironvale",
    "Juniperhill",
    "Kestrelbay",
    "Larchmont",
    "Oakhurst",
    "Pinecrest",
    "Quillon",
    "Rookwood",
    "Silverfen",
    "Thornbury",
    "Umberlake",
    "Valewood",
    "Willowmere",
    "Yarrowdale",
    "Zephyrine",
)
_NAME_KINDS = ("Semiconductor", "Microdevices", "Silicon Works", "Foundry", "Wafer Technologies")
_CITIES = {
    "US": ("Austin", "Phoenix", "Portland", "Albany", "Boise", "Dallas", "Colorado Springs"),
    "DE": ("Dresden",),
    "IE": ("Leixlip",),
    "IL": ("Kiryat Gat",),
    "SG": ("Singapore",),
    "JP": ("Kumamoto", "Hiroshima"),
    "KR": ("Pyeongtaek", "Icheon"),
    "TW": ("Hsinchu", "Tainan", "Taichung"),
    "CN": ("Shanghai", "Wuxi", "Beijing", "Shenzhen", "Xiamen", "Nanjing"),
    "MY": ("Kulim", "Penang"),
}
_US_ORG_COUNTRIES = (
    ("US", 0.55),
    ("DE", 0.10),
    ("IE", 0.08),
    ("IL", 0.07),
    ("SG", 0.05),
    ("JP", 0.05),
    ("KR", 0.05),
    ("TW", 0.05),
)
_CN_ORG_COUNTRIES = (("CN", 0.70), ("TW", 0.10), ("SG", 0.08), ("MY", 0.07), ("KR", 0.05))


WORK_CENTERS = {
    "RR": (
        "Round Rock",
        "US",
        "1000",
        ("Assembly Cell A", "Assembly Cell B", "Test Bay", "Final Integration"),
    ),
    "SZ": (
        "Shenzhen",
        "CN",
        "2000",
        (
            "Assembly Line 1",
            "Assembly Line 2",
            "Assembly Line 3",
            "Sub-assembly",
            "Test Floor",
            "Final Integration",
        ),
    ),
    "DG": ("Shenzhen", "CN", "2000", ("Assembly Line 1", "Assembly Line 2", "Sub-assembly")),
}


def generate_customers(profile: Profile) -> pl.DataFrame:
    """The manufacturing department's work centers as the internal partners of the deliveries (decision
    record 003): there are no external customers. Each issuing site serves the work centers of its plant;
    Dongguan serves Longgang's lines from the overflow hub."""
    rng = _rng(profile, 6)
    rows = []
    index = 0
    for site, (city, country, vkorg, centers) in WORK_CENTERS.items():
        plant = "RR" if site == "RR" else "Longgang"
        for center in centers:
            index += 1
            rows.append(
                {
                    "kunnr": f"{900001 + index:010d}",
                    "name1": f"{plant} {center} (manufacturing)"[:35],
                    "ort01": city,
                    "land1": country,
                    "ktokd": "ZINT",
                    "vkorg": vkorg,
                    "site": site,
                    "pod_required": True,
                    "size": float(rng.lognormal(0.0, 0.5)),
                }
            )
    return pl.DataFrame(rows)


def customer_tables(customers: pl.DataFrame) -> dict[str, pl.DataFrame]:
    kna1 = customers.select(pl.lit(MANDT).alias("mandt"), "kunnr", "name1", "ort01", "land1", "ktokd")
    knvv = customers.select(
        pl.lit(MANDT).alias("mandt"),
        "kunnr",
        "vkorg",
        pl.lit("10").alias("vtweg"),
        pl.lit("00").alias("spart"),
        pl.when(pl.col("pod_required")).then(pl.lit("X")).otherwise(pl.lit("")).alias("podkz"),
    )
    return {"raw_sap_ecc.kna1": kna1, "raw_sap_ecc.knvv": knvv}


# --------------------------------------------------------------------------------------------------
# Workforce
# --------------------------------------------------------------------------------------------------

FULL_HEADCOUNT = {"RR": 70, "SZ": 320, "DG": 130}


def generate_roster(profile: Profile) -> pl.DataFrame:
    """Pseudonymized roster. Guarantees at least two outbound FTEs per shift for the whole window."""
    rng = _rng(profile, 7)
    window_days = (profile.ewm_as_of.date() - profile.start).days + 1
    operator_ids = rng.permutation(np.arange(10_000, 100_000))
    rows = []
    serial = {code: 0 for code in SITES}

    def add(site_code: str, team: str, shift: str, employment: str, first: date, last: date | None) -> None:
        site = SITES[site_code]
        serial[site_code] += 1
        rows.append(
            {
                "operator_id": f"OP-{operator_ids[len(rows)]:05d}",
                "sap_uname": f"{site.uname_prefix}U{serial[site_code]:04d}",
                "werks": site.werks,
                "lgnum": site.lgnum,
                "team": team,
                "shift": shift,
                "employment_type": employment,
                "active_from": first,
                "active_to": last,
            }
        )

    for code, site in SITES.items():
        headcount = max(profile.min_operators_per_site, round(FULL_HEADCOUNT[code] * profile.scale))
        for shift in site.shifts:
            for _ in range(2):
                add(
                    code,
                    "OUTB",
                    shift,
                    "FTE",
                    profile.start - timedelta(days=int(rng.integers(200, 2000))),
                    None,
                )
        remaining = max(0, headcount - 2 * len(site.shifts))
        for _ in range(remaining):
            team = rng.choice(["OUTB", "INB", "INV"], p=[0.55, 0.30, 0.15])
            shift = rng.choice(site.shifts)
            if rng.random() < 0.25:
                first = profile.start + timedelta(days=int(rng.integers(0, max(1, window_days - 30))))
                last = first + timedelta(days=int(rng.integers(30, 181)))
                add(
                    code,
                    str(team),
                    str(shift),
                    "TEMP",
                    first,
                    last if last < profile.ewm_as_of.date() else None,
                )
            else:
                first = profile.start - timedelta(days=int(rng.integers(30, 2000)))
                leaves = rng.random() < 0.1
                earliest_exit = max(1, min(30, window_days - 1))
                exit_offset = int(rng.integers(earliest_exit, window_days)) if window_days > 1 else 0
                last = profile.start + timedelta(days=exit_offset) if leaves else None
                add(code, str(team), str(shift), "FTE", first, last)
    return pl.DataFrame(rows, schema_overrides={"active_to": pl.Date})


def shift_of(site_code: str, local_hour: np.ndarray) -> np.ndarray:
    """Shift letter for site-local hours (0-23)."""
    result = np.full(local_hour.shape, "", dtype=object)
    for shift in SITES[site_code].shifts:
        start, end = SHIFT_HOURS[(site_code, shift)]
        if end <= 24:
            mask = (local_hour >= start) & (local_hour < end)
        else:
            mask = (local_hour >= start) | (local_hour < end - 24)
        result[mask & (result == "")] = shift
    # Hours outside all shifts (overtime) are attributed to the nearest earlier shift.
    result[result == ""] = SITES[site_code].shifts[-1]
    return result
