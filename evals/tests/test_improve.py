import json
from datetime import date
from pathlib import Path

import pytest

from snm_evals.cases import load_answers, load_suite
from snm_evals.improve import collect as collect_module
from snm_evals.improve import loop
from snm_evals.improve.card import proposer_input, render_card
from snm_evals.improve.excerpt import excerpt, kb_pages_read, result_shape
from snm_evals.improve.guards import Change, Edit, LeakCorpus, apply_edits, check, kb_text, leaks
from snm_evals.improve.intake import intake_flags
from snm_evals.improve.screen import (
    CaseOutcome,
    ScreenPlan,
    ScreenResult,
    control_problems,
    estimate,
    pair,
    run_screen,
    select_cases,
)
from snm_evals.improve.sources import directory_digest

SUITE = load_suite("seed")
ANSWERS = load_answers(SUITE)
TODAY = date(2026, 10, 28)
QUESTION_01 = SUITE.case("seed_01").question


def _trace(*pages: str, answer: str = "Longgang 96.8 %.") -> list[dict]:
    calls = [{"id": f"t{i}", "name": "kb_read", "arguments": {"path": page}} for i, page in enumerate(pages)]
    return [
        {"event": "run_start", "run_id": "x"},
        {
            "event": "assistant",
            "turn": 1,
            "thinking": "Read the definition first.",
            "text": "",
            "tool_calls": calls,
        },
        *[
            {
                "event": "tool",
                "id": f"t{i}",
                "name": "kb_read",
                "is_error": False,
                "output": f"---\nid: p{i}\n---",
            }
            for i in range(len(pages))
        ],
        {
            "event": "assistant",
            "turn": 2,
            "thinking": "",
            "text": "",
            "tool_calls": [
                {"id": "q1", "name": "query_metric", "arguments": {"metric_view": "mv_on_time_pgi"}},
                {"id": "s1", "name": "submit_answer", "arguments": {"behavior": "answer", "answer": answer}},
            ],
        },
        {
            "event": "tool",
            "id": "q1",
            "name": "query_metric",
            "is_error": False,
            "output": json.dumps(
                {
                    "result_id": "r0001",
                    "columns": ["vstel", "rate"],
                    "row_count": 3,
                    "preview": [["C100", 0.97]],
                }
            ),
        },
        {"event": "faithfulness", "submission": 1, "rejected": True, "untraced": ["96.8"]},
        {"event": "run_end"},
    ]


def _write_batch(
    root: Path, name: str, runs: list[tuple[str, str, str, list[str]]], cost: float = 0.05
) -> Path:
    """runs: (case_id, status, behavior, pages read); writes results.jsonl and traces."""
    batch = root / name
    lines = []
    for case_id, status, behavior, pages in runs:
        run_id = f"{case_id}-main-r1"
        (batch / "runs" / run_id).mkdir(parents=True, exist_ok=True)
        trace = "".join(json.dumps(event) + "\n" for event in _trace(*pages))
        (batch / "runs" / run_id / "trace.jsonl").write_text(trace, encoding="utf-8")
        lines.append(
            {
                "run_id": run_id,
                "case_id": case_id,
                "arm": "main",
                "rep": 1,
                "status": status,
                "model": "anthropic/mid-model",
                "effort": "medium",
                "as_of": SUITE.as_of,
                "prompt": {"name": "main", "version": "v4", "digest": "d1"},
                "manifest": {"git_commit": None, "kb_digest": "k1"},
                "answer": {"behavior": behavior, "answer": "text", "values": []},
                "usage": {"cost_usd": cost, "cache_read": 900, "input": 10, "cache_write": 90},
            }
        )
    (batch / "results.jsonl").write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    return batch


# ---------------------------------------------------------------- excerpt


def test_excerpt_keeps_the_decisive_parts() -> None:
    events = _trace("metrics/on_time_pgi_rate.md", "kb/sites/longgang.md")
    text = excerpt(events)
    assert 'thinking "Read the definition first."' in text
    assert '`kb_read`(path="metrics/on_time_pgi_rate.md") → ' in text
    assert "r0001: columns ['vstel', 'rate'], 3 rows" in text
    assert "Faithfulness gate rejected submission 1" in text
    assert text.endswith("- Submission (answer): Longgang 96.8 %.")
    assert kb_pages_read(events) == ["metrics/on_time_pgi_rate.md", "sites/longgang.md"]
    assert result_shape("---\nid: concept.x\ntitle: X\n---\n# X") == "34 chars, id: concept.x"


