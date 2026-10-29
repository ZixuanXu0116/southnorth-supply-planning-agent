"""The local question page: ask the data agent, follow up, read the provenance and cost, flag wrong answers.

A thin layer over the harness CLI: the agent, its budgets, its gate, and the tool server's guards decide
what is answered, not the page. A thread keeps one role and one as-of date; a follow-up is answered in one
session with the thread's earlier questions asked first (``ask.js --earlier``), so it costs the earlier
questions again. Threads are JSON files under ``<runs>/threads/``; flagged answers are also appended to
``<runs>/flags.jsonl`` so they can become failure cards (``docs/PROMPT_CHANGES.md``).
"""

from __future__ import annotations

import json
import re
import secrets
import threading
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from . import markdown

TEMPLATES = Path(__file__).parent / "templates"
# Roles with a provisioned service principal and data to ask about (DATA_AGENT.md, state on 2026-10-19).
ROLES = {
    "ops_analyst_global": "Global operations analyst (all sites)",
    "site_manager_rr": "Round Rock site manager (US10 only)",
    "site_manager_cn": "China site manager (Longgang and Dongguan only)",
}
DEFAULT_AS_OF = "2026-09-21"  # the extraction date of the generated data (bank_v8)
MAX_QUESTIONS_PER_THREAD = 4  # a follow-up re-asks every earlier question, so the cost grows with each one
MAX_QUESTION_CHARS = 2000
THREAD_ID = re.compile(r"^[0-9]{8}T[0-9]{6}-[0-9a-f]{6}$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# (question, earlier questions, role, as_of, runs dir) -> the harness run result
AgentRunner = Callable[[str, list[str], str, str, Path], dict[str, Any]]


@dataclass
class AskSettings:
    runs_dir: Path = Path("runs/ask")
    max_usd_per_day: float = 3.0


def _now() -> datetime:
    return datetime.now(UTC)


class ThreadStore:
    def __init__(self, runs_dir: Path) -> None:
        self.dir = runs_dir / "threads"
        self.flags = runs_dir / "flags.jsonl"
        self.dir.mkdir(parents=True, exist_ok=True)

    def new_id(self) -> str:
        return f"{_now().strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(3)}"

    def path(self, thread_id: str) -> Path:
        if not THREAD_ID.match(thread_id):
            raise HTTPException(404, "no such thread")
        return self.dir / f"{thread_id}.json"

    def get(self, thread_id: str) -> dict[str, Any]:
        path = self.path(thread_id)
        if not path.exists():
            raise HTTPException(404, "no such thread")
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, thread: dict[str, Any]) -> None:
        path = self.path(thread["id"])
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(thread, indent=1, default=str), encoding="utf-8")
        temporary.replace(path)

    def all(self) -> list[dict[str, Any]]:
        threads = [json.loads(path.read_text(encoding="utf-8")) for path in self.dir.glob("*.json")]
        return sorted(threads, key=lambda thread: thread["created_at"], reverse=True)

    def spent_today(self) -> float:
        today = _now().date().isoformat()
        return sum(
            turn["cost_usd"]
            for thread in self.all()
            for turn in thread["turns"]
            if turn["asked_at"].startswith(today)
        )

    def flag(self, thread: dict[str, Any], index: int, note: str) -> None:
        turn = thread["turns"][index]
        turn["flag"] = {"note": note, "at": _now().isoformat(timespec="seconds")}
        self.save(thread)
        entry = {
            "at": turn["flag"]["at"], "thread": thread["id"], "turn": index, "role": thread["role"],
            "as_of": thread["as_of"], "question": turn["question"],
            "earlier": [earlier["question"] for earlier in thread["turns"][:index]],
            "run_id": turn["result"].get("run_id"), "note": note,
        }  # fmt: skip
        with self.flags.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")


def _redirect(url: str, error: str | None = None) -> RedirectResponse:
    target = f"{url}?{urllib.parse.urlencode({'error': error})}" if error else url
    return RedirectResponse(target, status_code=303)


def turn_cost(result: dict[str, Any]) -> float:
    return float((result.get("usage") or {}).get("cost_usd") or 0.0)


