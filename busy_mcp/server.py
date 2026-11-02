"""BUSY Accounting MCP server (read-only).

Run:  busy-mcp                 (stdio, for Claude Desktop / Cursor / any local MCP client)
      busy-mcp --http          (streamable HTTP on 127.0.0.1:8765, needs BUSY_MCP_TOKEN)
"""

from __future__ import annotations

import argparse
import functools
import json
import logging
import os
import sys
import threading
from typing import Annotated, Any, Callable

import anyio
from pydantic import Field

try:  # MCP Python SDK 2.x
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # MCP Python SDK 1.x
    from mcp.server.fastmcp import FastMCP as _Server  # type: ignore[no-redef]
from mcp.types import ToolAnnotations

from . import __version__, reports
from .config import Settings
from .db import BusyConnectionError, BusyDB, Target, choose_target, discover_targets, fy_label
from .reports import ReportError
from .schema import SCHEMA_GUIDE
from .sqlguard import UnsafeQueryError, validate_select

log = logging.getLogger("busy_mcp")

INSTRUCTIONS = """Read-only access to BUSY Accounting Software (Busy Infotech, India) data.
Each BUSY company + financial year (April-March) is a separate database.
Start with `list-companies` and `company-overview`. Use the report tools
(trial-balance, profit-and-loss, balance-sheet, ledger-statement, outstanding,
stock-summary, sales-purchase-analysis, day-book, voucher-details) before
falling back to `query-database`; read `schema-guide` before writing SQL.
Dates are YYYY-MM-DD and default to the selected financial year.
Amounts are in the company's base currency (normally INR); 'Dr'/'Cr' labels
follow BUSY's convention. Reports flag their own caveats - pass them on."""


Company = Annotated[str | None, Field(description="Company code e.g. COMP0001 (default: selected company)")]
FY = Annotated[int | None, Field(description="FY start year e.g. 2024 for FY 2024-25 (default: selected year)")]
FromDate = Annotated[str | None, Field(description="Start date YYYY-MM-DD (default: FY start)")]
ToDate = Annotated[str | None, Field(description="End date YYYY-MM-DD (default: FY end)")]
Kind = Annotated[str, Field(description="group | ledger | item_group | item")]


class State:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._targets: list[Target] | None = None
        self._dbs: dict[str, BusyDB] = {}
        self.active: Target | None = None
        self.lock = threading.RLock()

    def targets(self, refresh: bool = False) -> list[Target]:
        with self.lock:
            if self._targets is None or refresh:
                self._targets = discover_targets(self.settings)
            return self._targets

    def db(self, company: str | None = None, fy: int | None = None) -> BusyDB:
        with self.lock:
            if company or fy:
                base_company = company or (self.active.company if self.active else self.settings.company)
                target = choose_target(self.targets(), base_company, fy)
            else:
                if self.active is None:
                    self.active = choose_target(self.targets(), self.settings.company, self.settings.fy)
                target = self.active
            if target.key not in self._dbs:
                self._dbs[target.key] = BusyDB(target, self.settings)
            return self._dbs[target.key]

    def select(self, company: str | None, fy: int | None) -> Target:
        with self.lock:
            self.active = choose_target(self.targets(refresh=True), company or self.settings.company, fy)
            return self.active

    def close(self) -> None:
        for d in self._dbs.values():
            d.close()


def _json_safe(obj: Any) -> Any:
    return json.loads(json.dumps(obj, default=str))


