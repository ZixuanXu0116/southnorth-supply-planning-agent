"""The workflow's operations dashboard (Databricks AI/BI), as the serialized dashboard ``ops_deploy.py`` publishes.

It shows the demo rows only (``source = 'demo'``, decided 2026-11-05): the dashboard demonstrates what is
measured, while the real rows stay in the tables. Cards on top follow the filters (date range, agent and task,
case); charts over time below; latency and cost distributions; the cost of each case. Every card is computed in its dataset's SQL from ``snm.ops``,
with the definitions of ``snm_evals.ops.cards`` (``snm-evals ops-recount`` compares the two). The filters are
dashboard parameters, so the percentiles are computed over exactly the rows the filters keep.
"""

from __future__ import annotations

import json
from typing import Any

CATALOG = "snm"
DEFAULT_FROM, DEFAULT_TO = "2026-10-01", "2026-12-31"
PARAMS = {  # keyword -> (type, default)
    "from_date": ("DATE", DEFAULT_FROM),
    "to_date": ("DATE", DEFAULT_TO),
    "agent_task": ("STRING", "all"),
    "case_id": ("STRING", "all"),
}
SOURCE = "source = 'demo'"
CASE = "(:case_id = 'all' OR case_id = :case_id)"
TASK = "(:agent_task = 'all' OR agent_task = :agent_task)"
PASS = "CASE WHEN checks_passed IS NULL THEN NULL WHEN checks_passed THEN 1D ELSE 0D END"
FOLLOWED = "CASE WHEN followed IS NULL THEN NULL WHEN followed THEN 1D ELSE 0D END"


def _t(name: str) -> str:
    return f"{CATALOG}.ops.{name}"


RUNS = f"FROM {_t('agent_runs')} WHERE run_date BETWEEN :from_date AND :to_date AND {SOURCE} AND {TASK} AND {CASE}"
DECISIONS = (
    f"FROM {_t('decisions')} WHERE decision_date BETWEEN :from_date AND :to_date AND {SOURCE} AND {CASE}"
)
DOCUMENTS = (
    f"FROM {_t('documents')} WHERE confirm_date BETWEEN :from_date AND :to_date AND {SOURCE} AND {CASE}"
)
UPTIME = f"FROM {_t('uptime_checks')} WHERE check_date BETWEEN :from_date AND :to_date AND {SOURCE}"

