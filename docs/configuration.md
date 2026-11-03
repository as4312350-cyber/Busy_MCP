# Configuration

Most people never edit settings by hand: `busy-mcp setup` detects BUSY, tests the connection and writes them.
This page is the reference for everything it can set.

## Where settings come from

Later sources override earlier ones:

1. **Built-in defaults.** A single PC with BUSY data in `C:\BusyWin\DATA` needs nothing else.
2. **The config file** written by `busy-mcp setup`:
   * Windows: `%APPDATA%\busy-mcp\config.json`
   * macOS/Linux: `~/.config/busy-mcp/config.json`
   * any other location named in `BUSY_MCP_CONFIG`

   Keys are the setting names below in lower case, e.g. `{"data_dir": "D:\\Busy\\DATA", "company": "COMP0002"}`.
3. **Environment variables** (`BUSY_` + the setting name in upper case), for example from an MCP client's
   `env` block.

`busy-mcp doctor` and the `server-info` tool show which setting came from where. Invalid values stop the
server at start-up with a message naming the setting.

## Locating BUSY data

| Setting (env) | Default | Notes |
|---|---|---|
| `BUSY_BACKEND` | `auto` | `access`, `sqlserver`, `odbc` or `sqlite` (tests). `auto` picks `odbc` if a connection string is set, otherwise `sqlserver` if a server is set, otherwise `access` |
| `BUSY_DATA_DIR` | `C:\BusyWin\DATA` | Access edition. BUSY > Company > *Set Data Directory* shows it |
| `BUSY_ACCESS_PASSWORD` | BUSY's standard `.bds` password | Change only if your files use another one |
| `BUSY_ACCESS_DRIVER` | `Microsoft Access Driver (*.mdb, *.accdb)` | If it isn't installed, another Access driver is used, the Access Database Engine's first. `busy-mcp doctor` shows which |
| `BUSY_SQL_SERVER` | – | e.g. `localhost\SQLEXPRESS` or `192.168.1.10,1433` |
| `BUSY_SQL_USER` / `BUSY_SQL_PASSWORD` | – | Leave both empty for Windows authentication (recommended) |
| `BUSY_SQL_DRIVER` | newest `ODBC Driver NN for SQL Server` | |
| `BUSY_SQL_TRUST_CERT` | `1` | BUSY's SQL Express installs use self-signed certificates |
| `BUSY_SQL_NOLOCK` | `0` | `1` = never wait for BUSY users' locks (may read in-flight vouchers) |
| `BUSY_ODBC_CONNECTION_STRING` | – | Full custom ODBC string, used as-is |
| `BUSY_COMPANY` | first company | e.g. `COMP0003` |
| `BUSY_FY` | latest year | FY **start** year, e.g. `2024` = FY 2024-25 |
| `BUSY_DATABASE` | – | Explicit `.bds` path or SQL database name (skips discovery) |

## BUSY conventions

| Setting | Default | Notes |
|---|---|---|
| `BUSY_PL_GROUPS` | `Revenue Accounts` | Groups whose accounts belong to P&L. If none exist, a name heuristic is used and reported |
| `BUSY_DEBTOR_GROUPS` / `BUSY_CREDITOR_GROUPS` | `Sundry Debtors` / `Sundry Creditors` | For receivables/payables |
| `BUSY_CASH_BANK_GROUPS` | `Bank Accounts, Cash-in-hand, Bank O/D Account` | For `cash-bank-summary` and the dashboard |
| `BUSY_TAX_GROUPS` | `Duties & Taxes` | Where GST tax ledgers live. Accounts outside it, like a "Sales GST 18%" ledger, are never treated as tax |
| `BUSY_VCH_TYPE_NAMES` | – | JSON overrides, e.g. `{"4": "Purchase Challan", "50": "Production"}` |
| `BUSY_ITEM_INWARD_SIGN` | auto-detected | `1` or `-1` |
| `BUSY_ITEM_RECTYPES` | auto-detected | Which `Tran2.RecType` values are item lines, e.g. `2,20` |
| `BUSY_INCLUDE_CANCELLED` | `0` | `1` counts cancelled vouchers (not recommended) |

## Security

| Setting | Default | Notes |
|---|---|---|
| `BUSY_DENIED_TABLES` | `Config` | Tables never readable by generic tools or SQL |
| `BUSY_ALLOW_SENSITIVE` | `0` | `1` lifts table blocking and credential-column masking (administrators only) |
| `BUSY_AUDIT_QUERIES` | `1` | Logs every `query-database` statement at INFO |
| `BUSY_MCP_TOKEN` | generated per run | Bearer token for `serve --http` (minimum 16 characters) |
| `BUSY_MCP_ALLOWED_ORIGINS` | – | Browser origins allowed in HTTP mode |

## Performance, limits, logging

| Setting | Default | Notes |
|---|---|---|
| `BUSY_FRESHNESS_SECONDS` | `10` | How often to check whether BUSY data changed |
| `BUSY_CACHE_TTL` | `900` | Maximum cache age in seconds |
| `BUSY_PREFETCH` | `1` | Warm caches in the background at start-up |
| `BUSY_MAX_ROWS` | `1000` | Hard cap for `query-database` |
| `BUSY_QUERY_TIMEOUT` | `60` | Seconds (SQL Server; the Access driver ignores it) |
| `BUSY_LOG_LEVEL` | `WARNING` | `INFO` logs connections, tool timings and audited SQL |
| `BUSY_LOG_FILE` | – | Rotating log file (5 MB × 3) |
| `BUSY_MCP_HOST` / `BUSY_MCP_PORT` | `127.0.0.1` / `8765` | `serve --http` bind address |

## Commands

```text
busy-mcp                  run the MCP server over stdio (what AI apps launch)
busy-mcp setup            guided setup (find BUSY, test, save settings, connect Claude Desktop)
busy-mcp doctor           diagnose Python, drivers, data, configuration
busy-mcp verify           check the numbers on your own data and list five to compare with BUSY
                          (--party, --item, --company, --fy, --redact, --json, --out FILE)
busy-mcp serve --http     serve over HTTP for remote clients (token required)
```
