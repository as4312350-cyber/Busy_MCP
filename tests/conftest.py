from __future__ import annotations

import pytest

from busy_mcp.config import Settings
from busy_mcp.db import BusyDB, discover_targets
from fixtures.busy_fixture import build


@pytest.fixture(scope="session")
def db_path(tmp_path_factory: pytest.TempPathFactory) -> str:
    return build(str(tmp_path_factory.mktemp("busy") / "db12024.sqlite"))


@pytest.fixture(scope="session")
def settings(db_path: str) -> Settings:
    return Settings(backend="sqlite", database=db_path, fy=2024)


@pytest.fixture()
def db(settings: Settings) -> BusyDB:
    d = BusyDB(discover_targets(settings)[0], settings)
    yield d
    d.close()