DATASETS: dict[str, tuple[str, str]] = {  # name -> (display name, SQL)
    "cards_runs": (
        "Cards: runs",
        f"""SELECT COUNT(*) AS runs,
  AVG({PASS}) AS trace_check_pass_rate,
  SUM(refused_writes) / NULLIF(SUM(write_attempts), 0) AS refusal_rate,
  AVG(CASE WHEN status = 'error' THEN 1D ELSE 0D END) AS error_rate,
  percentile(latency_s, 0.5) AS latency_p50,
  percentile(latency_s, 0.95) AS latency_p95,
  percentile(cost_usd, 0.5) AS cost_p50,
  percentile(cost_usd, 0.95) AS cost_p95,
  COALESCE(SUM(cost_usd), 0) AS total_cost,
  SUM(CASE WHEN case_id IS NOT NULL THEN cost_usd END) / NULLIF(COUNT(DISTINCT case_id), 0) AS cost_per_case
{RUNS}""",
    ),
    "cards_decisions": (
        "Cards: decisions",
        f"""SELECT COUNT(*) AS decisions, AVG({FOLLOWED}) AS acceptance_rate
{DECISIONS}""",
    ),
    "cards_documents": (
        "Cards: documents",
        f"""SELECT COUNT(*) AS documents,
  AVG(CASE WHEN fields_changed = 0 THEN 1D ELSE 0D END) AS extractor_accuracy
{DOCUMENTS}""",
    ),
    "cards_uptime": (
        "Cards: uptime",
        f"""SELECT COUNT(*) AS checks, AVG(CASE WHEN healthy THEN 1D ELSE 0D END) AS uptime
{UPTIME}""",
    ),
    "trend_rates": (
        "Rates by day",
        f"""WITH r AS (
  SELECT run_date AS day, AVG({PASS}) AS pass_rate,
    SUM(refused_writes) / NULLIF(SUM(write_attempts), 0) AS refusal_rate,
    AVG(CASE WHEN status = 'error' THEN 1D ELSE 0D END) AS error_rate
  {RUNS} GROUP BY run_date),
d AS (SELECT decision_date AS day, AVG({FOLLOWED}) AS acceptance_rate {DECISIONS} GROUP BY decision_date),
c AS (SELECT confirm_date AS day, AVG(CASE WHEN fields_changed = 0 THEN 1D ELSE 0D END) AS accuracy
  {DOCUMENTS} GROUP BY confirm_date)
SELECT day, 'Trace-check pass rate' AS series, pass_rate AS value FROM r WHERE pass_rate IS NOT NULL
UNION ALL SELECT day, 'Refusal rate', refusal_rate FROM r WHERE refusal_rate IS NOT NULL
UNION ALL SELECT day, 'Error rate', error_rate FROM r
UNION ALL SELECT day, 'Acceptance rate', acceptance_rate FROM d WHERE acceptance_rate IS NOT NULL
UNION ALL SELECT day, 'Extractor accuracy', accuracy FROM c""",
    ),
    "trend_latency": (
        "Latency by day",
        f"""SELECT run_date AS day, 'p50' AS series, percentile(latency_s, 0.5) AS seconds
{RUNS} GROUP BY run_date
UNION ALL SELECT run_date, 'p95', percentile(latency_s, 0.95) {RUNS} GROUP BY run_date""",
    ),
    "trend_cost": (
        "Cost per run by day",
        f"""SELECT run_date AS day, 'p50' AS series, percentile(cost_usd, 0.5) AS usd
{RUNS} GROUP BY run_date
UNION ALL SELECT run_date, 'p95', percentile(cost_usd, 0.95) {RUNS} GROUP BY run_date""",
    ),
    "trend_total_cost": (
        "Total cost by day",
        f"""SELECT run_date AS day, SUM(cost_usd) AS total_cost, COUNT(*) AS runs
{RUNS} GROUP BY run_date""",
    ),
    "dist_latency": (
        "Latency distribution",
        f"""SELECT CAST(FLOOR(latency_s / 5) * 5 AS INT) AS latency_from_s, COUNT(*) AS runs
{RUNS} AND latency_s IS NOT NULL GROUP BY 1""",
    ),
    "dist_cost": (
        "Cost distribution",
        f"""SELECT ROUND(FLOOR(cost_usd * 100) / 100, 2) AS cost_from_usd, COUNT(*) AS runs
{RUNS} GROUP BY 1""",
    ),
    "cost_by_case": (
        "Cost per case",
        f"""SELECT case_id, COUNT(*) AS runs, ROUND(SUM(cost_usd), 4) AS total_cost_usd,
  ROUND(AVG(cost_usd), 4) AS cost_per_run_usd, MAX(run_date) AS last_run
{RUNS} AND case_id IS NOT NULL GROUP BY case_id ORDER BY total_cost_usd DESC LIMIT 200""",
    ),
    "options_task": (
        "Filter values: agent and task",
        f"""SELECT 'all' AS agent_task_value
UNION ALL SELECT DISTINCT agent_task FROM {_t("agent_runs")} WHERE {SOURCE}""",
    ),
    "options_case": (
        "Filter values: case",
        f"""SELECT 'all' AS case_value
UNION SELECT DISTINCT case_id FROM {_t("agent_runs")} WHERE case_id IS NOT NULL AND {SOURCE}
UNION SELECT DISTINCT case_id FROM {_t("decisions")} WHERE {SOURCE}""",
    ),
}


def parameters_of(sql: str) -> list[str]:
    return [name for name in PARAMS if f":{name}" in sql]


def _dataset(name: str) -> dict[str, Any]:
    display, sql = DATASETS[name]
    params = []
    for key in parameters_of(sql):
        kind, default = PARAMS[key]
        value = f"{default}T00:00:00.000" if kind == "DATE" else default
        params.append({
            "displayName": key, "keyword": key, "dataType": kind,
            "defaultSelection": {"values": {"dataType": kind, "values": [{"value": value}]}},
        })  # fmt: skip
    lines = [line + "\n" for line in sql.splitlines()]
    return {
        "name": name,
        "displayName": display,
        "queryLines": lines,
        **({"parameters": params} if params else {}),
    }


def _query(dataset: str, fields: list[str], name: str = "main_query") -> dict[str, Any]:
    return {
        "name": name,
        "query": {"datasetName": dataset, "fields": [{"name": f, "expression": f"`{f}`"} for f in fields],
                  "disaggregated": True},
    }  # fmt: skip


