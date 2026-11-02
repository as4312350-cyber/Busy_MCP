# BUSY MCP Server

[![CI](https://github.com/as4312350-cyber/Busy_MCP/actions/workflows/ci.yml/badge.svg)](https://github.com/as4312350-cyber/Busy_MCP/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%E2%80%933.13-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Read-only](https://img.shields.io/badge/access-read--only-brightgreen)

A **read-only** [Model Context Protocol](https://modelcontextprotocol.io) server for **BUSY Accounting
Software** (Busy Infotech, India). It lets Claude, ChatGPT, Cursor, VS Code or any other MCP client answer
questions from your live books. Examples:

* trial balance, P&L and balance sheet
* ledger statements and receivables/payables with aging
* stock summary and sales/purchase analysis
* day book and full voucher details
* guarded SQL

> **Status: 1.0, but not yet tested against a live BUSY install.** It's built on a schema
> reverse-engineered from real BUSY databases (see [docs/busy-internals.md](docs/busy-internals.md)).
> Every report has been verified against a test database with BUSY's exact layout. Before you rely on
> it, run `busy-mcp --check` and compare one trial balance with BUSY's own report.

## Why it reads the database (and Tally MCP doesn't)

Tally ships an XML server on port 9000. **BUSY has no public API.** Its own FAQ sends integrators to
channel partners. So this server opens BUSY's own database **read-only**, the same way BUSY-integrated
add-ons do:

```
AI client ──MCP (stdio or HTTPS+token)──▶ busy-mcp ──ODBC, read-only──▶ BUSY data
                                                        ├─ Access edition: <DATA>\COMP0001\db12024.bds
                                                        └─ SQL edition:    BusyCOMP0001_db12024
```

Each company + financial year is a separate database, and the server finds all of them automatically.
More detail is in [docs/architecture.md](docs/architecture.md).

## Features

* **27 tools, all read-only.** They cover discovery, masters, accounts, receivables, vouchers, sales and
  purchase, inventory, and power-user SQL ([full reference](docs/tools.md)).
* **3 prompts:** `financial-health-check`, `receivables-follow-up` and `month-end-review`.
* **Correct by construction:**
  * Cancelled vouchers are excluded.
  * Version-specific columns are detected at runtime.
  * Stock direction is checked against your own purchase vouchers.
  * Every estimate says so in a `caveat`/`method` field.
* **Multi-company, multi-year.** Any tool can target another company or year, e.g. "this year vs last year".
* **Paginated output** keeps responses inside AI context limits.
* **Four layers of read-only protection** plus a hardened HTTP mode ([SECURITY.md](SECURITY.md)).
* Works with MCP Python SDK 1.x and 2.x, on Python 3.10–3.13.

## Quick start (Windows, on the PC that has BUSY data)

1. Install **Python 3.10+** from [python.org](https://www.python.org/downloads/windows/). Tick *Add to PATH*.
2. Install the ODBC driver your edition needs:
   * **Access edition:** *Microsoft Access Database Engine 2016 Redistributable*. Its bitness must match
     Python. BUSY usually installs the 32-bit engine, so 32-bit Python is the easy path.
   * **SQL edition:** *ODBC Driver 18 for SQL Server*.
3. Install and test:

```powershell
git clone https://github.com/as4312350-cyber/Busy_MCP.git
cd Busy_MCP
powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -DataDir C:\BusyWin\DATA
```

The installer creates `.venv`, runs `busy-mcp --check` (which lists companies and confirms the trial
balance tallies), and prints the config block for your AI client.

### Claude Desktop

Go to Settings → Developer → Edit Config, add the block below, then restart Claude.

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

For SQL edition, Cursor, VS Code, Claude Code and remote clients (ChatGPT), see
[docs/clients.md](docs/clients.md). Every setting is listed in [docs/configuration.md](docs/configuration.md).

## Things to ask

* "Show the trial balance as on 31 March 2025 and point out anything unusual."
* "Which customers owe more than ₹50,000 for over 90 days? Give me their phone numbers."
* "Sharma Traders' ledger for Q1, and when did they last pay?"
* "Top 20 items by sales this year vs last year."
* "Which items have negative stock?"
* "Open invoice S/1045 and explain the GST split."

## Known limitations

* **Stock valuation:** `profit-and-loss` and `balance-sheet` are ledger-based. Closing-stock *value*
  depends on the valuation method you set in BUSY and isn't computed. Quantities are exact.
* **Aging:** it's a FIFO estimate. BUSY's own bill-by-bill references aren't decoded yet.
* **Year-end carry-forward:** if it wasn't run in BUSY, opening balances in the new FY database are zero.
  The trial balance flags this with a `warning`.
* **Labels marked `(inferred)`:** a few voucher-type codes are single-source (4, 5, 8, 11, 17, 18). Override
  them with `BUSY_VCH_TYPE_NAMES`.

More detail is in [docs/troubleshooting.md](docs/troubleshooting.md).

## Development

```bash
pip install -e ".[dev]"
ruff check src tests && mypy && bandit -q -r src -c pyproject.toml
pytest --cov
```

The repository layout and conventions are in [CONTRIBUTING.md](CONTRIBUTING.md). Release notes are in
[CHANGELOG.md](CHANGELOG.md).

## Disclaimer

This is an independent project and isn't affiliated with Busy Infotech Pvt. Ltd. BUSY is their
trademark. Figures are read from BUSY's database; check important numbers against BUSY before you use
them for filings or decisions.
