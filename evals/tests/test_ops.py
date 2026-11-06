"""The ops tables' rows and the dashboard's card metrics, on small inputs with hand-computed values."""

from __future__ import annotations

import io
import json
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from snm_evals import ops

NOW = datetime(2026, 11, 5, 21, 0)


def run(**fields) -> dict:
    base = {"run_id": "r", "status": "written", "error_place": None, "latency_s": 10.0, "cost_usd": 0.02,
            "prompt_tokens": 100, "cache_read_tokens": 50, "write_attempts": 1, "refused_writes": 0,
            "checks_passed": True, "case_id": "C-1"}  # fmt: skip
    return {**base, **fields}


def test_the_card_metrics_by_hand() -> None:
    runs = [
        run(run_id="a", latency_s=10.0, cost_usd=0.02, write_attempts=1, refused_writes=0, case_id="C-1"),
        run(run_id="b", latency_s=20.0, cost_usd=0.04, write_attempts=2, refused_writes=1, case_id="C-1"),
        run(run_id="c", latency_s=30.0, cost_usd=0.06, write_attempts=1, checks_passed=False, case_id="C-2"),
        run(run_id="d", latency_s=40.0, cost_usd=0.08, write_attempts=0, status="error",
            error_place="model_api",
            checks_passed=None, case_id=None, prompt_tokens=0, cache_read_tokens=0),
    ]  # fmt: skip
    decisions = [{"followed": True}, {"followed": True}, {"followed": False}, {"followed": None}]
    documents = [{"fields_changed": 0}, {"fields_changed": 2}, {"fields_changed": 0}, {"fields_changed": 0}]
    uptime = [{"healthy": True}] * 5 + [{"healthy": False}]
    c = ops.cards(runs, decisions, documents, uptime)
    assert c["acceptance_rate"] == pytest.approx(2 / 3)  # the decision without a recommendation is left out
    assert c["extractor_accuracy"] == pytest.approx(3 / 4)
    assert c["trace_check_pass_rate"] == pytest.approx(2 / 3)  # the extractor-like run has no checks
    assert c["refusal_rate"] == pytest.approx(1 / 4)  # refused writes / write attempts
    assert c["error_rate"] == pytest.approx(1 / 4) and c["errors_by_place"]["model_api"] == 1
    assert c["latency_p50"] == pytest.approx(25.0)  # between 20 and 30
    assert c["latency_p95"] == pytest.approx(38.5)  # 30 + 0.85 * 10
    assert c["cost_p50"] == pytest.approx(0.05) and c["cost_p95"] == pytest.approx(0.077)
    assert c["total_cost"] == pytest.approx(0.20)
    assert c["cost_per_case"] == pytest.approx(0.06)  # 0.12 over C-1 and C-2
    assert c["cache_share"] == pytest.approx(150 / 300)
    assert c["uptime"] == pytest.approx(5 / 6)


def test_percentiles_interpolate_as_spark_does() -> None:
    assert ops.percentile([1, 2, 3, 4], 0.5) == pytest.approx(2.5)
    assert ops.percentile([5], 0.95) == 5
    assert ops.percentile([], 0.5) is None


def trace(
    run_id: str, *, task: str = "evaluate", status: str = "written", refused: bool = False
) -> list[dict]:
    calls = [
        {"id": "t1", "name": "rank_options", "arguments": {}},
        {"id": "t2", "name": "get_case", "arguments": {}},
    ]
    events = [
        {"event": "run_start", "ts": "2026-11-04T15:00:00.000Z", "run_id": run_id, "case_id": "C-1",
         "task": task,
         "model": "anthropic/claude-sonnet-5-5", "prompt": {"name": "planner", "version": "v6"}},
        {"event": "assistant", "turn": 1, "tool_calls": calls},
        {"event": "tool", "id": "t1", "name": "rank_options", "output": "{}"},
        {"event": "tool", "id": "t2", "name": "get_case", "output": "{}"},
    ]  # fmt: skip
    write = {"id": "w1", "name": "propose_recommendation", "arguments": {"options": []}}
    if refused:
        first = {**write, "id": "w0", "arguments": {"options": [], "draft": 1}}
        events += [{"event": "assistant", "turn": 2, "tool_calls": [first]},
                   {"event": "tool", "id": "w0", "name": "propose_recommendation", "output": "UNRANKED",
                    "is_error": True}]  # fmt: skip
    events += [
        {"event": "assistant", "turn": 3, "tool_calls": [write]},
        {"event": "tool", "id": "w1", "name": "propose_recommendation",
         "output": '{"recorded":"recommendation_proposed","state_after":"recommended"}'},
        {"event": "run_end", "status": status, "wall_clock_s": 12.5,
         "usage": {"input": 10, "cache_read": 900, "cache_write": 90, "cost_usd": 0.03}},
    ]  # fmt: skip
    return events


def test_a_run_becomes_a_row_and_its_checks() -> None:
    row = ops.run_row(trace("r1", refused=True), "app", "app-20261104T150000Z", NOW)
    assert (row["run_date"], row["agent_task"], row["case_state_after"]) == (
        date(2026, 11, 4),
        "planner evaluate",
        "recommended",
    )
    assert (row["prompt_tokens"], row["cache_read_tokens"], row["write_attempts"], row["refused_writes"]) == (
        1000,
        900,
        2,
        1,
    )
    checks = ops.check_rows(row, trace("r1", refused=True), NOW)
    assert len(checks) == 8 and row["checks_passed"] is True
    assert {c["check_id"] for c in checks} == {"1", "2", "3", "4", "5", "6", "7", "9"}
    assert ops.error_place({"status": "error", "error": "MCP tool server closed"}) == "tool_server"
    assert ops.error_place({"status": "error", "error": "529 overloaded"}) == "model_api"
    assert ops.error_place({"status": "written"}) is None


