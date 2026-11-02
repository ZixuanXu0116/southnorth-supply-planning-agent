"""Read access to the engine's outputs (``curated.replenishment_case``, ``curated.supply_position``,
``curated.supplier_profile``) and the cost model, from a local planning run or from the lakehouse."""

from __future__ import annotations

import math
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import polars as pl

from snm_data.planning.inputs import cost_parameters
from snm_data.planning.params import PlanningParams

_SAFE = re.compile(r"^[A-Za-z0-9_\-]+$")
# How often a long-lived case server checks whether a new engine run was published (one small query).
FRESHNESS_SECONDS = 60.0


def _literal(value: str) -> str:
    if not _SAFE.match(value):
        raise ValueError(f"unsafe identifier value: {value!r}")
    return f"'{value}'"


class PlanningData(Protocol):
    """``version()`` (optional) names the published run the reads come from; results computed from them
    are cached under it."""

    def cases(self) -> pl.DataFrame: ...
    def position(self, material: str, plant: str) -> pl.DataFrame: ...
    def suppliers(self) -> pl.DataFrame: ...
    def costs(self) -> dict[str, float]: ...


class LocalPlanningData:
    """A planning run written by ``snm-data plan --from-profile`` (``out/planning/<as-of>``)."""

    def __init__(self, planning_dir: Path, cost_parameter: Path | None = None) -> None:
        curated = Path(planning_dir) / "curated"
        self._cases = pl.read_parquet(curated / "replenishment_case.parquet")
        self._position = pl.read_parquet(curated / "supply_position.parquet")
        self._suppliers = pl.read_parquet(curated / "supplier_profile.parquet")
        frame = pl.read_parquet(cost_parameter) if cost_parameter and Path(cost_parameter).exists() else None
        self._costs = cost_parameters(frame, PlanningParams())
        self._version = Path(planning_dir).name  # a local run is read once and never changes

    def cases(self) -> pl.DataFrame:
        return self._cases

    def position(self, material: str, plant: str) -> pl.DataFrame:
        return self._position.filter((pl.col("material") == material) & (pl.col("plant") == plant)).sort(
            "day"
        )

    def suppliers(self) -> pl.DataFrame:
        return self._suppliers

    def costs(self) -> dict[str, float]:
        return self._costs

    def version(self) -> str:
        return self._version


class DatabricksPlanningData:
    """The latest published run in the lakehouse; ``backend`` is a ``snm_tools.backend.SqlBackend``."""

    def __init__(self, backend: Any, catalog: str, clock: Callable[[], float] = time.monotonic) -> None:
        self.backend = backend
        self.catalog = catalog
        # Reads of the published run, kept until a new run is published: a case server that lives as long
        # as the app (docs/DEPLOYMENT.md) checks for one at most once a minute (version()).
        self._cache: dict[str, pl.DataFrame] = {}
        self._positions: dict[tuple[str, str], pl.DataFrame] = {}
        self._costs: dict[str, float] | None = None
        self._clock = clock
        self._version: tuple[str, ...] | None = None
        self._checked = -math.inf

    def version(self) -> str:
        """The published run: its as-of date, engine version, parameter digest, and case count. When it
        changed, the reads of the previous run are dropped."""
        now = self._clock()
        if self._version is None or now - self._checked >= FRESHNESS_SECONDS:
            table = f"`{self.catalog}`.`curated`.`replenishment_case`"
            result = self.backend.execute(
                f"SELECT as_of_date, MAX(engine_version), MAX(params_digest), COUNT(*) FROM {table} "
                f"WHERE as_of_date = (SELECT MAX(as_of_date) FROM {table}) GROUP BY as_of_date"
            )
            identity = tuple(str(value) for value in result.rows[0]) if result.rows else ("none",)
            if self._version is not None and identity != self._version:
                self._cache.clear()
                self._positions.clear()
                self._costs = None
            self._version, self._checked = identity, now
        return "/".join(self._version)

    def _frame(self, sql: str) -> pl.DataFrame:
        result = self.backend.execute(sql)
        return pl.DataFrame(result.rows, schema=result.columns, orient="row", infer_schema_length=None)

    def _latest(self, table: str) -> str:
        return (
            f"SELECT * FROM `{self.catalog}`.`curated`.`{table}` WHERE as_of_date = "
            f"(SELECT MAX(as_of_date) FROM `{self.catalog}`.`curated`.`{table}`)"
        )

    def cases(self) -> pl.DataFrame:
        self.version()
        if "cases" not in self._cache:
            self._cache["cases"] = self._frame(self._latest("replenishment_case"))
        return self._cache["cases"]

    def position(self, material: str, plant: str) -> pl.DataFrame:
        self.version()
        if (material, plant) in self._positions:
            return self._positions[(material, plant)]
        sql = (
            f"{self._latest('supply_position')} AND material = {_literal(material)} "
            f"AND plant = {_literal(plant)} ORDER BY day"
        )
        self._positions[(material, plant)] = self._frame(sql)
        return self._positions[(material, plant)]

    def suppliers(self) -> pl.DataFrame:
        self.version()
        if "suppliers" not in self._cache:
            self._cache["suppliers"] = self._frame(self._latest("supplier_profile"))
        return self._cache["suppliers"]

    def costs(self) -> dict[str, float]:
        self.version()
        if self._costs is None:
            frame = self._frame(f"SELECT * FROM `{self.catalog}`.`raw_ext`.`cost_parameter`")
            self._costs = cost_parameters(frame if frame.height else None, PlanningParams())
        return self._costs