def build_server(settings: Settings | None = None) -> tuple[Any, State]:
    settings = settings or Settings.from_env()
    state = State(settings)
    try:
        mcp = _Server(name="busy-accounting", instructions=INSTRUCTIONS, version=__version__)
    except TypeError:  # SDK 1.x has no version argument
        mcp = _Server(name="busy-accounting", instructions=INSTRUCTIONS)
    ro = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

    def tool(name: str, description: str):
        """Register a read-only tool whose body runs in a worker thread and
        converts known errors into clean messages for the AI."""

        def deco(fn: Callable[..., Any]):
            @functools.wraps(fn)
            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    result = await anyio.to_thread.run_sync(functools.partial(fn, *args, **kwargs))
                    return _json_safe(result)
                except (ReportError, UnsafeQueryError, BusyConnectionError) as exc:
                    return {"error": str(exc)}
                except Exception as exc:  # noqa: BLE001 - surface DB errors to the AI
                    log.exception("tool %s failed", name)
                    return {"error": f"{type(exc).__name__}: {exc}"}

            mcp.tool(name=name, description=description, annotations=ro)(wrapper)
            return fn

        return deco


    # -- discovery ---------------------------------------------------------
    @tool("list-companies", "List every BUSY company and financial-year database found on this machine, "
                            "and which one is currently selected.")
    def list_companies() -> dict[str, Any]:
        targets = state.targets(refresh=True)
        active = state.db().target
        return {"selected": active.describe(), "available": [t.describe() for t in targets]}

    @tool("select-company", "Switch the default company and/or financial year used by every other tool.")
    def select_company(company: Company = None, fy: FY = None) -> dict[str, Any]:
        t = state.select(company, fy)
        return {"selected": t.describe(), "overview": reports.company_overview(state.db())}

    @tool("company-overview", "Selected company's details, master counts and voucher date range.")
    def company_overview(company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.company_overview(state.db(company, fy))

    @tool("schema-guide", "How BUSY stores data (tables, type codes, sign conventions, SQL dialect tips). "
                          "Read this before using query-database.")
    def schema_guide() -> str:
        return SCHEMA_GUIDE

    @tool("list-tables", "List all tables in the selected BUSY database.")
    def list_tables(company: Company = None, fy: FY = None) -> dict[str, Any]:
        names = state.db(company, fy).table_names()
        return {"count": len(names), "tables": names}

    @tool("describe-table", "Columns and types of one table, plus 3 sample rows.")
    def describe_table(table: Annotated[str, Field(description="Table name, e.g. Tran1")],
                       company: Company = None, fy: FY = None) -> dict[str, Any]:
        db = state.db(company, fy)
        if table not in db.table_names():
            match = [t for t in db.table_names() if t.lower() == table.lower()]
            if not match:
                raise ReportError(f"Unknown table '{table}'")
            table = match[0]
        sample = db.query(f"SELECT {db.top(3)}* FROM [{table}]{db.limit(3)}")
        return {"table": table, "columns": db.columns(table), "sample_rows": sample}

    @tool("query-database", "Run ONE read-only SQL SELECT against the selected BUSY database "
                            "(Access edition = Jet SQL, SQL edition = T-SQL). Results are capped.")
    def query_database(sql: Annotated[str, Field(description="A single SELECT statement")],
                       max_rows: Annotated[int, Field(ge=1, le=10000)] = 200,
                       company: Company = None, fy: FY = None) -> dict[str, Any]:
        clean = validate_select(sql)
        db = state.db(company, fy)
        cap = min(max_rows, settings.max_rows)
        rows = db.query(clean, max_rows=cap + 1)
        return {"row_count": min(len(rows), cap), "truncated": len(rows) > cap, "rows": rows[:cap]}

    @tool("master-type-summary", "Count of masters by Master1.MasterType with examples (reveals units, "
                                 "material centres, series etc. that have no dedicated tool).")
    def master_type_summary(company: Company = None, fy: FY = None) -> list[dict[str, Any]]:
        return reports.master_type_summary(state.db(company, fy))

    @tool("voucher-types", "Voucher types used in this database with counts, date range and sample numbers.")
    def voucher_types(company: Company = None, fy: FY = None) -> list[dict[str, Any]]:
        return reports.voucher_types(state.db(company, fy))

    # -- masters -----------------------------------------------------------
    @tool("search-masters", "Find accounts, groups or items by (part of) name or alias.")
    def search_masters(text: str, kind: Annotated[str | None, Field(description="optional: group|ledger|item_group|item")] = None,
                       limit: int = 50, company: Company = None, fy: FY = None) -> list[dict[str, Any]]:
        return reports.search_masters(state.db(company, fy), text, kind, limit)

    @tool("list-masters", "List masters of one kind, optionally only those under a group.")
    def list_masters(kind: Kind, under_group: str | None = None, limit: int = 500,
                     company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.list_masters(state.db(company, fy), kind, under_group, limit)

    @tool("chart-of-accounts", "Account group tree (or item group tree with item_groups=true).")
    def chart_of_accounts(include_ledgers: bool = False, item_groups: bool = False,
                          company: Company = None, fy: FY = None) -> list[dict[str, Any]]:
        return reports.chart_of_accounts(state.db(company, fy), include_ledgers, item_groups)

    @tool("ledger-details", "Account master details: group, opening balance, address, GSTIN, contacts.")
    def ledger_details(ledger: Annotated[str, Field(description="Account name, alias or Code")],
                       company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.ledger_details(state.db(company, fy), ledger)

    # -- accounting reports -----------------------------------------------
    @tool("ledger-balance", "Closing balance of one account as on a date.")
    def ledger_balance(ledger: str, as_of: ToDate = None, company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.ledger_balance(state.db(company, fy), ledger, as_of)

    @tool("ledger-statement", "Account statement (ledger) with opening balance, every entry, "
                              "contra account and running balance.")
    def ledger_statement(ledger: str, from_date: FromDate = None, to_date: ToDate = None,
                         limit: int = 1000, company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.ledger_statement(state.db(company, fy), ledger, from_date, to_date, limit)

    @tool("trial-balance", "Trial balance: opening, debit, credit, closing per account and per primary group.")
    def trial_balance(from_date: FromDate = None, to_date: ToDate = None,
                      group: Annotated[str | None, Field(description="Only accounts under this group")] = None,
                      include_zero: bool = False,
                      ledger_level: Annotated[bool, Field(description="false = group totals only")] = True,
                      company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.trial_balance(state.db(company, fy), from_date, to_date, group, include_zero, ledger_level)

    @tool("group-summary", "Balances of the sub-groups and accounts directly under a group "
                           "(omit group for primary groups).")
    def group_summary(group: str | None = None, from_date: FromDate = None, to_date: ToDate = None,
                      company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.group_summary(state.db(company, fy), group, from_date, to_date)

    @tool("profit-and-loss", "Income and expenses for a period (ledger based, before stock adjustment).")
    def profit_and_loss(from_date: FromDate = None, to_date: ToDate = None,
                        company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.profit_and_loss(state.db(company, fy), from_date, to_date)

    @tool("balance-sheet", "Assets and liabilities by primary group as on a date.")
    def balance_sheet(as_of: ToDate = None, company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.balance_sheet(state.db(company, fy), as_of)

    @tool("outstanding", "Receivables (Sundry Debtors) or payables (Sundry Creditors) with FIFO aging.")
    def outstanding(nature: Annotated[str, Field(description="receivable | payable")],
                    as_of: ToDate = None, min_amount: float = 1.0, aging: bool = True, limit: int = 500,
                    company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.outstanding(state.db(company, fy), nature, as_of, min_amount, aging, limit)

    # -- vouchers ------------------------------------------------------------
    @tool("day-book", "Vouchers in a date range, optionally one voucher type (code from voucher-types) or party.")
    def day_book(from_date: FromDate = None, to_date: ToDate = None,
                 vch_type: Annotated[int | None, Field(description="Tran1.VchType code, e.g. 9 = Sales")] = None,
                 party: str | None = None, limit: int = 200,
                 company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.day_book(state.db(company, fy), from_date, to_date, vch_type, party, limit)

    @tool("voucher-details", "Full voucher: header, accounting entries, item lines (qty, rate, discount), "
                             "order lines. Look up by vch_code or by vch_no.")
    def voucher_details(vch_code: int | None = None, vch_no: str | None = None, vch_type: int | None = None,
                        include_raw: bool = False, company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.voucher_details(state.db(company, fy), vch_code, vch_no, vch_type, include_raw)

    @tool("sales-purchase-analysis", "Sales or purchase totals grouped by party, item, month or voucher, "
                                     "net of returns.")
    def sales_purchase_analysis(kind: Annotated[str, Field(description="sales | purchase")],
                                group_by: Annotated[str, Field(description="party | item | month | voucher")] = "party",
                                from_date: FromDate = None, to_date: ToDate = None, top: int = 50,
                                vch_types: Annotated[list[int] | None, Field(description="override voucher type codes")] = None,
                                company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.trade_analysis(state.db(company, fy), kind, group_by, from_date, to_date, top, vch_types)

    # -- inventory -----------------------------------------------------------
    @tool("stock-summary", "Item-wise opening, inward, outward and closing quantity for a period.")
    def stock_summary(from_date: FromDate = None, to_date: ToDate = None, item_group: str | None = None,
                      search: str | None = None, include_zero: bool = False, limit: int = 500,
                      company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.stock_summary(state.db(company, fy), from_date, to_date, item_group, search,
                                     include_zero, limit)

    @tool("stock-item-movement", "Every inward/outward movement of one item with running quantity.")
    def stock_item_movement(item: str, from_date: FromDate = None, to_date: ToDate = None, limit: int = 1000,
                            company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.stock_item_movement(state.db(company, fy), item, from_date, to_date, limit)

    @tool("refresh-cache", "Reload masters after changes were made in BUSY (cache auto-expires in 5 minutes).")
    def refresh_cache(company: Company = None, fy: FY = None) -> dict[str, Any]:
        db = state.db(company, fy)
        n = len(db.masters(refresh=True))
        db.cache.clear()
        return {"reloaded_masters": n, "target": db.target.describe()}

    @mcp.resource("busy://schema-guide", name="BUSY schema guide", mime_type="text/markdown")
    def schema_resource() -> str:
        return SCHEMA_GUIDE

    return mcp, state


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def _bearer_app(app: Any, token: str) -> Any:
    async def guarded(scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            if headers.get(b"authorization", b"").decode() != f"Bearer {token}":
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"text/plain")]})
                await send({"type": "http.response.body", "body": b"Unauthorized"})
                return
        await app(scope, receive, send)

    return guarded


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="busy-mcp", description="Read-only MCP server for BUSY Accounting")
    parser.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    parser.add_argument("--host", default=os.environ.get("BUSY_MCP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("BUSY_MCP_PORT", "8765")))
    parser.add_argument("--check", action="store_true", help="test the BUSY connection and exit")
    parser.add_argument("--version", action="version", version=f"busy-mcp {__version__}")
    args = parser.parse_args(argv)
    logging.basicConfig(level=os.environ.get("BUSY_LOG_LEVEL", "WARNING"), stream=sys.stderr)

    mcp, state = build_server()
    if args.check:
        try:
            targets = state.targets()
            print(f"Found {len(targets)} company/FY database(s):")
            for t in targets:
                print(f"  {t.key:<16} FY {fy_label(t.fy) or '?':<8} {t.location}")
            db = state.db()
            ov = reports.company_overview(db)
            print(f"Selected {db.target.key}: {ov['masters']}  vouchers={ov['vouchers']}")
            print("OK")
        except Exception as exc:  # noqa: BLE001
            print(f"FAILED: {exc}", file=sys.stderr)
            sys.exit(1)
        return

    if not args.http:
        mcp.run("stdio")
        return

    token = os.environ.get("BUSY_MCP_TOKEN")
    loopback = args.host in ("127.0.0.1", "localhost", "::1")
    if not token and not loopback:
        sys.exit("Refusing to listen on a non-loopback address without BUSY_MCP_TOKEN set.")
    import uvicorn

    app = mcp.streamable_http_app()
    if token:
        app = _bearer_app(app, token)
    print(f"busy-mcp listening on http://{args.host}:{args.port}/mcp", file=sys.stderr)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
