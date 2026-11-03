"""Access drivers: which one busy-mcp uses, the file's format, and the fixes it gives.

Runs anywhere: pyodbc is replaced by a fake module (the real drivers are tested by ``--backend access`` in CI).
"""

from __future__ import annotations

import subprocess
import sys
import types
from collections.abc import Callable
from pathlib import Path

import pytest

from busy_mcp.cli.doctor import run_doctor
from busy_mcp.config import Settings
from busy_mcp.db import BusyDB, Target, access_conn_str, drivers
from busy_mcp.db.connection import connect_hint, is_unrecognized_format, unrecognized_format_hint
from busy_mcp.errors import BusyConnectionError
from fixtures.jet_fixture import access_driver, describe_header

JET = "Microsoft Access Driver (*.mdb)"
ACE = "Microsoft Access Driver (*.mdb, *.accdb)"
OTHERS = ["SQL Server", "Microsoft Access dBASE Driver (*.dbf, *.ndx, *.mdx)", "Microsoft Excel Driver (*.xls)"]
# What Windows' Jet driver says when it is given an .accdb file.
JET_ON_ACCDB = ("('HY000', \"[HY000] [Microsoft][ODBC Microsoft Access Driver] Unrecognized database format "
                "'C:\\\\BusyWin\\\\DATA\\\\COMP0001\\\\db12024.bds'. (-1206) (SQLDriverConnect)\")")
JET4_HEADER = b"\x00\x01\x00\x00Standard Jet DB\x00\x01"
ACE12_HEADER = b"\x00\x01\x00\x00Standard ACE DB\x00\x02"
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def fake_pyodbc(monkeypatch: pytest.MonkeyPatch) -> Callable[..., types.SimpleNamespace]:
    """Install a stand-in pyodbc: these drivers are installed, and connect() fails with `connect_error`."""

    def install(installed: list[str], connect_error: str = "connect() is not available here") -> types.SimpleNamespace:
        class Error(Exception):
            pass

        def connect(conn_str: str, **kwargs: object) -> None:
            raise Error(connect_error)

        fake = types.SimpleNamespace(drivers=lambda: list(installed), connect=connect, Error=Error,
                                     version="fake", pooling=True)
        monkeypatch.setitem(sys.modules, "pyodbc", fake)
        return fake

    return install


# ------------------------------------------------------------------ driver choice


@pytest.mark.parametrize(("installed", "expected"), [
    ([*OTHERS, JET, ACE], ACE),  # the default setting: the Access Database Engine
    ([*OTHERS, JET], JET),  # only Windows' built-in Jet driver
    ([ACE, *OTHERS], ACE),
])
def test_default_driver(fake_pyodbc, installed, expected):
    fake_pyodbc(installed)
    assert drivers.pick_access_driver(Settings()) == expected
    assert f"DRIVER={{{expected}}};" in access_conn_str(Settings(), r"C:\BusyWin\DATA\COMP0001\db12024.bds")


def test_configured_driver_and_fallback(fake_pyodbc):
    fake_pyodbc([JET, ACE])
    assert drivers.pick_access_driver(Settings(access_driver=JET)) == JET  # an installed setting wins
    # A setting that isn't installed falls back to the Access Database Engine before Jet, whatever the list order.
    assert drivers.pick_access_driver(Settings(access_driver="Microsoft Access Driver (typo)")) == ACE
    fake_pyodbc(OTHERS)
    with pytest.raises(BusyConnectionError, match="Access Database Engine 2016 Redistributable"):
        drivers.pick_access_driver(Settings())


def test_driver_kinds():
    assert drivers.access_drivers([JET, *OTHERS, ACE]) == [ACE, JET]
    assert drivers.is_ace_driver(ACE) and not drivers.is_ace_driver(JET)
    assert not drivers.is_access_driver("Microsoft Access dBASE Driver (*.dbf, *.ndx, *.mdx)")


# ------------------------------------------------------------------ file format


def test_access_file_format(tmp_path):
    cases = {"jet4.bds": (JET4_HEADER, "mdb", "Jet 4, Access 2000-2003"),
             "jet3.bds": (JET4_HEADER[:20] + b"\x00", "mdb", "Jet 3, Access 97"),
             "ace12.bds": (ACE12_HEADER, "accdb", "ACE 12, Access 2007"),
             "ace16.bds": (ACE12_HEADER[:20] + b"\x05", "accdb", "ACE 16"),
             "ace99.bds": (ACE12_HEADER[:20] + b"\x63", "accdb", "version byte 0x63")}
    for name, (head, kind, label) in cases.items():
        (tmp_path / name).write_bytes(head + bytes(64))
        found, text = drivers.access_file_format(str(tmp_path / name))
        assert found == kind and text.startswith(f".{kind} ({label}); "), (name, text)
    assert drivers.access_file_format(str(tmp_path / "jet4.bds"))[1].endswith("can both read it")
    assert drivers.access_file_format(str(tmp_path / "ace12.bds"))[1].endswith(
        "only the Microsoft Access Database Engine (ACE) can read it")
    (tmp_path / "sqlite.bds").write_bytes(b"SQLite format 3\x00" + bytes(64))
    assert drivers.access_file_format(str(tmp_path / "sqlite.bds")) == (
        "", "not an Access database (first bytes: 53 51 4c 69 74 65 20 66 6f 72 6d 61 74 20 33 00 00 00 00 00 00)")
    (tmp_path / "empty.bds").write_bytes(b"")
    assert drivers.access_file_format(str(tmp_path / "empty.bds")) == ("", "not an Access database (first bytes: none)")
    assert drivers.access_file_format(str(tmp_path / "missing.bds"))[1].startswith("unknown (cannot read the file")


