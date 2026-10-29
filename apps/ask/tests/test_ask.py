"""The local question page: a thread with a follow-up, provenance shown, escaping, flags, the daily limit, and
the guards against double spending."""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from snm_ask import agent
from snm_ask.app import MAX_QUESTIONS_PER_THREAD, AskSettings, create_app
from snm_ask.markdown import render


def fake_result(question: str, earlier: list[str], cost: float = 0.05) -> dict:
    return {
        "run_id": f"run-{len(earlier)}",
        "status": "answered",
        "question": question,
        "answer": {
            "behavior": "answer",
            "answer": f"Round Rock shipped **1,234** lines. <script>alert(1)</script> ({question})",
            "values": [{"label": "US10 lines shipped", "value": 1234, "unit": "lines", "result_id": "r_abc"}],
            "assumptions": ["Last week is ISO week 38."],
            "definitions_used": ["metrics/lines_shipped.md"],
            "confidence": "high",
        },
        "faithfulness": {"checked": 1, "traced": 1, "untraced": []},
        "queries": [{"result_id": "r_abc", "sql": "SELECT COUNT(*) FROM curated.delivery_line"}],
        "usage": {"input": 1, "output": 1, "cache_read": 0, "cache_write": 0, "cost_usd": cost},
        "turns": 4,
        "sql_statements": 2,
        "wall_clock_s": 12.5,
        "model": "test-model",
        "prompt": {"name": "main", "version": "v4", "digest": "x"},
        "trace_path": "runs/ask/run/trace.jsonl",
        **({"earlier": [{"question": text, "cost_usd": 0.01} for text in earlier]} if earlier else {}),
    }


class Recorder:
    def __init__(self, cost: float = 0.05) -> None:
        self.calls: list[tuple] = []
        self.cost = cost

    def __call__(self, question, earlier, role, as_of, runs_dir):
        self.calls.append((question, list(earlier), role, as_of))
        return fake_result(question, earlier, self.cost)


@pytest.fixture
def page(tmp_path: Path):
    runner = Recorder()
    app = create_app(AskSettings(runs_dir=tmp_path, max_usd_per_day=3.0), runner, lambda role: [])
    return TestClient(app), runner, tmp_path


def start(
    client: TestClient, question="How many lines were shipped from Round Rock last week?", **form
) -> str:
    response = client.post(
        "/ask", data={"question": question, "role": "site_manager_rr", "as_of": "2026-09-21", **form},
        follow_redirects=False,
    )  # fmt: skip
    assert response.status_code == 303, response.text
    return response.headers["location"]


def test_a_thread_with_a_follow_up_reasks_the_earlier_question(page):
    client, runner, runs = page
    location = start(client)
    thread_id = location.rsplit("/", 1)[1]
    html = client.get(location).text
    assert "1,234" in html and "r_abc" in html and "SELECT COUNT(*) FROM curated.delivery_line" in html
    assert "1/1 numbers traced" in html and "kb/metrics/lines_shipped.md" in html
    assert "&lt;script&gt;" in html and "<script>alert(1)" not in html  # the answer is escaped

    client.post(f"/thread/{thread_id}/ask", data={"question": "And the week before?"})
    assert runner.calls[1] == (
        "And the week before?", ["How many lines were shipped from Round Rock last week?"], "site_manager_rr",
        "2026-09-21",
    )  # fmt: skip
    html = client.get(location).text
    assert "re-asking 1 earlier" in html
    stored = json.loads((runs / "threads" / f"{thread_id}.json").read_text())
    assert [turn["question"] for turn in stored["turns"]] == [runner.calls[0][0], "And the week before?"]
    assert client.get("/").text.count(f"/thread/{thread_id}") == 1


def test_a_thread_is_capped_and_inputs_are_checked(page):
    client, runner, _ = page
    thread_id = start(client).rsplit("/", 1)[1]
    for index in range(MAX_QUESTIONS_PER_THREAD):
        client.post(f"/thread/{thread_id}/ask", data={"question": f"Follow-up {index}"})
    assert len(runner.calls) == MAX_QUESTIONS_PER_THREAD
    assert "Unknown+role" in str(
        client.post("/ask", data={"question": "x", "role": "executive", "as_of": "2026-09-21"},
                    follow_redirects=False).headers["location"]
    )  # fmt: skip
    assert client.get("/thread/..%2F..%2Fetc").status_code == 404
    assert client.get("/thread/20261020T010203-abcdef").status_code == 404
    assert len(runner.calls) == MAX_QUESTIONS_PER_THREAD


def test_the_daily_limit_refuses_new_questions(tmp_path):
    runner = Recorder(cost=0.6)
    client = TestClient(
        create_app(AskSettings(runs_dir=tmp_path, max_usd_per_day=1.0), runner, lambda role: [])
    )
    start(client)
    start(client)
    location = client.post(
        "/ask",
        data={"question": "third", "role": "site_manager_rr", "as_of": "2026-09-21"},
        follow_redirects=False,
    ).headers["location"]
    assert "limit" in location and len(runner.calls) == 2


