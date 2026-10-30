"""Where the loop reads its inputs: a batch (local or from MLflow), and a suite, answers, or knowledge
base as of the working tree or a commit (a replay of an old failure sees what that batch saw)."""

from __future__ import annotations

import hashlib
import io
import shutil
import subprocess
import tarfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..cases import ANSWERS_DIR, CASES_DIR, Suite, load_answers, load_suite
from ..run import REPO_ROOT, RUNS_ROOT, load_results

IMPROVE_ROOT = REPO_ROOT / "runs" / "improve"
KB_DIR = REPO_ROOT / "kb"
PROMPTS_DIR = REPO_ROOT / "prompts"

Fetch = Callable[[str, Path], Path]


def mlflow_fetch(batch: str, target: Path) -> Path:
    from ..tracking import configure_tracking, default_experiment, download_batch

    configure_tracking()
    return download_batch(batch, target, default_experiment())


def locate_batch(batch: str, fetch: Fetch | None = mlflow_fetch) -> Path:
    """``runs/evals/<batch>``, fetched from MLflow first when it is not on this machine."""
    local = RUNS_ROOT / batch
    if (local / "results.jsonl").is_file():
        return local
    if fetch is None:
        raise FileNotFoundError(f"batch {batch} is not under {RUNS_ROOT}")
    fetch(batch, local)
    if not (local / "results.jsonl").is_file():
        raise FileNotFoundError(f"batch {batch}: no results.jsonl after the download")
    return local


def git_export(commit: str, paths: list[str], target: Path) -> Path:
    """Write ``paths`` as they were at ``commit`` under ``target`` (same relative layout)."""
    archive = subprocess.run(
        ["git", "archive", "--format=tar", commit, *paths], cwd=REPO_ROOT, capture_output=True, check=True
    )
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        tar.extractall(target, filter="data")
    return target


def suite_at(name: str, commit: str | None, workdir: Path) -> tuple[Suite, dict[str, Any]]:
    """The suite and its reference answers in the working tree, or as of ``commit``."""
    if commit is None:
        suite = load_suite(name)
        return suite, load_answers(suite)
    root = git_export(commit, ["evals/cases", "evals/answers"], workdir / "suite")
    cases_dir, answers_dir = root / "evals" / "cases", root / "evals" / "answers"
    suite = load_suite(name, cases_dir)
    return suite, load_answers(suite, answers_dir)


def all_suites(commit: str | None, workdir: Path) -> list[tuple[Suite, dict[str, Any]]]:
    """Every suite with its answers (the leakage guard checks against all of them, test split included)."""
    if commit is None:
        cases_dir, answers_dir = CASES_DIR, ANSWERS_DIR
    else:
        root = git_export(commit, ["evals/cases", "evals/answers"], workdir / "suite")
        cases_dir, answers_dir = root / "evals" / "cases", root / "evals" / "answers"
    suites = []
    for path in sorted(cases_dir.glob("*.yaml")):
        suite = load_suite(path, cases_dir)
        answers_file = answers_dir / f"{suite.suite}.json"
        answers = load_answers(suite, answers_dir) if answers_file.is_file() else {"results": {}}
        suites.append((suite, answers))
    return suites


def kb_snapshot(commit: str | None, target: Path) -> Path:
    """A copy of ``kb/`` (working tree, or as of ``commit``) at ``target``."""
    if target.exists():
        shutil.rmtree(target)
    if commit is None:
        shutil.copytree(KB_DIR, target)
        return target
    staging = git_export(commit, ["kb"], target.parent / f"{target.name}.export")
    shutil.move(str(staging / "kb"), target)
    shutil.rmtree(staging)
    return target


def directory_digest(root: Path) -> str:
    """The run manifest's ``kb_digest`` (``harness/src/agent/manifest.ts``): paths and contents, sorted."""
    digest = hashlib.sha256()
    for path in sorted((p for p in root.rglob("*") if p.is_file()), key=str):
        for part in (path.relative_to(root).as_posix().encode(), b"\0", path.read_bytes(), b"\0"):
            digest.update(part)
    return digest.hexdigest()[:16]


def batch_results(batch_dir: Path) -> list[dict[str, Any]]:
    return load_results(batch_dir)


def git_log(since: str | None, paths: list[str]) -> list[str]:
    """One line per commit after ``since`` that touched ``paths`` (empty when ``since`` is unknown)."""
    if not since:
        return []
    completed = subprocess.run(
        ["git", "log", "--oneline", f"{since}..HEAD", "--", *paths],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return completed.stdout.splitlines() if completed.returncode == 0 else []
