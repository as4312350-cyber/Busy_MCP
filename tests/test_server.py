"""End-to-end: start the MCP server over stdio in a subprocess and call tools
exactly as Claude Desktop would."""

import json
import os
import sys

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .fixture import build


def _payload(result):
    assert not getattr(result, "is_error", getattr(result, "isError", False)), result
    text = result.content[0].text
    return json.loads(text) if text.strip()[:1] in "[{" else text


@pytest.fixture(scope="module")
def db_path(tmp_path_factory):
    return build(str(tmp_path_factory.mktemp("busy") / "db12024.sqlite"))


def test_stdio_roundtrip(db_path):
    async def run():
        env = dict(os.environ, BUSY_BACKEND="sqlite", BUSY_DATABASE=db_path, BUSY_FY="2024")
        params = StdioServerParameters(command=sys.executable, args=["-m", "busy_mcp"], env=env)
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = {t.name: t for t in (await s.list_tools()).tools}
                assert len(tools) >= 25
                assert all(getattr(t.annotations, "read_only_hint", None) or getattr(t.annotations, "readOnlyHint", None) for t in tools.values())
                assert not any(k in name for name in tools for k in ("create", "delete", "update", "write"))

                tb = _payload(await s.call_tool("trial-balance", {"ledger_level": False}))
                assert tb["totals"]["net_difference"] == "0.00"

                st = _payload(await s.call_tool("ledger-statement", {"ledger": "Sharma Traders"}))
                assert st["closing_balance"] == "2,620.00 Dr"

                q = _payload(await s.call_tool("query-database", {"sql": "SELECT Name FROM Master1 WHERE MasterType = 6"}))
                assert q["row_count"] == 2

                bad = _payload(await s.call_tool("query-database", {"sql": "DELETE FROM Master1"}))
                assert "not allowed" in bad["error"] or "Only SELECT" in bad["error"]

                missing = _payload(await s.call_tool("ledger-balance", {"ledger": "nope"}))
                assert "error" in missing

                desc = _payload(await s.call_tool("describe-table", {"table": "tran1"}))
                assert desc["table"] == "Tran1" and len(desc["sample_rows"]) == 3

                guide = _payload(await s.call_tool("schema-guide", {}))
                assert "Tran2" in str(guide)

    anyio.run(run)


def test_database_file_is_never_modified(db_path):
    before = os.path.getmtime(db_path), os.path.getsize(db_path)
    test_stdio_roundtrip(db_path)
    assert (os.path.getmtime(db_path), os.path.getsize(db_path)) == before
