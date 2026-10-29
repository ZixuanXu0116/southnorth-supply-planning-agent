"""Tool implementations. Every data access goes through the SQL guard and the audit log."""

from __future__ import annotations

import re
from typing import Any

from snm_data.semantic import MetricView

from .audit import AuditLog
from .backend import BackendError, QueryResult, SqlBackend
from .metrics import MetricFilter, MetricOrder, MetricQueryError, compile_query, describe, summarize
from .policy import AccessPolicy
from .results import ResultStore, column_stats
from .sql_guard import SqlGuard

TABLE_RE = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$")
GOVERNED_LAYERS = frozenset({"curated", "marts", "semantic"})
# "full": contract descriptions, comments, keys, relationships (Unity Catalog semantics).
# "bare": names and types only, like a catalog without documentation (evaluation ablation).
METADATA_MODES = frozenset({"full", "bare"})


class ToolFailure(Exception):
    """A tool-level error the model can act on (bad input, policy block, query failure)."""

    def __init__(self, code: str, message: str, hints: tuple[str, ...] | list[str] = ()):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hints = list(hints)

    def payload(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "hints": self.hints}


def _quote(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


class DataTools:
    def __init__(
        self,
        policy: AccessPolicy,
        guard: SqlGuard,
        backend: SqlBackend,
        store: ResultStore | None = None,
        audit: AuditLog | None = None,
        preview_rows: int = 20,
        metadata: str = "full",
        metric_views: list[MetricView] | None = None,
    ):
        if metadata not in METADATA_MODES:
            raise ValueError(f"metadata must be one of {sorted(METADATA_MODES)}")
        self.metadata = metadata
        self.policy = policy
        self.guard = guard
        self.backend = backend
        self.store = store or ResultStore()
        self.audit = audit or AuditLog(None)
        self.preview_rows = preview_rows
        self.metric_views = {view.name: view for view in metric_views or []}

    # -- metadata -----------------------------------------------------------------------------

    def list_tables(self, schema: str | None = None) -> dict[str, Any]:
        if self.metadata == "bare":
            names = [
                {"table": contract.table}
                for contract in sorted(self.policy.readable_tables(), key=lambda item: item.table)
                if schema is None or contract.schema_name == schema
            ]
            return {**self._role_header(), "tables": names}
        tables = [
            {
                "table": contract.table,
                "layer": contract.layer,
                "domain": contract.domain,
                "subdomain": contract.subdomain,
                "certified": contract.layer in GOVERNED_LAYERS,
                "description": contract.description,
                "grain": contract.grain,
            }
            for contract in sorted(self.policy.readable_tables(), key=lambda item: item.table)
            if schema is None or contract.schema_name == schema
        ]
        return {**self._role_header(), "tables": tables}

    def describe_table(self, table: str) -> dict[str, Any]:
        contract = self._readable_contract(table)
        restricted = self.policy.restricted_columns(table)
        # Access information, not documentation: shown in both metadata modes.
        scope = self.policy.row_scope(contract)
        row_scope = {"row_scope": scope["note"]} if scope else {}
        if self.metadata == "bare":
            return {
                "table": contract.table,
                **row_scope,
                "columns": [
                    {
                        "name": column.name,
                        "type": column.type,
                        "restricted_for_role": column.name in restricted,
                    }
                    for column in contract.columns
                ],
            }
        return {
            "table": contract.table,
            "description": contract.description,
            "grain": contract.grain,
            "primary_key": contract.primary_key,
            "layer": contract.layer,
            "domain": contract.domain,
            "subdomain": contract.subdomain,
            "sensitivity": contract.sensitivity,
            **row_scope,
            "columns": [
                {
                    "name": column.name,
                    "type": column.type,
                    "comment": column.comment,
                    "sap_type": column.sap_type,
                    "restricted_for_role": column.name in restricted,
                }
                for column in contract.columns
            ],
            "accepted_values": {test.column: test.values for test in contract.tests.accepted_values},
            "relationships": [
                {"columns": rel.columns, "references": rel.references, "ref_columns": rel.ref_columns}
                for rel in contract.tests.relationships
            ],
        }

    # -- queries --------------------------------------------------------------------------------

    def run_sql(self, sql: str, purpose: str = "") -> dict[str, Any]:
        decision = self.guard.check(sql)
        if not decision.allowed:
            self.audit.record(tool="run_sql", role=self.policy.role.name, allowed=False, code=decision.code,
                              sql=sql, purpose=purpose)  # fmt: skip
            raise ToolFailure(
                decision.code or "REJECTED", decision.message or "Query rejected.", decision.hints
            )
        try:
            result = self.backend.execute(decision.sql or "")
        except BackendError as error:
            self.audit.record(tool="run_sql", role=self.policy.role.name, allowed=True, error=str(error),
                              sql=decision.sql, purpose=purpose)  # fmt: skip
            raise ToolFailure(
                "QUERY_FAILED", str(error), ["Check column names with describe_table and simplify the query."]
            ) from error
        limit = decision.row_limit or len(result.rows)
        truncated = decision.probe_truncation and len(result.rows) > limit
        extra = {"tables": list(decision.tables)}
        return self._respond("run_sql", decision.sql or "", result, limit, truncated, purpose, extra)

    def _respond(
        self,
        tool: str,
        sql: str,
        result: QueryResult,
        limit: int,
        truncated: bool,
        purpose: str,
        extra: dict[str, Any],
    ) -> dict[str, Any]:
        """Store a query result behind a result_id, audit it, and return its preview."""
        rows = result.rows[:limit]
        stored = self.store.put(sql, result.columns, rows, truncated, extra.get("metric_view"))
        self.audit.record(tool=tool, role=self.policy.role.name, allowed=True, sql=sql, purpose=purpose,
                          rows=len(rows), truncated=truncated, elapsed_s=result.elapsed_s,
                          result_id=stored.result_id, **extra)  # fmt: skip
        response: dict[str, Any] = {
            "result_id": stored.result_id,
            "columns": result.columns,
            "row_count": len(rows),
            "truncated": truncated,
            "preview": rows[: self.preview_rows],
            "stats": column_stats(result.columns, rows),
            **extra,
            "elapsed_s": result.elapsed_s,
        }
        if truncated:
            response["note"] = (
                f"Result truncated at {limit} rows; aggregate in SQL instead of paging raw rows."
            )
        elif len(rows) > self.preview_rows:
            response["note"] = (
                f"Preview shows {self.preview_rows} of {len(rows)} rows; use get_result to page."
            )
        return response

    # -- governed metrics -------------------------------------------------------------------------

    def list_metrics(self) -> dict[str, Any]:
        views = [view for view in self.metric_views.values() if self.policy.role.name in view.access]
        menu = [summarize(view, self.policy.restricted_dimensions(view)) for view in views]
        response: dict[str, Any] = {"role": self.policy.role.name, "metric_views": menu}
        if menu:
            response["note"] = "Call describe_metric for a view's measure and dimension definitions."
        else:
            response["note"] = f"No metric views are granted to role {self.policy.role.name}."
        return response

    def describe_metric(self, metric_view: str) -> dict[str, Any]:
        view = self._metric_view(metric_view)
        return {"role": self.policy.role.name, **describe(view, self.policy.restricted_dimensions(view))}

    def _metric_view(self, name: str) -> MetricView:
        view = self.metric_views.get(name)
        if view is None or self.policy.role.name not in view.access:
            message = f"No metric view {name!r} for this role."
            raise ToolFailure("UNKNOWN_METRIC_VIEW", message, ["Call list_metrics."])
        return view

    def query_metric(
        self,
        metric_view: str,
        measures: list[str],
        dimensions: list[str] | None = None,
        filters: list[MetricFilter] | None = None,
        order_by: list[MetricOrder] | None = None,
        limit: int | None = None,
        purpose: str = "",
    ) -> dict[str, Any]:
        view = self._metric_view(metric_view)
        used = set(dimensions or []) | {item.dimension for item in filters or []}
        blocked = sorted(used & self.policy.restricted_dimensions(view))
        if blocked:
            self.audit.record(tool="query_metric", role=self.policy.role.name, allowed=False,
                              code="RESTRICTED_DIMENSION", metric_view=view.name)  # fmt: skip
            raise ToolFailure(
                "RESTRICTED_DIMENSION",
                f"{view.name} dimension {blocked} is restricted for role {self.policy.role.name}.",
                ["Use team/shift aggregates instead of operator-level data."],
            )
        max_rows = self.guard.max_rows
        try:
            sql = compile_query(view, self.guard.catalog, measures, dimensions, filters, order_by,
                                min(limit or max_rows, max_rows) + 1)  # fmt: skip
        except MetricQueryError as error:
            self.audit.record(tool="query_metric", role=self.policy.role.name, allowed=False,
                              code="INVALID_METRIC_QUERY", metric_view=metric_view)  # fmt: skip
            raise ToolFailure("INVALID_METRIC_QUERY", str(error), error.hints) from error
        try:
            result = self.backend.execute(sql)
        except BackendError as error:
            self.audit.record(tool="query_metric", role=self.policy.role.name, allowed=True, error=str(error),
                              sql=sql, purpose=purpose)  # fmt: skip
            hints = ["Check the filter values and their types."]
            raise ToolFailure("QUERY_FAILED", str(error), hints) from error
        cap = min(limit or max_rows, max_rows)
        response = self._respond(
            "query_metric", sql, result, cap, len(result.rows) > cap, purpose, {"metric_view": view.name}
        )
        return {**response, "sql": sql}

    def get_result(self, result_id: str, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        stored = self.store.get(result_id)
        if stored is None:
            raise ToolFailure("UNKNOWN_RESULT", f"No stored result {result_id!r} in this session.")
        offset = max(0, offset)
        limit = max(1, min(limit, 200))
        return {
            "result_id": result_id,
            "columns": stored.columns,
            "offset": offset,
            "rows": stored.rows[offset : offset + limit],
            "total_rows": len(stored.rows),
            "truncated": stored.truncated,
            "sql": stored.sql,
            **({"metric_view": stored.metric_view} if stored.metric_view else {}),
        }

    def profile_column(self, table: str, column: str) -> dict[str, Any]:
        contract = self._readable_contract(table)
        self._check_column(contract.table, column, [c.name for c in contract.columns])
        quoted = _quote(column)
        summary = self.run_sql(
            f"SELECT COUNT(*) AS row_count, COUNT({quoted}) AS non_null, "
            f"COUNT(DISTINCT {quoted}) AS distinct_values, "
            f"MIN({quoted}) AS min_value, MAX({quoted}) AS max_value FROM {table}",
            purpose=f"profile {table}.{column}",
        )
        top = self.run_sql(
            f"SELECT {quoted} AS value, COUNT(*) AS row_count FROM {table} GROUP BY {quoted} "
            f"ORDER BY row_count DESC LIMIT 10",
            purpose=f"top values {table}.{column}",
        )
        return {
            "table": table,
            "column": column,
            "summary": dict(zip(summary["columns"], summary["preview"][0], strict=True)),
            "top_values": [dict(zip(top["columns"], row, strict=True)) for row in top["preview"]],
        }

    def sample_rows(self, table: str, n: int = 10) -> dict[str, Any]:
        contract = self._readable_contract(table)
        restricted = self.policy.restricted_columns(table)
        columns = [column.name for column in contract.columns if column.name not in restricted]
        n = max(1, min(n, 20))
        result = self.run_sql(
            f"SELECT {', '.join(_quote(name) for name in columns)} FROM {table} LIMIT {n}",
            purpose=f"sample {table}",
        )
        response = {"table": table, "columns": result["columns"], "rows": result["preview"]}
        if restricted:
            response["omitted_columns"] = sorted(restricted)
        return response

    def data_freshness(self) -> dict[str, Any]:
        checks = {
            "ecc_last_entry_chicago_time": (
                "raw_sap_ecc.mkpf",
                "SELECT MAX(concat(cpudt, cputm)) AS last_entry FROM raw_sap_ecc.mkpf",
            ),
            "ewm_last_confirmation_utc": (
                "raw_sap_ewm.scwm_ordim_c",
                "SELECT MAX(confirmed_at) AS last_confirmation FROM raw_sap_ewm.scwm_ordim_c",
            ),
        }
        response: dict[str, Any] = {
            "notes": [
                "ECC is extracted nightly (00:00 America/Chicago); EWM is replicated hourly and is fresher.",
                "The most recent day is incomplete in both systems.",
            ]
        }
        for key, (table, sql) in checks.items():
            if self.policy.can_read_table(table):
                response[key] = self.run_sql(sql, purpose="data freshness")["preview"][0][0]
        return response

    # -- helpers --------------------------------------------------------------------------------

    def _role_header(self) -> dict[str, Any]:
        header: dict[str, Any] = {"role": self.policy.role.name, "catalog": self.guard.catalog}
        if not self.policy.role.all_sites:
            header["sites"] = list(self.policy.role.sites)
        return header

    def _readable_contract(self, table: str):
        name = (table or "").strip().lower()
        if not TABLE_RE.match(name):
            raise ToolFailure("INVALID_TABLE", f"Expected schema.table, got {table!r}.")
        contract = self.policy.contract(name)
        if contract is None:
            raise ToolFailure("UNKNOWN_TABLE", f"Table {name} is not available.", ["Call list_tables."])
        if not self.policy.can_read_table(name):
            raise ToolFailure("TABLE_NOT_ALLOWED", f"Role {self.policy.role.name} may not read {name}.")
        return contract

    def _check_column(self, table: str, column: str, known: list[str]) -> None:
        if column not in known:
            raise ToolFailure(
                "UNKNOWN_COLUMN", f"{table} has no column {column!r}.", ["Call describe_table."]
            )
        if column in self.policy.restricted_columns(table):
            raise ToolFailure(
                "RESTRICTED_COLUMN",
                f"{table}.{column} is restricted for role {self.policy.role.name}.",
                ["Use team/shift aggregates instead of operator-level data."],
            )
