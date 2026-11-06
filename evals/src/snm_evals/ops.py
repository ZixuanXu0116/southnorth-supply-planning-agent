"""The workflow's daily numbers: agent runs, trace checks, decisions, and documents, as rows of ``snm.ops``.

    uv run snm-evals ops-daily [--evals] [--demo] [--judge-sample N]

The same code runs in the daily Databricks job (``deploy/databricks/jobs/ops_daily.py``, through Spark) and
on a machine with the repository (through the SQL warehouse):

- the app's agent runs, from the runs volume (``app-<start>/agents/<run>/trace.jsonl``), each once;
- with ``--evals``, the workflow's evaluation batches under ``runs/evals/``, where they were run;
- every new planner run's code checks (``trace_checks``), and with ``--judge-sample N`` the LLM judge (rubric
  v2) on N of them, where the harness runs (the Anthropic key stays in the TypeScript process, so the
  Databricks job checks in code only);
- the case store's decisions and confirmed supplier documents;
- with ``--demo``, the demo set of every table again (``demo_tables``), which the dashboard shows.

Only rows not yet in a table are added, so a second run adds nothing. This module computes rows and metrics
with the standard library only; the writers below bring their own client.
"""

from __future__ import annotations

import json
import random
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from .trace_checks import CHECKS, calls_of, check_trace
from .workflow_mlflow import case_state_after

Row = dict[str, Any]
SCHEMA = "ops"
WRITES = frozenset(
    {
        "draft_supplier_request",
        "prepare_transfer",
        "propose_recommendation",
        "draft_handoff",
        "add_note",
        "record_extraction",
    }
)
SYNTHETIC_MARKER = "PLANTED"  # a folder of planted copies, not runs: never counted


# --------------------------------------------------------------------------------------------- rows


def _ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC).replace(tzinfo=None)


def error_place(end: dict[str, Any]) -> str | None:
    """Where a run that ended in error failed: the model API, a tool server, or the harness itself."""
    if end.get("status") != "error":
        return None
    text = str(end.get("error") or "").lower()
    if any(word in text for word in ("mcp", "tool server", "case server", "toolserver", "econnrefused")):
        return "tool_server"
    if any(
        word in text for word in ("anthropic", "overloaded", "rate limit", "429", "529", "api error", "model")
    ):
        return "model_api"
    return "harness"


def run_row(events: list[dict[str, Any]], source: str, batch: str | None, now: datetime) -> Row:
    """One ``ops.agent_runs`` row from a run's trace events (``checks_passed`` is set by the caller)."""
    start = next((e for e in events if e.get("event") == "run_start"), {})
    end = next((e for e in reversed(events) if e.get("event") == "run_end"), {})
    prompt = start.get("prompt") if isinstance(start.get("prompt"), dict) else {}
    agent = str(prompt.get("name") or start.get("agent") or "unknown")
    task = str(start.get("task") or ("extract" if agent == "extractor" else "-"))
    usage = end.get("usage") or {}
    calls = calls_of(events)
    started = _ts(start.get("ts")) or now
    return {
        "run_id": str(start.get("run_id") or end.get("run_id")),
        "source": source,
        "batch": batch,
        "run_date": started.date(),
        "started_at": started,
        "agent": agent,
        "task": task,
        "agent_task": f"{agent} {task}",
        "case_id": start.get("case_id") or end.get("case_id"),
        "status": str(end.get("status") or "unfinished"),
        "error_place": error_place(end),
        "latency_s": float(end["wall_clock_s"]) if end.get("wall_clock_s") is not None else None,
        "cost_usd": float(usage.get("cost_usd") or 0.0),
        "prompt_tokens": int(usage.get("input") or 0)
        + int(usage.get("cache_read") or 0)
        + int(usage.get("cache_write") or 0),
        "cache_read_tokens": int(usage.get("cache_read") or 0),
        "write_attempts": sum(1 for c in calls if c.name in WRITES),
        "refused_writes": sum(1 for c in calls if c.name in WRITES and c.ok is False),
        "checks_passed": None,
        "case_state_after": None if case_state_after(events) == "-" else case_state_after(events),
        "model": start.get("model") or end.get("model"),
        "prompt_version": prompt.get("version"),
        "loaded_at": now,
    }


