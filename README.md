# BUSY MCP Server (read-only)

Let Claude, ChatGPT, Cursor or any other MCP-capable AI read your **BUSY Accounting Software**
data: trial balance, P&L, balance sheet, ledger statements, receivables/payables with aging,
stock summary, sales/purchase analysis, day book, full voucher details, and free-form SQL.

Inspired by [tally-mcp-server](https://github.com/dhananjay1405/tally-mcp-server), but BUSY works
very differently from Tally, so the design is different too. Read the next section before you rely
on the numbers.

---

## How it works (and why it's not like Tally)

Tally ships an XML server on port 9000 that answers report requests. **BUSY has no
equivalent public API.** BUSY's own FAQ says third-party integration has to go through a
channel partner. The only programmatic interface is a 32-bit COM DLL (`Busy2L21.CFixedInterface`),
and it isn't publicly documented.

So this server **reads BUSY's own database files directly, read-only**:

```
 AI app (Claude Desktop / Cursor / ChatGPT …)
        │  MCP (stdio, or HTTP + token)
        ▼
 busy-mcp  (this project, Python, runs on the BUSY PC or a LAN PC)
        │  ODBC, read-only
        ▼
 BUSY data ── Standard/Basic edition : C:\BusyWin\DATA\COMP0001\db12024.bds  (MS-Access file)
          └─ SQL edition             : SQL Server database BusyCOMP0001_db12024
```

There is one database per **company + financial year**. `db12024` means FY 2024-25.

BUSY doesn't publish its table layout, so the reports here are built on a schema that was
reverse-engineered from live BUSY databases. The sources are listed in
[docs/BUSY_INTERNALS.md](docs/BUSY_INTERNALS.md). The core of it (masters, voucher headers,
ledger and item lines, opening balances, sign convention) has been confirmed on real data. Some
voucher-type labels are inferred, and the server marks those as `(inferred)`.
**Before you trust it, run `trial-balance` and `ledger-statement` for a couple of
accounts and compare them with BUSY's own reports.**

## What you get: 26 read-only tools

| Area | Tools |
|---|---|
| Discovery | `list-companies`, `select-company`, `company-overview`, `voucher-types`, `master-type-summary` |
| Masters | `search-masters`, `list-masters`, `chart-of-accounts`, `ledger-details` (address, GSTIN, contacts) |
| Accounts | `ledger-balance`, `ledger-statement` (running balance, contra account), `trial-balance`, `group-summary`, `profit-and-loss`, `balance-sheet` |
| Receivables / payables | `outstanding` (Sundry Debtors / Creditors with 0-30/31-60/61-90/91-180/180+ aging) |
| Vouchers | `day-book`, `voucher-details` (ledger lines, items with qty/rate/discount, order lines) |
| Sales / purchase | `sales-purchase-analysis` (by party, item, month or voucher, net of returns) |
| Inventory | `stock-summary` (opening, inward, outward, closing qty), `stock-item-movement` |
| Power users | `schema-guide`, `list-tables`, `describe-table`, `query-database` (SELECT only), `refresh-cache` |

Every report tool takes optional `company` and `fy` arguments, so the AI can compare years
("sales this year vs last year") without switching back and forth.

There are **no write tools**. The server can't create, alter or delete anything in BUSY.

## Requirements

* Windows PC that has the BUSY data. This can be the BUSY PC itself or any PC on the LAN that can reach the data folder or the SQL Server.
* **Python 3.10+** from [python.org](https://www.python.org/downloads/windows/). Tick *Add python.exe to PATH* during install.
* An ODBC driver:
  * **Access edition:** *Microsoft Access Database Engine 2016 Redistributable*. Its bitness (x64 or x86) must match your Python's bitness. See Troubleshooting.
  * **SQL edition:** *ODBC Driver 18 for SQL Server* (17 and the built-in "SQL Server" driver also work).

## Install

```powershell
git clone https://github.com/as4312350-cyber/Busy_MCP.git
cd Busy_MCP
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

The script creates a virtual environment, installs the package, runs a connection check, and
prints the exact config block to paste into your AI app. To install by hand instead:

```powershell
python -m venv .venv
.venv\Scripts\pip install .
.venv\Scripts\busy-mcp --check
```

`--check` lists every company and year it found, then opens the latest one. Fix any error it
reports before you go further.

## Configure

All settings are environment variables. Most installs need **one** of these blocks.

**Access edition (most common)**

| Variable | Default | Notes |
|---|---|---|
| `BUSY_DATA_DIR` | `C:\BusyWin\DATA` | BUSY > Company > *Set Data Directory* shows yours |
| `BUSY_ACCESS_PASSWORD` | BUSY's standard `.bds` password | Change it only if your files use a different one |

**SQL edition**

| Variable | Example | Notes |
|---|---|---|
| `BUSY_SQL_SERVER` | `localhost\SQLEXPRESS` or `192.168.1.10,1433` | Setting this switches to SQL mode |
| `BUSY_SQL_USER` / `BUSY_SQL_PASSWORD` | `busy_reader` | Leave both empty to use Windows login |
| `BUSY_SQL_NOLOCK` | `0` | `1` = never block BUSY users, but may read half-saved vouchers |

**Optional**

| Variable | Default | Purpose |
|---|---|---|
| `BUSY_COMPANY` | first found | e.g. `COMP0003` |
| `BUSY_FY` | latest | FY start year, e.g. `2024` |
| `BUSY_DATABASE` | – | Explicit `.bds` path or SQL database name (skips discovery) |
| `BUSY_ODBC_CONNECTION_STRING` | – | Full custom ODBC string, used as-is |
| `BUSY_PL_GROUPS` | `Revenue Accounts` | Group(s) whose accounts go to P&L |
| `BUSY_DEBTOR_GROUPS` / `BUSY_CREDITOR_GROUPS` | `Sundry Debtors` / `Sundry Creditors` | Comma-separated, for `outstanding` |
| `BUSY_VCH_TYPE_NAMES` | – | JSON override for voucher labels, e.g. `{"14":"Receipt","19":"Payment"}` |
| `BUSY_ITEM_INWARD_SIGN` | auto | `1` or `-1`. Normally detected from purchase vouchers |
| `BUSY_MAX_ROWS` | `1000` | Row cap for `query-database` |

### Claude Desktop

Go to Settings > Developer > *Edit Config* and add the server. Use double backslashes in paths.

```json
{
  "mcpServers": {
    "busy": {
      "command": "C:\\Busy_MCP\\.venv\\Scripts\\busy-mcp.exe",
      "env": { "BUSY_DATA_DIR": "C:\\BusyWin\\DATA" }
    }
  }
}
```

There's a SQL-edition example in [examples/claude_desktop_config.json](examples/claude_desktop_config.json).
Restart Claude Desktop after saving.

### Cursor, Windsurf, VS Code, Claude Code, other stdio clients

Use the same command and env, in whatever format the client wants. For Claude Code:

```powershell
claude mcp add busy -e BUSY_DATA_DIR=C:\BusyWin\DATA -- C:\Busy_MCP\.venv\Scripts\busy-mcp.exe
```

### ChatGPT and other web-based assistants (remote MCP)

Web apps can't start a program on your PC, so they need an HTTPS URL instead:

```powershell
$env:BUSY_MCP_TOKEN = "a-long-random-secret"
busy-mcp --http --host 127.0.0.1 --port 8765
```

Put an HTTPS reverse proxy or tunnel (Cloudflare Tunnel, ngrok, Caddy) in front of
`http://127.0.0.1:8765/mcp`, and send `Authorization: Bearer <token>` from the client. The server
refuses to listen on a non-loopback address unless a token is set. Be aware that this sends your
books to a cloud AI provider and exposes them on the internet behind a single token. Only do it
if that's acceptable for your business.

## Read-only guarantees

1. **No write tools exist.** Every tool is a SELECT and is flagged `readOnlyHint` to the client.
2. **The connection is opened read-only.** Access files open with ODBC `ReadOnly=1`, in shared mode,
   so BUSY keeps working. SQL connections use `ApplicationIntent=ReadOnly` and autocommit (no
   open transactions).
3. **`query-database` takes only a single `SELECT`/`WITH` statement.** Anything containing INSERT,
   UPDATE, DELETE, INTO, EXEC, DROP, OPENROWSET, `;`, etc. is rejected before it reaches the database.
4. **SQL edition: also use a read-only login** so the guarantee is enforced by SQL Server
   itself. [scripts/create_readonly_sql_login.sql](scripts/create_readonly_sql_login.sql) creates
   `busy_reader` with `db_datareader` on every BUSY database and explicit DENY on writes.

## Known limitations

* **Stock valuation:** `stock-summary` gives exact quantities and inward/outward values, but not
  BUSY's closing-stock value. That depends on the valuation method you set (FIFO, average, etc.).
  For the same reason, `profit-and-loss` and `balance-sheet` are ledger-based and say so in their
  `caveat` field.
* **Bill-by-bill outstanding:** aging is a FIFO estimate against the latest invoices. BUSY's own
  bill references (in `Tran3`) aren't fully decoded yet.
* **Opening balances across years:** each FY is a separate database. If the year-end
  carry-forward wasn't run in BUSY, the new year's opening balances are zero, and
  `trial-balance` shows a non-zero `net_difference`.
* **Voucher type labels:** Receipt (14), Payment (19), Credit/Debit Note (17/18) and stock
  journals (5/8) are inferred from accounting signs, and they're shown as `(inferred)`. If your
  data differs, override them with `BUSY_VCH_TYPE_NAMES`.
* **Custom fields, GST return tables, multi-currency, cost centres, material centres:** no
  dedicated tools yet. Use `describe-table` and `query-database`.
* Tested against a BUSY-shaped SQLite fixture in CI, not against every BUSY release (BUSY 18,
  21 and later). Schema details can shift between major versions.

## Things to ask the AI

* "Show the trial balance as on 31 March 2025 and tell me if anything looks unusual."
* "Who owes us more than ₹50,000 for over 90 days?"
* "Give me Sharma Traders' ledger for Q1 and summarise the payments."
* "Top 20 items by sales value this year vs last year."
* "Month-wise purchase trend for FY 2024-25."
* "Open voucher S/1045 and explain the GST split."
* "Which items have negative stock?"

## Troubleshooting

| Symptom | Fix |
|---|---|
| `No Microsoft Access ODBC driver visible to this 64-bit Python` | BUSY usually installs the **32-bit** Access engine. Either install 32-bit Python, or install the x64 *Access Database Engine 2016 Redistributable*. If Office is 32-bit, run that installer with `/quiet` from a command prompt. |
| `Not a valid password` | Set `BUSY_ACCESS_PASSWORD` to your `.bds` password |
| `BUSY data folder not found` | Set `BUSY_DATA_DIR` |
| Can't reach SQL Server | Check `BUSY_SQL_SERVER` (instance name / port), that TCP/IP is enabled in SQL Server Configuration Manager, and that the firewall allows it |
| Numbers differ from BUSY | Check the date range (defaults to the full FY), whether you're on the right `fy`, and the `caveat` field. Open an issue with the tool output (anonymised) |
| New masters not visible | Call `refresh-cache` (the cache expires every 5 minutes anyway) |

## Development

```bash
pip install -e .[dev]
pytest
```

The tests build a SQLite database with BUSY's table layout and sign conventions
([tests/fixture.py](tests/fixture.py)). They check every report against hand-calculated figures,
and they drive the real server over stdio the way an MCP client does.

## Disclaimer

This is an independent project and isn't affiliated with Busy Infotech Pvt. Ltd. BUSY is their
trademark. Accounting figures come from reading BUSY's database; check important numbers
against BUSY before you use them for filing or decisions.
