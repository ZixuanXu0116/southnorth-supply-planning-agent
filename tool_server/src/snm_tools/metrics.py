"""Governed KPIs: metric-view queries compiled from a structured request.

The agent chooses a metric view from the menu (``list_metrics``: one line per view with the names it can be
queried by), reads its definitions (``describe_metric``), and asks for measures, dimensions, and filters. The
tool server checks every name against the metric view definitions in ``semantic/metric_views`` and writes the
SQL itself (``MEASURE()`` over the metric view), so a KPI's definition never depends on model-written SQL.
"""

from __future__ import annotations

import math
from collections.abc import Collection
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from snm_data.ddl import quote_ident, sql_string
from snm_data.semantic import SEMANTIC_SCHEMA, MetricField, MetricView

Operator = Literal["=", "!=", "<", "<=", ">", ">=", "in", "between"]
Scalar = str | int | float | bool


class MetricFilter(BaseModel):
    """A condition on a dimension; dates are 'YYYY-MM-DD' strings."""

    model_config = ConfigDict(extra="forbid")

    dimension: str
    op: Operator
    value: Scalar | list[Scalar]


class MetricOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    descending: bool = False


class MetricQueryError(ValueError):
    def __init__(self, message: str, hints: list[str]):
        super().__init__(message)
        self.hints = hints


def literal(value: Scalar) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int | float):
        if not math.isfinite(value):
            raise MetricQueryError("Filter values must be finite numbers.", [])
        return repr(value)
    return sql_string(str(value))


def _condition(item: MetricFilter) -> str:
    column = quote_ident(item.dimension)
    values = item.value if isinstance(item.value, list) else [item.value]
    if item.op == "in":
        if not values:
            raise MetricQueryError("'in' needs at least one value.", [])
        return f"{column} IN ({', '.join(literal(value) for value in values)})"
    if item.op == "between":
        if len(values) != 2:
            raise MetricQueryError("'between' needs a list of two values: [low, high].", [])
        return f"{column} BETWEEN {literal(values[0])} AND {literal(values[1])}"
    if len(values) != 1:
        raise MetricQueryError(f"'{item.op}' needs a single value.", [])
    return f"{column} {item.op} {literal(values[0])}"


def compile_query(
    view: MetricView,
    catalog: str,
    measures: list[str],
    dimensions: list[str] | None = None,
    filters: list[MetricFilter] | None = None,
    order_by: list[MetricOrder] | None = None,
    limit: int | None = None,
) -> str:
    """The SQL for one metric-view query; every name is checked against the view's definition."""
    dimensions = list(dimensions or [])
    known_measures = [field.name for field in view.definition.measures]
    known_dimensions = [field.name for field in view.definition.dimensions]
    if not measures:
        raise MetricQueryError("Ask for at least one measure.", [f"Measures: {', '.join(known_measures)}."])
    unknown = [name for name in measures if view.measure(name) is None]
    if unknown:
        raise MetricQueryError(
            f"{view.name} has no measure {unknown}.", [f"Measures: {', '.join(known_measures)}."]
        )
    referenced = dimensions + [item.dimension for item in filters or []]
    unknown = sorted({name for name in referenced if view.dimension(name) is None})
    if unknown:
        hints = [f"Dimensions: {', '.join(known_dimensions)}."]
        raise MetricQueryError(f"{view.name} has no dimension {unknown}.", hints)
    if len(set(dimensions)) != len(dimensions) or len(set(measures)) != len(measures):
        raise MetricQueryError("List each dimension and measure once.", [])
    selected = dimensions + measures
    columns = [quote_ident(name) for name in dimensions]
    columns += [f"MEASURE({quote_ident(name)}) AS {quote_ident(name)}" for name in measures]
    target = f"{quote_ident(catalog)}.{quote_ident(SEMANTIC_SCHEMA)}.{quote_ident(view.name)}"
    sql = f"SELECT {', '.join(columns)}\nFROM {target}"
    if filters:
        sql += "\nWHERE " + "\n  AND ".join(_condition(item) for item in filters)
    if dimensions:
        sql += "\nGROUP BY ALL"
    if order_by:
        unknown = [item.name for item in order_by if item.name not in selected]
        if unknown:
            raise MetricQueryError(f"Order only by selected names, not {unknown}.", [f"Selected: {selected}"])
        sql += "\nORDER BY " + ", ".join(
            f"{quote_ident(item.name)}{' DESC' if item.descending else ''}" for item in order_by
        )
    elif dimensions:
        sql += "\nORDER BY " + ", ".join(quote_ident(name) for name in dimensions)
    if limit is not None:
        sql += f"\nLIMIT {max(1, int(limit))}"
    return sql


def _entry(item: MetricField, restricted: Collection[str]) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": item.name, "description": item.comment}
    if item.name in restricted:
        entry["restricted_for_role"] = True
    return entry


def summarize(view: MetricView, restricted: Collection[str] = ()) -> dict[str, Any]:
    """The menu line of a metric view: what it computes and the names it can be queried by."""
    entry: dict[str, Any] = {
        "metric_view": view.name,
        "description": " ".join(view.definition.comment.split()),
        "measures": [item.name for item in view.definition.measures],
        "dimensions": [item.name for item in view.definition.dimensions],
        "definitions": view.kb,
    }
    if restricted:
        entry["restricted_for_role"] = sorted(restricted)
    return entry


def describe(view: MetricView, restricted: Collection[str] = ()) -> dict[str, Any]:
    """A metric view's definitions: every measure and dimension with its meaning; ``restricted`` dimensions
    (operator identities the role may not use) are flagged."""
    return {
        "metric_view": view.name,
        "description": " ".join(view.definition.comment.split()),
        "measures": [_entry(item, ()) for item in view.definition.measures],
        "dimensions": [_entry(item, restricted) for item in view.definition.dimensions],
        "definitions": view.kb,
    }
