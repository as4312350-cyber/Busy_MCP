"""MCP server definition: every tool, prompt and resource."""

from __future__ import annotations

import functools
import json
import logging
import time
from collections.abc import Callable
from typing import Annotated, Any, Literal

import anyio
from pydantic import Field

try:  # MCP Python SDK 2.x
    from mcp.server.mcpserver import MCPServer as _Server
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # MCP Python SDK 1.x
    from mcp.server.fastmcp import FastMCP as _Server  # type: ignore[no-redef]
    from mcp.server.fastmcp.exceptions import ToolError  # type: ignore[no-redef]
from mcp.types import ToolAnnotations

from .. import __version__, reports
from ..config import Settings
from ..errors import BusyMCPError
from ..schema import SCHEMA_GUIDE
from ..sqlguard import validate_select
from .prompts import register_prompts
from .state import State

log = logging.getLogger("busy_mcp.server")
audit = logging.getLogger("busy_mcp.audit")

INSTRUCTIONS = """Read-only access to BUSY Accounting Software (Busy Infotech, India).
Each BUSY company + financial year (April-March) is a separate database.
Start with `list-companies` / `company-overview`. Prefer the report tools
(trial-balance, profit-and-loss, balance-sheet, ledger-statement, outstanding,
stock-summary, sales-purchase-analysis, day-book, voucher-details) over
`query-database`; read `schema-guide` before writing SQL.
Dates are YYYY-MM-DD and default to the selected financial year. Amounts are in
the company's base currency (normally INR). Cancelled vouchers are excluded.
Long lists are paginated: follow `next_offset` when `has_more` is true.
Reports carry `caveat`/`method`/`warning` fields - always pass those on to the user."""

# Parameter types (module level so SDK type-hint evaluation can resolve them)
Company = Annotated[str | None, Field(description="Company code e.g. COMP0001 (default: selected company)")]
FY = Annotated[int | None, Field(ge=1990, le=2100,
                                 description="FY start year, e.g. 2024 = FY 2024-25 (default: selected)")]
FromDate = Annotated[str | None, Field(description="Start date YYYY-MM-DD (default: FY start)")]
ToDate = Annotated[str | None, Field(description="End date YYYY-MM-DD (default: FY end)")]
AsOf = Annotated[str | None, Field(description="As-on date YYYY-MM-DD (default: FY end)")]
Offset = Annotated[int, Field(ge=0, description="Pagination offset (use next_offset from the previous page)")]
Limit = Annotated[int, Field(ge=1, le=1000, description="Page size")]
LedgerRef = Annotated[str, Field(min_length=1, max_length=200, description="Account name, alias or Code")]
ItemRef = Annotated[str, Field(min_length=1, max_length=200, description="Item name, alias or Code")]
VchTypeList = Annotated[list[int] | None, Field(description="Override voucher type codes, e.g. [9]")]
MasterKind = Literal["group", "ledger", "item_group", "item"]


def _json_safe(obj: Any) -> Any:
    return json.loads(json.dumps(obj, default=str))


