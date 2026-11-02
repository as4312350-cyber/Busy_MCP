"""Builds the MCP server: tools (server/tools), prompts and resources."""

from __future__ import annotations

from typing import Any

try:  # MCP Python SDK 2.x
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # MCP Python SDK 1.x
    from mcp.server.fastmcp import FastMCP as _Server  # type: ignore[no-redef]

from .. import __version__
from ..config import Settings, load_settings
from ..schema import SCHEMA_GUIDE
from .prompts import register_prompts
from .registry import Registry
from .state import State
from .tools import register_all

INSTRUCTIONS = """Read-only access to BUSY Accounting Software (Busy Infotech, India).
Each BUSY company + financial year (April-March) is a separate database.
For general questions start with `dashboard`; use `find` to locate anything by
name, phone, GSTIN, invoice number or serial number. Prefer the dedicated tools
(trial-balance, profit-and-loss, balance-sheet, ledger-statement,
bills-outstanding, stock-summary, gst-summary, voucher-details, ...). To reach
any other BUSY data use `catalog` + `read-table`; `query-database` is the last
resort. Dates are YYYY-MM-DD and default to the selected financial year;
amounts are in the company's base currency (normally INR); cancelled vouchers
are excluded. Lists are paginated: follow `next_offset` while `has_more`.
Always pass on `caveat`, `method`, `confidence` and `warning` fields."""


def build_server(settings: Settings | None = None) -> tuple[Any, State]:
    settings = settings or load_settings()
    state = State(settings)
    try:
        mcp = _Server(name="busy-accounting", instructions=INSTRUCTIONS, version=__version__)
    except TypeError:  # SDK 1.x has no version argument
        mcp = _Server(name="busy-accounting", instructions=INSTRUCTIONS)
    register_all(Registry(mcp, state))

    @mcp.resource("busy://schema-guide", name="BUSY schema guide", mime_type="text/markdown")
    def schema_resource() -> str:
        return SCHEMA_GUIDE

    register_prompts(mcp)
    return mcp, state
