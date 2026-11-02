"""End-to-end: launch the real server over stdio and call it like an MCP client."""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _is_error(result: Any) -> bool:
    return bool(getattr(result, "is_error", getattr(result, "isError", False)))


def _payload(result: Any) -> Any:
    assert not _is_error(result), result
    text = result.content[0].text
    return json.loads(text) if text.strip()[:1] in "[{" else text


def _hint(tool: Any, name: str) -> Any:
    snake = "".join("_" + c.lower() if c.isupper() else c for c in name)
    return getattr(tool.annotations, snake, None) if hasattr(tool.annotations, snake) else getattr(
        tool.annotations, name, None)


async def _session_run(db_path: str, body: Any) -> None:
    env = dict(os.environ, BUSY_BACKEND="sqlite", BUSY_DATABASE=db_path, BUSY_FY="2024")
    params = StdioServerParameters(command=sys.executable, args=["-m", "busy_mcp"], env=env)
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        await body(s)


def test_tools_are_all_read_only(db_path):
    async def body(s: ClientSession) -> None:
        tools = {t.name: t for t in (await s.list_tools()).tools}
        assert len(tools) == 27
        for t in tools.values():
            assert _hint(t, "readOnlyHint") is True
            assert _hint(t, "destructiveHint") is False
        assert not any(k in n for n in tools for k in ("create", "delete", "update", "write", "insert"))
        prompts = {p.name for p in (await s.list_prompts()).prompts}
        assert prompts == {"financial-health-check", "receivables-follow-up", "month-end-review"}

    anyio.run(_session_run, db_path, body)


def test_reports_over_stdio(db_path):
    async def body(s: ClientSession) -> None:
        tb = _payload(await s.call_tool("trial-balance", {"ledger_level": False}))
        assert tb["totals"]["net_difference"] == "0.00"
        st = _payload(await s.call_tool("ledger-statement", {"ledger": "Sharma Traders", "limit": 2}))
        assert st["closing_balance"] == "2,620.00 Dr" and st["has_more"] is True
        info = _payload(await s.call_tool("server-info", {}))
        assert info["settings"]["access_password"] == "***"
        q = _payload(await s.call_tool("query-database", {"sql": "SELECT Name FROM Master1 WHERE MasterType = 6"}))
        assert q["row_count"] == 2
        desc = _payload(await s.call_tool("describe-table", {"table": "tran1"}))
        assert desc["table"] == "Tran1" and len(desc["sample_rows"]) == 3
        assert "Tran2" in str(_payload(await s.call_tool("schema-guide", {})))

    anyio.run(_session_run, db_path, body)


def test_errors_are_reported_as_tool_errors(db_path):
    async def body(s: ClientSession) -> None:
        bad = await s.call_tool("query-database", {"sql": "DELETE FROM Master1"})
        assert _is_error(bad) and "Only SELECT" in bad.content[0].text
        missing = await s.call_tool("ledger-balance", {"ledger": "nope"})
        assert _is_error(missing) and "No Account" in missing.content[0].text
        enum = await s.call_tool("outstanding", {"nature": "sideways"})
        assert _is_error(enum)
        inject = await s.call_tool("describe-table", {"table": "Tran1]; DROP TABLE x;--"})
        assert _is_error(inject)

    anyio.run(_session_run, db_path, body)


def test_database_file_is_never_modified(db_path):
    before = (os.path.getmtime(db_path), os.path.getsize(db_path))
    test_reports_over_stdio(db_path)
    test_errors_are_reported_as_tool_errors(db_path)
    assert (os.path.getmtime(db_path), os.path.getsize(db_path)) == before
