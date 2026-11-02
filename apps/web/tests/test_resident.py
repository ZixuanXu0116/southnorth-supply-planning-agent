"""The resident case server of a deployed app (snm_web/resident.py): started once, reached over HTTP with a
token made at start, started again when it has stopped. The data tool server stands in for the case server
(it needs no planning data); the mechanism is the same."""

import asyncio
import os
import socket

import httpx2
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from snm_web.resident import ResidentCaseServer


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _tools() -> set[str]:
    async def run() -> set[str]:
        headers = {"Authorization": f"Bearer {os.environ['SNM_CASE_SERVER_TOKEN']}"}
        async with (
            httpx2.AsyncClient(headers=headers, timeout=30) as client,
            streamable_http_client(os.environ["SNM_CASE_SERVER_URL"], http_client=client) as (read, write),
            ClientSession(read, write) as mcp,
        ):
            await mcp.initialize()
            return {tool.name for tool in (await mcp.list_tools()).tools}

    return asyncio.run(run())


def test_the_resident_server_starts_once_and_again_after_it_stops(monkeypatch):
    monkeypatch.delenv("SNM_CASE_SERVER_URL", raising=False)
    monkeypatch.delenv("SNM_CASE_SERVER_TOKEN", raising=False)
    monkeypatch.setenv("SNM_ANTHROPIC_API_KEY", "not-for-the-server")
    server = ResidentCaseServer(_free_port(), module="snm_tools.server")
    try:
        server.start()
        first_token = os.environ["SNM_CASE_SERVER_TOKEN"]
        assert os.environ["SNM_CASE_SERVER_URL"] == server.url and len(first_token) >= 32
        assert "run_sql" in _tools()
        pid = server.process.pid
        server.ensure()  # still running: nothing happens
        assert server.process.pid == pid
        server.process.kill()
        server.process.wait()
        server.ensure()  # stopped: started again, with a new token
        assert server.process.pid != pid and os.environ["SNM_CASE_SERVER_TOKEN"] != first_token
        assert "run_sql" in _tools()
    finally:
        server.stop()
