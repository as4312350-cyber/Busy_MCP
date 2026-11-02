# Configuration

All settings are environment variables, set in your MCP client's `env` block. Invalid values stop the
server at start-up with a clear message.

## Locating data

| Variable | Default | Notes |
|---|---|---|
| `BUSY_BACKEND` | `auto` | `access`, `sqlserver`, `odbc` or `sqlite`. `auto` picks `odbc` if a connection string is set, otherwise `sqlserver` if a server is set, otherwise `access` |
| `BUSY_DATA_DIR` | `C:\BusyWin\DATA` | Access edition. BUSY > Company > *Set Data Directory* shows it |
| `BUSY_ACCESS_PASSWORD` | BUSY's standard `.bds` password | Change it only if your files use another one |
| `BUSY_ACCESS_DRIVER` | `Microsoft Access Driver (*.mdb, *.accdb)` | Any installed Access driver is used as a fallback |
| `BUSY_SQL_SERVER` | – | e.g. `localhost\SQLEXPRESS`, `192.168.1.10,1433` |
| `BUSY_SQL_USER` / `BUSY_SQL_PASSWORD` | – | Leave both empty for Windows authentication |
| `BUSY_SQL_DRIVER` | newest `ODBC Driver NN for SQL Server` | |
| `BUSY_SQL_TRUST_CERT` | `1` | `TrustServerCertificate=yes` (BUSY's SQL Express installs use self-signed certificates) |
| `BUSY_SQL_NOLOCK` | `0` | `1` = READ UNCOMMITTED. Never blocks BUSY users, but may read half-saved vouchers |
| `BUSY_ODBC_CONNECTION_STRING` | – | Full custom ODBC string, used as-is |

## Selecting company and year

| Variable | Default | Notes |
|---|---|---|
| `BUSY_COMPANY` | first company | e.g. `COMP0003` |
| `BUSY_FY` | latest year | FY **start** year, e.g. `2024` = FY 2024-25 |
| `BUSY_DATABASE` | – | Explicit `.bds` path or SQL database name (skips discovery) |

## Accounting conventions

| Variable | Default | Notes |
|---|---|---|
| `BUSY_PL_GROUPS` | `Revenue Accounts` | Comma-separated groups whose accounts belong to P&L. If none is found, top-level groups named like income/expense/sale/purchase are used, and the output says so |
| `BUSY_DEBTOR_GROUPS` | `Sundry Debtors` | For `outstanding nature=receivable` |
| `BUSY_CREDITOR_GROUPS` | `Sundry Creditors` | For `outstanding nature=payable` |
| `BUSY_VCH_TYPE_NAMES` | – | JSON overrides, e.g. `{"4": "Purchase Challan", "50": "Production"}` |
| `BUSY_ITEM_INWARD_SIGN` | auto-detected | `1` or `-1` |
| `BUSY_INCLUDE_CANCELLED` | `0` | `1` includes cancelled vouchers (not recommended) |

## Limits, logging, HTTP

| Variable | Default | Notes |
|---|---|---|
| `BUSY_MAX_ROWS` | `1000` | Hard cap for `query-database` |
| `BUSY_QUERY_TIMEOUT` | `60` | Seconds (SQL Server; the Access driver ignores it) |
| `BUSY_CACHE_TTL` | `300` | Seconds before masters are reloaded |
| `BUSY_LOG_LEVEL` | `WARNING` | `INFO` shows connections and audited SQL |
| `BUSY_LOG_FILE` | – | Rotating log file (5 MB × 3) |
| `BUSY_AUDIT_QUERIES` | `1` | Logs every `query-database` statement at INFO |
| `BUSY_MCP_TOKEN` | generated per run | Bearer token for `--http` (min 16 chars) |
| `BUSY_MCP_ALLOWED_ORIGINS` | – | Comma-separated browser origins allowed in `--http` mode |
| `BUSY_MCP_HOST` / `BUSY_MCP_PORT` | `127.0.0.1` / `8765` | `--http` bind address |