def check_rows(run: Row, events: list[dict[str, Any]], now: datetime) -> list[Row]:
    """The code checks of a planner run, one row each; sets the run's ``checks_passed``."""
    report = check_trace(events, run["run_id"])
    run["checks_passed"] = report.sound
    return [
        {
            "run_id": run["run_id"],
            "source": run["source"],
            "run_date": run["run_date"],
            "check_id": str(r.check),
            "check_name": CHECKS[r.check],
            "outcome": r.status,
            "detail": r.detail or None,
            "checked_at": now,
        }
        for r in report.results
    ]


def judge_row(run: Row, verdict: str, summary: str, now: datetime) -> Row:
    return {
        "run_id": run["run_id"],
        "source": run["source"],
        "run_date": run["run_date"],
        "check_id": "judge_v2",
        "check_name": "LLM judge (rubric v2)",
        "outcome": verdict if verdict in ("sound", "unsound", "unclear") else "error",
        "detail": summary[:2000] or None,
        "checked_at": now,
    }


def _payload(event: Row) -> dict[str, Any]:
    payload = event.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return {}
    return payload if isinstance(payload, dict) else {}


def decision_rows(case_events: Iterable[Row], now: datetime) -> list[Row]:
    """One ``ops.decisions`` row per ``decision_recorded`` event."""
    rows = []
    for event in case_events:
        if event.get("event_type") != "decision_recorded":
            continue
        payload, at = _payload(event), event["created_at"]
        at = _ts(at) if isinstance(at, str) else at
        rows.append(
            {
                "case_id": event["case_id"],
                "seq": int(event["seq"]),
                "source": "app",
                "decided_at": at,
                "decision_date": at.date(),
                "recommended": payload.get("recommended"),
                "chosen": str(payload.get("choice")),
                "followed": payload.get("followed_recommendation"),
                "reason": payload.get("rationale_text") or None,
                "loaded_at": now,
            }
        )
    return rows


def document_rows(case_events: Iterable[Row], now: datetime) -> list[Row]:
    """One ``ops.documents`` row per supplier document purchasing confirmed from an extractor's draft."""
    rows = []
    for event in case_events:
        if event.get("event_type") != "proposal_recorded":
            continue
        source = _payload(event).get("source") or {}
        if not isinstance(source, dict) or source.get("extraction_seq") is None:
            continue
        at = event["created_at"]
        at = _ts(at) if isinstance(at, str) else at
        changed = [str(f) for f in source.get("changed_fields") or []]
        rows.append(
            {
                "case_id": event["case_id"],
                "seq": int(event["seq"]),
                "source": "app",
                "confirmed_at": at,
                "confirm_date": at.date(),
                "method": source.get("method"),
                "fields_changed": len(changed),
                "changed_fields": ", ".join(changed) or None,
                "loaded_at": now,
            }
        )
    return rows


# The demo set: what the dashboard shows (decided 2026-11-05: the dashboard demonstrates what it measures, on
# generated rows, not the few real ones). Shapes follow the measured runs: latency and cost by task near the
# evaluation batches' medians, about two thirds of prompt tokens read from the cache.
DEMO_TASKS = {  # agent task -> (share, median latency s, median cost USD, median prompt tokens, state after)
    "planner prepare": (0.30, 6.0, 0.014, 5200, "prepared"),
    "planner evaluate": (0.40, 12.0, 0.030, 7400, "recommended"),
    "planner handoff": (0.10, 8.0, 0.018, 5600, "decided"),
    "extractor extract": (0.20, 5.0, 0.012, 7300, None),
}
DEMO_DAYS = 28


def _at(rng: random.Random, day: date) -> datetime:
    return datetime.combine(day, datetime.min.time()) + timedelta(
        hours=rng.randrange(13, 23), minutes=rng.randrange(60)
    )


