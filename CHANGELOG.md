# Changelog

All notable changes are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/),
and versions follow [Semantic Versioning](https://semver.org/).

## [1.1.1] — 2026-11-02

### Fixed
- CI "Lint, types, security" failed with mypy 2.4 / pydantic 2.14: tool annotations were built with
  camelCase keyword arguments and the MCP SDK 1.x fallback import confused the type checker. All SDK
  1.x / 2.x differences now live in `server/_compat.py` (dynamic fallbacks, annotations built from the
  protocol's wire names), so the code type-checks cleanly against either SDK. No behaviour change.

- `install.cmd` now returns the installer's exit code and only pauses for people (not in CI).
- `busy-mcp setup` saves folder paths as absolute paths, because AI apps start the server from another folder.

### Added
- CI job **Windows installer**: on a real Windows machine it runs `install.cmd` exactly like a double-click,
  then the guided setup against the test company. It checks that the Claude Desktop config was merged and
  backed up, then runs `doctor` from another folder.
- [docs/production-checklist.md](docs/production-checklist.md): a 15-minute acceptance test against your own
  BUSY data, plus operating, update, rollback and uninstall guidance.

### Changed
- CI uses `actions/checkout@v7`, `actions/setup-python@v7` and `actions/upload-artifact@v7` (Node.js 24;
  removes the Node.js 20 deprecation warning). None of their breaking changes apply to this workflow.
- Linters and checkers are pinned (`ruff`, `mypy`, `bandit`, `pip-audit`) so local runs and CI always
  agree; Dependabot proposes upgrades through pull requests that CI checks first.

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
