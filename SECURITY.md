# Security

## Threat model

busy-mcp gives an AI assistant read access to a company's complete books. The main risks are:

1. **The AI (or a prompt-injected document it read) tries to change data.**
2. **The AI is tricked into reading files or databases outside BUSY.**
3. **In `--http` mode, someone else on the network or internet reaches the endpoint.**
4. **Secrets leak**, for example through tool output.

## Controls

| Risk | Control | Where |
|---|---|---|
| 1 | No tool writes. Every tool is annotated `readOnlyHint: true` | `server/app.py` |
| 1 | Connections are opened read-only: Access `ReadOnly=1` plus pyodbc `readonly=True`; SQL Server `ApplicationIntent=ReadOnly`, autocommit, no open transactions | `db/drivers.py`, `db/connection.py` |
| 1 | `query-database` accepts only one `SELECT`/`WITH` statement. It blocks DML, DDL and DCL, `INTO`, `EXEC`, `xp_`/`sp_` calls, `WAITFOR`, `SET`/`USE`/`DECLARE`, `NEXT VALUE FOR`, multiple statements, NUL bytes, and statements over 20k characters | `sqlguard.py` |
| 1 | SQL edition: a dedicated `db_datareader` login with explicit DENY on every write permission | `scripts/create_readonly_sql_login.sql` |
| 2 | Blocks Access external references (`IN 'file'`, `[;DATABASE=…]`, `[Text;…]`), Jet expression functions that touch the OS (`Shell`, `Eval`, `Dir`, `Environ`, …), and T-SQL `OPENROWSET`/`OPENDATASOURCE`/`OPENQUERY`/`BULK` | `sqlguard.py` |
| 2 | All report SQL uses bind parameters. Interpolated fragments are code constants, `int()`-cast values or catalog-resolved table names (audited, see `[tool.bandit]` in `pyproject.toml`) | `reports/` |
| 2 | `describe-table` only accepts `[A-Za-z0-9_]+` names that exist in the database catalog | `server/app.py`, `db/connection.py` |
| 3 | `--http` always requires a bearer token (generated if unset, minimum 16 characters), compared in constant time | `server/http.py`, `server/cli.py` |
| 3 | Requests with a browser `Origin` header are rejected unless allow-listed (blocks DNS-rebinding/CSRF from web pages) | `server/http.py` |
| 3 | Request bodies over 1 MB are rejected. The server binds to `127.0.0.1` by default | `server/http.py` |
| 4 | `server-info` masks passwords and connection strings. Driver errors never include the connection string | `config.py` |
| — | Optional audit log of every raw SQL statement (`BUSY_AUDIT_QUERIES`, `BUSY_LOG_FILE`) | `server/app.py` |

## Residual risks (accepted)

* **Read access is total.** Anything in the BUSY database is readable: salaries, party details, and
  any GST portal credentials BUSY stores in its `Config` table. Treat the AI client as having the same
  access as a BUSY user with full view rights.
* **Heavy queries on the Access edition.** The Access ODBC driver ignores query timeouts, so a very
  expensive SELECT can slow BUSY on that PC.
* **Secrets in client config.** Passwords set in an MCP client config sit in plain text in that file.
  Prefer Windows authentication (SQL edition) and restrict the file's permissions.

## Verification (1.0.0)

The following run in CI on every push:

* `ruff` (including the flake8-bandit `S` rules)
* `mypy`
* `bandit -r src`
* `pip-audit` on the runtime dependencies
* 101 tests, including SQL-guard bypass attempts, HTTP auth/origin/size checks, and a check that the
  database file is never modified

## Reporting a vulnerability

Please open a private security advisory on GitHub (*Security → Report a vulnerability*) instead of a
public issue.
