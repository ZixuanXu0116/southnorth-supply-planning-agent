"""Result handles: full query results stay server-side; the model sees previews and pages."""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StoredResult:
    result_id: str
    sql: str
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool
    # Set when the SQL was compiled from a metric view by query_metric.
    metric_view: str | None = None


class ResultStore:
    """In-memory store scoped to one tool-server process (one agent session)."""

    def __init__(self, max_results: int = 200):
        self._results: dict[str, StoredResult] = {}
        self._counter = itertools.count(1)
        self.max_results = max_results

    def put(
        self,
        sql: str,
        columns: list[str],
        rows: list[list[Any]],
        truncated: bool,
        metric_view: str | None = None,
    ) -> StoredResult:
        result = StoredResult(f"r{next(self._counter):04d}", sql, columns, rows, truncated, metric_view)
        self._results[result.result_id] = result
        while len(self._results) > self.max_results:
            self._results.pop(next(iter(self._results)))
        return result

    def get(self, result_id: str) -> StoredResult | None:
        return self._results.get(result_id)


def column_stats(
    columns: list[str], rows: list[list[Any]], max_distinct: int = 50
) -> dict[str, dict[str, Any]]:
    """Cheap per-column statistics computed on the stored (possibly truncated) rows."""
    stats: dict[str, dict[str, Any]] = {}
    for index, name in enumerate(columns):
        values = [row[index] for row in rows]
        present = [value for value in values if value is not None]
        entry: dict[str, Any] = {"nulls": len(values) - len(present)}
        numeric = []
        for value in present:
            try:
                numeric.append(float(value))
            except (TypeError, ValueError):
                numeric = []
                break
        if numeric:
            entry.update(min=min(numeric), max=max(numeric), mean=round(sum(numeric) / len(numeric), 6))
        else:
            distinct = {str(value) for value in present}
            entry["distinct"] = len(distinct) if len(distinct) <= max_distinct else f">{max_distinct}"
        stats[name] = entry
    return stats
