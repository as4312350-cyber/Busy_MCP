# Architecture

```
src/busy_mcp/
├── config.py           Settings: defaults < config file < environment; validation; secret masking
├── errors.py           BusyMCPError → BusyConnectionError / ReportError / UnsafeQueryError
├── logging_setup.py    stderr + optional rotating file; audit logger for raw SQL
├── schema.py           BUSY domain knowledge: type codes, field meanings, data dictionary, schema guide
├── sqlguard.py         Validator for free-form SQL (single read-only SELECT; blocked tables/columns)
├── db/
│   ├── drivers.py      ODBC driver selection and connection strings (Access / SQL Server)
│   ├── discovery.py    Finds COMPxxxx / FY databases and BUSY data folders; Target model
│   └── connection.py   BusyDB: read-only connection, masters cache, schema probing, change detection
├── reports/            Pure functions BusyDB → dict (no MCP dependency)
│   ├── _index.py       MovementIndex: per-day prefix sums for O(log n) range totals
│   ├── _common.py      Dates, Dr/Cr, pagination, master lookup, ledger index and balances
│   ├── overview.py     Company overview, capabilities, voucher/master types, search, COA
│   ├── explore.py      catalog, read-table, find, master-details; row decoding and masking
│   ├── accounts.py     Ledger details/balance/statement, TB, group summary, P&L, balance sheet
│   ├── analytics.py    Dashboard, monthly trends, cash & bank
│   ├── receivables.py  Outstanding with FIFO aging
│   ├── bills.py        Bill-by-bill outstanding, pending orders (Tran3)
│   ├── banking.py      Bank reconciliation, post-dated cheques
│   ├── vouchers.py     Day book, end-to-end voucher details
│   ├── trade.py        Sales/purchase analysis
│   ├── inventory.py    Stock summary/movement, godowns, batches, serials; item-line detection
│   ├── taxes.py        GST summary, TDS register
│   └── audit.py        Audit trail
├── server/
│   ├── app.py          build_server(): instructions, tools, prompts, resources
│   ├── registry.py     Tool decorator (threading, error mapping, read-only annotations), parameter types
│   ├── state.py        Targets, selected company, connection pool, change checks, prefetch
│   ├── tools/          One module per feature area (overview, explore, accounts, receivables, vouchers,
│   │                   inventory, taxes)
│   ├── prompts.py      Workflow prompts
│   └── http.py         ASGI guard: bearer token, Origin allow-list, body cap
└── cli/
    ├── __init__.py     `busy-mcp [serve|setup|doctor|verify]`
    ├── serve.py        stdio / HTTP serving
    ├── wizard.py       Guided setup
    ├── doctor.py       Diagnostics
    ├── verify.py       Consistency checks on the user's data; five numbers to compare with BUSY
    └── clients.py      Claude Desktop registration (backup + atomic write), client snippets
```

The `reports` package has no MCP imports, so it can be used from scripts, notebooks or other servers.

## Request flow

1. The client calls a tool, e.g. `bills-outstanding {"nature": "receivable"}`.
2. `registry.py` validates arguments with pydantic and runs the tool in a worker thread (ODBC blocks).
3. `State.db()` picks the company/year and calls `BusyDB.ensure_fresh()`. If the data fingerprint changed,
   derived caches are dropped.
4. The report uses the masters cache and the movement index (built on first use, or by prefetch) plus a few
   targeted queries.
5. `BusyMCPError`s become clean MCP tool errors (`isError: true`). Unexpected errors are logged with a
   traceback and reported briefly.

## Portable SQL

The same SQL runs on Jet/ACE (Access edition), T-SQL (SQL edition) and SQLite (tests). Reports use only:

* single `INNER JOIN`s
* `IIF`, `ABS`, `SUM`, `COUNT`, `MIN`, `MAX` and `GROUP BY`
* `?` bind parameters for every value
* `[Date]` quoting
* `db.top()` / `db.limit()` for row limits

## Runtime detection instead of assumptions

| Question | How it is answered |
|---|---|
| Are cancelled vouchers flagged? | `Tran1.Cancelled` / `VchCancelled` probed in the catalog |
| Where is narration stored? | `VchOtherInfo.Narration1`, else `Tran1.Narration1` |
| Which sign means stock in? | Sum of item quantities on this company's purchase (else sales) vouchers |
| Which line types are item movements? | RecTypes carrying item codes; those that mirror RecType 2 lines are ignored |
| Which key links bills to settlements? | `RefCode` or bill number, whichever leaves fewer orphan settlements |
| Does the data still match the cache? | Fingerprint of counts, max codes, modification times and opening balances |

Every detection result is visible in `server-info` and `busy-mcp doctor`, and can be overridden by settings.