# ---------------------------------------------------------------- collect and card


def test_collect_cards_failures_and_skips_the_rest(tmp_path) -> None:
    batch = _write_batch(
        tmp_path,
        "b1",
        [
            ("seed_17", "answered", "answer", []),  # expects policy_restricted: a failure
            ("seed_19", "error", "", []),  # infrastructure
            ("seed_16", "answered", "decline", []),  # failure covered by an open card
            ("seed_03", "answered", "decline", []),  # failure on a test-split case
            ("seed_14", "answered", "decline", []),  # failure on a discovery task
        ],
    )
    cards = tmp_path / "failures"
    cards.mkdir()
    (cards / "old-seed_16.md").write_text(
        "# Failure card: old / seed_16\n\n- **Status:** open\n", encoding="utf-8"
    )
    discovery = [case.model_copy(update={"discovery": case.id == "seed_14"}) for case in SUITE.cases]
    suite = SUITE.model_copy(update={"cases": discovery})
    failures, skipped = collect_module.collect(suite, ANSWERS, batch, failures_dir=cards)
    assert [failure.card_id for failure in failures] == ["b1-seed_17"]
    reasons = dict(skipped)
    assert reasons["seed_19"].startswith("errored run")
    assert reasons["seed_16"] == "covered by card old-seed_16 (open)"
    assert reasons["seed_03"] == "locked test split"
    assert reasons["seed_14"].startswith("discovery task")
    replayed, _ = collect_module.collect(suite, ANSWERS, batch, replay=True, failures_dir=cards)
    assert "b1-seed_16-replay" in [failure.card_id for failure in replayed]

    card = render_card(failures[0], "seed", ANSWERS, TODAY)
    assert card.startswith("# Failure card: b1 / seed_17\n\n- **Status:** open")
    assert "`policy_restricted`" in card and "behavior not acceptable" in card
    assert "- Turn 1:" in card and "## Root cause\n\nTo be proposed." in card
    payload = proposer_input(failures[0], ANSWERS, card)
    assert payload["case"]["question"] == SUITE.case("seed_17").question
    assert payload["observed"]["behavior_ok"] is False


# ---------------------------------------------------------------- guards


@pytest.fixture
def kb(tmp_path) -> Path:
    root = tmp_path / "kb"
    (root / "concepts").mkdir(parents=True)
    (root / "policies").mkdir()
    page = (
        "---\nid: concept.demo\ntitle: Demo\nlast_reviewed: 2026-10-01\n---\n\n"
        "# Demo\n\nRules:\n- Use `311` at `2100`.\n"
    )
    (root / "concepts" / "demo.md").write_text(page, encoding="utf-8")
    (root / "policies" / "site_isolation.md").write_text(page, encoding="utf-8")
    (root / "INDEX.md").write_text("# Index\n", encoding="utf-8")
    return root


@pytest.fixture
def prompts(tmp_path) -> Path:
    root = tmp_path / "prompts" / "main"
    root.mkdir(parents=True)
    (root / "v4.md").write_text(
        "You answer questions as {{role}}.\nRules:\n- Cite results.\n", encoding="utf-8"
    )
    return root.parent


CORPUS = LeakCorpus.build([(SUITE, ANSWERS)])
ANCHOR = "- Use `311` at `2100`.\n"


def _guard(kb: Path, prompts: Path, new: str, path: str = "kb/concepts/demo.md", old: str = ANCHOR):
    changes, problems = apply_edits([Edit(path, old, new)], kb, prompts, "v4", TODAY)
    report = check(changes, kb_text(kb), CORPUS)
    return changes, problems + report.problems


def test_a_clean_rule_passes_and_moves_last_reviewed(kb, prompts) -> None:
    changes, problems = _guard(
        kb, prompts, ANCHOR + "- Count `311` issuing items once, never the receiving item.\n"
    )
    assert problems == []
    assert "last_reviewed: 2026-10-28" in changes[0].after
    assert changes[0].added_lines() == ["- Count `311` issuing items once, never the receiving item."]
    assert "+- Count `311`" in changes[0].diff()


@pytest.mark.parametrize(
    ("added", "problem"),
    [
        ("- On-time PGI at Longgang was 96.83 %.", "reference value"),
        ("- Longgang's rate is 0.9683.", "reference value"),
        ("- Like case seed_17, decline.", "names case id seed_17"),
        (f"- Example: {QUESTION_01}", "repeats a case question"),
        ("- Never read the eval schema.", "evaluation schema"),
        ("\n".join(f"- Rule {i}." for i in range(13)), "adds 13 lines"),
    ],
)
def test_leaks_and_oversized_edits_are_stopped(kb, prompts, added, problem) -> None:
    _changes, problems = _guard(kb, prompts, ANCHOR + added + "\n")
    assert any(problem in found for found in problems), problems


