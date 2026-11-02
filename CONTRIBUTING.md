# Contributing

## Setup

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pre-commit install                                 # optional
```

## Checks (all run in CI)

```bash
ruff check src tests
mypy
bandit -q -r src -c pyproject.toml
pytest --cov
```

## Layout

```
src/busy_mcp/        package (see docs/architecture.md)
tests/fixtures/      SQLite database with BUSY's table layout and sign conventions
tests/unit/          reports, SQL guard, discovery, config, in-process tool calls
tests/integration/   real stdio server subprocess, HTTP security
docs/                user and developer documentation
scripts/             Windows installer, read-only SQL login
examples/            client configuration files
```

## Rules

1. **Read-only, always.** No tool, connection option or query may change BUSY data. PRs that add
   writes will not be merged.
2. **Portable SQL.** Report SQL must run on Jet/ACE, T-SQL and SQLite. That means single `INNER JOIN`s,
   `IIF`, bind parameters, `[Date]` quoting, and `db.top()`/`db.limit()`.
3. **Probe, don't assume.** Columns that may be missing in some BUSY versions are checked with
   `db.has_column()`.
4. **Evidence for schema facts.** Any new type code or column meaning goes into
   `docs/busy-internals.md` with its source and a confidence tag. Labels without two sources stay
   `likely` (shown as `(inferred)`).
5. **Say when it's an estimate.** If a figure is derived or approximate, the output says so
   (`caveat`, `method`, `warning`).
6. **Tests with hand-computed expectations.** Extend `tests/fixtures/busy_fixture.py` and assert exact
   numbers.

## Releasing

1. Update `__version__` in `src/busy_mcp/__init__.py`, plus `CHANGELOG.md`.
2. Tag `vX.Y.Z`. The CI `build` job produces the wheel and sdist.
