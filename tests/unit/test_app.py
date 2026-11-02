"""In-process tests of every MCP tool (fast, and counted by coverage)."""

from __future__ import annotations

import json
from typing import Any

import anyio
import pytest

from busy_mcp.server.app import build_server


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
        if getattr(res, "is_error", getattr(res, "isError", False)):
            raise RuntimeError(text)
        return json.loads(text) if text.strip()[:1] in "[{" else text

    return anyio.run(run)


ALL_TOOLS: list[tuple[str, dict[str, Any]]] = [
    # overview
    ("dashboard", {}), ("server-info", {}), ("list-companies", {}), ("select-company", {"fy": 2024}),
    ("company-overview", {}), ("voucher-types", {}), ("master-type-summary", {}), ("schema-guide", {}),
    ("refresh-cache", {}),
    # explore
    ("catalog", {}), ("read-table", {"table": "Tran1", "limit": 2}), ("find", {"text": "Sharma"}),
    ("master-details", {"name_or_code": "Widget A"}), ("list-tables", {}), ("describe-table", {"table": "Master1"}),
    ("query-database", {"sql": "SELECT Code FROM Master1"}),
    # accounts
    ("search-masters", {"text": "bank"}), ("list-masters", {"kind": "item"}),
    ("chart-of-accounts", {"include_ledgers": True}), ("ledger-details", {"ledger": "Cash"}),
    ("ledger-balance", {"ledger": "Cash"}), ("ledger-statement", {"ledger": "Cash"}),
    ("ledger-monthly", {"account_or_group": "Sale"}), ("trial-balance", {}), ("group-summary", {}),
    ("profit-and-loss", {}), ("balance-sheet", {}), ("cash-bank-summary", {}),
    # receivables & banking
    ("outstanding", {"nature": "receivable"}), ("bills-outstanding", {}),
    ("bank-reconciliation", {"bank": "HDFC Bank"}), ("post-dated-cheques", {"as_of": "2025-03-31"}),
    # vouchers
    ("day-book", {}), ("voucher-details", {"vch_code": 2}),
    ("sales-purchase-analysis", {"kind": "sales", "group_by": "month"}), ("pending-orders", {}),
    ("audit-trail", {}),
    # inventory
    ("stock-summary", {}), ("stock-item-movement", {"item": "Widget A"}), ("stock-by-location", {}),
    ("batch-stock", {}), ("serial-numbers", {}),
    # taxes
    ("gst-summary", {}), ("tds-register", {}),
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


def test_sensitive_data_never_leaks(server):
    with pytest.raises(Exception, match="blocked"):
        call(server, "query-database", {"sql": "SELECT * FROM [Config]"})
    with pytest.raises(Exception, match="blocked"):
        call(server, "read-table", {"table": "Config"})
    described = call(server, "describe-table", {"table": "Config"})
    assert "hidden" in described["sample_rows"]
    info = call(server, "server-info", {})
    assert "super-secret-password" not in str(info) and info["settings"]["access_password"] == "***"


def test_read_table_tool_accepts_filters(server):
    out = call(server, "read-table", {"table": "Tran2", "filters": [
        {"column": "MasterCode1", "op": "eq", "value": "Sharma Traders"}], "columns": ["VchCode", "Value1"]})
    assert out["rows"][0]["vchcode"] == 2


def test_prompts_render(server):
    async def run() -> list[str]:
        prompts = await server.list_prompts()
        texts = []
        for p in prompts:
            got = await server.get_prompt(p.name, {})
            texts.append(got.messages[0].content.text)
        return texts

    texts = anyio.run(run)
    assert len(texts) == 5 and all(len(t) > 50 for t in texts)