def _demo_run(rng: random.Random, n: int, day: date, now: datetime) -> tuple[Row, list[Row]]:
    names = list(DEMO_TASKS)
    agent_task = rng.choices(names, weights=[DEMO_TASKS[k][0] for k in names])[0]
    _, latency, cost, tokens, state = DEMO_TASKS[agent_task]
    agent, task = agent_task.split()
    draw = rng.random()
    if agent == "extractor":
        status = "extracted" if draw < 0.97 else "error"
    else:
        status = (
            "written"
            if draw < 0.955
            else "refused"
            if draw < 0.975
            else "error"
            if draw < 0.99
            else "budget_exhausted"
        )
    place = (
        rng.choices(["model_api", "tool_server", "harness"], weights=[6, 3, 1])[0]
        if status == "error"
        else None
    )
    retried = status == "written" and rng.random() < 0.05
    attempts = 0 if status in ("error", "budget_exhausted") else 2 if status == "refused" or retried else 1
    refused = 2 if status == "refused" else 1 if retried else 0
    prompt = int(tokens * rng.lognormvariate(0, 0.25))
    started = _at(rng, day)
    run = {
        "run_id": f"demo-{n:05d}",
        "source": "demo",
        "batch": f"demo-{day.isoformat()}",
        "run_date": day,
        "started_at": started,
        "agent": agent,
        "task": task,
        "agent_task": agent_task,
        "case_id": f"DEMO-C-{rng.randrange(1, 121):04d}" if agent == "planner" else None,
        "status": status,
        "error_place": place,
        "latency_s": round(latency * rng.lognormvariate(0, 0.4) * (1.6 if retried else 1.0), 1),
        "cost_usd": round(cost * rng.lognormvariate(0, 0.35) * (1.5 if retried else 1.0), 6),
        "prompt_tokens": prompt,
        "cache_read_tokens": int(prompt * rng.uniform(0.55, 0.78)),
        "write_attempts": attempts,
        "refused_writes": refused,
        "checks_passed": None,
        "case_state_after": state if status in ("written", "extracted") else None,
        "model": "anthropic/claude-haiku-5-5" if agent == "extractor" else "anthropic/claude-sonnet-5-5",
        "prompt_version": "v1" if agent == "extractor" else "v6",
        "loaded_at": now,
    }
    checks: list[Row] = []
    if agent == "planner":
        failing = None
        if status != "written":
            failing = 9 if status in ("error", "budget_exhausted") else 4
        elif rng.random() < 0.03:
            failing = rng.choice([3, 5, 7])
        run["checks_passed"] = failing is None
        for number, name in CHECKS.items():
            applies = not (
                (number in (2, 3, 7) and task != "evaluate")
                or (number == 6 and task != "prepare")
                or (number == 5 and refused == 0)
            )
            outcome = "fail" if number == failing else "pass" if applies else "na"
            checks.append(
                {
                    "run_id": run["run_id"],
                    "source": "demo",
                    "run_date": day,
                    "check_id": str(number),
                    "check_name": name,
                    "outcome": outcome,
                    "detail": None,
                    "checked_at": now,
                }
            )
    return run, checks


def demo_tables(today: date, now: datetime, seed: int = 20261105) -> dict[str, list[Row]]:
    """The demo rows of every ``snm.ops`` table over the last four weeks, all marked ``demo``: agent runs and
    their checks, decisions (about four in five follow the recommendation), confirmed documents (about four in
    five with no field changed), and four demo days of uptime checks."""
    rng = random.Random(seed)
    days = [today - timedelta(days=d) for d in range(DEMO_DAYS - 1, -1, -1)]
    runs, checks = [], []
    for day in days:
        for _ in range(rng.randrange(10, 26)):
            run, run_checks = _demo_run(rng, len(runs) + 1, day, now)
            runs.append(run)
            checks += run_checks
    reasons = [
        "Earlier arrival needed for the line.",
        "Supplier relationship.",
        "Cheaper split is fine here.",
    ]
    decisions = []
    for n in range(72):
        day, followed = rng.choice(days), rng.random() < 0.82
        at = _at(rng, day)
        decisions.append(
            {
                "case_id": f"DEMO-C-{rng.randrange(1, 121):04d}",
                "seq": 100 + n,
                "source": "demo",
                "decided_at": at,
                "decision_date": day,
                "recommended": "P-1",
                "chosen": "P-1" if followed else rng.choice(["P-2", "expedited_new_order"]),
                "followed": followed,
                "reason": None if followed else rng.choice(reasons),
                "loaded_at": now,
            }
        )
    fields = ["1.unit_price", "1.arrival_date", "1.quantity", "2.surcharge_pct", "1.freight_amount"]
    documents = []
    for n in range(48):
        day = rng.choice(days)
        changed = [] if rng.random() < 0.78 else rng.sample(fields, rng.choice([1, 1, 2]))
        documents.append(
            {
                "case_id": f"DEMO-C-{rng.randrange(1, 121):04d}",
                "seq": 200 + n,
                "source": "demo",
                "confirmed_at": _at(rng, day),
                "confirm_date": day,
                "method": rng.choice(["claude-haiku-5-5", "claude-haiku-5-5", "rules"]),
                "fields_changed": len(changed),
                "changed_fields": ", ".join(changed) or None,
                "loaded_at": now,
            }
        )
    uptime = []
    for day in (
        days[7],
        days[14],
        days[21],
        days[26],
    ):  # demo days: checks every 10 minutes, 13:00 to 23:00 UTC
        stale = rng.randrange(20, 50)
        for k in range(61):
            at = datetime.combine(day, datetime.min.time()) + timedelta(hours=13, minutes=10 * k)
            healthy = k > 0 and k != stale
            action = "started" if k == 0 else "restarted" if k == stale else "none"
            uptime.append(
                {
                    "checked_at": at,
                    "check_date": day,
                    "source": "demo",
                    "app_state": "RUNNING" if k > 0 else "UNAVAILABLE",
                    "compute_state": "ACTIVE" if k > 0 else "STOPPED",
                    "heartbeat_age_s": None
                    if k == 0
                    else 400.0
                    if k == stale
                    else float(rng.randrange(5, 60)),
                    "healthy": healthy,
                    "action": action,
                    "detail": "demo",
                }
            )
    return {
        "agent_runs": runs,
        "trace_checks": checks,
        "decisions": decisions,
        "documents": documents,
        "uptime_checks": uptime,
    }


