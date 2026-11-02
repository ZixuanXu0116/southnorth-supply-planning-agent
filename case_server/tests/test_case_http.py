"""The case server's planner profile over Streamable HTTP (decision record 010): a real HTTP server in this
process, the role and the run taken from the harness's headers behind the service token."""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time

import httpx2
import pytest
import uvicorn
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from test_case_service import CASE, Clock, FakePlanning, case_row

from snm_cases.server import build_planner, http_planner_actor
from snm_cases.service import CaseService
from snm_cases.store import LocalCaseStore
from snm_tools.http import MIN_TOKEN_CHARS, http_app

TOKEN = "c" * MIN_TOKEN_CHARS


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def served(tmp_path):
    service = CaseService(LocalCaseStore(tmp_path / "store"), FakePlanning([case_row()]), clock=Clock())
    service.sync_engine_run("plan-1")
    app = http_app(build_planner(service, http_planner_actor(frozenset({"purchasing"}))), TOKEN)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}", service
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def _session(url: str, headers: dict[str, str], tool: str, arguments: dict) -> tuple[bool, str]:
    async def run() -> tuple[bool, str]:
        async with (
            httpx2.AsyncClient(headers={"Authorization": f"Bearer {TOKEN}", **headers}, timeout=30) as client,
            streamable_http_client(f"{url}/mcp", http_client=client) as (read, write),
            ClientSession(read, write) as mcp,
        ):
            await mcp.initialize()
            result = await mcp.call_tool(tool, arguments)
            return bool(result.is_error), result.content[0].text

    return asyncio.run(run())


def test_the_planner_writes_as_the_role_and_run_the_harness_names(served):
    url, service = served
    error, text = _session(
        url,
        {"X-SNM-Role": "purchasing", "X-SNM-Run-Id": "run-http-7"},
        "add_note",
        {"case_id": CASE, "text": "Prepared over HTTP for purchasing to review."},
    )
    assert not error and json.loads(text)["recorded"] == "note_added"
    event = service.store.events(CASE)[-1]
    assert (event.actor_kind, event.actor_role, event.run_id) == ("planner_agent", "purchasing", "run-http-7")


def test_a_role_outside_the_allowed_ones_is_refused(served):
    url, _service = served
    error, text = _session(url, {"X-SNM-Role": "trade_compliance"}, "get_case", {"case_id": CASE})
    assert error and "OUT_OF_SCOPE" in text
    error, text = _session(url, {}, "get_case", {"case_id": CASE})  # no role named at all
    assert error and "OUT_OF_SCOPE" in text
