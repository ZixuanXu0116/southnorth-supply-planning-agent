"""The ops dashboard's definition holds together: widgets read existing datasets, filters set every dataset
that uses their parameter, and the cards' SQL computes the fields the cards show."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "deploy" / "databricks"))

import ops_dashboard as od


def widgets() -> list[dict]:
    return [item["widget"] for item in od.dashboard()["pages"][0]["layout"]]


def test_every_widget_reads_a_dataset_that_exists_and_its_fields() -> None:
    names = {d["name"] for d in od.dashboard()["datasets"]}
    for widget in widgets():
        for query in widget.get("queries", []):
            dataset = query["query"]["datasetName"]
            assert dataset in names, widget["name"]
            sql = od.DATASETS[dataset][1]
            for field in query["query"].get("fields", []):
                assert re.search(rf"\b{field['name']}\b", sql), (widget["name"], field["name"])


def test_every_parameter_is_declared_and_every_filter_reaches_each_dataset_using_it() -> None:
    declared = {
        d["name"]: {p["keyword"] for p in d.get("parameters", [])} for d in od.dashboard()["datasets"]
    }
    for name, (_, sql) in od.DATASETS.items():
        used = set(re.findall(r":([a-z_]+)", sql)) & set(od.PARAMS)
        assert used == declared[name], name
    filters = {w["name"]: w for w in widgets() if "filter" in w.get("spec", {}).get("widgetType", "")}
    for keyword in od.PARAMS:
        bound = {
            q["query"]["datasetName"]
            for w in filters.values()
            for q in w["queries"]
            if any(p["keyword"] == keyword for p in q["query"].get("parameters", []))
        }
        assert bound == {name for name, params in declared.items() if keyword in params}, keyword


def test_the_cards_and_the_layout() -> None:
    cards = [w for w in widgets() if w.get("spec", {}).get("widgetType") == "counter"]
    titles = {c["spec"]["frame"]["title"] for c in cards}
    expected = {
        "Acceptance rate", "Extractor accuracy", "Trace-check pass rate", "Refusal rate", "Error rate",
        "Uptime", "Latency p50 (s)", "Latency p95 (s)", "Cost p50", "Cost p95", "Total cost", "Cost per case",
    }  # fmt: skip
    assert titles == expected
    for item in od.dashboard()["pages"][0]["layout"]:
        position = item["position"]
        assert position["x"] >= 0 and position["x"] + position["width"] <= 6
    assert json.loads(od.serialized()) == od.dashboard()


def test_the_dashboard_shows_the_demo_rows_only() -> None:
    for name, (_, sql) in od.DATASETS.items():
        for table in re.findall(r"snm\.ops\.([a-z_]+)", sql):
            reads = sql.count(f"snm.ops.{table}")
            assert sql.count("source = 'demo'") >= reads, (name, table)
    assert "source" not in od.PARAMS
    assert not any(w["name"] == "f_source" for w in widgets())
