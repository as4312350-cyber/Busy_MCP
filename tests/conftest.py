from __future__ import annotations

import platform
import shutil
import sys
from collections.abc import Iterator

import pytest

from busy_mcp.config import Settings, settings_from
from busy_mcp.db import BusyDB, discover_targets
from fixtures.busy_fixture import build

ACCESS_DRIVER = pytest.StashKey[str]()  # exact name of the driver that builds and reads the Access test company


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--backend", choices=["sqlite", "access"], default="sqlite",
                     help="where the test company lives: SQLite (default) or a real Microsoft Access database "
                          "in BUSY's layout (Windows, with an Access ODBC driver of this Python's bitness)")
    parser.addoption("--access-driver", choices=["auto", "jet", "ace"], default="auto",
                     help="with --backend access: the ODBC driver that builds the Access file and that busy-mcp "
                          "reads it with. auto (default) takes the Access Database Engine (ACE) if installed, else "
                          "Windows' built-in Jet driver; jet and ace insist on that driver, fail the run if it is "
                          "missing, and require the password to be set")
    parser.addoption("--access-format", choices=["mdb", "accdb"], default="mdb",
                     help="with --backend access: the Access file format, .mdb (default; Jet and ACE read it) or "
                          ".accdb (Access 2007 and later; needs ACE)")


def pytest_configure(config: pytest.Config) -> None:
    if config.getoption("--backend") != "access":
        if config.getoption("--access-driver") != "auto" or config.getoption("--access-format") != "mdb":
            raise pytest.UsageError("--access-driver and --access-format only apply with --backend access")
        return
    if sys.platform != "win32":
        pytest.exit(f"--backend access needs Windows with a Microsoft Access ODBC driver; this is "
                    f"{platform.system()}. Run it on Windows (CI jobs 'access' and 'access-ace').", returncode=4)
    try:
        import pyodbc
    except ImportError as exc:
        pytest.exit(f"--backend access needs Windows with a Microsoft Access ODBC driver ({exc})", returncode=4)
    pyodbc.pooling = False  # must be set before the first ODBC call; see fixtures/jet_fixture.py
    from fixtures.jet_fixture import access_driver

    try:
        config.stash[ACCESS_DRIVER] = access_driver(config.getoption("--access-driver"),
                                                    config.getoption("--access-format"))
    except RuntimeError as exc:
        pytest.exit(str(exc), returncode=4)


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
    config = request.config
    if config.getoption("--backend") == "access":
        from busy_mcp.db.drivers import pick_access_driver
        from fixtures.jet_fixture import build_access_company

        driver = config.stash[ACCESS_DRIVER]
        data_dir, password = build_access_company(db_path, str(tmp_path_factory.mktemp("access")), driver,
                                                  config.getoption("--access-format"),
                                                  require_password=config.getoption("--access-driver") != "auto")
        s = settings_from({"backend": "access", "data_dir": data_dir, "access_password": password,
                           "access_driver": driver, "fy": 2024})
        used = pick_access_driver(s)  # busy-mcp must read the file through the driver that built it
        if used != driver:
            raise RuntimeError(f"busy-mcp would read the test company with '{used}', not '{driver}'")
        print(f"busy-mcp reads it with access_driver '{used}'")
        return s
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
