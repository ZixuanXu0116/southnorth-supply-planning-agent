import json
import subprocess

import pytest

from snm_evals import run
from snm_evals import snapshot as snapshot_module
from snm_evals.cases import load_suite


def test_main_model_prefers_overrides(monkeypatch) -> None:
    monkeypatch.setenv("SNM_MODEL_MAIN", "strong-model")
    monkeypatch.delenv("SNM_MODEL_PROVIDER", raising=False)
    assert run.main_model() == "anthropic/strong-model"
    assert run.main_model({"SNM_MODEL_MAIN": "mid-model"}) == "anthropic/mid-model"


def test_a_batch_never_mixes_main_models(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(run, "RUNS_ROOT", tmp_path)
    monkeypatch.setenv("SNM_MODEL_MAIN", "strong-model")
    batch = tmp_path / "b1"
    batch.mkdir()
    previous = {"case_id": "seed_01", "arm": "main", "rep": 1, "model": "anthropic/strong-model"}
    (batch / "results.jsonl").write_text(json.dumps(previous) + "\n", encoding="utf-8")
    suite = load_suite("seed")
    with pytest.raises(ValueError, match="another batch"):
        run.run_batch(suite, ["main"], ["seed_01"], 1, "b1", env_overrides={"SNM_MODEL_MAIN": "mid-model"})
    # Same model: the finished run is skipped and nothing new is launched.
    assert run.run_batch(suite, ["main"], ["seed_01"], 1, "b1") == batch


def test_retry_errors_reruns_only_the_errored_runs(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(run, "RUNS_ROOT", tmp_path)
    monkeypatch.setenv("SNM_MODEL_MAIN", "strong-model")
    batch = tmp_path / "b2"
    (batch / "runs" / "seed_02-main-r1").mkdir(parents=True)
    results = [
        {"run_id": "seed_01-main-r1", "case_id": "seed_01", "arm": "main", "rep": 1, "status": "answered",
         "model": "anthropic/strong-model"},
        {"run_id": "seed_02-main-r1", "case_id": "seed_02", "arm": "main", "rep": 1, "status": "error",
         "error": "400 credit balance too low", "model": "anthropic/strong-model"},
    ]  # fmt: skip
    (batch / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in results), encoding="utf-8")
    launched = []

    def fake_run(command, **_kwargs):
        launched.append(command[command.index("--run-id") + 1])
        stdout = '{"status": "answered", "answer": {}}\n'
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(run.subprocess, "run", fake_run)
    suite = load_suite("seed")
    # Without the flag, the errored run counts as done.
    run.run_batch(suite, ["main"], ["seed_01", "seed_02"], 1, "b2")
    assert launched == []
    run.run_batch(suite, ["main"], ["seed_01", "seed_02"], 1, "b2", retry_errors=True)
    assert launched == ["seed_02-main-r1"]
    rows = [json.loads(line) for line in (batch / "results.jsonl").read_text().splitlines()]
    assert [(row["case_id"], row["status"]) for row in rows] == [
        ("seed_01", "answered"), ("seed_02", "answered")
    ]  # fmt: skip


def _loaded(**overrides: str) -> dict[str, dict[str, str]]:
    snapshot = {
        "profile": "small",
        "generator_version": "0.6.0",
        "seed": "20260924",
        "contracts_digest": "abc",
        "load_status": "complete",
        "loaded_at": "2026-10-02T01:00:00+00:00",
        **overrides,
    }
    return {schema: dict(snapshot) for schema in snapshot_module.RAW_SCHEMAS}


def test_runs_only_against_the_suite_snapshot() -> None:
    expected = {"profile": "small", "generator_version": "0.6.0", "seed": 20260924}
    snapshot = snapshot_module.check_snapshot(expected, _loaded())
    assert snapshot["generator_version"] == "0.6.0" and "load_status" not in snapshot
    with pytest.raises(snapshot_module.SnapshotMismatch, match="generator_version"):
        snapshot_module.check_snapshot(expected, _loaded(generator_version="0.5.0"))
    with pytest.raises(snapshot_module.SnapshotMismatch, match="load loading"):
        snapshot_module.check_snapshot(expected, _loaded(load_status="loading"))
    with pytest.raises(snapshot_module.SnapshotMismatch, match="not recorded"):
        snapshot_module.check_snapshot(expected, {})
    mixed = _loaded()
    mixed["raw_ext"]["contracts_digest"] = "other"
    with pytest.raises(snapshot_module.SnapshotMismatch, match="different snapshots"):
        snapshot_module.check_snapshot(expected, mixed)


def test_run_case_records_the_data_snapshot(monkeypatch, tmp_path) -> None:
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout='{"status": "answered"}\n', stderr="")

    monkeypatch.setattr(run.subprocess, "run", fake_run)
    suite = load_suite("seed")
    case = suite.case("seed_01")
    run.run_case(case, suite, "main", 1, tmp_path, data_snapshot={"generator_version": "0.6.0"})
    command = calls[0]
    assert command[command.index("--data-snapshot") + 1] == '{"generator_version": "0.6.0"}'
    assert command[-1] == case.question


def test_discovery_tasks_stay_out_of_the_headline() -> None:
    from snm_evals.score import CaseScore

    suite = load_suite("bank_v8")
    assert suite.case("c154").discovery and not suite.case("c142").discovery

    def score(case_id: str, passed: bool) -> CaseScore:
        return CaseScore(
            case_id, "main", "answered", "answer", passed, passed, True, passed, discovery=case_id == "c154"
        )

    report = run.render_report(suite, [score("c142", True), score("c154", False)], "b")
    assert "| main | - | 1 | 1/1 (100%) |" in report
    assert "Discovery tasks (c154;" in report and "main 0/1 (0%)" in report


def test_reports_show_the_context_of_each_question_of_a_thread() -> None:
    from snm_evals.score import score_case

    suite = load_suite("followup_v1")
    case = suite.case("f03")

    def turn(behavior: str, tokens: int) -> dict:
        return {
            "answer": {"behavior": behavior, "answer": "ok", "values": []},
            "context": {"first_prompt_tokens": tokens},
        }

    result = {"arm": "main", "status": "answered", **turn("policy_restricted", 18000)}
    result["earlier"] = [turn("answer", 9000), turn("answer", 16000)]
    score = score_case(case, None, result)
    assert score.question_prompt_tokens == [9000, 16000, 18000]
    report = run.render_report(suite, [score], "b")
    assert "## Context per question" in report and "| main | 3 | 1 | 18,000 | 18,000 |" in report
