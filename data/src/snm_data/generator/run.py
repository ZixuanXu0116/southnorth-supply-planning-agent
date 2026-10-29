"""Orchestrate generation of all tables for a profile and write contract-conformant Parquet files."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import polars as pl

from ..contracts import ContractRegistry, check_frame, check_relationships, conform
from .artifacts import duplicate_replication_day
from .autostore import generate_autostore
from .calendar import fiscal_year_of
from .config import PROFILES, Profile
from .inbound import generate_inbound
from .inventory import generate_inventory
from .manufacturing import production_plan
from .master import (
    customer_tables,
    fiscal_calendar_tables,
    generate_customers,
    generate_materials,
    generate_roster,
    material_tables,
    organization_tables,
    product_keys,
    slotting,
    storage_tables,
)
from .outbound import generate_outbound
from .planning_data import generate_planning_data
from .rfid import generate_rfid

GENERATOR_VERSION = "0.8.0"


@dataclass
class GenerationResult:
    tables: dict[str, pl.DataFrame]
    facts: dict[str, pl.DataFrame]
    violations: list[str]


def generate(profile: Profile, registry: ContractRegistry, keep_facts: bool = False) -> GenerationResult:
    """Generate every table, conform it to its contract, and collect contract violations.

    ``keep_facts`` retains internal ground-truth frames (used by tests); it increases peak memory.
    """
    tables: dict[str, pl.DataFrame] = {}
    tables.update(organization_tables())
    tables.update(
        fiscal_calendar_tables(
            fiscal_year_of(profile.start) - 1, fiscal_year_of(profile.ewm_as_of.date()) + 1
        )
    )
    materials = generate_materials(profile)
    tables.update(material_tables(materials, profile))
    customers = generate_customers(profile)
    tables.update(customer_tables(customers))
    storage, bins = storage_tables(profile)
    tables.update(storage)
    keys = product_keys(materials, profile)
    tables["raw_sap_ewm.sapapo_matkey"] = keys.select("mandt", "matid", "matnr")
    slots = slotting(materials, bins, profile)
    roster = generate_roster(profile)
    tables["raw_ext.workforce_roster"] = roster
    plan = production_plan(profile, materials)
    outbound = generate_outbound(
        profile,
        materials,
        customers,
        slots,
        keys,
        roster,
        tables["raw_sap_ecc.mbew"],
        tables["raw_sap_ecc.marm"],
        plan,
    )
    tables.update(outbound.tables)
    inventory = generate_inventory(
        profile,
        materials,
        tables["raw_sap_ecc.mbew"],
        outbound.facts["events"],
        outbound.facts["delivery_items"],
        tables["raw_sap_ecc.mkpf"],
        tables["raw_sap_ecc.mseg"],
        plan,
        tables["raw_sap_ecc.marc"],
    )
    tables.update(inventory.tables)
    planning = generate_planning_data(
        profile,
        tables["raw_sap_ecc.lfa1"],
        inventory.facts["purchase_orders"],
        inventory.facts["inventory_ledger"],
    )
    tables.update(planning.tables)
    inbound = generate_inbound(
        profile,
        materials,
        slots,
        bins,
        keys,
        roster,
        tables["raw_sap_ecc.mseg"],
        tables["raw_sap_ecc.mkpf"],
        tables["raw_sap_ecc.ekko"],
        tables["raw_sap_ecc.eket"],
    )
    tables.update(inbound.tables)
    for name, rows in (
        ("raw_sap_ewm.scdl_db_refdoc", inbound.refdoc),
        ("raw_sap_ewm.scwm_ordim_c", inbound.tasks),
    ):
        tables[name] = pl.concat([tables[name], rows], how="vertical_relaxed")
    autostore = generate_autostore(
        profile,
        tables["raw_sap_ewm.scwm_ordim_c"],
        tables["raw_sap_ewm.scwm_wavehdr"],
        outbound.facts["tasks"],
        outbound.facts["deliveries"],
    )
    tables.update(autostore.tables)
    tables["raw_sap_ewm.scwm_ordim_c"] = autostore.ordim_c
    tables["raw_sap_ewm.scwm_wavehdr"] = autostore.wavehdr
    rfid = generate_rfid(
        profile,
        tables["raw_sap_ewm.scwm_ordim_c"],
        tables["raw_sap_ewm.scwm_wavehdr"],
        outbound.facts["tasks"],
        outbound.facts["deliveries"],
    )
    tables.update(rfid.tables)
    tables["raw_sap_ewm.scwm_ordim_c"] = duplicate_replication_day(rfid.ordim_c)
    tables["raw_sap_ewm.scwm_wavehdr"] = rfid.wavehdr
    if not keep_facts:
        outbound.facts.clear()
        inventory.facts.clear()
        inbound.facts.clear()
        autostore.facts.clear()
        rfid.facts.clear()

    missing = sorted(set(registry.generated()) - set(tables))
    unexpected = sorted(set(tables) - set(registry.generated()))
    violations = [f"no generator output for contract {name}" for name in missing]
    violations += [f"generated table without contract {name}" for name in unexpected]
    conformed: dict[str, pl.DataFrame] = {}
    for name in sorted(tables):
        frame = tables.pop(name)
        if name not in registry:
            continue
        contract = registry[name]
        try:
            conformed[name] = conform(frame, contract)
            del frame
        except Exception as error:  # surface every table's problem at once
            violations.append(f"{name}: {error}")
            continue
        violations.extend(check_frame(conformed[name], contract))
    violations.extend(check_relationships(conformed, registry))
    facts: dict[str, pl.DataFrame] = {}
    if keep_facts:
        facts = dict(outbound.facts)
        facts.update(inventory.facts)
        facts.update(inbound.facts)
        facts.update(autostore.facts)
        facts.update(rfid.facts)
        facts.update(
            {"materials": materials, "customers": customers, "slotting": slots, "product_keys": keys}
        )
    return GenerationResult(tables=conformed, facts=facts, violations=violations)


def contracts_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.yaml")):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def write(result: GenerationResult, profile: Profile, out_dir: Path, contracts_root: Path) -> Path:
    """Write one Parquet file per table plus a manifest; returns the manifest path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    counts = {}
    for name, frame in sorted(result.tables.items()):
        schema, table = name.split(".")
        target = out_dir / schema / f"{table}.parquet"
        target.parent.mkdir(parents=True, exist_ok=True)
        frame.write_parquet(target, compression="zstd", statistics=True)
        counts[name] = frame.height
    profile_dict = asdict(profile)
    manifest = {
        "generator_version": GENERATOR_VERSION,
        "profile": {key: str(value) for key, value in profile_dict.items()},
        "contracts_digest": contracts_digest(contracts_root),
        "row_counts": counts,
        "total_rows": sum(counts.values()),
        "violations": result.violations,
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def resolve_profile(name: str) -> Profile:
    if name not in PROFILES:
        raise SystemExit(f"Unknown profile {name!r}; choose from {sorted(PROFILES)}")
    return PROFILES[name]
