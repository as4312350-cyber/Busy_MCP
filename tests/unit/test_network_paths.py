r"""BUSY data in a network folder (\\SERVER\share\DATA): path logic and plain-words errors.

Real UNC paths need Windows and a server, so the network is simulated: ``os.scandir`` fails for UNC paths the
way Windows does when the server is off or the folder isn't shared with this user.
"""

from __future__ import annotations

import os
import sys
import types

import pytest

from busy_mcp.cli.doctor import run_doctor
from busy_mcp.cli.wizard import EXIT_NO_DATA, UI, run_setup
from busy_mcp.config import Settings
from busy_mcp.db import data_folder_problem, discover_targets, has_company_folders, is_network_path, is_unc_path
from busy_mcp.errors import BusyConnectionError

UNC = r"\\SERVER\BusyWin\DATA"


@pytest.mark.parametrize(("path", "expected"), [
    (UNC, True),
    (r"\\192.168.1.10\busy\DATA", True),
    ("//SERVER/BusyWin/DATA", True),
    (f'"{UNC}"', True),  # pasted with quotes
    (r"\\SERVER\BusyWin", True),  # the share itself
    (r"\\?\UNC\SERVER\BusyWin\DATA", True),
    (r"\\SERVER", False),  # a computer, not a folder
    (r"\\?\C:\BusyWin\DATA", False),
    (r"\\.\pipe\busy", False),
    (r"C:\BusyWin\DATA", False),
    ("/home/busy/DATA", False),
    ("", False),
])
def test_unc_paths_are_recognised(path, expected):
    assert is_unc_path(path) is expected


def test_network_path(tmp_path):
    assert is_network_path(UNC)
    assert not is_network_path(str(tmp_path))  # on Windows this asks the drive type of a local disk


def _network_down(monkeypatch: pytest.MonkeyPatch, error: OSError) -> None:
    """Make every UNC folder fail to open with `error`, as Windows does; local folders are untouched."""
    real = os.scandir

    def scandir(path="."):  # type: ignore[no-untyped-def]
        if is_unc_path(os.fspath(path)):
            raise error
        return real(path)

    monkeypatch.setattr(os, "scandir", scandir)


def test_unreachable_network_folder_says_why(monkeypatch):
    _network_down(monkeypatch, FileNotFoundError(53, "The network path was not found"))
    with pytest.raises(BusyConnectionError) as err:
        discover_targets(Settings(backend="access", data_dir=UNC))
    text = str(err.value)
    assert "not found" in text and UNC in text and "The network path was not found" in text
    assert "network folder" in text and "File Explorer" in text and "BUSY_DATA_DIR" in text


def test_unreachable_mapped_drive_suggests_the_network_path(monkeypatch):
    from busy_mcp.db import discovery

    mapped = r"Z:\BusyWin\DATA"
    _network_down(monkeypatch, FileNotFoundError(3, "The system cannot find the path specified"))
    real = os.scandir

    def scandir(path="."):  # type: ignore[no-untyped-def]
        if os.fspath(path) == mapped:
            raise FileNotFoundError(3, "The system cannot find the path specified")
        return real(path)  # the UNC failure installed above

    monkeypatch.setattr(os, "scandir", scandir)
    # GetDriveTypeW would report Z: as a network drive
    monkeypatch.setattr(discovery, "is_network_path", lambda path: path == mapped or is_unc_path(path))
    problem = data_folder_problem(mapped) or ""
    assert "network folder" in problem and "drive letter" in problem and "\\\\SERVER\\share" in problem
    unc_problem = data_folder_problem(UNC) or ""
    assert "network folder" in unc_problem and "drive letter" not in unc_problem  # no advice to use what it is


def test_folder_without_permission_says_why(monkeypatch):
    _network_down(monkeypatch, PermissionError(13, "Access is denied"))
    problem = data_folder_problem(UNC)
    assert problem is not None and "cannot be read" in problem and "Access is denied" in problem
    assert not has_company_folders(UNC)


def test_local_folder_messages(tmp_path):
    assert "not found" in (data_folder_problem(str(tmp_path / "missing")) or "")
    assert "network folder" not in (data_folder_problem(str(tmp_path / "missing")) or "")
    assert "does not contain BUSY company folders" in (data_folder_problem(str(tmp_path)) or "")
    company = tmp_path / "COMP0001"
    company.mkdir()
    (company / "db12024.bds").write_bytes(b"")
    assert data_folder_problem(str(tmp_path)) is None


def test_empty_data_dir_setting_is_explained():
    for empty in ("", None):
        with pytest.raises(BusyConnectionError, match="No BUSY data folder is configured"):
            discover_targets(Settings(backend="access", data_dir=empty))  # type: ignore[arg-type]


def test_unreadable_company_folder_does_not_hide_the_others(monkeypatch, tmp_path):
    for comp in ("COMP0001", "COMP0002"):
        (tmp_path / comp).mkdir()
        (tmp_path / comp / "db12024.bds").write_bytes(b"")
    locked = str(tmp_path / "COMP0002")
    real = os.scandir

    def scandir(path="."):  # type: ignore[no-untyped-def]
        if os.fspath(path) == locked:
            raise PermissionError(13, "Access is denied")
        return real(path)

    monkeypatch.setattr(os, "scandir", scandir)
    assert [t.key for t in discover_targets(Settings(backend="access", data_dir=str(tmp_path)))] == ["COMP0001/2024"]


def test_setup_explains_an_unreachable_network_folder(monkeypatch):
    _network_down(monkeypatch, FileNotFoundError(53, "The network path was not found"))
    monkeypatch.setattr(os.path, "abspath", lambda p: p)  # Linux would prefix the current folder to a UNC path
    lines: list[str] = []
    args = type("Args", (), {"data_dir": UNC, "sql_server": None, "sql_user": None, "company": None,
                             "backend": None, "database": None, "claude_config": None, "no_claude": True,
                             "yes": True})()
    assert run_setup(args, UI(True, out=lines.append)) == EXIT_NO_DATA
    text = "\n".join(lines)
    assert "The network path was not found" in text and "network folder" in text
    assert "does not contain BUSY company folders" not in text  # the old, misleading message


def test_doctor_explains_an_unreachable_network_folder(monkeypatch):
    _network_down(monkeypatch, FileNotFoundError(53, "The network path was not found"))
    driver = types.SimpleNamespace(version="test", drivers=lambda: ["Microsoft Access Driver (*.mdb)"])
    monkeypatch.setitem(sys.modules, "pyodbc", driver)  # the doctor stops early where no ODBC manager is installed
    monkeypatch.setenv("BUSY_BACKEND", "access")
    monkeypatch.setenv("BUSY_DATA_DIR", UNC)
    lines: list[str] = []
    assert run_doctor(lines.append) == 1
    fail = [x for x in lines if x.startswith("[FAIL]") and UNC in x]
    assert fail and "The network path was not found" in fail[0] and "network folder" in fail[0]