def replace_demo(
    store: Store, today: date, now: datetime, log: Callable[[str], None] = print
) -> dict[str, int]:
    """Drop every table's demo rows and write the demo set again (relative to ``today``)."""
    counts = {}
    for table, rows in demo_tables(today, now).items():
        store.replace_demo(table, rows)
        counts[table] = len(rows)
        log(f"ops.{table}: {len(rows)} demo rows")
    return counts


# --------------------------------------------------------------------------------------------- metrics
# The card definitions of the dashboard; ``deploy/databricks/ops_dashboard.py`` computes the same in SQL and
# ``snm-evals ops-recount`` compares the two on the real tables.


def share(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def percentile(values: Iterable[float], q: float) -> float | None:
    """Linear interpolation between the closest ranks, as Spark SQL's ``percentile``."""
    data = sorted(v for v in values if v is not None)
    if not data:
        return None
    position = (len(data) - 1) * q
    low = int(position)
    high = min(low + 1, len(data) - 1)
    return data[low] + (data[high] - data[low]) * (position - low)


def cards(runs: list[Row], decisions: list[Row], documents: list[Row], uptime: list[Row]) -> dict[str, Any]:
    checked = [r for r in runs if r.get("checks_passed") is not None]
    compared = [d for d in decisions if d.get("followed") is not None]
    with_case = [r for r in runs if r.get("case_id")]
    latencies = [r["latency_s"] for r in runs if r.get("latency_s") is not None]
    costs = [r["cost_usd"] for r in runs]
    errors = [r for r in runs if r["status"] == "error"]
    return {
        "runs": len(runs),
        "acceptance_rate": share(sum(1 for d in compared if d["followed"]), len(compared)),
        "extractor_accuracy": share(sum(1 for d in documents if d["fields_changed"] == 0), len(documents)),
        "trace_check_pass_rate": share(sum(1 for r in checked if r["checks_passed"]), len(checked)),
        "refusal_rate": share(sum(r["refused_writes"] for r in runs), sum(r["write_attempts"] for r in runs)),
        "error_rate": share(len(errors), len(runs)),
        "errors_by_place": {
            p: sum(1 for r in errors if r["error_place"] == p)
            for p in ("model_api", "tool_server", "harness")
        },
        "latency_p50": percentile(latencies, 0.5),
        "latency_p95": percentile(latencies, 0.95),
        "cost_p50": percentile(costs, 0.5),
        "cost_p95": percentile(costs, 0.95),
        "total_cost": sum(costs),
        "cost_per_case": share(sum(r["cost_usd"] for r in with_case), len({r["case_id"] for r in with_case})),
        "cache_share": share(
            sum(r["cache_read_tokens"] for r in runs), sum(r["prompt_tokens"] for r in runs)
        ),
        "uptime": share(sum(1 for u in uptime if u["healthy"]), len(uptime)),
    }


# --------------------------------------------------------------------------------------------- sources


class Files(Protocol):
    def list_directory_contents(self, path: str) -> Iterable[Any]: ...
    def download(self, path: str) -> Any: ...


class LocalFiles:
    """The Files API's two calls over a mounted path (``/Volumes/...`` inside a Databricks job)."""

    class _Entry:
        def __init__(self, path: Path) -> None:
            self.name, self.path = path.name, str(path)

    class _Download:
        def __init__(self, path: Path) -> None:
            import io

            self.contents = io.BytesIO(path.read_bytes())

    def list_directory_contents(self, path: str) -> list[Any]:
        return [self._Entry(p) for p in sorted(Path(path).iterdir())]

    def download(self, path: str) -> Any:
        return self._Download(Path(path))


def read_jsonl(text: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def app_runs(files: Files, volume: str) -> list[tuple[str, list[dict[str, Any]]]]:
    """The app's agent runs in the runs volume, each once, with the app start that first holds it."""
    from .workflow_mlflow import app_runs_in_volume

    runs = []
    for _, (start, path) in sorted(app_runs_in_volume(files, volume).items()):
        try:
            runs.append((start, read_jsonl(files.download(path).contents.read().decode("utf-8"))))
        except Exception:
            continue
    return runs


def is_synthetic(batch_dir: Path) -> bool:
    return (batch_dir / SYNTHETIC_MARKER).exists()


def eval_runs(runs_root: Path) -> list[tuple[str, list[dict[str, Any]]]]:
    """The workflow's evaluation runs: batches without the data agent's scores, planted copies left out."""
    runs = []
    for batch in sorted(p for p in runs_root.iterdir() if p.is_dir()):
        if (batch / "scores.json").exists() or is_synthetic(batch):
            continue
        for trace in sorted(batch.rglob("trace.jsonl")):
            runs.append((batch.name, read_jsonl(trace.read_text(encoding="utf-8"))))
    return runs


def eval_judgments(runs_root: Path) -> dict[str, tuple[str, str]]:
    """The judge's verdicts (rubric v2) already made on real evaluation runs, by run id: the repeat test's
    first judgment of each sound run."""
    found: dict[str, tuple[str, str]] = {}
    for path in sorted(runs_root.glob("trace-judge-v2-*/judgments*.jsonl")):
        for row in read_jsonl(path.read_text(encoding="utf-8")):
            if row.get("kind") not in ("sound_1",) or not row.get("source"):
                continue
            trace = runs_root / row["source"]
            if trace.is_file():
                run_id = read_jsonl(trace.read_text(encoding="utf-8"))[0].get("run_id")
                found.setdefault(str(run_id), (row["verdict"], row.get("summary") or ""))
    return found


# --------------------------------------------------------------------------------------------- the job


class Store(Protocol):
    """Where the rows go: the SQL warehouse (``SqlStore``) or Spark inside a job (``SparkStore``)."""

    def keys(self, table: str, columns: tuple[str, ...]) -> set[tuple]: ...
    def append(self, table: str, rows: list[Row]) -> None: ...
    def case_events(self, event_types: tuple[str, ...]) -> list[Row]: ...
    def replace_demo(self, table: str, rows: list[Row]) -> None: ...


KEYS = {
    "agent_runs": ("run_id",),
    "trace_checks": ("run_id", "check_id"),
    "decisions": ("case_id", "seq"),
    "documents": ("case_id", "seq"),
}


def _new(rows: list[Row], existing: set[tuple], key: tuple[str, ...]) -> list[Row]:
    out, seen = [], set(existing)
    for row in rows:
        k = tuple(row[c] for c in key)
        if k not in seen:
            seen.add(k)
            out.append(row)
    return out


def daily(
    store: Store,
    files: Files,
    *,
    volume: str = "/Volumes/snm/cases/runs",
    evals_root: Path | None = None,
    judge: Callable[[list[dict[str, Any]]], tuple[str, str]] | None = None,
    judge_sample: int = 0,
    now: datetime | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, int]:
    """Add the new rows to every ``snm.ops`` table; returns how many each table got."""
    now = now or datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    sources = [("app", start, events) for start, events in app_runs(files, volume)]
    if evals_root is not None:
        sources += [("eval", batch, events) for batch, events in eval_runs(evals_root)]
    done = store.keys("agent_runs", KEYS["agent_runs"])
    runs, checks, fresh = [], [], []
    for source, batch, events in sources:
        row = run_row(events, source, batch, now)
        if (row["run_id"],) in done or any(r["run_id"] == row["run_id"] for r in runs):
            continue
        if row["agent"] == "planner":
            checks += check_rows(row, events, now)
            fresh.append((row, events))
        runs.append(row)
    if evals_root is not None:
        verdicts = eval_judgments(evals_root)
        checks += [judge_row(r, *verdicts[r["run_id"]], now) for r in runs if r["run_id"] in verdicts]
    if judge is not None and judge_sample > 0:
        unjudged = [(r, e) for r, e in fresh if r["source"] == "app"]
        for row, events in random.Random(now.toordinal()).sample(unjudged, min(judge_sample, len(unjudged))):
            checks.append(judge_row(row, *judge(events), now))
    events = store.case_events(("decision_recorded", "proposal_recorded"))
    decisions, documents = decision_rows(events, now), document_rows(events, now)
    counts = {}
    for table, rows in (
        ("agent_runs", runs),
        ("trace_checks", checks),
        ("decisions", decisions),
        ("documents", documents),
    ):
        new = _new(rows, store.keys(table, KEYS[table]) if table != "agent_runs" else done, KEYS[table])
        if new:
            store.append(table, new)
        counts[table] = len(new)
        log(f"ops.{table}: {len(new)} new rows")
    return counts


# --------------------------------------------------------------------------------------------- stores


def sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int | float):
        return repr(float(value)) if isinstance(value, float) else str(value)
    if isinstance(value, datetime):
        return f"TIMESTAMP '{value.isoformat(sep=' ')}'"
    if isinstance(value, date):
        return f"DATE '{value.isoformat()}'"
    text = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{text}'"


class SqlStore:
    """The SQL warehouse, through the Databricks SQL connector (a machine with the repository)."""

    def __init__(self, connection: Any, catalog: str) -> None:
        self.connection, self.catalog = connection, catalog

    def _name(self, table: str, schema: str = SCHEMA) -> str:
        return f"`{self.catalog}`.`{schema}`.`{table}`"

    def _rows(self, sql: str) -> list[Row]:
        with self.connection.cursor() as cursor:
            cursor.execute(sql)
            names = [d[0] for d in cursor.description]
            return [dict(zip(names, values, strict=True)) for values in cursor.fetchall()]

    def keys(self, table: str, columns: tuple[str, ...]) -> set[tuple]:
        cols = ", ".join(f"`{c}`" for c in columns)
        return {tuple(r[c] for c in columns) for r in self._rows(f"SELECT {cols} FROM {self._name(table)}")}

    def append(self, table: str, rows: list[Row], chunk: int = 200) -> None:
        columns = list(rows[0])
        names = ", ".join(f"`{c}`" for c in columns)
        with self.connection.cursor() as cursor:
            for offset in range(0, len(rows), chunk):
                values = ",\n".join(
                    "(" + ", ".join(sql_literal(row[c]) for c in columns) + ")"
                    for row in rows[offset : offset + chunk]
                )
                cursor.execute(f"INSERT INTO {self._name(table)} ({names}) VALUES\n{values}")

    def replace_demo(self, table: str, rows: list[Row]) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(f"DELETE FROM {self._name(table)} WHERE source = 'demo'")
        if rows:
            self.append(table, rows)

    def case_events(self, event_types: tuple[str, ...]) -> list[Row]:
        kinds = ", ".join(sql_literal(k) for k in event_types)
        return self._rows(
            f"SELECT case_id, seq, event_type, payload, created_at FROM {self._name('case_event', 'cases')} "
            f"WHERE event_type IN ({kinds})"
        )


class SparkStore:
    """Spark inside a Databricks job."""

    def __init__(self, spark: Any, catalog: str) -> None:
        self.spark, self.catalog = spark, catalog

    def _name(self, table: str, schema: str = SCHEMA) -> str:
        return f"`{self.catalog}`.`{schema}`.`{table}`"

    def keys(self, table: str, columns: tuple[str, ...]) -> set[tuple]:
        frame = self.spark.table(self._name(table)).select(*columns)
        return {tuple(row) for row in frame.collect()}

    def append(self, table: str, rows: list[Row]) -> None:
        target = self.spark.table(self._name(table))
        frame = self.spark.createDataFrame(
            [tuple(r.get(f.name) for f in target.schema) for r in rows], target.schema
        )
        frame.write.mode("append").saveAsTable(self._name(table))

    def replace_demo(self, table: str, rows: list[Row]) -> None:
        self.spark.sql(f"DELETE FROM {self._name(table)} WHERE source = 'demo'")
        if rows:
            self.append(table, rows)

    def case_events(self, event_types: tuple[str, ...]) -> list[Row]:
        frame = self.spark.table(self._name("case_event", "cases"))
        frame = frame.where(frame.event_type.isin(list(event_types))).select(
            "case_id", "seq", "event_type", "payload", "created_at"
        )
        return [row.asDict() for row in frame.collect()]