def build_server(settings: Settings | None = None) -> tuple[Any, State]:
    settings = settings or Settings.from_env()
    state = State(settings)
    try:
        mcp = _Server(name="busy-accounting", instructions=INSTRUCTIONS, version=__version__)
    except TypeError:  # SDK 1.x has no version argument
        mcp = _Server(name="busy-accounting", instructions=INSTRUCTIONS)

    def tool(name: str, title: str, description: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Register a read-only tool. The body runs in a worker thread; known
        errors become clean tool errors, unknown ones are logged with a trace."""

        def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
            @functools.wraps(fn)
            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                started = time.perf_counter()
                try:
                    result = await anyio.to_thread.run_sync(functools.partial(fn, *args, **kwargs))
                except BusyMCPError as exc:
                    log.info("tool %s rejected: %s", name, exc)
                    raise ToolError(str(exc)) from None
                except Exception as exc:
                    log.exception("tool %s failed", name)
                    raise ToolError(f"{type(exc).__name__}: {exc}") from None
                log.debug("tool %s ok in %.0f ms", name, (time.perf_counter() - started) * 1000)
                return _json_safe(result)

            annotations = ToolAnnotations(title=title, readOnlyHint=True, destructiveHint=False,
                                          idempotentHint=True, openWorldHint=False)
            mcp.tool(name=name, description=description, annotations=annotations)(wrapper)
            return fn

        return deco

    # ------------------------------------------------------------ discovery
    @tool("server-info", "Server info", "Server version, selected database, detected schema capabilities "
                                        "(cancelled-voucher handling, narration source, stock sign) and settings.")
    def server_info(company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.server_info(state.db(company, fy))

    @tool("list-companies", "List companies", "Every BUSY company and financial-year database found, "
                                              "and which one is selected.")
    def list_companies() -> dict[str, Any]:
        targets = state.targets(refresh=True)
        return {"selected": state.db().target.describe(), "available": [t.describe() for t in targets]}

    @tool("select-company", "Select company", "Switch the default company and/or financial year for all tools.")
    def select_company(company: Company = None, fy: FY = None) -> dict[str, Any]:
        t = state.select(company, fy)
        return {"selected": t.describe(), "overview": reports.company_overview(state.db())}

    @tool("company-overview", "Company overview", "Company name/details, master counts and voucher date range.")
    def company_overview(company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.company_overview(state.db(company, fy))

    @tool("schema-guide", "Schema guide", "How BUSY stores data: tables, type codes, sign conventions, SQL "
                                          "dialect tips. Read before using query-database.")
    def schema_guide() -> str:
        return SCHEMA_GUIDE

    @tool("list-tables", "List tables", "All tables in the selected BUSY database.")
    def list_tables(company: Company = None, fy: FY = None) -> dict[str, Any]:
        names = state.db(company, fy).table_names()
        return {"count": len(names), "tables": names}

    @tool("describe-table", "Describe table", "Columns and types of one table plus 3 sample rows.")
    def describe_table(table: Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_]+$")],
                       company: Company = None, fy: FY = None) -> dict[str, Any]:
        db = state.db(company, fy)
        real = db.resolve_table(table)
        sample = db.query(f"SELECT {db.top(3)}* FROM [{real}]{db.limit(3)}")
        return {"table": real, "columns": db.columns(real), "sample_rows": sample}

    @tool("query-database", "Run read-only SQL",
          "Run ONE read-only SELECT against the selected BUSY database (Access edition = Jet SQL, "
          "SQL edition = T-SQL). Writes, multiple statements and external references are rejected. "
          "Results are capped; exclude cancelled vouchers with (Cancelled = 0 OR Cancelled IS NULL).")
    def query_database(sql: Annotated[str, Field(min_length=6, max_length=20000, description="A single SELECT")],
                       max_rows: Annotated[int, Field(ge=1, le=10000)] = 200,
                       company: Company = None, fy: FY = None) -> dict[str, Any]:
        clean = validate_select(sql)
        db = state.db(company, fy)
        cap = min(max_rows, settings.max_rows)
        if settings.audit_queries:
            audit.info("query-database on %s: %s", db.target.key, " ".join(clean.split()))
        rows = db.query(clean, max_rows=cap + 1)
        return {"row_count": min(len(rows), cap), "truncated": len(rows) > cap, "rows": rows[:cap]}

    @tool("master-type-summary", "Master types", "Count of masters by MasterType with examples (reveals "
                                                 "units, series, taxes etc. with no dedicated tool).")
    def master_type_summary(company: Company = None, fy: FY = None) -> list[dict[str, Any]]:
        return reports.master_type_summary(state.db(company, fy))

    @tool("voucher-types", "Voucher types", "Voucher types used in this database: counts, date range, "
                                            "sample voucher numbers.")
    def voucher_types(company: Company = None, fy: FY = None) -> list[dict[str, Any]]:
        return reports.voucher_types(state.db(company, fy))

    # -------------------------------------------------------------- masters
    @tool("search-masters", "Search masters", "Find accounts, groups or items by part of name or alias.")
    def search_masters(text: Annotated[str, Field(min_length=1, max_length=200)],
                       kind: MasterKind | None = None, limit: Limit = 50,
                       company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.search_masters(state.db(company, fy), text, kind, limit)

    @tool("list-masters", "List masters", "List masters of one kind, optionally only those under a group.")
    def list_masters(kind: MasterKind, under_group: str | None = None, offset: Offset = 0, limit: Limit = 200,
                     company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.list_masters(state.db(company, fy), kind, under_group, offset, limit)

    @tool("chart-of-accounts", "Chart of accounts", "Account group tree (item group tree with item_groups=true).")
    def chart_of_accounts(include_ledgers: bool = False, item_groups: bool = False,
                          company: Company = None, fy: FY = None) -> list[dict[str, Any]]:
        return reports.chart_of_accounts(state.db(company, fy), include_ledgers, item_groups)

    @tool("ledger-details", "Account details", "Account master: group path, opening balance, address, GSTIN, "
                                               "PAN, phone, email.")
    def ledger_details(ledger: LedgerRef, company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.ledger_details(state.db(company, fy), ledger)

    # ------------------------------------------------------------- accounts
    @tool("ledger-balance", "Account balance", "Closing balance of one account as on a date.")
    def ledger_balance(ledger: LedgerRef, as_of: AsOf = None, company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.ledger_balance(state.db(company, fy), ledger, as_of)

    @tool("ledger-statement", "Account statement", "Account ledger: opening balance, each entry with contra "
                                                   "account, narration and running balance. Paginated.")
    def ledger_statement(ledger: LedgerRef, from_date: FromDate = None, to_date: ToDate = None,
                         offset: Offset = 0, limit: Limit = 200,
                         company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.ledger_statement(state.db(company, fy), ledger, from_date, to_date, offset, limit)

    @tool("trial-balance", "Trial balance", "Opening, debit, credit, closing per account and per primary group.")
    def trial_balance(from_date: FromDate = None, to_date: ToDate = None,
                      group: Annotated[str | None, Field(description="Only accounts under this group")] = None,
                      include_zero: bool = False,
                      ledger_level: Annotated[bool, Field(description="false = group totals only")] = True,
                      offset: Offset = 0, limit: Limit = 500,
                      company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.trial_balance(state.db(company, fy), from_date, to_date, group, include_zero,
                                     ledger_level, offset, limit)

    @tool("group-summary", "Group summary", "Balances of sub-groups/accounts directly under a group "
                                            "(omit group for primary groups).")
    def group_summary(group: str | None = None, from_date: FromDate = None, to_date: ToDate = None,
                      company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.group_summary(state.db(company, fy), group, from_date, to_date)

    @tool("profit-and-loss", "Profit & loss", "Income and expenses by head for a period (ledger based, before "
                                              "stock adjustment).")
    def profit_and_loss(from_date: FromDate = None, to_date: ToDate = None,
                        company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.profit_and_loss(state.db(company, fy), from_date, to_date)

    @tool("balance-sheet", "Balance sheet", "Assets and liabilities by primary group as on a date.")
    def balance_sheet(as_of: AsOf = None, company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.balance_sheet(state.db(company, fy), as_of)

    @tool("outstanding", "Receivables / payables", "Parties under Sundry Debtors (receivable) or Sundry "
                                                   "Creditors (payable) with FIFO aging buckets. Paginated.")
    def outstanding(nature: Literal["receivable", "payable"], as_of: AsOf = None,
                    min_amount: Annotated[float, Field(ge=0)] = 1.0, aging: bool = True,
                    offset: Offset = 0, limit: Limit = 200,
                    company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.outstanding(state.db(company, fy), nature, as_of, min_amount, aging, offset, limit)

    # ------------------------------------------------------------- vouchers
    @tool("day-book", "Day book", "Vouchers in a date range with party, amount, series and narration; "
                                  "filter by voucher type code (see voucher-types) or party. Paginated.")
    def day_book(from_date: FromDate = None, to_date: ToDate = None,
                 vch_type: Annotated[int | None, Field(ge=0, le=999, description="e.g. 9 = Sales")] = None,
                 party: str | None = None, offset: Offset = 0, limit: Limit = 100,
                 company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.day_book(state.db(company, fy), from_date, to_date, vch_type, party, offset, limit)

    @tool("voucher-details", "Voucher details", "Full voucher: header, narration, accounting entries, item "
                                                "lines (qty, rate, discount), order lines. By vch_code or vch_no.")
    def voucher_details(vch_code: Annotated[int | None, Field(ge=0)] = None,
                        vch_no: Annotated[str | None, Field(max_length=100)] = None,
                        vch_type: Annotated[int | None, Field(ge=0, le=999)] = None,
                        include_raw: bool = False, company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.voucher_details(state.db(company, fy), vch_code, vch_no, vch_type, include_raw)

    @tool("sales-purchase-analysis", "Sales / purchase analysis",
          "Sales or purchase totals by party, item, month or voucher, net of returns, incl. and excl. tax.")
    def sales_purchase_analysis(kind: Literal["sales", "purchase"],
                                group_by: Literal["party", "item", "month", "voucher"] = "party",
                                from_date: FromDate = None, to_date: ToDate = None,
                                top: Annotated[int, Field(ge=1, le=1000)] = 50,
                                vch_types: VchTypeList = None,
                                company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.trade_analysis(state.db(company, fy), kind, group_by, from_date, to_date, top, vch_types)

    # ------------------------------------------------------------ inventory
    @tool("stock-summary", "Stock summary", "Item-wise opening, inward, outward and closing quantity. "
                                            "only_negative=true lists items with negative stock. Paginated.")
    def stock_summary(from_date: FromDate = None, to_date: ToDate = None, item_group: str | None = None,
                      search: str | None = None, include_zero: bool = False, only_negative: bool = False,
                      offset: Offset = 0, limit: Limit = 200,
                      company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.stock_summary(state.db(company, fy), from_date, to_date, item_group, search,
                                     include_zero, only_negative, offset, limit)

    @tool("stock-item-movement", "Item movement", "Every inward/outward movement of one item with running "
                                                  "quantity. Paginated.")
    def stock_item_movement(item: ItemRef, from_date: FromDate = None, to_date: ToDate = None,
                            offset: Offset = 0, limit: Limit = 200,
                            company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.stock_item_movement(state.db(company, fy), item, from_date, to_date, offset, limit)

    @tool("refresh-cache", "Refresh cache", "Reload masters after edits in BUSY (auto-refresh every "
                                            "BUSY_CACHE_TTL seconds anyway).")
    def refresh_cache(company: Company = None, fy: FY = None) -> dict[str, Any]:
        db = state.db(company, fy)
        n = len(db.masters(refresh=True))
        return {"reloaded_masters": n, "target": db.target.describe()}

    @mcp.resource("busy://schema-guide", name="BUSY schema guide", mime_type="text/markdown")
    def schema_resource() -> str:
        return SCHEMA_GUIDE

    register_prompts(mcp)
    return mcp, state
