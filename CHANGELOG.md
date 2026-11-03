# Changelog

All notable changes are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/),
and versions follow [Semantic Versioning](https://semver.org/).

## [1.2.2] — 2026-11-03

### Fixed
- **No BUSY data at the usual place ended the install with a red error.** The first real run of the
  one-command install on a PC showed it: Python and busy-mcp installed fine, but the data was not in
  `C:\BusyWin\DATA`, and setup stopped with a PowerShell error trace. Now:
  - `busy-mcp setup` exits with code 4 when the BUSY data folder can't be found or opened;
  - the installer asks you to type the folder (BUSY: Company > Set Data Directory) and finishes setup;
  - if you skip, it says "installed, not connected to your BUSY data yet" and prints the exact command to run
    later, including the SQL Server variant.
- `get.ps1` no longer throws under `irm | iex`; it ends with a message and never closes your window.
- Packaging: the license is declared as an SPDX expression (`MIT`), which removes the long
  "`project.license` as a TOML table is deprecated" warning that pip printed during install.

### Added
- **The installer finds your BUSY data by itself**, on this PC or on a server, so you never have to type a path:
  - it looks next to a **running BUSY** (the program's own folder shows where it lives);
  - then in the usual places on every drive, mapped network drives included;
  - and, if that finds nothing, searches the drives a few levels down (at most 25 seconds);
  - when several data folders exist, the one with a **company open in BUSY right now** wins (Microsoft's Access
    engine keeps a small lock file next to an open company), otherwise the most recently saved;
  - with several companies in that folder, the **open** one is connected.
  Only if nothing is found does it ask you for the folder.
- CI step: with no BUSY data on the machine, the one-command install must end with exit code 4 and the
  friendly message, with no error trace.

## [1.2.1] — 2026-11-03

### Added
- **One command installs everything.** Paste one line into PowerShell
  (`irm https://raw.githubusercontent.com/as4312350-cyber/Busy_MCP/main/scripts/get.ps1 | iex`).
  `scripts/get.ps1` downloads busy-mcp into `%USERPROFILE%\Busy_MCP`, runs the installer, and keeps your
  environment and settings when run again to update.
- **The installer no longer asks anything.** If no suitable Python is installed, it downloads the official
  32-bit Python 3.14 into the project folder by itself (it used to ask Y/n).
- **The installer adds the Access Database Engine when BUSY's file needs it.** `busy-mcp setup` now exits with
  code 3 when a file can't be opened by the installed drivers because of its format. `install.cmd` then
  downloads Microsoft's Access Database Engine (x86), refuses it unless it is signed by Microsoft Corporation,
  installs it (Windows asks permission once) and runs setup again. `-InstallAce` does only that step.
- CI jobs on Windows: **one-command** (a 64-bit-only runner, nothing prepared: the 32-bit Python is
  downloaded and busy-mcp works; a second run keeps the environment) and **installer-ace** (a BUSY file in
  the `.accdb` format makes the installer install ACE; then setup and doctor read a real `.accdb` file).

## [1.2.0] — 2026-11-02

### Added
- **`busy-mcp verify`**: run once on the BUSY PC.
  - Checks your own data and prints PASS / WARN / FAIL in plain words:
    - every voucher balances;
    - the trial balance tallies, or the difference is explained;
    - the fast index equals a direct read;
    - bill-wise pending equals ledger balances;
    - stock adds up;
    - opening balances were carried forward.
  - Then prints the five numbers to compare with BUSY's own screens.
  - `--redact` hides names so the report can be shared; `--json` and `--out` save it.
- **The installer can fetch Python itself.** If no suitable Python is installed, `install.cmd` offers to
  download the official 32-bit Python 3.14.10 into the project folder (`.python32`).
  - Source: the Python Software Foundation's `pythonx86` package on nuget.org.
  - The SHA256 fingerprint is pinned and checked before unpacking.
  - Nothing is installed system-wide. `-BootstrapPython` forces it.
- **Tests through the Access Database Engine (ACE)**, the driver most BUSY integrations use. A CI job
  installs Microsoft's ACE 2016 redistributable (x86; Microsoft signature checked). It then runs the report,
  tool, index and verify tests on a password-protected company file in both the older `.mdb` format and the
  newer `.accdb` format.
- **Large company in a network folder, measured.** A CI job builds 50,000 vouchers (36 MB, BUSY's
  password) and shares the folder over SMB. It reads the company through `\\localhost\...` with all
  correctness checks, then runs `setup` and `doctor` on the network path.
  - The one-time index build took about 3 s; reports took 0.01–1.6 s.
  - The figures are in [docs/performance.md](docs/performance.md).
  - `scripts/benchmark_access.py` runs the same measurement on your own PC and network.
- `doctor` shows which Access drivers it found, which one it uses, and the company file's format.

### Fixed
- **Wrong driver for a newer BUSY file.** When Windows' old Jet driver meets an `.accdb` BUSY file, the
  error now says to install ACE, whatever Jet's wording. CI showed Jet answers "Cannot open database ...
  (-1028)" rather than "Unrecognized database format".
- **Network folders.**
  - Setup and doctor explain an unreachable network folder with Windows' own reason ("The network path was
    not found", "Access is denied") instead of "no company folders".
  - An unreadable company folder no longer crashes discovery.
  - An empty `data_dir` gives a clear error.
- If the configured Access driver is missing, busy-mcp now prefers ACE over Jet.

### Changed
- Version 1.2.0. CI lints `scripts/` too. The README's Python download wording is checked against the source
  of python.org's download page (32-bit: "Windows installer (32-bit)"; or `py install 3.14-32`).

## [1.1.1] — 2026-11-02

### Fixed
- **BUSY's standard (Access) edition could not be opened at all.** busy-mcp sent the `.bds` password as
  `PWD={ILoveMyINDIA}`. Unlike the SQL Server drivers, the Microsoft Access driver keeps the braces as part
  of the password, so BUSY company files, which carry this password by default, were rejected with "Not a
  valid password". Braces are now used only when a password can't be written without them. Found by the
  new CI job that reads a real password-protected Access file (see Added); the unit test had asserted the
  braced form.
- **Bill numbers and batches were missing on the Access edition.** In Access SQL a bare `No` means False,
  so `SELECT … No …` from `Tran3` returned False instead of the bill or batch number:
  - `bills-outstanding` showed no bill numbers
  - `batch-stock` found no batches
  - pending orders use the same query

  Column lists built at run time are now bracketed (`[No]`, `[Date]`) everywhere: bills, orders, batches,
  serial numbers, TDS, BRS openings and company details.
- **The installer could pick a Python that cannot work.** It took the newest Python (`py -3`):
  - Python 3.15 failed to install, because pyodbc has no Windows package for it yet and a normal PC can't
    compile one.
  - A 64-bit Python was chosen even where BUSY's Access driver is 32-bit.

  It now prefers versions with ready-made packages for every dependency (3.10–3.14), then the bitness of
  the Access driver installed on the PC, then the newest. It prints what it found and chose; `-Python`
  forces a specific one.
- **Running the installer again kept the old environment**, so the documented fix for a 32/64-bit mismatch
  ("install 32-bit Python and run `install.cmd` again") had no effect. `.venv` is now rebuilt when a
  different Python is chosen, with a clear message if Claude Desktop still has it open.
- The installer installs ready-made packages only, so it never tries to compile something on a PC without
  a compiler, and CI can't pass by compiling something users can't.
- CI "Lint, types, security" failed with mypy 2.4 / pydantic 2.14: tool annotations were built with
  camelCase keyword arguments and the MCP SDK 1.x fallback import confused the type checker. All SDK
  1.x / 2.x differences now live in `server/_compat.py` (dynamic fallbacks, annotations built from the
  protocol's wire names), so the code type-checks cleanly against either SDK. No behaviour change.
- `install.cmd` now returns the installer's exit code and only pauses for people (not in CI).
- `busy-mcp setup` saves folder paths as absolute paths, because AI apps start the server from another folder.

### Added
- CI job **Windows installer**, on a real Windows machine:
  - runs `install.cmd` like a double-click with 64-bit Python, then again with **32-bit Python** (the
    recommended setup), and checks that the environment was rebuilt and pyodbc loads
  - runs the guided setup against the test company, checks that the Claude Desktop config was merged and
    backed up, and runs `doctor` from another folder
  - finally lets the installer choose among all the Pythons on the machine

  `tests/windows/test_install.ps1` checks the selection rules.
- CI job **real Microsoft Access database**. Until now every test ran on SQLite, so the SQL had never
  run on Jet/ACE, the engine behind BUSY's standard edition. `pytest --backend access` copies the test
  company into a password-protected Access file in BUSY's layout (`COMP0001\db12024.bds`, BUSY's default
  password, Date/Time columns). It then runs the report tests, every MCP tool and the fast-index checks
  through Microsoft's own Access ODBC driver (32-bit Python, Windows' built-in Jet driver).
- [docs/production-checklist.md](docs/production-checklist.md): a 15-minute acceptance test against your own
  BUSY data, plus operating, update, rollback and uninstall guidance.

### Changed
- CI uses `actions/checkout@v7`, `actions/setup-python@v7` and `actions/upload-artifact@v7` (Node.js 24;
  removes the Node.js 20 deprecation warning). None of their breaking changes apply to this workflow.
- Linters and checkers are pinned (`ruff`, `mypy`, `bandit`, `pip-audit`) so local runs and CI always
  agree; Dependabot proposes upgrades through pull requests that CI checks first.
- Tests run on Python 3.10 and 3.14 (oldest and newest supported; was 3.10 and 3.13). The README now
  recommends Python 3.14, 32-bit.

## [1.1.0] — 2026-11-02

### Added — read everything
- **Generic access to every BUSY table:**
  - `catalog`: all tables grouped by BUSY feature, with meaning and confidence
  - `read-table`: any table without SQL; filters, dates, sort, paging; codes decoded to names; account or
    item names accepted for code filters
  - `find`: search names, phones, GSTIN/PAN, e-mail, invoice numbers, narrations, bill/batch/serial numbers
  - `master-details`: any master of any type, with decoded unit, tax category, MRP, prices, HSN, credit days,
    balance or stock
- **New BUSY features covered:**
  - `bills-outstanding`: bill-by-bill with due dates, overdue aging and reconciliation to the ledger
  - `pending-orders`, `bank-reconciliation` (clearing dates and BRS openings), `post-dated-cheques`
  - `stock-by-location` (material centres), `batch-stock` (expiry, MRP), `serial-numbers`
  - `gst-summary` (output vs input by head; turnover by Sale/Purchase Type), `tds-register`
  - `audit-trail` (modified / deleted vouchers, change log, user activity)
- **Fast answers:** `dashboard` (key figures in one call), `ledger-monthly` (account or whole group),
  `cash-bank-summary`.
- **`voucher-details` is now end to end:**
  - header: sale/purchase type, godown, series, audit fields
  - bill sundries, bill references
  - GST and TDS lines, serial numbers, batches
  - billing and transport details, POS payment split, packing
- Two new prompts: `stock-review`, `audit-check`.

### Performance
- In-memory **movement index** (per-day prefix sums for every account and item). Reports for any period are
  answered from memory: about 320 ms down to 4–40 ms per report on a 50,000-voucher test company.
- **Change detection** replaces fixed expiry: a cheap data fingerprint is checked at most every 10 s, and
  caches are rebuilt only when BUSY data actually changed.
- **Prefetch** builds caches in the background at start-up. Voucher-type samples now take one query instead
  of one per type.
- `scripts/benchmark.py` reproduces the measurements.

### Easy configuration
- **`busy-mcp setup`** guides you through setup:
  - finds BUSY data folders on all drives (or asks for SQL Server details) and tests the connection
  - lets you pick the default company
  - saves a config file and registers Claude Desktop (with a backup)
  - prints snippets for Cursor, VS Code and Claude Code
- **`busy-mcp doctor`** gives plain-language diagnostics (drivers, bitness, data, index build time, trial
  balance).
- **`install.cmd`** is a double-click installer that ends in the setup wizard.
- Settings can live in a JSON config file; environment variables still override it. `server-info` shows
  where each setting came from.

### Fixed (correctness)
- Item lines in RecType 7/20 (used by some BUSY versions) are detected. Independent lines are counted;
  mirrors of RecType 2 are ignored, so stock is neither missed nor doubled.
- Voucher type 1 is labelled as opening; more master types are labelled (unit, material centre, sale and
  purchase type, tax category).
- `master-details` and `find` show the real type of every master.
- GST summary takes tax ledgers from "Duties & Taxes" (`BUSY_TAX_GROUPS`), so sales/purchase ledgers named
  like "Sales GST 18%" are never counted as tax.
- `stock-by-location` keeps the opening stock of items without godown-wise opening data (shown as
  "(not allocated)"), so location totals always equal the stock summary.

### Security
- Credential protection:
  - the `Config` table (may hold GST portal credentials) is blocked in every generic path
  - credential-like columns are never returned and can't be filtered or sorted on (no row-count oracle)
  - raw SQL naming them is rejected (no alias bypass)
- The setup wizard backs up client configs, writes atomically, refuses invalid JSON, and never writes
  passwords into client configs.
- `BusyDB` is a context manager. Tests now fail on unclosed connections.

### Changed
- `server/` is split into `registry`, `state`, `tools/` (one module per feature area), `prompts` and `http`.
  The CLI moved to `busy_mcp.cli` with `serve`, `setup` and `doctor` sub-commands. `--check` and `--http`
  still work.
- 44 tools (was 27), 5 prompts (was 3), 180 tests (was 101).

## [1.0.0] — 2026-11-02

### Fixed (correctness)
- **Cancelled vouchers are now excluded** from every balance, statement, report and count. Before this,
  they were counted. The server detects `Tran1.Cancelled` / `VchCancelled` at runtime.
- **Stock direction default corrected** to positive `Tran2.Value1` = stock in. This is backed by public
  evidence, and the server still checks it against the database's own purchase vouchers.
- Voucher labels confirmed by a second, independent source: 14 Receipt, 19 Payment, 16 Journal,
  15 Contra (11 Sales Challan is single-source, shown as inferred).
- `day-book` falls back to ledger totals when a voucher's header amount is empty (e.g. journals).
- P&L classification falls back to a heuristic, and says so, when `Revenue Accounts` doesn't exist.

### Added
- Narrations (`VchOtherInfo` / `Tran1.Narration1` / `Tran2.ShortNar`), voucher series, created-by and
  modified-by fields, and a `cancelled` flag in `voucher-details`.
- Company name from `Locks.CompanyName`.
- `server-info` tool (capabilities, masked settings) and three prompts.
- Pagination (`offset`, `limit`, `next_offset`) on every list.
- `only_negative` stock filter, taxable values in `sales-purchase-analysis`, and a TB tally `warning`.
- Enum- and range-validated tool parameters; human-readable tool titles.
- `--check` now also reports detected capabilities and the trial-balance difference.

### Security
- `--http` always requires a token (one is generated if unset). Comparison is constant-time, browser
  Origins are allow-listed, and request bodies are capped at 1 MB.
- SQL guard blocks Access external-database references, Jet OS functions, `NEXT VALUE FOR`, NUL bytes
  and oversized queries.
- Tool errors are returned as proper MCP tool errors without stack traces. Unexpected errors are logged.
- Optional audit log for raw SQL, and an optional rotating log file.

### Changed
- Repository restructured into a `src/` layout with `db`, `reports` and `server` subpackages.
- CI runs on Windows and Linux, Python 3.10/3.13, and MCP SDK 1.x/2.x, with lint, types, bandit and
  pip-audit.
- Licensed under MIT.

## [0.1.0] — 2026-11-02
- First version: 26 read-only tools over BUSY's Access/SQL databases.
