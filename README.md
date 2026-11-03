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

> **Status 1.2.** This release is built on BUSY's schema as decoded from real BUSY databases and production
> integrations ([docs/busy-internals.md](docs/busy-internals.md)). Every change is tested against a test
> company in BUSY's exact table layout. On Windows, CI also runs it as a real password-protected Access file
> (`COMP0001\db12024.bds`):
> * through Windows' own Jet driver and through Microsoft's Access Database Engine;
> * in both the older `.mdb` and the newer `.accdb` format;
> * for a 50,000-voucher company read from a network folder.
>
> It hasn't been checked against every BUSY release. Run `busy-mcp verify` on your data before relying on it.

## Install (Windows, one command, about 5 minutes)

Open **PowerShell** (Start menu, type `powershell`), paste this one line and press Enter:

```powershell
irm https://raw.githubusercontent.com/as4312350-cyber/Busy_MCP/main/scripts/get.ps1 | iex
```

That's all. It downloads busy-mcp into `%USERPROFILE%\Busy_MCP` and does everything else by itself:

* **Python:** uses one that works with BUSY (3.10 to 3.14). If there is none (no Python, only Python 3.15, or
  only 64-bit Python while BUSY's Access driver is 32-bit), it downloads the official **32-bit Python 3.14**
  (about 14 MB) into `.python32` inside that folder. Nothing is installed on the PC itself.
* **Packages:** installs busy-mcp and its dependencies from ready-made packages. No compiler needed.
* **Access engine:** if your BUSY files turn out to need Microsoft's Access Database Engine (only the newer
  `.accdb` format does), it downloads it from Microsoft, checks Microsoft's signature, and installs it.
  Windows asks permission once; choose **Yes**.
* **BUSY and Claude:** finds your BUSY data (or SQL Server), tests it, and connects **Claude Desktop** after
  backing up its settings.

Run the same line again whenever you want to update; your settings are kept.

Prefer not to run a pasted line? Download this project (green **Code** button → **Download ZIP**), unzip it
and **double-click `install.cmd`**. It does the same thing. The Python it downloads is the one the Python
Software Foundation publishes for build tools ([pythonx86 on nuget.org](https://www.nuget.org/packages/pythonx86));
the installer checks it against a fixed SHA-256 fingerprint.

The last step is `busy-mcp setup`, which:

* finds your BUSY data folder (or SQL Server) automatically and checks it can be read;
* asks which company to use by default, if you have several;
* adds BUSY to **Claude Desktop** for you, after backing up the existing settings.

Restart Claude Desktop and ask: *"Show my BUSY dashboard for this year."*

If anything goes wrong, run `busy-mcp doctor`. It explains in plain words what works and what to fix.
`busy-mcp verify` checks busy-mcp's numbers on your own data and picks five to compare with BUSY.

**Before relying on it, take the 15-minute [production checklist](docs/production-checklist.md).** It compares
five numbers with your BUSY.
For Cursor, VS Code, Claude Code or ChatGPT, see [docs/clients.md](docs/clients.md).

### Installing Python yourself (optional)

Do this if you prefer, or if the installer's download fails (no internet, or a proxy blocks it). Any Python
3.10–3.14 works; **32-bit** is preferred because BUSY's Access driver is 32-bit. On
[python.org's Windows downloads page](https://www.python.org/downloads/windows/), either:

* get the **Python install manager**, then open **Command Prompt** from the Start menu and run
  `py install 3.14-32`; or
* under a Python 3.14 release, click **Windows installer (32-bit)**.

Then double-click `install.cmd` again. If several Pythons are installed, it picks the one that suits BUSY.

**Not Python 3.15 yet:** pyodbc, the database driver, has no ready-made Windows package for it
(November 2026).

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
