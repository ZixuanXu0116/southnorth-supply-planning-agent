from pathlib import Path

import polars as pl
import pytest

from snm_data.contracts import Contract, ContractRegistry, ContractViolation, check_frame, conform

CONTRACTS = Path(__file__).resolve().parents[2] / "data_contracts"


@pytest.fixture(scope="module")
def registry() -> ContractRegistry:
    return ContractRegistry.load(CONTRACTS)


def test_all_contracts_load(registry: ContractRegistry) -> None:
    assert len(registry) >= 30
    for contract in registry:
        # Curated views stay internal until the curated layer is complete; metric views expose them.
        assert contract.access or contract.layer in {"eval", "curated"}, f"{contract.table} has no readers"
        assert contract.primary_key, f"{contract.table} has no primary key"


def test_eval_schema_is_never_readable_by_roles(registry: ContractRegistry) -> None:
    for contract in registry:
        if contract.schema_name == "eval":
            assert contract.access == [], f"{contract.table} must not be readable by agent roles"


def test_raw_sap_columns_declare_sap_types(registry: ContractRegistry) -> None:
    for contract in registry:
        if contract.source_system in {"SAP_ECC", "SAP_EWM"}:
            missing = [column.name for column in contract.columns if column.sap_type is None]
            assert not missing, f"{contract.table} columns without sap_type: {missing}"


def _tiny_contract() -> Contract:
    return Contract.model_validate(
        {
            "table": "raw_test.sample",
            "layer": "raw",
            "description": "A tiny contract used in unit tests.",
            "grain": "One row per id.",
            "primary_key": ["id"],
            "domain": "Test",
            "sensitivity": "internal",
            "access": ["ops_analyst_global"],
            "columns": [
                {
                    "name": "id",
                    "type": "STRING",
                    "nullable": False,
                    "sap_type": "NUMC4",
                    "comment": "Identifier.",
                },
                {
                    "name": "day",
                    "type": "STRING",
                    "nullable": False,
                    "sap_type": "DATS",
                    "comment": "A date.",
                },
                {"name": "qty", "type": "DECIMAL(13,3)", "comment": "A quantity."},
            ],
        }
    )


def test_format_checks_catch_iso_dates_and_bad_numc() -> None:
    contract = _tiny_contract()
    frame = conform(
        pl.DataFrame({"id": ["0001", "12"], "day": ["20260101", "2026-01-02"], "qty": [1.0, 2.5]}), contract
    )
    violations = check_frame(frame, contract)
    assert any("day" in violation and "DATS" in violation for violation in violations)
    assert any("id" in violation and "NUMC4" in violation for violation in violations)


def test_duplicate_keys_are_reported() -> None:
    contract = _tiny_contract()
    frame = conform(
        pl.DataFrame({"id": ["0001", "0001"], "day": ["20260101", "20260101"], "qty": [1.0, 1.0]}), contract
    )
    assert any("duplicate" in violation for violation in check_frame(frame, contract))


def test_conform_rejects_missing_or_extra_columns() -> None:
    contract = _tiny_contract()
    with pytest.raises(ContractViolation):
        conform(pl.DataFrame({"id": ["0001"], "day": ["20260101"]}), contract)
    with pytest.raises(ContractViolation):
        conform(pl.DataFrame({"id": ["0001"], "day": ["20260101"], "qty": [1.0], "extra": [1]}), contract)
