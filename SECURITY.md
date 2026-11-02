# Security

## Threat model

busy-mcp gives an AI assistant read access to a company's complete books. The main risks are:

1. **Data is changed:** the AI, or a prompt-injected document it read, tries to change data.
2. **Data outside BUSY is read:** the AI is tricked into reading other files or databases.
3. **Credentials leak:** BUSY's `Config` table can hold GST portal credentials in plain text.
4. **The HTTP endpoint is reached:** in `serve --http` mode, someone else reaches it.
5. **Setup damages client configs:** an AI client's configuration is lost or corrupted during setup.

## Controls

| Risk | Control | Where |
|---|---|---|
| 1 | No tool writes. Every tool is annotated `readOnlyHint: true` | `server/tools/`, `server/registry.py` |
| 1 | Connections are read-only: Access `ReadOnly=1` + pyodbc `readonly=True`; SQL Server `ApplicationIntent=ReadOnly`, autocommit, no open transactions; SQLite `mode=ro` | `db/` |
| 1 | `query-database` accepts only one `SELECT`/`WITH`. It blocks DML/DDL/DCL, `INTO`, `EXEC`, `xp_`/`sp_`, `WAITFOR`, `SET`/`USE`/`DECLARE`, `NEXT VALUE FOR`, multiple statements, NUL bytes and statements over 20k characters | `sqlguard.py` |
| 1 | SQL edition: a dedicated `db_datareader` login with explicit DENY on writes | `scripts/create_readonly_sql_login.sql` |
| 2 | Blocks Access external references (`IN 'file'`, `[;DATABASE=…]`, `[Text;…]`), Jet OS functions (`Shell`, `Eval`, `Dir`, `Environ`, …) and T-SQL `OPENROWSET`/`OPENDATASOURCE`/`OPENQUERY`/`BULK` | `sqlguard.py` |
| 2 | `read-table` and `find` never take SQL text. Tables and columns must exist in the catalog; values are bind parameters; `contains` text is stripped of wildcards; `in` takes ≤ 200 values; result windows are capped at 20,000 rows | `reports/explore.py` |
| 2 | Report SQL interpolates only code constants, `int()`-cast values and catalog-resolved names (audited; see `[tool.bandit]`) | `reports/` |
| 3 | Tables in `BUSY_DENIED_TABLES` (default `Config`) are refused by `read-table`, `query-database` (in any spelling, including `[config]` and `"CONFIG"`), voucher attachments and `describe-table` samples | `sqlguard.py`, `reports/explore.py` |
| 3 | Credential-like columns (`pass`, `pwd`, `secret`, `token`, `otp`, `apikey`, `private`) are never selected or returned (masked as `***` where unavoidable). They can't be **filtered or sorted on**, which would otherwise leak them through row counts. Raw SQL that **names** one is rejected, which blocks `SELECT Password AS p` | `schema.py`, `reports/explore.py`, `sqlguard.py` |
| 3 | `server-info` masks passwords and connection strings. Driver errors never include the connection string | `config.py` |
| 4 | `serve --http` always needs a bearer token (generated if unset, minimum 16 characters), compared in constant time. Browser `Origin`s are refused unless allow-listed (blocks DNS rebinding and CSRF). Bodies are capped at 1 MB. Binds to `127.0.0.1` by default | `server/http.py`, `cli/serve.py` |
| 5 | `busy-mcp setup` backs up `claude_desktop_config.json` before changing it, writes atomically, keeps other servers, refuses to touch invalid JSON, and never writes passwords into client configs | `cli/clients.py` |
| — | Audit log of every raw SQL statement (`BUSY_AUDIT_QUERIES`, `BUSY_LOG_FILE`) | `server/tools/explore.py` |

## Residual risks (accepted)

* **Read access is total** apart from the blocked tables and columns. Salaries, party details and margins
  are all visible to the AI client. Treat it like a BUSY user with full view rights.
* **Saved queries and views.** A saved Access query or SQL view that selects from a blocked table isn't
  detected by name. BUSY doesn't ship such objects. For hard guarantees on the SQL edition, use the
  read-only login and `DENY SELECT ON Config`.
* **Heavy queries on the Access edition.** The Access ODBC driver ignores query timeouts, so a very expensive
  SELECT can slow BUSY on that PC.
* **Stored SQL password.** If SQL authentication (rather than Windows authentication) is used, the password is
  stored in the per-user config file. On Windows, `%APPDATA%` is private to the user; on Linux/macOS the file
  is `chmod 600`. Prefer Windows authentication.

## Verification

The following run in CI on every push:

* `ruff` (including the bandit `S` rules), `mypy` and `bandit -r src`
* `pip-audit` on the runtime dependencies
* 180 tests, including:
  * SQL-guard bypass attempts, blocked-table spellings and credential aliasing
  * filter-oracle protection
  * HTTP token, origin and size checks
  * Claude config backup and invalid-JSON handling
  * a check that the company database file is never modified

## Reporting a vulnerability

Please use a private security advisory on GitHub (*Security → Report a vulnerability*) instead of a public
issue.
