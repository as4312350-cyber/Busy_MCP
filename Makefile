.PHONY: install lint typecheck security test check benchmark build clean

install:
	pip install -e ".[dev]"

lint:
	ruff check src tests

typecheck:
	mypy

security:
	bandit -q -r src -c pyproject.toml

test:
	pytest --cov

check: lint typecheck security test

benchmark:
	python scripts/benchmark.py

build:
	python -m build

clean:
	rm -rf build dist .pytest_cache .mypy_cache .ruff_cache .coverage src/*.egg-info
