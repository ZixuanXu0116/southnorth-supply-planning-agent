"""Check that the warehouse holds the data snapshot a suite's reference answers are bound to."""

from __future__ import annotations

from typing import Any

RAW_SCHEMAS = ("raw_sap_ecc", "raw_sap_ewm", "raw_ext")
SUITE_KEYS = ("profile", "generator_version", "seed")
_VOLATILE = ("load_status", "loaded_at")


class SnapshotMismatch(ValueError):
    """The loaded data is not the snapshot the suite expects (or its load did not complete)."""


def check_snapshot(expected: dict[str, Any], loaded: dict[str, dict[str, str]]) -> dict[str, str]:
    """Return the loaded snapshot if every raw schema completed loading the expected one; raise otherwise."""
    problems = []
    for schema in RAW_SCHEMAS:
        properties = loaded.get(schema) or {}
        status = properties.get("load_status")
        if status != "complete":
            problems.append(f"{schema}: load {status or 'not recorded'}")
            continue
        problems += [
            f"{schema}: {key} is {properties.get(key)!r}, the suite expects {expected.get(key)!r}"
            for key in SUITE_KEYS
            if str(properties.get(key)) != str(expected.get(key))
        ]
    identities = {
        tuple(
            sorted((key, value) for key, value in (loaded.get(schema) or {}).items() if key not in _VOLATILE)
        )
        for schema in RAW_SCHEMAS
    }
    if not problems and len(identities) > 1:
        problems.append("the raw schemas hold different snapshots")
    if problems:
        raise SnapshotMismatch(
            "the loaded data does not match the suite's data snapshot: "
            + "; ".join(problems)
            + ". Generate and load the expected profile with `snm-data`."
        )
    return {key: value for key, value in loaded[RAW_SCHEMAS[0]].items() if key != "load_status"}


def verify_loaded_snapshot(expected: dict[str, Any], settings: Any) -> dict[str, str]:
    """Read the snapshot recorded on the raw schemas (Databricks) and check it against the suite."""
    from snm_data.load import read_snapshots

    return check_snapshot(expected, read_snapshots(settings, list(RAW_SCHEMAS)))