PERCENT = {"type": "number-percent", "decimalPlaces": {"type": "max", "places": 1}}
USD = {"type": "number-currency", "currencyCode": "USD", "decimalPlaces": {"type": "max", "places": 4}}
SECONDS = {"type": "number-plain", "decimalPlaces": {"type": "max", "places": 1}}


def counter(name: str, dataset: str, field: str, title: str, description: str, fmt: dict, pos: tuple) -> dict:
    return {
        "widget": {
            "name": name,
            "queries": [_query(dataset, [field])],
            "spec": {
                "version": 2,
                "widgetType": "counter",
                "encodings": {"value": {"fieldName": field, "displayName": title, "format": fmt}},
                "frame": {
                    "showTitle": True,
                    "title": title,
                    "showDescription": True,
                    "description": description,
                },
            },
        },
        "position": dict(zip(("x", "y", "width", "height"), pos, strict=True)),
    }


def chart(kind: str, name: str, dataset: str, x: str, y: str, title: str, pos: tuple, *, color: str | None = None,
          x_type: str = "temporal") -> dict:  # fmt: skip
    fields = [x, y] + ([color] if color else [])
    encodings: dict[str, Any] = {
        "x": {"fieldName": x, "scale": {"type": x_type}, "displayName": x.replace("_", " ")},
        "y": {"fieldName": y, "scale": {"type": "quantitative"}, "displayName": y.replace("_", " ")},
    }
    if color:
        encodings["color"] = {"fieldName": color, "scale": {"type": "categorical"}, "displayName": color}
    return {
        "widget": {
            "name": name,
            "queries": [_query(dataset, fields)],
            "spec": {"version": 3, "widgetType": kind, "encodings": encodings,
                     "frame": {"showTitle": True, "title": title}},
        },
        "position": dict(zip(("x", "y", "width", "height"), pos, strict=True)),
    }  # fmt: skip


def table(name: str, dataset: str, columns: list[tuple[str, str, str]], title: str, pos: tuple) -> dict:
    cols = [
        {"fieldName": field, "type": kind, "displayAs": "number" if kind in ("integer", "float") else kind,
         "visible": True, "order": i, "title": label, "displayName": label, "alignContent": "left"
         if kind == "string" else "right"}
        for i, (field, label, kind) in enumerate(columns)
    ]  # fmt: skip
    return {
        "widget": {
            "name": name,
            "queries": [_query(dataset, [c[0] for c in columns])],
            "spec": {"version": 1, "widgetType": "table", "encodings": {"columns": cols},
                     "frame": {"showTitle": True, "title": title}, "itemsPerPage": 25, "condensed": True},
        },
        "position": dict(zip(("x", "y", "width", "height"), pos, strict=True)),
    }  # fmt: skip


def parameter_filter(
    name: str, keyword: str, title: str, kind: str, pos: tuple, values: tuple | None = None
) -> dict:
    """A filter that sets ``keyword`` in every dataset that uses it; ``values`` (dataset, field) fills its list."""
    queries, fields = [], []
    if values:
        dataset, field = values
        queries.append({"name": f"values_{keyword}", "query": {"datasetName": dataset, "fields": [
            {"name": field, "expression": f"`{field}`"}], "disaggregated": False}})  # fmt: skip
        fields.append({"fieldName": field, "displayName": title, "queryName": f"values_{keyword}"})
    for dataset, (_, sql) in DATASETS.items():
        if keyword in parameters_of(sql):
            query = f"parameter_{dataset}_{keyword}"
            queries.append({"name": query, "query": {"datasetName": dataset, "parameters": [
                {"name": keyword, "keyword": keyword}], "disaggregated": False}})  # fmt: skip
            fields.append({"parameterName": keyword, "queryName": query})
    return {
        "widget": {
            "name": name,
            "queries": queries,
            "spec": {"version": 2, "widgetType": kind, "encodings": {"fields": fields},
                     "frame": {"showTitle": True, "title": title}},
        },
        "position": dict(zip(("x", "y", "width", "height"), pos, strict=True)),
    }  # fmt: skip


