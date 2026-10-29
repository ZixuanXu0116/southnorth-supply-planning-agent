"""Table contracts: the single source of truth for schemas, comments, tags, tests, and access.

Each YAML file under ``data_contracts/`` describes one table. Contracts drive:

* Parquet/Delta schemas produced by the generator and pipelines,
* Unity Catalog column/table comments and governed tags,
* data-quality tests (uniqueness, not-null, accepted values, relationships),
* role-based read access and site isolation (see ``governance``).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import polars as pl
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Layer = Literal["raw", "stg", "curated", "restricted", "marts", "semantic", "cases", "ops", "eval", "audit"]
Sensitivity = Literal["public", "internal", "restricted", "pii", "commercial"]
Role = Literal[
    "ops_analyst_global",
    "site_manager_rr",
    "site_manager_cn",
    "trade_compliance",
    "executive",
    "purchasing",
    "case_writer",
]

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_DECIMAL_RE = re.compile(r"^DECIMAL\((\d+),\s*(\d+)\)$")
_SIMPLE_TYPES: dict[str, pl.DataType] = {
    "STRING": pl.String(),
    "SMALLINT": pl.Int16(),
    "INT": pl.Int32(),
    "BIGINT": pl.Int64(),
    "DOUBLE": pl.Float64(),
    "BOOLEAN": pl.Boolean(),
    "DATE": pl.Date(),
    "TIMESTAMP": pl.Datetime("us", "UTC"),
    "TIMESTAMP_NTZ": pl.Datetime("us"),
}


def polars_dtype(sql_type: str) -> pl.DataType:
    """Map a Databricks SQL type used in contracts to the matching Polars dtype."""
    normalized = sql_type.strip().upper()
    if normalized in _SIMPLE_TYPES:
        return _SIMPLE_TYPES[normalized]
    match = _DECIMAL_RE.match(normalized)
    if match:
        return pl.Decimal(int(match.group(1)), int(match.group(2)))
    raise ValueError(f"Unsupported contract type: {sql_type!r}")


class Column(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: str
    comment: str = Field(min_length=3)
    nullable: bool = True
    sap_type: str | None = None
    sensitivity: Sensitivity | None = None

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        if not _NAME_RE.match(value):
            raise ValueError(f"Column name must be lower snake case: {value!r}")
        return value

    @field_validator("type")
    @classmethod
    def _check_type(cls, value: str) -> str:
        polars_dtype(value)
        return value.strip().upper()


class AcceptedValues(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column: str
    values: list[str]


class Relationship(BaseModel):
    model_config = ConfigDict(extra="forbid")

    columns: list[str]
    references: str
    ref_columns: list[str]
    # SAP stores "no reference" as blank strings; skip rows whose key columns (other than mandt) are blank.
    skip_blank: bool = False
    # Check only rows whose columns equal these values (e.g. the reference category of a polymorphic key).
    where: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _same_arity(self) -> Relationship:
        if len(self.columns) != len(self.ref_columns):
            raise ValueError("Relationship columns and ref_columns must have the same length")
        return self


class Tests(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unique: list[list[str]] = Field(default_factory=list)
    accepted_values: list[AcceptedValues] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)


class SiteKey(BaseModel):
    """The column that assigns a row of a multi-site table to a site (used by the site row filters)."""

    model_config = ConfigDict(extra="forbid")

    column: str
    kind: Literal["plant", "warehouse", "shipping_point", "organization"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: str
    layer: Layer
    source_system: str | None = None
    description: str = Field(min_length=10)
    grain: str = Field(min_length=5)
    primary_key: list[str]
    domain: str
    subdomain: str | None = None
    sensitivity: Sensitivity
    site_scope: Literal["all", "US10", "CN10"] = "all"
    site_key: SiteKey | None = None
    # Views are defined by data/pipelines/<layer>/<name>.sql and read the tables in depends_on.
    materialization: Literal["table", "view"] = "table"
    depends_on: list[str] = Field(default_factory=list)
    columns: list[Column]
    tests: Tests = Field(default_factory=Tests)
    access: list[Role]

    @field_validator("table")
    @classmethod
    def _check_table(cls, value: str) -> str:
        parts = value.split(".")
        if len(parts) != 2 or not all(_NAME_RE.match(part) for part in parts):
            raise ValueError(f"Table must be '<schema>.<table>' in lower snake case: {value!r}")
        return value

    @model_validator(mode="after")
    def _check_columns(self) -> Contract:
        names = [column.name for column in self.columns]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"{self.table}: duplicate columns {duplicates}")
        known = set(names)
        referenced = list(self.primary_key)
        for key in self.tests.unique:
            referenced.extend(key)
        referenced.extend(test.column for test in self.tests.accepted_values)
        for relationship in self.tests.relationships:
            referenced.extend(relationship.columns)
            referenced.extend(relationship.where)
        if self.site_key:
            referenced.append(self.site_key.column)
            if self.site_scope != "all":
                raise ValueError(f"{self.table}: a single-site table needs no site_key")
            if self.materialization == "view":
                raise ValueError(f"{self.table}: views inherit row filters from their tables; drop site_key")
        if (self.materialization == "view") != bool(self.depends_on):
            raise ValueError(f"{self.table}: views, and only views, declare depends_on")
        missing = sorted(set(referenced) - known)
        if missing:
            raise ValueError(f"{self.table}: tests or keys reference unknown columns {missing}")
        return self

    @property
    def schema_name(self) -> str:
        return self.table.split(".")[0]

    @property
    def table_name(self) -> str:
        return self.table.split(".")[1]

    def polars_schema(self) -> dict[str, pl.DataType]:
        return {column.name: polars_dtype(column.type) for column in self.columns}


class ContractRegistry:
    """All contracts, keyed by fully qualified table name (``schema.table``)."""

    def __init__(self, contracts: list[Contract]):
        self._by_table: dict[str, Contract] = {}
        for contract in contracts:
            if contract.table in self._by_table:
                raise ValueError(f"Duplicate contract for {contract.table}")
            self._by_table[contract.table] = contract
        self._check_relationships()

    @classmethod
    def load(cls, root: Path) -> ContractRegistry:
        contracts = []
        for path in sorted(root.rglob("*.yaml")):
            with path.open(encoding="utf-8") as handle:
                payload = yaml.safe_load(handle)
            try:
                contracts.append(Contract.model_validate(payload))
            except Exception as error:  # re-raise with the file path for fast diagnosis
                raise ValueError(f"Invalid contract {path}: {error}") from error
        return cls(contracts)

    def _check_relationships(self) -> None:
        for contract in self._by_table.values():
            for relationship in contract.tests.relationships:
                target = self._by_table.get(relationship.references)
                if target is None:
                    raise ValueError(
                        f"{contract.table}: relationship references unknown table {relationship.references}"
                    )
                target_columns = {column.name for column in target.columns}
                missing = set(relationship.ref_columns) - target_columns
                if missing:
                    raise ValueError(
                        f"{contract.table}: {relationship.references} lacks columns {sorted(missing)}"
                    )

    def __getitem__(self, table: str) -> Contract:
        return self._by_table[table]

    def __contains__(self, table: str) -> bool:
        return table in self._by_table

    def get(self, table: str) -> Contract | None:
        return self._by_table.get(table)

    def __iter__(self):
        return iter(self._by_table.values())

    def __len__(self) -> int:
        return len(self._by_table)

    def tables(self) -> list[str]:
        return sorted(self._by_table)

    def generated(self) -> list[str]:
        """Tables the synthetic data generator produces (source replicas and evaluation ground truth)."""
        return sorted(table for table, item in self._by_table.items() if item.layer in {"raw", "eval"})


class ContractViolation(Exception):
    """Raised when a data frame does not satisfy its contract."""


def conform(frame: pl.DataFrame, contract: Contract) -> pl.DataFrame:
    """Select and cast contract columns in contract order; fail on missing or extra columns."""
    expected = [column.name for column in contract.columns]
    missing = [name for name in expected if name not in frame.columns]
    extra = [name for name in frame.columns if name not in expected]
    if missing or extra:
        raise ContractViolation(f"{contract.table}: missing columns {missing}, unexpected columns {extra}")
    return frame.select(
        [pl.col(column.name).cast(polars_dtype(column.type), strict=True) for column in contract.columns]
    )


_SAP_FORMATS: dict[str, str] = {
    "DATS": r"^\d{8}$",
    "TIMS": r"^\d{6}$",
    "CLNT3": r"^\d{3}$",
    "LANG1": r"^[A-Z]$",
}
_SAP_LENGTH_RE = re.compile(r"^(CHAR|NUMC|UNIT|CUKY)(\d+)$")


def _format_rule(sap_type: str) -> tuple[str | None, int | None]:
    """Regex and maximum length implied by an SAP type annotation (None when not applicable)."""
    if sap_type in _SAP_FORMATS:
        return _SAP_FORMATS[sap_type], None
    match = _SAP_LENGTH_RE.match(sap_type)
    if not match:
        return None, None
    kind, length = match.group(1), int(match.group(2))
    if kind == "NUMC":
        return rf"^\d{{{length}}}$", length
    return None, length


def check_formats(frame: pl.DataFrame, contract: Contract) -> list[str]:
    """Validate string columns against their SAP type (DATS/TIMS/NUMC patterns, CHAR/UNIT/CUKY lengths)."""
    violations: list[str] = []
    for column in contract.columns:
        if column.sap_type is None or contract.polars_schema()[column.name] != pl.String():
            continue
        pattern, max_length = _format_rule(column.sap_type)
        values = frame.get_column(column.name).drop_nulls()
        if pattern is not None:
            bad = values.filter(~values.str.contains(pattern))
            if bad.len():
                violations.append(
                    f"{contract.table}.{column.name}: {bad.len()} values not matching {column.sap_type}, "
                    f"e.g. {bad.head(3).to_list()}"
                )
        if max_length is not None:
            too_long = values.filter(values.str.len_chars() > max_length)
            if too_long.len():
                violations.append(
                    f"{contract.table}.{column.name}: {too_long.len()} values longer than "
                    f"{max_length} characters"
                )
    return violations


def check_frame(frame: pl.DataFrame, contract: Contract) -> list[str]:
    """Return human-readable violations of table-local rules (schema, nullability, keys, accepted values)."""
    violations: list[str] = []
    schema = contract.polars_schema()
    if list(frame.columns) != list(schema):
        violations.append(f"{contract.table}: column order/set {frame.columns} != contract {list(schema)}")
        return violations
    for name, dtype in schema.items():
        if frame.schema[name] != dtype:
            violations.append(f"{contract.table}.{name}: dtype {frame.schema[name]} != {dtype}")
    for column in contract.columns:
        if not column.nullable:
            nulls = frame.get_column(column.name).null_count()
            if nulls:
                violations.append(f"{contract.table}.{column.name}: {nulls} nulls in non-nullable column")
    keys = [contract.primary_key, *contract.tests.unique]
    for key in keys:
        if key and frame.height:
            duplicates = frame.height - frame.select(key).unique().height
            if duplicates:
                violations.append(f"{contract.table}: {duplicates} duplicate rows for key {key}")
    for test in contract.tests.accepted_values:
        allowed = set(test.values)
        observed = set(frame.get_column(test.column).drop_nulls().unique().to_list())
        unexpected = sorted(str(value) for value in observed - allowed)
        if unexpected:
            violations.append(f"{contract.table}.{test.column}: unexpected values {unexpected[:10]}")
    violations.extend(check_formats(frame, contract))
    return violations


def check_relationships(frames: dict[str, pl.DataFrame], registry: ContractRegistry) -> list[str]:
    """Return referential-integrity violations across tables present in ``frames``."""
    violations: list[str] = []
    for contract in registry:
        frame = frames.get(contract.table)
        if frame is None:
            continue
        for relationship in contract.tests.relationships:
            target = frames.get(relationship.references)
            if target is None:
                continue
            rows = frame
            for column, value in relationship.where.items():
                rows = rows.filter(pl.col(column) == value)
            left = rows.select(relationship.columns).drop_nulls().unique()
            if relationship.skip_blank:
                keys = [column for column in relationship.columns if column != "mandt"]
                left = left.filter(pl.all_horizontal([pl.col(column) != "" for column in keys]))
            right = target.select(relationship.ref_columns).unique()
            right = right.rename(dict(zip(relationship.ref_columns, relationship.columns, strict=True)))
            orphans = left.join(right, on=relationship.columns, how="anti").height
            if orphans:
                violations.append(
                    f"{contract.table}{relationship.columns} -> {relationship.references}"
                    f"{relationship.ref_columns}: {orphans} orphan keys"
                )
    return violations
