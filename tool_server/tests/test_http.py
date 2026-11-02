"""The tool server over Streamable HTTP (decision record 010): a real server process, the service token, the
health check, and the run the harness names in a header reaching the audit log. No Databricks access: the
calls used here are answered from the contracts or refused by the SQL guard before any query."""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx2
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from snm_tools.http import MIN_TOKEN_CHARS, ServiceToken

TOKEN = "t" * MIN_TOKEN_CHARS


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _plain_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 204, "headers": []})
    await send({"type": "http.response.body", "body": b""})


def test_the_service_token_guards_every_path_but_health():
    async def call(path: str, headers: dict[str, str]) -> int:
        transport = httpx2.ASGITransport(app=ServiceToken(_plain_app, TOKEN))
        async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
            return (await client.get(path, headers=headers)).status_code

    assert asyncio.run(call("/health", {})) == 200
    assert asyncio.run(call("/mcp", {})) == 401
    assert asyncio.run(call("/mcp", {"Authorization": "Bearer wrong"})) == 401
    assert asyncio.run(call("/mcp", {"Authorization": f"Bearer {TOKEN}"})) == 204


def test_http_refuses_to_start_without_a_long_token():
    env = {**os.environ, "SNM_MCP_TRANSPORT": "http", "SNM_SERVICE_TOKEN": "short"}
    done = subprocess.run(
        [sys.executable, "-m", "snm_tools.server"], env=env, capture_output=True, text=True, timeout=60
    )
    assert done.returncode != 0 and "SNM_SERVICE_TOKEN" in done.stderr


@pytest.fixture
def http_server(tmp_path):
    port = _free_port()
    env = {
        **os.environ,
        "SNM_MCP_TRANSPORT": "http",
        "SNM_MCP_PORT": str(port),
        "SNM_SERVICE_TOKEN": TOKEN,
        "SNM_AUDIT_DIR": str(tmp_path / "audit"),
        "SNM_ROLE": "ops_analyst_global",
    }
    process = subprocess.Popen([sys.executable, "-m", "snm_tools.server"], env=env)
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                if httpx2.get(f"{url}/health", timeout=1).status_code == 200:
                    break
            except httpx2.HTTPError:
                time.sleep(0.2)
        else:
            pytest.fail("the tool server did not start")
        yield url, tmp_path / "audit"
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_the_tool_server_answers_over_http_and_audits_the_named_run(http_server):
    url, audit_dir = http_server
    headers = {"Authorization": f"Bearer {TOKEN}", "X-SNM-Run-Id": "run-http-1"}

    async def session() -> tuple[set[str], str]:
        async with (
            httpx2.AsyncClient(headers=headers, timeout=30) as client,
            streamable_http_client(f"{url}/mcp", http_client=client) as (read, write),
            ClientSession(read, write) as mcp,
        ):
            await mcp.initialize()
            names = {tool.name for tool in (await mcp.list_tools()).tools}
            refused = await mcp.call_tool("run_sql", {"sql": "DELETE FROM raw_sap_ecc.likp"})
            return names, refused.content[0].text

    names, refused = asyncio.run(session())
    assert {"list_tables", "describe_table", "run_sql", "get_result"} <= names
    assert "NOT_READ_ONLY" in refused
    records = [
        json.loads(line) for path in Path(audit_dir).glob("*.jsonl") for line in path.read_text().splitlines()
    ]
    assert any(r.get("run_id") == "run-http-1" and r["allowed"] is False for r in records)