def test_numbers_already_in_the_knowledge_base_are_not_leaks() -> None:
    assert leaks("Use 0.9683 here.", "", CORPUS)
    assert leaks("Use 0.9683 here.", "The rate 0.9683 appears in the page.", CORPUS) == []
    assert leaks("Round numbers like 3,000 or 100 are fine.", "", CORPUS) == []


def test_paths_open_to_proposals(kb, prompts) -> None:
    for path in ("kb/INDEX.md", "kb/policies/site_isolation.md", "kb/concepts/new.md", "harness/src/x.ts"):
        _changes, problems = _guard(kb, prompts, "x", path=path)
        assert problems, path
    _changes, problems = _guard(kb, prompts, "x", old="- not in the page\n")
    assert problems[0] == "kb/concepts/demo.md: the old text occurs 0 times (it must occur exactly once)"


def test_prompt_edits_become_the_next_version(kb, prompts) -> None:
    changes, problems = _guard(
        kb, prompts, "- Cite results by result_id.\n", "prompts/main/v4.md", "- Cite results.\n"
    )
    assert problems == []
    assert changes[0].path == "prompts/main/v5.md" and changes[0].new_file
    _changes, problems = _guard(kb, prompts, "- Cite results.\n", "prompts/main/v3.md", "x")
    assert problems[0] == "prompts/main/v3.md: edit the current version v4; the edits go into v5"
    _changes, problems = _guard(
        kb, prompts, "- As of 2026-09-21, cite results.\n", "prompts/main/v4.md", "- Cite results.\n"
    )
    assert any("would contain a date" in problem for problem in problems)


def test_front_matter_stays_stable(kb, prompts) -> None:
    _changes, problems = _guard(kb, prompts, "id: concept.renamed", old="id: concept.demo")
    assert any("page id or title changed" in problem for problem in problems)


# ---------------------------------------------------------------- screen


def test_screen_selects_the_affected_cases_and_reuses_the_control(tmp_path) -> None:
    batch = _write_batch(
        tmp_path,
        "src",
        [
            ("seed_01", "answered", "answer", ["metrics/on_time_pgi_rate.md"]),
            ("seed_02", "answered", "answer", []),
            ("seed_08", "answered", "answer", []),
            ("seed_10", "answered", "answer", ["sites/longgang.md"]),
            ("seed_17", "answered", "answer", []),
        ],
    )
    runs = {}
    for line in (batch / "results.jsonl").read_text(encoding="utf-8").splitlines():
        result = json.loads(line)
        runs.setdefault(result["case_id"], []).append(result)
    target = SUITE.case("seed_02")
    chosen = select_cases(SUITE, target, ["kb/sites/longgang.md"], runs, batch)
    assert chosen == {
        "seed_01": ["category covered"],
        "seed_02": ["target", "category covered", "requires an edited page"],
        "seed_08": ["requires an edited page"],
        "seed_10": ["read an edited page in the source batch"],
    }
    assert estimate(runs, list(chosen), "seed_02", 3) == pytest.approx(0.05 * (len(chosen) + 2))
    selected = [run for case_id in chosen for run in runs[case_id]]
    assert control_problems(selected, "k1", "prompts/main/v4.md", True) == [
        "a source run has no commit in its manifest"
    ]
    assert control_problems(selected, "k2", "prompts/main/v4.md", True)[0].startswith("the source runs used")


def test_run_screen_caps_and_points_the_agent_at_the_candidate(tmp_path) -> None:
    plan = ScreenPlan(
        "card",
        "improve-card",
        "seed_02",
        ["seed_01", "seed_02"],
        "mid-model",
        "anthropic",
        "medium",
        True,
        [],
        0.2,
    )
    calls = []

    def runner(suite, arms, cases, parallel, batch, **kwargs):
        calls.append((cases, kwargs["repeats"], kwargs["env_overrides"], kwargs["max_usd"]))
        folder = tmp_path / batch
        folder.mkdir(exist_ok=True)
        (folder / "results.jsonl").touch()
        return folder

    candidate, control = run_screen(plan, SUITE, tmp_path / "kb", None, max_usd=1.0, runner=runner)
    assert control is None and candidate.name == "improve-card"
    assert [(cases, repeats) for cases, repeats, _, _ in calls] == [
        (["seed_01", "seed_02"], 1),
        (["seed_02"], 3),
    ]
    assert calls[0][2] == {
        "SNM_KB_ROOT": str(tmp_path / "kb"),
        "SNM_MODEL_MAIN": "mid-model",
        "SNM_MODEL_PROVIDER": "anthropic",
        "SNM_EFFORT_MAIN": "medium",
    }
    plan.estimate_usd = 5.0
    with pytest.raises(ValueError, match="above the"):
        run_screen(plan, SUITE, tmp_path / "kb", None, max_usd=4.0, runner=runner)


