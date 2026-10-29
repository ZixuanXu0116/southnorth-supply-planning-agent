"""Read access to the engine's outputs (``curated.replenishment_case``, ``curated.supply_position``,
``curated.supplier_profile``) and the cost model, from a local planning run or from the lakehouse."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Protocol

import polars as pl

from snm_data.planning.inputs import cost_parameters
from snm_data.planning.params import PlanningParams

_SAFE = re.compile(r"^[A-Za-z0-9_\-]+$")


def _literal(value: str) -> str:
    if not _SAFE.match(value):
        raise ValueError(f"unsafe identifier value: {value!r}")
    return f"'{value}'"


class PlanningData(Protocol):
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


class DatabricksPlanningData:
    """The latest published run in the lakehouse; ``backend`` is a ``snm_tools.backend.SqlBackend``."""

    def __init__(self, backend: Any, catalog: str) -> None:
        self.backend = backend
        self.catalog = catalog
        self._cache: dict[str, pl.DataFrame] = {}

    def _frame(self, sql: str) -> pl.DataFrame:
        result = self.backend.execute(sql)
        return pl.DataFrame(result.rows, schema=result.columns, orient="row", infer_schema_length=None)

    def _latest(self, table: str) -> str:
        return (
            f"SELECT * FROM `{self.catalog}`.`curated`.`{table}` WHERE as_of_date = "
            f"(SELECT MAX(as_of_date) FROM `{self.catalog}`.`curated`.`{table}`)"
        )

    def cases(self) -> pl.DataFrame:
        if "cases" not in self._cache:
            self._cache["cases"] = self._frame(self._latest("replenishment_case"))
        return self._cache["cases"]

    def position(self, material: str, plant: str) -> pl.DataFrame:
        sql = (
            f"{self._latest('supply_position')} AND material = {_literal(material)} "
            f"AND plant = {_literal(plant)} ORDER BY day"
        )
        return self._frame(sql)

    def suppliers(self) -> pl.DataFrame:
        if "suppliers" not in self._cache:
            self._cache["suppliers"] = self._frame(self._latest("supplier_profile"))
        return self._cache["suppliers"]

    def costs(self) -> dict[str, float]:
        frame = self._frame(f"SELECT * FROM `{self.catalog}`.`raw_ext`.`cost_parameter`")
        return cost_parameters(frame if frame.height else None, PlanningParams())
