## What & why

## How it was verified
- [ ] `ruff check src tests`, `mypy`, `bandit -r src -c pyproject.toml` pass
- [ ] `pytest` passes
- [ ] Checked against a real BUSY database (version/edition: ___ ) or N/A

## Read-only guarantee
- [ ] No tool, query or connection option in this change can modify BUSY data
