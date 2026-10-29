"""SQL execution backends. The Databricks backend holds the only database credentials in the system."""

from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from snm_data.databricks import DatabricksSettings, sql_connection


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    elapsed_s: float


class BackendError(Exception):
    """A database-side failure (bad column, timeout, warehouse unavailable, ...)."""


class SqlBackend(Protocol):
    def execute(self, sql: str) -> QueryResult: ...


def to_json_value(value: Any) -> Any:
    """Convert driver values into JSON-safe primitives without losing decimal precision."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


class DatabricksBackend:
    """Executes guarded SQL on a Databricks SQL warehouse with a per-session statement timeout."""

    def __init__(self, settings: DatabricksSettings, statement_timeout_s: int = 120):
        self.settings = settings
        self.statement_timeout_s = statement_timeout_s
        self._connection = None

    def _cursor(self):
        if self._connection is None:
            self._connection = sql_connection(self.settings)
            with self._connection.cursor() as cursor:
                cursor.execute(f"USE CATALOG `{self.settings.catalog}`")
                cursor.execute(f"SET STATEMENT_TIMEOUT = {int(self.statement_timeout_s)}")
        return self._connection.cursor()

    def execute(self, sql: str) -> QueryResult:
        started = time.perf_counter()
        try:
            with self._cursor() as cursor:
                cursor.execute(sql)
                columns = [description[0] for description in cursor.description or []]
                rows = [[to_json_value(value) for value in row] for row in cursor.fetchall()]
        except Exception as error:  # normalize driver errors for the tool layer
            self.close()
            raise BackendError(str(error).splitlines()[0][:500]) from error
        return QueryResult(columns=columns, rows=rows, elapsed_s=round(time.perf_counter() - started, 3))

    def close(self) -> None:
        if self._connection is not None:
            try:
                self._connection.close()
            finally:
                self._connection = None