def test_pairing_and_the_advisory_verdict(tmp_path) -> None:
    control = _write_batch(tmp_path, "control", [("seed_17", "answered", "answer", []),
                           ("seed_19", "answered", "clarify", [])])  # fmt: skip
    candidate = _write_batch(tmp_path, "candidate", [("seed_17", "answered", "policy_restricted", []),
                             ("seed_19", "answered", "answer", [])])  # fmt: skip
    plan = ScreenPlan(
        "card", "candidate", "seed_17", ["seed_17", "seed_19"], "m", "anthropic", "medium", True, [], 0.1
    )
    result = pair(plan, SUITE, ANSWERS, control, candidate)
    assert result.target_outcome.before == [False] and result.target_outcome.after == [True]
    assert result.regressions == ["seed_19"]
    assert result.verdict() == "reject"
    assert result.cache_after == pytest.approx(0.9)

    def outcome(after: list[bool]) -> ScreenResult:
        outcomes = [CaseOutcome("t", [False], after), CaseOutcome("o", [True], [True])]
        return ScreenResult("t", outcomes, 0.05, 0.06, 0.9, 0.9, 4, True)

    assert outcome([True, True, True]).verdict() == "adopt"
    assert outcome([True, False, True]).verdict() == "iterate"
    assert outcome([True, True, True]).verdict(guards_ok=False) == "reject"


# ---------------------------------------------------------------- the owner's decision and flagged answers