# ------------------------------------------------------------------ hints


def test_unrecognized_format_hint_depends_on_the_driver():
    assert is_unrecognized_format(JET_ON_ACCDB)
    assert is_unrecognized_format("('HY000', '[HY000] <the same error in the Windows language> (-1206)')")
    assert not is_unrecognized_format("('HY000', '[HY000] Not a valid password. (-1905)')")
    bits = drivers.python_bits()
    jet = connect_hint(JET_ON_ACCDB, "access", f"DRIVER={{{JET}}};DBQ=x.bds;ReadOnly=1;Exclusive=0;")
    assert jet == ("this BUSY file needs the Microsoft Access Database Engine (ACE). Install the Microsoft Access "
                   "Database Engine 2016 Redistributable with the same bitness as this Python "
                   f"({'x64' if bits == 64 else 'x86'} for this {bits}-bit Python), then run 'busy-mcp doctor'")
    ace = connect_hint(JET_ON_ACCDB, "access", f"DRIVER={{{ACE}}};DBQ=x.bds;")
    assert ace.startswith(f"the Access Database Engine ('{ACE}') could not read this file")
    assert "damaged" in ace and "BUSY itself opens this company" in ace
    # A custom ODBC connection without DRIVER=: the usual cause is still Jet.
    assert connect_hint(JET_ON_ACCDB, "odbc", "DSN=Busy;") == unrecognized_format_hint("") == jet


def test_other_connect_hints_are_unchanged():
    assert connect_hint("Not a valid password.", "access", "") == "wrong .bds password: set BUSY_ACCESS_PASSWORD"
    assert connect_hint("Not a valid password.", "sqlserver", "") == ""
    assert connect_hint("[IM002] Data source name not found", "access", "") == (
        "ODBC driver missing or 32/64-bit mismatch with this Python")
    assert connect_hint("Login timeout expired", "sqlserver", "") == ""


def test_opening_an_accdb_file_with_jet_gives_the_fix(fake_pyodbc, tmp_path):
    bds = tmp_path / "db12024.bds"
    bds.write_bytes(ACE12_HEADER)
    fake_pyodbc([JET], connect_error=JET_ON_ACCDB)
    db = BusyDB(Target("access", "COMP0001", 2024, str(bds)), Settings(backend="access"))
    with pytest.raises(BusyConnectionError) as ei:
        db.table_names()
    assert str(ei.value) == f"Cannot open COMP0001/2024: {JET_ON_ACCDB} ({unrecognized_format_hint(JET)})"


# ------------------------------------------------------------------ doctor


def _doctor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, header: bytes, **env: str) -> tuple[int, list[str]]:
    company = tmp_path / "DATA" / "COMP0001"
    company.mkdir(parents=True)
    (company / "db12024.bds").write_bytes(header + bytes(64))
    monkeypatch.setenv("BUSY_BACKEND", "access")
    monkeypatch.setenv("BUSY_DATA_DIR", str(tmp_path / "DATA"))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    lines: list[str] = []
    return run_doctor(lines.append), lines


def test_doctor_with_only_jet_and_an_accdb_file(monkeypatch, tmp_path, fake_pyodbc):
    fake_pyodbc(["SQL Server", JET], connect_error=JET_ON_ACCDB)
    code, lines = _doctor(monkeypatch, tmp_path, ACE12_HEADER)
    text = "\n".join(lines)
    assert code == 1
    bits = drivers.python_bits()
    assert f"[INFO] Access drivers for this {bits}-bit Python: {JET}; busy-mcp will use '{JET}'" in lines
    assert any(x.startswith("[INFO] Only Windows' built-in Jet driver is installed.") and
               "newer BUSY files may need the Microsoft Access Database Engine (ACE)" in x for x in lines)
    assert ("[INFO] File format of COMP0001/2024: .accdb (ACE 12, Access 2007); only the Microsoft Access "
            "Database Engine (ACE) can read it") in lines
    fail = next(x for x in lines if x.startswith("[FAIL]"))
    assert fail == f"[FAIL] Cannot read COMP0001/2024: Cannot open COMP0001/2024: {JET_ON_ACCDB} " \
                   f"({unrecognized_format_hint(JET)})"
    assert lines[lines.index(fail) + 1] == f"[INFO] Fix: {unrecognized_format_hint(JET)}"
    assert text.endswith("Result: problems found - fix the [FAIL] lines above.")


