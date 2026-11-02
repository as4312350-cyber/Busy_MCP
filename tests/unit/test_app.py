"""In-process tests of every MCP tool (fast, and counted by coverage)."""

from __future__ import annotations

import json
from typing import Any

import anyio
import pytest

from busy_mcp.server.app import build_server
from busy_mcp.server.cli import main


@pytest.fixture()
def server(settings):
    mcp, state = build_server(settings)
    yield mcp
    state.close()


def call(mcp: Any, name: str, args: dict[str, Any] | None = None) -> Any:
    async def run() -> Any:
        res = await mcp.call_tool(name, args or {})
        if isinstance(res, tuple):  # SDK 1.x returns (content, structured)
            res = res[0]
        text = res[0].text if isinstance(res, list) else res.content[0].text  # 1.x list / 2.x CallToolResult
        return json.loads(text) if text.strip()[:1] in "[{" else text

    return anyio.run(run)


ALL_TOOLS: list[tuple[str, dict[str, Any]]] = [
    ("server-info", {}), ("list-companies", {}), ("select-company", {"fy": 2024}),
    ("company-overview", {}), ("schema-guide", {}), ("list-tables", {}),
    ("describe-table", {"table": "Master1"}), ("query-database", {"sql": "SELECT Code FROM Master1"}),
    ("master-type-summary", {}), ("voucher-types", {}), ("search-masters", {"text": "bank"}),
    ("list-masters", {"kind": "item"}), ("chart-of-accounts", {"include_ledgers": True}),
    ("ledger-details", {"ledger": "Cash"}), ("ledger-balance", {"ledger": "Cash"}),
    ("ledger-statement", {"ledger": "Cash"}), ("trial-balance", {}), ("group-summary", {}),
    ("profit-and-loss", {}), ("balance-sheet", {}), ("outstanding", {"nature": "receivable"}),
    ("day-book", {}), ("voucher-details", {"vch_code": 2}),
    ("sales-purchase-analysis", {"kind": "sales", "group_by": "month"}),
    ("stock-summary", {}), ("stock-item-movement", {"item": "Widget A"}), ("refresh-cache", {}),
]


@pytest.mark.parametrize(("name", "args"), ALL_TOOLS, ids=[t[0] for t in ALL_TOOLS])
def test_every_tool_runs(server, name, args):
    assert call(server, name, args)


def test_tool_inventory_matches(server):
    async def names() -> set[str]:
        return {t.name for t in await server.list_tools()}

    listed = anyio.run(names)
    assert listed == {n for n, _ in ALL_TOOLS}


def test_query_database_caps_rows(server):
    out = call(server, "query-database", {"sql": "SELECT Code FROM Master1", "max_rows": 5})
    assert out["row_count"] == 5 and out["truncated"] is True


def test_prompts_render(server):
    async def run() -> list[str]:
        prompts = await server.list_prompts()
        texts = []
        for p in prompts:
            got = await server.get_prompt(p.name, {})
            texts.append(got.messages[0].content.text)
        return texts

    texts = anyio.run(run)
    assert len(texts) == 3 and all("BUSY" in t or "outstanding" in t for t in texts)


def test_cli_check(monkeypatch, db_path, capsys):
    monkeypatch.setenv("BUSY_BACKEND", "sqlite")
    monkeypatch.setenv("BUSY_DATABASE", db_path)
    monkeypatch.setenv("BUSY_FY", "2024")
    with pytest.raises(SystemExit) as ei:
        main(["--check"])
    assert ei.value.code == 0
    out = capsys.readouterr().out
    assert "OK" in out and "Demo Traders" in out and "difference: 0.00" in out


def test_cli_check_reports_bad_config(monkeypatch, capsys):
    monkeypatch.setenv("BUSY_BACKEND", "access")
    monkeypatch.setenv("BUSY_DATA_DIR", "/nope")
    with pytest.raises(SystemExit) as ei:
        main(["--check"])
    assert ei.value.code == 1
    assert "BUSY_DATA_DIR" in capsys.readouterr().err
