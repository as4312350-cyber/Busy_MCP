"""The movement index must give exactly the same answers as direct SQL, and caches
must refresh when someone changes data in BUSY."""

from __future__ import annotations

import datetime as dt
import sqlite3

import pytest

from busy_mcp import reports
from busy_mcp.config import settings_from
from busy_mcp.db import BusyDB, discover_targets
from busy_mcp.reports._common import LEDGER_INDEX, ledger_movements
from busy_mcp.reports._index import MovementIndex

RANGES = [(None, dt.date(2025, 3, 31)), (dt.date(2024, 5, 1), dt.date(2024, 6, 30)),
          (dt.date(2024, 6, 2), dt.date(2024, 6, 2)), (dt.date(2024, 4, 1), dt.date(2024, 4, 9))]


@pytest.mark.parametrize(("start", "end"), RANGES)
def test_index_equals_direct_query(settings, start, end):
    target = discover_targets(settings)[0]
    with BusyDB(target, settings) as direct_db, BusyDB(target, settings) as indexed_db:
        reports.ledger_index(indexed_db)
        for code in (202, 203, 204, 205, 206, 999):
            assert not direct_db.is_cached(LEDGER_INDEX)
            direct = ledger_movements(direct_db, start, end, ledger=code)
            indexed = ledger_movements(indexed_db, start, end, ledger=code)
            assert direct == indexed, (code, start, end)


def test_movement_index_math():
    idx = MovementIndex(2, [(1, dt.date(2024, 4, 1), (10.0, 0.0)), (1, dt.date(2024, 4, 1), (5.0, 1.0)),
                            (1, dt.date(2024, 5, 3), (0.0, 7.0)), (2, dt.date(2024, 4, 2), (3.0, 3.0))])
    assert idx.range(1, None, dt.date(2024, 4, 30)) == (15.0, 1.0)
    assert idx.range(1, dt.date(2024, 4, 2), dt.date(2024, 12, 31)) == (0.0, 7.0)
    assert idx.range(1, dt.date(2024, 6, 1), dt.date(2024, 6, 30)) == (0.0, 0.0)
    assert idx.range(42, None, dt.date(2030, 1, 1)) == (0.0, 0.0)
    assert idx.all_ranges(dt.date(2024, 4, 2), dt.date(2024, 4, 2)) == {2: (3.0, 3.0)}
    months = idx.monthly([1, 2], dt.date(2024, 4, 1), dt.date(2024, 5, 31))
    assert months == {"2024-04": [18.0, 4.0], "2024-05": [0.0, 7.0]}
    assert idx.stats() == {"codes": 2, "daily_rows": 3}


def test_caches_refresh_when_busy_data_changes(writable_db_path):
    s = settings_from({"backend": "sqlite", "database": writable_db_path, "fy": 2024, "freshness_seconds": 0})
    db = BusyDB(discover_targets(s)[0], s)
    db.ensure_fresh()
    assert reports.ledger_balance(db, "Cash", None)["closing_balance"] == "16,500.00 Dr"
    reports.trial_balance(db, None, None)
    assert db.is_cached(LEDGER_INDEX)
    assert db.ensure_fresh() is False  # nothing changed

    con = sqlite3.connect(writable_db_path)  # someone enters a cash sale in BUSY
    con.execute("INSERT INTO Tran1 (VchCode, VchType, VchNo, Date, MasterCode1, VchAmtBaseCur) "
                "VALUES (99, 9, 'S/99', '2024-10-01', 203, 1000)")
    con.executemany("INSERT INTO Tran2 (VchCode, SrNo, RecType, MasterCode1, Value1, Date) VALUES (?,?,?,?,?,?)",
                    [(99, 1, 1, 203, -1000.0, "2024-10-01"), (99, 2, 1, 206, 1000.0, "2024-10-01")])
    con.commit()
    con.close()

    assert db.ensure_fresh() is True
    assert not db.is_cached(LEDGER_INDEX)
    assert reports.ledger_balance(db, "Cash", None)["closing_balance"] == "17,500.00 Dr"
    assert reports.trial_balance(db, None, None)["totals"]["net_difference"] == "0.00"
    assert db.cache_info()["seconds_since_data_change"] is not None
    db.close()


def test_freshness_check_is_rate_limited(writable_db_path):
    s = settings_from({"backend": "sqlite", "database": writable_db_path, "fy": 2024, "freshness_seconds": 3600})
    with BusyDB(discover_targets(s)[0], s) as db:
        db.ensure_fresh()
        assert db.ensure_fresh() is False  # within the window: not even checked
        assert db.ensure_fresh(force=True) is True