def test_doctor_prefers_ace_and_names_a_missing_setting(monkeypatch, tmp_path, fake_pyodbc):
    fake_pyodbc([JET, ACE], connect_error="[HY000] something else")
    code, lines = _doctor(monkeypatch, tmp_path, JET4_HEADER, BUSY_ACCESS_DRIVER="Microsoft Access Driver (typo)")
    text = "\n".join(lines)
    assert (f"Python: {ACE}, {JET}; busy-mcp will use '{ACE}' (the access_driver setting "
            "'Microsoft Access Driver (typo)' is not installed)") in text
    assert "Only Windows' built-in Jet driver" not in text
    assert "File format of COMP0001/2024: .mdb (Jet 4, Access 2000-2003)" in text
    assert code == 1 and "[INFO] Fix:" not in text  # it failed for another reason: no ACE advice


def test_doctor_without_an_access_driver(monkeypatch, tmp_path, fake_pyodbc):
    fake_pyodbc(["SQL Server"])
    code, lines = _doctor(monkeypatch, tmp_path, JET4_HEADER)
    bits = drivers.python_bits()
    assert code == 1
    assert (f"[FAIL] No Microsoft Access ODBC driver for this {bits}-bit Python. Install the 'Microsoft Access "
            f"Database Engine 2016 Redistributable' ({'x64' if bits == 64 else 'x86'}) or use Python of the other "
            "bitness (BUSY usually installs the 32-bit engine).") in lines
    assert not any("busy-mcp will use" in x for x in lines)


# ------------------------------------------------------------------ the --backend access test fixture


def test_fixture_driver_choice():
    both = [*OTHERS, JET, ACE]
    assert access_driver("auto", "mdb", both) == ACE  # as before: the Access Database Engine when installed
    assert access_driver("auto", "mdb", [JET]) == JET
    assert access_driver("jet", "mdb", both) == JET
    assert access_driver("ace", "mdb", both) == ACE
    assert access_driver("ace", "accdb", both) == access_driver("auto", "accdb", both) == ACE
    for kind, fmt, installed, message in [
        ("ace", "mdb", [JET], "needs the Access Database Engine's ODBC driver"),
        ("auto", "accdb", [JET], "needs the Access Database Engine's ODBC driver"),
        ("jet", "accdb", both, r"Jet driver can't read \.accdb files"),
        ("jet", "mdb", [ACE], "needs Windows' built-in Jet ODBC driver"),
        ("auto", "mdb", OTHERS, "needs a Microsoft Access ODBC driver"),
    ]:
        with pytest.raises(RuntimeError, match=message):
            access_driver(kind, fmt, installed)


def test_fixture_header_description():
    assert describe_header(ACE12_HEADER) == ("00 01 00 00 53 74 61 6e 64 61 72 64 20 41 43 45 20 44 42 00 02 = "
                                             "'Standard ACE DB', version byte 0x02 (ACE 12)")
    assert describe_header(b"\x00\x01") == "00 01 (too short for an Access file)"


def _pytest(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "--co", "-q", *args,
                           "tests/unit/test_sqlguard.py"], cwd=ROOT, capture_output=True, text=True, timeout=120)


def test_access_options_need_the_access_backend():
    run = _pytest("--access-format", "accdb")
    assert run.returncode == 4 and "--access-driver and --access-format only apply with --backend access" in run.stderr


@pytest.mark.skipif(sys.platform == "win32", reason="checks the message shown off Windows")
def test_access_backend_exits_cleanly_off_windows():
    run = _pytest("--backend", "access", "--access-driver", "ace", "--access-format", "accdb")
    assert run.returncode == 4
    assert "--backend access needs Windows with a Microsoft Access ODBC driver" in run.stderr


# The exact text Windows Server 2025's Jet driver gave for an .accdb file in CI (run 41453464987).
JET_1028 = ("('HY000', \"[HY000] [Microsoft][ODBC Microsoft Access Driver] Cannot open database '(unknown)'.  It may "
            "not be a database that your application recognizes, or the file may be corrupt. (-1028) "
            "(SQLDriverConnect)\")")


def test_jet_cannot_open_accdb_wording_gets_the_ace_fix():
    assert is_unrecognized_format(JET_1028)
    hint = connect_hint(JET_1028, "access", f"DRIVER={{{JET}}};DBQ=x.bds;")
    assert "Access Database Engine" in hint
