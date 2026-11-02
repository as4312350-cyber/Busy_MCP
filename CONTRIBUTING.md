# Contributing

## Setup

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pre-commit install                                 # optional
```

## Checks (all run in CI)

```bash
make check          # or run them one by one:
ruff check src tests
mypy
bandit -q -r src -c pyproject.toml
pytest --cov
```

`python scripts/benchmark.py` measures report speed on a synthetic 50,000-voucher company.

## Layout

```
src/busy_mcp/
  config.py, errors.py, schema.py, sqlguard.py, logging_setup.py
  db/        connection, discovery, drivers
  reports/   pure report functions (no MCP imports)
  server/    MCP wiring: registry, state, tools/ (one module per feature area), prompts, http
  cli/       busy-mcp serve | setup | doctor
tests/
  fixtures/      a small company in BUSY's exact layout, with the story and expected numbers
  unit/          reports, features, index & freshness, explorer, SQL guard, config, CLI, in-process tools
  integration/   real stdio server subprocess, HTTP security
  windows/       installer checks (PowerShell; run in CI on Windows)
docs/          user and developer documentation
scripts/       installer, read-only SQL login, benchmark
examples/      client configuration files
```

## Rules

1. **Read-only, always.** No tool, connection option or query may change BUSY data. PRs that add writes will
   not be merged.
2. **Portable SQL.** Report SQL must run on Jet/ACE, T-SQL and SQLite. That means single `INNER JOIN`s,
   `IIF`, bind parameters, `[Date]` quoting, and `db.top()`/`db.limit()`. Prefer the movement index over new
   aggregate queries.
3. **Probe, don't assume.** Columns that may be missing in some BUSY versions are checked with
   `db.has_column()` / `db.existing_columns()`.
4. **Evidence for schema facts.** New type codes or column meanings go into `src/busy_mcp/schema.py` and
   `docs/busy-internals.md` with a source and a confidence tag. Labels without two sources stay `likely`.
5. **Say when it's an estimate.** Derived or approximate figures carry `method`, `confidence` or `caveat`.
6. **Never leak credentials.** Generic readers go through `decode_row`; new raw-SQL paths go through
   `sqlguard`.
7. **Tests with hand-computed expectations.** Extend the story in `tests/fixtures/busy_fixture.py` and assert
   exact numbers. Close every `BusyDB` (`with BusyDB(...) as db:`).

## Releasing

1. Update `__version__` in `src/busy_mcp/__init__.py`, plus `CHANGELOG.md`.
2. Tag `vX.Y.Z`. The CI `build` job produces the wheel and sdist.
