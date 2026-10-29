"""Consistency checks for the agent-facing knowledge base in ``kb/``."""

import re
from pathlib import Path

import yaml

KB = Path(__file__).resolve().parents[2] / "kb"
REQUIRED = {"id", "title", "type", "aliases", "status", "last_reviewed"}
NON_ENGLISH = re.compile(r"[　-〿一-鿿＀-￯]")


def _documents() -> list[Path]:
    return sorted(path for path in KB.rglob("*.md") if path.name != "INDEX.md")


def _front_matter(path: Path) -> dict:
    match = re.match(r"^---\n(.*?)\n---\n", path.read_text(encoding="utf-8"), re.S)
    assert match, f"{path} has no front matter"
    return yaml.safe_load(match.group(1))


def test_documents_have_complete_front_matter() -> None:
    for path in _documents():
        missing = REQUIRED - set(_front_matter(path))
        assert not missing, f"{path}: missing {sorted(missing)}"


def test_ids_are_unique_and_indexed() -> None:
    ids = [_front_matter(path)["id"] for path in _documents()]
    assert len(ids) == len(set(ids))
    index = (KB / "INDEX.md").read_text(encoding="utf-8")
    for doc_id in ids:
        assert f"| {doc_id} |" in index, f"{doc_id} missing from INDEX.md"


def test_index_links_resolve() -> None:
    index = (KB / "INDEX.md").read_text(encoding="utf-8")
    for link in re.findall(r"\]\(([^)]+\.md)\)", index):
        assert (KB / link).exists(), link


def test_kb_is_english_only() -> None:
    for path in [*_documents(), KB / "INDEX.md"]:
        assert not NON_ENGLISH.search(path.read_text(encoding="utf-8")), path
