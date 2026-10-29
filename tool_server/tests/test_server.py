import asyncio
import json
import os

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from snm_data.governance import ROLES
from snm_tools.server import build_server, create_tools

EXPECTED_TOOLS = {
    "list_tables", "describe_table", "run_sql", "get_result", "list_metrics", "describe_metric",
    "query_metric", "profile_column", "sample_rows", "data_freshness",
}  # fmt: skip


def test_server_registers_all_tools() -> None:
    server = build_server(create_tools())
    names = {tool.name for tool in asyncio.run(server.list_tools())}
    assert names == EXPECTED_TOOLS


def test_tool_failures_surface_as_structured_errors() -> None:
    server = build_server(create_tools())
    with pytest.raises(ToolError) as error:
        asyncio.run(server.call_tool("run_sql", {"sql": "DELETE FROM raw_sap_ecc.likp"}))
    payload = json.loads(str(error.value).split(": ", 1)[1])
    assert payload["code"] == "NOT_READ_ONLY"


OPS = ROLES["ops_analyst_global"]


@pytest.mark.skipif(not os.environ.get(OPS.client_secret_env), reason="needs the agent role credentials")
def test_live_query_against_databricks(monkeypatch) -> None:
    # The tool server connects as the role's service principal, as the harness launches it.
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", os.environ[OPS.client_id_env])
    monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", os.environ[OPS.client_secret_env])
    tools = create_tools()
    response = tools.run_sql(
        "SELECT vstel, COUNT(*) AS deliveries FROM raw_sap_ecc.likp GROUP BY vstel ORDER BY vstel"
    )
    assert [row[0] for row in response["preview"]] == ["C100", "C300", "U100"]
    freshness = tools.data_freshness()
    assert len(freshness["ecc_last_entry_chicago_time"]) == 14
    sample = tools.sample_rows("raw_sap_ewm.scwm_ordim_c", n=3)
    assert "confirmed_by" not in sample["columns"] and len(sample["rows"]) == 3
