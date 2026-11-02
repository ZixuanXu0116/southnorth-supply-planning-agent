"""The lakehouse planning reads: cached until a new engine run is published (a long-lived case server)."""

from __future__ import annotations

from snm_cases.planning import FRESHNESS_SECONDS, DatabricksPlanningData
from snm_tools.backend import QueryResult


class FakeWarehouse:
    def __init__(self):
        self.run = ["2026-09-21", "1.1.0", "efd8", "120"]
        self.queries: list[str] = []

    def execute(self, sql):
        self.queries.append(sql)
        if "GROUP BY as_of_date" in sql:
            return QueryResult(
                columns=["as_of_date", "engine", "digest", "n"], rows=[list(self.run)], elapsed_s=0.0
            )
        if "supply_position" in sql:
            return QueryResult(
                columns=["day", "projected_qty"], rows=[["2026-09-21", float(self.run[3])]], elapsed_s=0.0
            )
        return QueryResult(columns=["x"], rows=[[1]], elapsed_s=0.0)


def test_reads_are_cached_until_a_new_run_is_published():
    warehouse, now = FakeWarehouse(), [0.0]
    planning = DatabricksPlanningData(warehouse, "snm", clock=lambda: now[0])
    first = planning.position("40000410", "US10")
    planning.position("40000410", "US10")
    reads = [q for q in warehouse.queries if "supply_position" in q]
    checks = [q for q in warehouse.queries if "GROUP BY" in q]
    assert len(reads) == 1 and len(checks) == 1  # one identity check within the minute
    warehouse.run = ["2026-09-22", "1.1.0", "efd8", "121"]  # a new run is published
    now[0] += FRESHNESS_SECONDS - 1
    assert planning.position("40000410", "US10").equals(first)  # not checked yet: still the old run
    now[0] += 2
    newer = planning.position("40000410", "US10")
    assert newer.get_column("projected_qty")[0] == 121.0
    assert planning.version() == "2026-09-22/1.1.0/efd8/121"
    assert len([q for q in warehouse.queries if "supply_position" in q]) == 2
