"""Compute reference answers by executing each case's reference SQL on the warehouse."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from snm_tools.backend import SqlBackend

from .cases import Suite, answers_path


def compute_answers(suite: Suite, backend: SqlBackend) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for case in suite.cases:
        references = {}
        for name, sql, spec in case.references():
            result = backend.execute(sql)
            needed = [*spec.keys, *(value.column for value in spec.values)]
            missing = [column for column in needed if column not in result.columns]
            if missing:
                raise ValueError(f"{case.id}/{name}: reference result lacks columns {missing}")
            rows = result.rows
            if not spec.order_matters:
                key_index = [result.columns.index(key) for key in spec.keys]
                rows = sorted(rows, key=lambda row: [str(row[index]) for index in key_index])
            references[name] = {"columns": result.columns, "rows": rows}
        if references:
            results[case.id] = references
    return {
        "suite": suite.suite,
        "as_of": suite.as_of,
        "data_snapshot": suite.data_snapshot,
        "computed_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "results": results,
    }


def write_answers(suite: Suite, answers: dict[str, Any]) -> str:
    path = answers_path(suite)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(answers, indent=2) + "\n", encoding="utf-8")
    return str(path)