def test_decisions_and_documents_from_case_events() -> None:
    at = datetime(2026, 11, 5, 18, 0)
    events = [
        {"case_id": "C-1", "seq": 9, "event_type": "decision_recorded", "created_at": at,
         "payload": json.dumps({"choice": "P-2", "recommended": "P-1", "followed_recommendation": False,
                                "rationale_text": "Earlier arrival."})},
        {"case_id": "C-2", "seq": 6, "event_type": "proposal_recorded", "created_at": at,
         "payload": json.dumps({"source": {"extraction_seq": 5, "method": "claude-haiku-5-5",
                                           "changed_fields": ["1.unit_price"]}})},
        {"case_id": "C-3", "seq": 4, "event_type": "proposal_recorded", "created_at": at, "payload": "{}"},
    ]  # fmt: skip
    [decision] = ops.decision_rows(events, NOW)
    assert (decision["followed"], decision["chosen"], decision["reason"]) == (
        False,
        "P-2",
        "Earlier arrival.",
    )
    [document] = ops.document_rows(events, NOW)  # a proposal typed in by purchasing is no document
    assert (document["fields_changed"], document["changed_fields"], document["method"]) == (
        1,
        "1.unit_price",
        "claude-haiku-5-5",
    )


def test_the_demo_set_covers_every_table_and_is_stable() -> None:
    tables = ops.demo_tables(date(2026, 11, 5), NOW)
    assert set(tables) == {"agent_runs", "trace_checks", "decisions", "documents", "uptime_checks"}
    assert all(row["source"] == "demo" for rows in tables.values() for row in rows)
    assert tables == ops.demo_tables(date(2026, 11, 5), NOW)
    runs = tables["agent_runs"]
    assert {r["agent_task"] for r in runs} == set(ops.DEMO_TASKS)
    assert min(r["run_date"] for r in runs) == date(2026, 10, 9) and max(r["run_date"] for r in runs) == date(
        2026, 11, 5
    )
    planner = [r for r in runs if r["agent"] == "planner"]
    assert len(tables["trace_checks"]) == 8 * len(planner)
    failed = {c["run_id"] for c in tables["trace_checks"] if c["outcome"] == "fail"}
    assert failed == {r["run_id"] for r in planner if not r["checks_passed"]}
    c = ops.cards(runs, tables["decisions"], tables["documents"], tables["uptime_checks"])
    assert 0.7 < c["acceptance_rate"] < 0.95 and 0.6 < c["extractor_accuracy"] < 0.9 and 0.9 < c["uptime"] < 1


class FakeStore:
    def __init__(self) -> None:
        self.tables: dict[str, list[dict]] = {t: [] for t in ops.KEYS}

    def keys(self, table, columns):
        return {tuple(r[c] for c in columns) for r in self.tables[table]}

    def append(self, table, rows):
        self.tables[table] += rows

    def case_events(self, event_types):
        return []

    def replace_demo(self, table, rows):
        self.tables.setdefault(table, [])
        self.tables[table] = [r for r in self.tables[table] if r["source"] != "demo"] + rows


class FakeFiles:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files

    def list_directory_contents(self, path):
        prefix = path.rstrip("/") + "/"
        names = sorted({p[len(prefix) :].split("/")[0] for p in self.files if p.startswith(prefix)})
        if not names:
            raise FileNotFoundError(path)
        return [SimpleNamespace(name=n, path=prefix + n) for n in names]

    def download(self, path):
        return SimpleNamespace(contents=io.BytesIO(self.files[path]))


def test_the_daily_job_adds_each_row_once() -> None:
    def jsonl(events):
        return "".join(json.dumps(e) + "\n" for e in events).encode()

    volume = "/Volumes/snm/cases/runs"
    files = FakeFiles({
        f"{volume}/app-20261104T150000Z/agents/r1/trace.jsonl": jsonl(trace("r1")),
        f"{volume}/app-20261105T150000Z/agents/r1/trace.jsonl": jsonl(trace("r1")),
        f"{volume}/app-20261105T150000Z/agents/r2/trace.jsonl": jsonl(trace("r2", refused=True)),
    })  # fmt: skip
    store = FakeStore()
    judged = []

    def judge(events):
        judged.append(events[0]["run_id"])
        return "sound", "fine"

    first = ops.daily(store, files, volume=volume, judge=judge, judge_sample=1, now=NOW, log=lambda _: None)
    assert first["agent_runs"] == 2 and first["trace_checks"] == 2 * 8 + 1 and len(judged) == 1
    assert {r["batch"] for r in store.tables["agent_runs"] if r["run_id"] == "r1"} == {"app-20261104T150000Z"}
    second = ops.daily(store, files, volume=volume, now=NOW, log=lambda _: None)
    assert second == {"agent_runs": 0, "trace_checks": 0, "decisions": 0, "documents": 0}
    # The demo set is written again, never twice.
    for _ in range(2):
        ops.replace_demo(store, date(2026, 11, 5), NOW, log=lambda _: None)
    demo = [r for r in store.tables["agent_runs"] if r["source"] == "demo"]
    assert len(demo) == len(ops.demo_tables(date(2026, 11, 5), NOW)["agent_runs"])
    assert len([r for r in store.tables["agent_runs"] if r["source"] == "app"]) == 2


def test_sql_literals() -> None:
    assert ops.sql_literal("it's") == "'it\\'s'"
    assert ops.sql_literal(None) == "NULL" and ops.sql_literal(True) == "TRUE"
    assert ops.sql_literal(date(2026, 11, 5)) == "DATE '2026-11-05'"
    assert ops.sql_literal(datetime(2026, 11, 5, 1, 2, 3)) == "TIMESTAMP '2026-11-05 01:02:03'"
