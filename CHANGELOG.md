# Changelog

All notable changes are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/),
and versions follow [Semantic Versioning](https://semver.org/).

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
