"""Builds the MCP server: tools (server/tools), prompts and resources."""

from __future__ import annotations

from typing import Any

from .. import __version__
from ..config import Settings, load_settings
from ..schema import SCHEMA_GUIDE
from ._compat import create_server
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
    mcp = create_server("busy-accounting", INSTRUCTIONS, __version__)
    register_all(Registry(mcp, state))

    @mcp.resource("busy://schema-guide", name="BUSY schema guide", mime_type="text/markdown")
    def schema_resource() -> str:
        return SCHEMA_GUIDE

    register_prompts(mcp)
    return mcp, state