def create_app(
    settings: AskSettings, runner: AgentRunner, environment: Callable[[str], list[str]]
) -> FastAPI:
    """``environment(role)`` names the variables a run as that role still needs (empty when ready)."""
    app = FastAPI(title="SouthNorth Solutions data agent", docs_url=None, redoc_url=None, openapi_url=None)
    templates = Jinja2Templates(directory=str(TEMPLATES))
    templates.env.filters["markdown"] = markdown.render
    store = ThreadStore(settings.runs_dir)
    busy = threading.Lock()  # one question at a time: a double submit would pay twice

    def render(request: Request, name: str, **context: Any) -> HTMLResponse:
        return templates.TemplateResponse(
            request, name,
            {"error": request.query_params.get("error"), "spent_today": store.spent_today(),
             "max_usd_per_day": settings.max_usd_per_day, **context},
        )  # fmt: skip

    def ask(thread: dict[str, Any], question: str) -> str | None:
        """Run one question of ``thread``; returns an error to show, or None when the turn was recorded."""
        question = question.strip()
        if not question:
            return "Type a question."
        if len(question) > MAX_QUESTION_CHARS:
            return f"A question has at most {MAX_QUESTION_CHARS} characters."
        if len(thread["turns"]) >= MAX_QUESTIONS_PER_THREAD:
            return f"A thread has at most {MAX_QUESTIONS_PER_THREAD} questions; start a new one."
        spent = store.spent_today()
        if spent >= settings.max_usd_per_day:
            return (
                f"Today's spend is USD {spent:.2f}, at the page's limit of "
                f"USD {settings.max_usd_per_day:.2f} (snm-ask serve --max-usd-per-day)."
            )
        missing = environment(thread["role"])
        if missing:
            return f"{', '.join(missing)} not set; see apps/ask/README.md."
        if not busy.acquire(blocking=False):
            return "The agent is still answering another question; wait for it to finish."
        try:
            earlier = [turn["question"] for turn in thread["turns"]]
            asked_at = _now().isoformat(timespec="seconds")
            result = runner(question, earlier, thread["role"], thread["as_of"], settings.runs_dir)
        finally:
            busy.release()
        if result.get("status") == "error" and not turn_cost(result):
            return f"The agent did not run: {result.get('error') or 'unknown error'}"
        thread["turns"].append(
            {"question": question, "asked_at": asked_at, "cost_usd": turn_cost(result), "result": result}
        )
        store.save(thread)
        return None

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        role = request.query_params.get("role", "ops_analyst_global")
        return render(
            request, "index.html", roles=ROLES, role=role if role in ROLES else "ops_analyst_global",
            as_of=DEFAULT_AS_OF, threads=store.all()[:30], missing=environment(role if role in ROLES else ""),
        )  # fmt: skip

    @app.post("/ask")
    def new_thread(
        question: str = Form(""), role: str = Form(...), as_of: str = Form(...)
    ) -> RedirectResponse:
        if role not in ROLES:
            return _redirect("/", "Unknown role.")
        if not DATE.match(as_of):
            return _redirect("/", "The as-of date is YYYY-MM-DD.")
        if not question.strip():
            return _redirect("/", "Type a question.")
        thread = {
            "id": store.new_id(), "created_at": _now().isoformat(timespec="seconds"), "role": role,
            "as_of": as_of, "turns": [],
        }  # fmt: skip
        error = ask(thread, question)
        if error:
            return _redirect("/", error)
        return _redirect(f"/thread/{thread['id']}")

    @app.get("/thread/{thread_id}", response_class=HTMLResponse)
    def show_thread(request: Request, thread_id: str) -> HTMLResponse:
        thread = store.get(thread_id)
        return render(
            request, "thread.html", thread=thread, role_label=ROLES.get(thread["role"], thread["role"]),
            can_follow_up=len(thread["turns"]) < MAX_QUESTIONS_PER_THREAD,
            max_questions=MAX_QUESTIONS_PER_THREAD,
        )  # fmt: skip

    @app.post("/thread/{thread_id}/ask")
    def follow_up(thread_id: str, question: str = Form("")) -> RedirectResponse:
        thread = store.get(thread_id)
        error = ask(thread, question)
        return _redirect(f"/thread/{thread_id}", error)

    @app.post("/thread/{thread_id}/turn/{index}/flag")
    def flag(thread_id: str, index: int, note: str = Form("")) -> RedirectResponse:
        thread = store.get(thread_id)
        if not 0 <= index < len(thread["turns"]):
            raise HTTPException(404, "no such question")
        if not note.strip():
            return _redirect(f"/thread/{thread_id}", "Say briefly what is wrong.")
        store.flag(thread, index, note.strip())
        return _redirect(f"/thread/{thread_id}#q{index}")

    @app.get("/thread/{thread_id}/export")
    def export(thread_id: str) -> JSONResponse:
        thread = store.get(thread_id)
        return JSONResponse(
            thread, headers={"Content-Disposition": f'attachment; filename="ask-{thread_id}.json"'}
        )

    return app