def test_decide_applies_an_adopted_change_and_records_a_rejection(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(loop, "IMPROVE_ROOT", tmp_path / "improve")
    monkeypatch.setattr(loop, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(loop, "FAILURES_DIR", tmp_path / "failures")
    (tmp_path / "failures").mkdir()
    page = tmp_path / "kb" / "concepts" / "demo.md"
    page.parent.mkdir(parents=True)
    page.write_text("before\n", encoding="utf-8")
    for card_id in ("c1", "c2"):
        folder = tmp_path / "improve" / card_id
        folder.mkdir(parents=True)
        (folder / "card.md").write_text(
            "# Failure card: b / x\n\n- **Status:** measured\n\n"
            "## Owner review\n\n- pending\n\n## Decision\n\nPending.\n",
            encoding="utf-8",
        )
        loop.save_state(card_id, {"card_id": card_id, "batch": "b"})
        change = Change("kb/concepts/demo.md", "before\n", "after\n")
        (folder / "changes.json").write_text(json.dumps([change.__dict__]), encoding="utf-8")
    assert loop.decide("c1", "adopt", "", TODAY) == ["kb/concepts/demo.md", "failures/c1.md"]
    assert page.read_text(encoding="utf-8") == "after\n"
    card = (tmp_path / "failures" / "c1.md").read_text(encoding="utf-8")
    assert (
        "- **Status:** adopted (2026-10-28)" in card
        and "## Decision\n\nAdopted (2026-10-28), by the owner." in card
    )
    with pytest.raises(ValueError, match="changed since the proposal"):
        loop.decide("c2", "adopt", "", TODAY)
    loop.decide("c2", "reject", "the rule names one site only", TODAY)
    card = (tmp_path / "failures" / "c2.md").read_text(encoding="utf-8")
    assert "- **Status:** reverted (the rule names one site only)" in card


def test_flagged_answers_become_cards_that_need_a_case(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(loop, "IMPROVE_ROOT", tmp_path / "improve")
    thread = {
        "id": "20261023T183000-abc123",
        "role": "ops_analyst_global",
        "as_of": "2026-09-21",
        "turns": [
            {"question": "How many lines?", "result": {"run_id": "r1", "answer": {"answer": "12 lines"}}},
            {
                "question": "And Dongguan?",
                "result": {"run_id": "r2", "answer": {"answer": "7 lines"}},
                "flag": {"note": "Dongguan shares plant CN10", "at": "2026-10-23T18:31:00+00:00"},
            },
        ],
    }
    path = tmp_path / "thread.json"
    path.write_text(json.dumps(thread), encoding="utf-8")
    assert intake_flags(path, TODAY, runs_dir=tmp_path / "ask") == ["ask-20261023T183000-abc123-t2"]
    card = (tmp_path / "improve" / "ask-20261023T183000-abc123-t2" / "card.md").read_text(encoding="utf-8")
    assert "- **Status:** needs case" in card and "Dongguan shares plant CN10" in card
    assert "- **Earlier question:** How many lines?" in card and "7 lines" in card
    assert intake_flags(path, TODAY, runs_dir=tmp_path / "ask") == []  # already carded


def test_directory_digest_matches_the_manifest_rule(tmp_path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "x.md").write_text("x", encoding="utf-8")
    (tmp_path / "b.md").write_text("b", encoding="utf-8")
    first = directory_digest(tmp_path)
    assert len(first) == 16 and first == directory_digest(tmp_path)
    (tmp_path / "b.md").write_text("c", encoding="utf-8")
    assert directory_digest(tmp_path) != first


def _card(tmp_path: Path, monkeypatch, card_id: str = "b-seed_01") -> Path:
    monkeypatch.setattr(loop, "IMPROVE_ROOT", tmp_path / "improve")
    folder = tmp_path / "improve" / card_id
    (folder / "base_kb" / "concepts").mkdir(parents=True)
    page = "---\nid: concept.demo\ntitle: Demo\nlast_reviewed: 2026-10-01\n---\n\n# Demo\n\n- Use `311`.\n"
    (folder / "base_kb" / "concepts" / "demo.md").write_text(page, encoding="utf-8")
    (folder / "card.md").write_text(
        "# Failure card: b / seed_01\n\n- **Status:** open\n\n## Root cause\n\nTo be proposed.\n\n"
        "## Fix hypothesis\n\nTo be proposed.\n\n## Decision\n\nPending.\n",
        encoding="utf-8",
    )
    (folder / "input.json").write_text(json.dumps({"prompt": {"version": "v4"}}), encoding="utf-8")
    loop.save_state(card_id, {"card_id": card_id, "batch": "b", "at": None, "spent": {"proposal": 0.0}})
    return folder


def test_propose_repairs_once_after_the_guards_refuse(tmp_path, monkeypatch) -> None:
    folder = _card(tmp_path, monkeypatch)
    edit = {"path": "kb/concepts/demo.md", "old": "- Use `311`.\n"}
    leaky = {
        "status": "proposed", "root_cause": "interpretation", "layer": "kb", "hypothesis": "h",
        "cost_usd": 0.2,
        "edits": [{**edit, "new": "- Use `311`.\n- Longgang's rate is 0.9683.\n"}],
    }  # fmt: skip
    clean = {
        **leaky,
        "cost_usd": 0.1,
        "edits": [{**edit, "new": "- Use `311`.\n- Count issuing items once.\n"}],
    }
    calls = []

    def proposer(card_id, repair=None, max_usd=0.5):
        calls.append((repair, round(max_usd, 2)))
        return leaky if repair is None else clean

    monkeypatch.setattr(loop, "run_proposer", proposer)
    outcome = loop.propose("b-seed_01", today=TODAY)
    assert outcome["guards"].ok
    assert calls[1][1] == 0.3 and "reference value" in calls[1][0][0]
    assert loop.load_state("b-seed_01")["spent"]["proposal"] == pytest.approx(0.3)
    assert "Count issuing items once" in (folder / "kb" / "concepts" / "demo.md").read_text(encoding="utf-8")
    card = (folder / "card.md").read_text(encoding="utf-8")
    assert "- **Status:** fix proposed" in card and "`interpretation` (proposed)" in card


def test_propose_records_a_proposer_that_did_not_propose(tmp_path, monkeypatch) -> None:
    folder = _card(tmp_path, monkeypatch)
    monkeypatch.setattr(
        loop, "run_proposer", lambda *_args, **_kwargs: {"status": "budget_exhausted", "cost_usd": 0.5}
    )
    outcome = loop.propose("b-seed_01", today=TODAY)
    assert not outcome["guards"].ok
    card = (folder / "card.md").read_text(encoding="utf-8")
    assert "No proposal: the proposer ended with budget_exhausted" in card
    assert "- **Status:** open" in card and not (folder / "kb").exists()
