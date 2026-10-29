"""Curated views and metric views: repository definitions and the SQL that deploys them to Unity Catalog.

* Curated views are described by their contracts (``materialization: view``) and defined by
  ``data/pipelines/<layer>/<name>.sql``.
* Metric views are defined by ``semantic/metric_views/<name>.yaml``: ``definition`` is the Unity Catalog
  metric view (YAML 1.1), deployed to ``<catalog>.semantic.<name>``; ``access`` lists the agent roles that may
  query it, ``restricted_dimensions`` the dimensions only roles with people-level access may use (operator
  identities; the tool server refuses them for other roles, as the SQL guard refuses restricted columns), and
  ``kb`` the knowledge-base documents whose definitions it implements.

Definitions refer to the catalog as ``{catalog}``. Access rules for both kinds of view live in ``governance``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .contracts import Contract, ContractRegistry, Role
from .ddl import SCHEMA_COMMENTS, quote_ident, sql_string

REPO_ROOT = Path(__file__).resolve().parents[3]
PIPELINES_DIR = REPO_ROOT / "data" / "pipelines"
METRIC_VIEWS_DIR = REPO_ROOT / "semantic" / "metric_views"
SEMANTIC_SCHEMA = "semantic"
CATALOG_TOKEN = "{catalog}"
_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


class MetricField(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    expr: str
    comment: str = Field(min_length=3)

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        if not _NAME.match(value):
            raise ValueError(f"metric view field names must be lower snake case: {value!r}")
        return value


class MetricViewDefinition(BaseModel):
    """Unity Catalog metric view YAML: checked where the project relies on it, passed through otherwise."""

    model_config = ConfigDict(extra="allow")

    version: float | str
    comment: str = Field(min_length=10)
    source: str
    filter: str | None = None
    dimensions: list[MetricField]
    measures: list[MetricField]


class MetricView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    access: list[Role]
    restricted_dimensions: list[str] = Field(default_factory=list)
    kb: list[str] = Field(default_factory=list)
    definition: MetricViewDefinition

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        if not _NAME.match(value):
            raise ValueError(f"metric view names must be lower snake case: {value!r}")
        return value

    @property
    def table(self) -> str:
        return f"{SEMANTIC_SCHEMA}.{self.name}"

    @property
    def source_table(self) -> str:
        """The source as ``schema.table`` (the catalog token removed)."""
        return self.definition.source.removeprefix(f"{CATALOG_TOKEN}.")

    def dimension(self, name: str) -> MetricField | None:
        return next((field for field in self.definition.dimensions if field.name == name), None)

    def measure(self, name: str) -> MetricField | None:
        return next((field for field in self.definition.measures if field.name == name), None)


def load_metric_views(root: Path = METRIC_VIEWS_DIR) -> list[MetricView]:
    views = []
    for path in sorted(root.glob("*.yaml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        try:
            view = MetricView.model_validate(payload)
        except Exception as error:
            raise ValueError(f"Invalid metric view {path}: {error}") from error
        if view.name != path.stem:
            raise ValueError(f"{path}: name {view.name!r} must match the file name")
        names = [field.name for field in view.definition.dimensions + view.definition.measures]
        if len(names) != len(set(names)):
            raise ValueError(f"{path}: dimension and measure names must be unique")
        unknown = [name for name in view.restricted_dimensions if view.dimension(name) is None]
        if unknown:
            raise ValueError(f"{path}: restricted_dimensions must name dimensions, not {unknown}")
        views.append(view)
    return views


def _resolve(text: str, catalog: str) -> str:
    return text.replace(CATALOG_TOKEN, quote_ident(catalog))


def view_sql(contract: Contract, catalog: str, root: Path = PIPELINES_DIR) -> str:
    path = root / contract.layer / f"{contract.table_name}.sql"
    return _resolve(path.read_text(encoding="utf-8").strip(), catalog)


def views_in_order(registry: ContractRegistry) -> list[Contract]:
    """View contracts, each after the views it depends on."""
    views = {contract.table: contract for contract in registry if contract.materialization == "view"}
    ordered: list[Contract] = []
    visiting: set[str] = set()

    def visit(table: str) -> None:
        if table not in views or views[table] in ordered:
            return
        if table in visiting:
            raise ValueError(f"views depend on each other in a cycle through {table}")
        visiting.add(table)
        for dependency in views[table].depends_on:
            visit(dependency)
        visiting.discard(table)
        ordered.append(views[table])

    for table in sorted(views):
        visit(table)
    return ordered


def view_statement(contract: Contract, catalog: str, root: Path = PIPELINES_DIR) -> str:
    schema, name = contract.table.split(".")
    columns = ",\n".join(
        f"  {quote_ident(column.name)} COMMENT {sql_string(column.comment)}" for column in contract.columns
    )
    comment = f"{contract.description} Grain: {contract.grain}"
    target = f"{quote_ident(catalog)}.{quote_ident(schema)}.{quote_ident(name)}"
    return (
        f"CREATE OR REPLACE VIEW {target} (\n{columns}\n)\n"
        f"COMMENT {sql_string(comment)}\nAS\n{view_sql(contract, catalog, root)}"
    )


def metric_view_yaml(view: MetricView, catalog: str) -> str:
    definition: dict[str, Any] = view.definition.model_dump(exclude_none=True)
    definition["source"] = view.definition.source.replace(CATALOG_TOKEN, catalog)
    return yaml.safe_dump(definition, sort_keys=False, allow_unicode=False, width=110)


def metric_view_statement(view: MetricView, catalog: str) -> str:
    target = f"{quote_ident(catalog)}.{quote_ident(SEMANTIC_SCHEMA)}.{quote_ident(view.name)}"
    body = metric_view_yaml(view, catalog)
    return f"CREATE OR REPLACE VIEW {target}\nWITH METRICS\nLANGUAGE YAML\nAS $$\n{body}$$"


def build_statements(
    registry: ContractRegistry, metric_views: list[MetricView], catalog: str, root: Path = PIPELINES_DIR
) -> list[str]:
    """Schemas, curated views (in dependency order), and metric views."""
    views = views_in_order(registry)
    schemas = sorted({view.schema_name for view in views} | ({SEMANTIC_SCHEMA} if metric_views else set()))
    statements = [
        f"CREATE SCHEMA IF NOT EXISTS {quote_ident(catalog)}.{quote_ident(schema)} "
        f"COMMENT {sql_string(SCHEMA_COMMENTS.get(schema, f'Schema {schema}.'))}"
        for schema in schemas
    ]
    statements += [view_statement(view, catalog, root) for view in views]
    return statements + [metric_view_statement(view, catalog) for view in metric_views]
