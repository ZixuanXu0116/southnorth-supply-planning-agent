"""AST-based SQL guard: only single, read-only queries over allowed tables reach the warehouse.

The guard parses SQL with sqlglot (Databricks dialect) and rejects anything that is not a single
``SELECT``/``WITH``/set-operation query, touches tables outside the role's contracts, reads files or
table-valued functions, calls risky functions, or references columns restricted for the role.
Accepted queries get an enforced row limit.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from .policy import AccessPolicy

DIALECT = "databricks"
MAX_SQL_CHARS = 20_000
FILE_FORMATS = frozenset({"parquet", "csv", "json", "delta", "orc", "avro", "text", "binaryfile", "xml"})
DENIED_FUNCTIONS = frozenset(
    {"http_request", "secret", "try_secret", "java_method", "reflect", "vector_search", "read_files"}
)
DENIED_PREFIXES = ("ai_", "read_", "list_secrets")
_WRITE_NODES = tuple(
    getattr(exp, name)
    for name in (
        "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "Command", "Set", "Use", "Copy",
        "Grant", "TruncateTable", "Cache", "Uncache", "Refresh", "LoadData", "Analyze",
    )
    if hasattr(exp, name)
)  # fmt: skip


@dataclass(frozen=True)
class GuardDecision:
    allowed: bool
    sql: str | None = None
    tables: tuple[str, ...] = ()
    row_limit: int | None = None
    probe_truncation: bool = False
    code: str | None = None
    message: str | None = None
    hints: tuple[str, ...] = field(default_factory=tuple)


def _deny(code: str, message: str, *hints: str) -> GuardDecision:
    return GuardDecision(allowed=False, code=code, message=message, hints=tuple(hints))


def _function_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.name).lower()
    return node.sql_name().lower()


class SqlGuard:
    def __init__(self, policy: AccessPolicy, catalog: str = "snm", max_rows: int = 1000):
        self.policy = policy
        self.catalog = catalog
        self.max_rows = max_rows

    def check(self, sql: str) -> GuardDecision:
        text = (sql or "").strip()
        if not text:
            return _deny("EMPTY_SQL", "No SQL was provided.")
        if len(text) > MAX_SQL_CHARS:
            return _deny("SQL_TOO_LONG", f"SQL exceeds {MAX_SQL_CHARS} characters.")
        try:
            statements = [
                statement for statement in sqlglot.parse(text, dialect=DIALECT) if statement is not None
            ]
        except ParseError as error:
            return _deny("PARSE_ERROR", f"SQL could not be parsed: {str(error).splitlines()[0][:300]}")
        if len(statements) != 1:
            return _deny("MULTIPLE_STATEMENTS", "Exactly one statement is allowed.")
        root = statements[0]
        if not isinstance(root, exp.Query) or any(True for _ in root.find_all(*_WRITE_NODES)):
            return _deny("NOT_READ_ONLY", "Only read-only SELECT / WITH queries are allowed.")

        for node in root.find_all(exp.Func):
            name = _function_name(node)
            if isinstance(node, exp.Anonymous) and isinstance(node.parent, exp.Dot):
                return _deny("FUNCTION_NOT_ALLOWED", "Calling catalog functions (UDFs) is not allowed.")
            if name in DENIED_FUNCTIONS or name.startswith(DENIED_PREFIXES):
                return _deny("FUNCTION_NOT_ALLOWED", f"Function {name}() is not allowed.")

        cte_names = {cte.alias_or_name.lower() for cte in root.find_all(exp.CTE)}
        tables: list[str] = []
        for table in root.find_all(exp.Table):
            name, schema, catalog = table.name.lower(), table.db.lower(), table.catalog.lower()
            if not name or isinstance(table.this, exp.Func):
                return _deny(
                    "TABLE_FUNCTION", "Table-valued functions are not allowed; query tables instead."
                )
            if name.startswith("/") or schema in FILE_FORMATS:
                return _deny("PATH_ACCESS", "Reading files or paths directly is not allowed.")
            if not schema and name in cte_names:
                continue
            if not schema:
                return _deny(
                    "UNQUALIFIED_TABLE",
                    f"Table {name!r} must be schema-qualified.",
                    "Use schema.table, e.g. raw_sap_ecc.likp. Call list_tables to see available tables.",
                )
            if catalog and catalog != self.catalog:
                return _deny("CATALOG_NOT_ALLOWED", f"Only catalog {self.catalog!r} may be queried.")
            key = f"{schema}.{name}"
            if self.policy.contract(key) is None:
                return _deny(
                    "UNKNOWN_TABLE",
                    f"Table {key} is not available.",
                    "Call list_tables to see available tables.",
                )
            if not self.policy.can_read_table(key):
                return _deny("TABLE_NOT_ALLOWED", f"Role {self.policy.role.name} may not read {key}.")
            tables.append(key)

        restricted = {column for table in tables for column in self.policy.restricted_columns(table)}
        if restricted:
            referenced = {column.name.lower() for column in root.find_all(exp.Column)}
            blocked = sorted(referenced & restricted)
            if blocked:
                return _deny(
                    "RESTRICTED_COLUMN",
                    f"Columns {blocked} are restricted for role {self.policy.role.name}.",
                    "Operator-level data is available only as team/shift aggregates for this role.",
                )
            if any(isinstance(star.parent, (exp.Select, exp.Column)) for star in root.find_all(exp.Star)):
                return _deny(
                    "STAR_ON_RESTRICTED_TABLE",
                    "SELECT * is not allowed on tables with restricted columns.",
                    "List the columns you need explicitly.",
                )

        limit = root.args.get("limit")
        requested = None
        if (
            isinstance(limit, exp.Limit)
            and isinstance(limit.expression, exp.Literal)
            and limit.expression.is_int
        ):
            requested = int(limit.expression.this)
        if requested is not None and requested <= self.max_rows:
            final, row_limit, probe = root, requested, False
        else:
            final, row_limit, probe = root.limit(self.max_rows + 1), self.max_rows, True
        return GuardDecision(
            allowed=True,
            sql=final.sql(dialect=DIALECT),
            tables=tuple(sorted(set(tables))),
            row_limit=row_limit,
            probe_truncation=probe,
        )