def test_missing_settings_stop_before_any_run(tmp_path):
    runner = Recorder()
    app = create_app(AskSettings(runs_dir=tmp_path), runner, lambda role: ["SNM_ANTHROPIC_API_KEY"])
    client = TestClient(app)
    assert "SNM_ANTHROPIC_API_KEY" in client.get("/").text
    location = client.post(
        "/ask",
        data={"question": "q", "role": "site_manager_rr", "as_of": "2026-09-21"},
        follow_redirects=False,
    ).headers["location"]
    assert "SNM_ANTHROPIC_API_KEY" in location and runner.calls == []


def test_a_second_question_while_one_runs_is_refused(tmp_path):
    release = threading.Event()
    started = threading.Event()
    calls = []

    def slow(question, earlier, role, as_of, runs_dir):
        calls.append(question)
        started.set()
        release.wait(5)
        return fake_result(question, earlier)

    client = TestClient(create_app(AskSettings(runs_dir=tmp_path), slow, lambda role: []))
    first = threading.Thread(target=start, args=(client, "first"))
    first.start()
    assert started.wait(5)
    location = client.post(
        "/ask",
        data={"question": "second", "role": "site_manager_rr", "as_of": "2026-09-21"},
        follow_redirects=False,
    ).headers["location"]
    release.set()
    first.join(5)
    assert "still+answering" in location and calls == ["first"]


def test_flagging_an_answer_records_it_for_a_failure_card(page):
    client, _, runs = page
    location = start(client)
    thread_id = location.rsplit("/", 1)[1]
    client.post(f"/thread/{thread_id}/turn/0/flag", data={"note": "Expected 1,240: transfers are missing."})
    assert "Expected 1,240" in client.get(location).text
    flag = json.loads((runs / "flags.jsonl").read_text().splitlines()[0])
    assert flag["thread"] == thread_id and flag["role"] == "site_manager_rr" and flag["run_id"] == "run-0"
    exported = client.get(f"{location}/export")
    assert exported.json()["turns"][0]["flag"]["note"].startswith("Expected")
    assert re.search(r'filename="ask-.+\.json"', exported.headers["content-disposition"])


def test_an_agent_that_did_not_run_leaves_no_turn(tmp_path):
    def broken(question, earlier, role, as_of, runs_dir):
        return {"status": "error", "error": "harness not built"}

    client = TestClient(create_app(AskSettings(runs_dir=tmp_path), broken, lambda role: []))
    location = start(client)
    assert location.startswith("/?error=") and "harness+not+built" in location
    assert list((tmp_path / "threads").glob("*.json")) == []


def test_run_agent_passes_the_thread_and_checks_names_only(tmp_path, monkeypatch):
    cli = tmp_path / "ask.js"
    cli.write_text("")
    for name in agent.AGENT_ENVIRONMENT:
        monkeypatch.setenv(name, "set")
    monkeypatch.delenv("SNM_DATABRICKS_CLIENT_ID_SITE_MANAGER_RR", raising=False)
    monkeypatch.setenv("SNM_DATABRICKS_CLIENT_SECRET_SITE_MANAGER_RR", "set")
    refused = agent.run_agent(cli, "q", [], "site_manager_rr", "2026-09-21", tmp_path)
    assert refused["status"] == "error" and "SNM_DATABRICKS_CLIENT_ID_SITE_MANAGER_RR" in refused["error"]

    monkeypatch.setenv("SNM_DATABRICKS_CLIENT_ID_SITE_MANAGER_RR", "set")
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command

        class Completed:
            stdout = 'log line\n{"status": "answered"}\n'
            stderr = ""

        return Completed()

    monkeypatch.setattr(agent.subprocess, "run", fake_run)
    assert agent.run_agent(cli, "And Dongguan?", ["First?"], "site_manager_rr", "2026-09-21", tmp_path) == {
        "status": "answered"
    }
    command = seen["command"]
    assert command[-3:] == ["--earlier", "First?", "And Dongguan?"]
    assert command[command.index("--role") + 1] == "site_manager_rr"


def test_markdown_subset_escapes_everything_else():
    html = str(
        render(
            "**13** deliveries at `U100`.\n\n- one <b>\n- two\n\n| Week | Count |\n|---|---|\n| 44 | 3 |\n"
            "<img src=x onerror=alert(1)>"
        )
    )
    assert "<b>13</b>" in html and "<code>U100</code>" in html
    assert "<ul><li>one &lt;b&gt;</li><li>two</li></ul>" in html
    assert "<th>Week</th>" in html and "<td>44</td>" in html and "---" not in html
    assert "<img" not in html and "&lt;img" in html