def dashboard() -> dict[str, Any]:
    intro = [
        "## Replenishment workflow: operations\n",
        "**Demo data.** Every figure here comes from generated example rows (`source = 'demo'`), to show what "
        "the dashboard measures: agent runs of the planner (prepare, evaluate, handoff) and the supplier-document "
        "extractor, purchasing's decisions, confirmed documents, and uptime checks. The real rows stay in "
        "`snm.ops`. The cards follow the filters.\n",
    ]
    layout: list[dict[str, Any]] = [
        {"widget": {"name": "intro", "multilineTextboxSpec": {"lines": intro}}, "position": {"x": 0, "y": 0, "width": 6, "height": 2}},
        parameter_filter("f_from", "from_date", "From", "filter-date-picker", (0, 2, 1, 1)),
        parameter_filter("f_to", "to_date", "To", "filter-date-picker", (1, 2, 1, 1)),
        parameter_filter("f_task", "agent_task", "Agent and task", "filter-single-select", (2, 2, 2, 1), ("options_task", "agent_task_value")),
        parameter_filter("f_case", "case_id", "Case", "filter-single-select", (4, 2, 2, 1), ("options_case", "case_value")),
        counter("c_accept", "cards_decisions", "acceptance_rate", "Acceptance rate",
                "Decisions that followed the recommendation / decisions with one.", PERCENT, (0, 3, 1, 3)),
        counter("c_extract", "cards_documents", "extractor_accuracy", "Extractor accuracy",
                "Documents confirmed with no field changed / confirmed documents.", PERCENT, (1, 3, 1, 3)),
        counter("c_checks", "cards_runs", "trace_check_pass_rate", "Trace-check pass rate",
                "Planner runs passing every code check / planner runs.", PERCENT, (2, 3, 1, 3)),
        counter("c_refusal", "cards_runs", "refusal_rate", "Refusal rate",
                "Refused writes / write attempts.", PERCENT, (3, 3, 1, 3)),
        counter("c_error", "cards_runs", "error_rate", "Error rate",
                "Runs ended in error / runs.", PERCENT, (4, 3, 1, 3)),
        counter("c_uptime", "cards_uptime", "uptime", "Uptime",
                "Healthy checks / checks while the uptime schedule runs (dates only).", PERCENT, (5, 3, 1, 3)),
        counter("c_lat50", "cards_runs", "latency_p50", "Latency p50 (s)", "The usual run.", SECONDS, (0, 6, 1, 3)),
        counter("c_lat95", "cards_runs", "latency_p95", "Latency p95 (s)", "The slow tail.", SECONDS, (1, 6, 1, 3)),
        counter("c_cost50", "cards_runs", "cost_p50", "Cost p50", "The usual run.", USD, (2, 6, 1, 3)),
        counter("c_cost95", "cards_runs", "cost_p95", "Cost p95", "The costly tail.", USD, (3, 6, 1, 3)),
        counter("c_total", "cards_runs", "total_cost", "Total cost", "Model cost in the range.", USD, (4, 6, 1, 3)),
        counter("c_case", "cards_runs", "cost_per_case", "Cost per case",
                "Cost of runs on a case / cases.", USD, (5, 6, 1, 3)),
        chart("line", "t_rates", "trend_rates", "day", "value", "Rates by day", (0, 9, 3, 6), color="series"),
        chart("bar", "t_total", "trend_total_cost", "day", "total_cost", "Total cost by day (USD)", (3, 9, 3, 6)),
        chart("line", "t_latency", "trend_latency", "day", "seconds", "Latency by day (s): p50 and p95", (0, 15, 3, 6),
              color="series"),
        chart("line", "t_cost", "trend_cost", "day", "usd", "Cost per run by day (USD): p50 and p95", (3, 15, 3, 6),
              color="series"),
        chart("bar", "d_latency", "dist_latency", "latency_from_s", "runs", "Latency distribution (runs per 5 s)",
              (0, 21, 3, 6), x_type="quantitative"),
        chart("bar", "d_cost", "dist_cost", "cost_from_usd", "runs", "Cost distribution (runs per USD 0.01)",
              (3, 21, 3, 6), x_type="quantitative"),
        table("tb_case", "cost_by_case", [("case_id", "Case", "string"), ("runs", "Runs", "integer"),
              ("total_cost_usd", "Total cost (USD)", "float"), ("cost_per_run_usd", "Cost per run (USD)", "float"),
              ("last_run", "Last run", "date")], "Cost per case", (0, 27, 6, 8)),
    ]  # fmt: skip
    return {
        "datasets": [_dataset(name) for name in DATASETS],
        "pages": [
            {
                "name": "operations",
                "displayName": "Operations",
                "layout": layout,
                "pageType": "PAGE_TYPE_CANVAS",
            }
        ],
    }


def serialized() -> str:
    return json.dumps(dashboard())
