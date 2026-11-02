from __future__ import annotations

import shutil
from collections.abc import Iterator

import pytest

from busy_mcp.config import Settings, settings_from
from busy_mcp.db import BusyDB, discover_targets
from fixtures.busy_fixture import build


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--backend", choices=["sqlite", "access"], default="sqlite",
                     help="where the test company lives: SQLite (default) or a real Microsoft Access database "
                          "in BUSY's layout (Windows, with an Access ODBC driver of this Python's bitness)")


def pytest_configure(config: pytest.Config) -> None:
    if config.getoption("--backend") == "access":
        try:
            import pyodbc
        except ImportError as exc:
            pytest.exit(f"--backend access needs Windows with a Microsoft Access ODBC driver ({exc})", returncode=4)
        pyodbc.pooling = False  # must be set before the first ODBC call; see fixtures/jet_fixture.py


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    """Never read the developer's own config file or BUSY_* variables."""
    import os

    for key in list(os.environ):
        if key.startswith("BUSY_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("BUSY_MCP_CONFIG", str(tmp_path_factory.mktemp("cfg") / "config.json"))


@pytest.fixture(scope="session")
def db_path(tmp_path_factory: pytest.TempPathFactory) -> str:
    return build(str(tmp_path_factory.mktemp("busy") / "db12024.sqlite"))


@pytest.fixture(scope="session")
def settings(db_path: str, request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> Settings:
    if request.config.getoption("--backend") == "access":
        from fixtures.jet_fixture import build_access_company

        data_dir, password = build_access_company(db_path, str(tmp_path_factory.mktemp("access")))
        return settings_from({"backend": "access", "data_dir": data_dir, "access_password": password, "fy": 2024})
    return settings_from({"backend": "sqlite", "database": db_path, "fy": 2024})


@pytest.fixture()
def db(settings: Settings) -> Iterator[BusyDB]:
    d = BusyDB(discover_targets(settings)[0], settings)
    yield d
    d.close()


@pytest.fixture()
def writable_db_path(db_path: str, tmp_path) -> str:  # type: ignore[no-untyped-def]
    """A private copy that a test may modify (simulating someone working in BUSY)."""
    target = tmp_path / "db12024.sqlite"
    shutil.copy(db_path, target)
    return str(target)
