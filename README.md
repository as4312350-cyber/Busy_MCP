# BUSY MCP Server

[![CI](https://github.com/as4312350-cyber/Busy_MCP/actions/workflows/ci.yml/badge.svg)](https://github.com/as4312350-cyber/Busy_MCP/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%E2%80%933.14-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Read-only](https://img.shields.io/badge/access-read--only-brightgreen)

Ask Claude, ChatGPT, Cursor or any AI assistant about your **BUSY Accounting** books in plain language:

> *"How is the business doing this year?"*
> *"Which customers are overdue by more than 60 days, and what are their phone numbers?"*
> *"Show stock in the Branch godown and the batches expiring this quarter."*
> *"Who deleted vouchers last month?"*

`busy-mcp` is a **read-only** [Model Context Protocol](https://modelcontextprotocol.io) server. It reads
BUSY's own company files directly; it cannot change anything in BUSY.

> **Status 1.1.** This release is built on BUSY's schema as decoded from real BUSY databases and production
> integrations ([docs/busy-internals.md](docs/busy-internals.md)). Every change is tested against a test
> company in BUSY's exact table layout. On Windows, that company is also stored as a real
> password-protected Access file (`COMP0001\db12024.bds`) and read through Microsoft's own Access driver,
> exactly as your data is. It hasn't been checked against every BUSY release, so run `busy-mcp doctor` and
> compare one trial balance with BUSY before relying on it.

## Install (Windows, about 5 minutes)

1. Install **Python 3.14, 32-bit** ("Windows installer (32-bit)") from
   [python.org](https://www.python.org/downloads/windows/). Tick **Add python.exe to PATH**.
   - Why 32-bit: BUSY's data is read through Microsoft's Access driver, which is 32-bit on most PCs.
   - Python 3.10 to 3.14 all work. If you have several, the installer picks the one that matches the driver.
   - **Not Python 3.15 yet:** pyodbc, the database driver, has no ready-made Windows package for it
     (November 2026).
2. Download this project (green **Code** button on GitHub → **Download ZIP**), unzip it (e.g. to
   `C:\Busy_MCP`), and **double-click `install.cmd`**.

The installer sets everything up and then runs `busy-mcp setup`, which:

* finds your BUSY data folder (or SQL Server) automatically and checks it can be read;
* asks which company to use by default, if you have several;
* adds BUSY to **Claude Desktop** for you, after backing up the existing settings.

Restart Claude Desktop and ask: *"Show my BUSY dashboard for this year."*

If anything goes wrong, run `busy-mcp doctor`. It explains in plain words what works and what to fix.

**Before relying on it, take the 15-minute [production checklist](docs/production-checklist.md).** It compares
five numbers with your BUSY.
For Cursor, VS Code, Claude Code or ChatGPT, see [docs/clients.md](docs/clients.md).

## What it can read

| Area | What you can ask about |
|---|---|
| Overview | Dashboard of sales, purchases, profit, receivables, payables, cash & bank, negative stock |
| Accounts | Ledger statements, balances, monthly trends, trial balance, P&L, balance sheet, group summaries |
| Parties | Bill-wise outstanding with due dates and aging, FIFO aging, credit days, phone, GSTIN, e-mail |
| Banking | Bank reconciliation (cleared/uncleared), post-dated cheques, cash & bank flow |
| Vouchers | Day book, complete vouchers (items, GST/TDS lines, bill references, batches, serials, transport, POS) |
| Trade | Sales/purchase analysis by party, item, month; pending sales and purchase orders |
| Inventory | Stock summary, item movement, stock by godown (material centre), batch & expiry, serial numbers |
| Taxes | GST output vs input by head with taxable turnover by tax type; TDS/TCS register |
| Audit | Who created, changed or deleted vouchers and masters, and when |
| **Anything else** | `catalog` lists every BUSY table by feature; `read-table` reads any table with codes turned into names; `find` searches the whole company by name, phone, GSTIN, invoice, batch or serial number |

That's 44 tools and 5 ready-made workflows (health check, receivables follow-up, month-end, stock, audit).
Full list: [docs/tools.md](docs/tools.md). Coverage by BUSY feature: [docs/feature-coverage.md](docs/feature-coverage.md).

## Fast by design

The first question builds an in-memory index of every account's and item's daily movements. After that,
trial balances, P&L, balance sheets, aging and stock summaries for **any period** take milliseconds. The
index is rebuilt automatically within seconds of anyone saving a voucher in BUSY. On a 50,000-voucher test
company, reports drop from about 320 ms each to 4–40 ms ([docs/performance.md](docs/performance.md)).

## Safe by design

* Nothing can be written. Every tool is read-only, and connections are opened read-only.
* Free-form SQL is limited to one `SELECT`, with no external files and no system calls.
* Tables and columns that can hold passwords (e.g. BUSY's `Config`, where GST portal credentials may be
  stored) are blocked or masked.
* The HTTP mode for remote clients requires a token.

Details in [SECURITY.md](SECURITY.md).

## Known limitations

* **Stock value:** closing-stock *value* follows BUSY's valuation method and isn't computed. Quantities are
  exact. P&L and balance sheet are ledger-based and say so.
* **Labels:** a few voucher-type labels are single-source and shown as "(inferred)". Override them with
  `BUSY_VCH_TYPE_NAMES`.
* **Opening balances:** if BUSY's year-end carry-forward wasn't run, the new year's opening balances are
  zero. The trial balance warns about this.

## For developers

```bash
pip install -e ".[dev]"
ruff check src tests && mypy && bandit -q -r src -c pyproject.toml
pytest --cov
python scripts/benchmark.py
```

* Architecture: [docs/architecture.md](docs/architecture.md)
* Contributing: [CONTRIBUTING.md](CONTRIBUTING.md)
* Release notes: [CHANGELOG.md](CHANGELOG.md)

This is an independent project, not affiliated with Busy Infotech Pvt. Ltd. BUSY is their trademark.
Check important figures against BUSY before you use them for filings.
