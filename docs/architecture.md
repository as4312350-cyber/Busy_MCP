# Architecture

## Components

```
src/busy_mcp/
├── config.py          Settings from environment variables (validated, secrets masked)
├── errors.py          BusyMCPError → BusyConnectionError / ReportError / UnsafeQueryError
├── logging_setup.py   stderr + optional rotating file; audit logger for raw SQL
├── schema.py          Known BUSY type codes, sign conventions, schema guide for the AI
├── sqlguard.py        Validator for free-form SQL (single read-only SELECT only)
├── db/
│   ├── drivers.py     ODBC driver selection, connection strings (Access / SQL Server)
│   ├── discovery.py   Finds COMPxxxx / FY databases, Target model, FY helpers
│   └── connection.py  BusyDB: read-only connection, masters cache, runtime schema probing
├── reports/           Pure functions BusyDB → dict (no MCP dependency)
│   ├── _common.py     Dates, Dr/Cr formatting, pagination, balances, master lookup
│   ├── overview.py    Company overview, capabilities, master/voucher types, search, COA
│   ├── accounts.py    Ledger details/balance/statement, TB, group summary, P&L, BS
│   ├── receivables.py Outstanding with FIFO aging
│   ├── vouchers.py    Day book, voucher details, narration lookup
│   ├── inventory.py   Stock summary, item movement, stock-direction detection
│   └── trade.py       Sales/purchase analysis
└── server/
    ├── app.py         MCP tool/resource registration (read-only annotations, error mapping)
    ├── prompts.py     Workflow prompts
    ├── state.py       Discovered targets, selected target, connection pool
    ├── http.py        ASGI guard: bearer token, Origin allow-list, body cap
    └── cli.py         `busy-mcp` entry point (stdio, --http, --check)
```

The report layer has no MCP imports. It can be reused from scripts, notebooks or other servers.

## Request flow

1. The MCP client calls a tool, for example `ledger-statement {"ledger": "Sharma Traders"}`.
2. `server/app.py` validates the arguments with pydantic (types, ranges, enums). It then runs the report
   in a worker thread so the event loop never blocks on ODBC.
3. `State.db()` picks the target: the selected company/FY, or the `company`/`fy` arguments.
4. The report resolves names against the cached `Master1` (5-minute TTL) and runs a few aggregate SELECTs.
5. `BusyMCPError`s become clean tool errors (`isError: true`). Anything else is logged with a traceback
   and reported briefly.

## Portable SQL

The same SQL must run on Jet/ACE (Access edition), T-SQL (SQL edition) and SQLite (the tests), so
reports use only:

* single `INNER JOIN`s
* `IIF`, `ABS`, `SUM`, `COUNT`, `MIN`, `MAX` and `GROUP BY`
* `?` bind parameters for every value
* `[Date]` quoting
* `TOP`/`LIMIT` through `db.top()`/`db.limit()`

Tree walks (group hierarchy) and name resolution are done in Python.

## Runtime schema probing

BUSY's column set differs between releases. `BusyDB.column_set()` reads the ODBC catalog once per
table, and features switch on only when their columns exist:

| Feature | Column(s) probed | Fallback |
|---|---|---|
| Exclude cancelled vouchers | `Tran1.Cancelled`, `Tran1.VchCancelled` | No filter (reported in `server-info`) |
| Voucher narration | `VchOtherInfo.Narration1/2`, else `Tran1.Narration1` | Omitted |
| Line narration | `Tran2.ShortNar` | Omitted |
| Voucher series | `Tran1.VchSeriesCode` | Omitted |
| Company name | `Locks.CompanyName`, `Company*` tables | Folder code only |

Stock direction (which sign of `Tran2.Value1` means "in") is checked against the database's own
purchase vouchers. Sales vouchers are the fallback. You can also set it with `BUSY_ITEM_INWARD_SIGN`.
