"""Benchmark the movement index on a synthetic BUSY-shaped database.

    python scripts/benchmark.py [--vouchers 50000]

Builds a SQLite database with BUSY's layout (2,000 accounts, 1,000 items,
N vouchers of ~4 lines each), then times:
  * the per-report aggregate SQL that version 1.0 ran on every report call,
  * building the movement index once,
  * common reports once the index exists.
Absolute numbers on Access/SQL Server differ; the ratio is what matters.
"""

from __future__ import annotations

import argparse
import random
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]

from busy_mcp import reports  # noqa: E402
from busy_mcp.config import settings_from  # noqa: E402
from busy_mcp.db import BusyDB, discover_targets  # noqa: E402
from fixtures.busy_fixture import build  # noqa: E402


def populate(path: str, vouchers: int, seed: int = 7) -> None:
    rnd = random.Random(seed)
    con = sqlite3.connect(path)
    parties = list(range(10_000, 12_000))
    items = list(range(20_000, 21_000))
    con.executemany("INSERT INTO Master1 (Code, MasterType, Name, ParentGrp) VALUES (?,2,?,110)",
                    [(c, f"Party {c}") for c in parties])
    con.executemany("INSERT INTO Master1 (Code, MasterType, Name, ParentGrp) VALUES (?,6,?,501)",
                    [(c, f"Item {c}") for c in items])
    t1, t2 = [], []
    for v in range(1000, 1000 + vouchers):
        day = f"2024-{rnd.randint(4, 12):02d}-{rnd.randint(1, 28):02d}"
        party, item = rnd.choice(parties), rnd.choice(items)
        qty, rate = rnd.randint(1, 20), rnd.randint(50, 500)
        amount = qty * rate
        t1.append((v, 9, f"S/{v}", day, party, amount * 1.18, amount))
        t2 += [(v, 1, 1, party, -amount * 1.18, 0, day), (v, 2, 1, 206, amount, 0, day),
               (v, 3, 1, 209, amount * 0.18, 0, day), (v, 1, 2, item, -qty, -amount, day)]
    con.executemany("INSERT INTO Tran1 (VchCode, VchType, VchNo, Date, MasterCode1, VchAmtBaseCur, VchSalePurcAmt) "
                    "VALUES (?,?,?,?,?,?,?)", t1)
    con.executemany("INSERT INTO Tran2 (VchCode, SrNo, RecType, MasterCode1, Value1, Value3, Date) "
                    "VALUES (?,?,?,?,?,?,?)", t2)
    con.execute("CREATE INDEX ix_t2_vch ON Tran2 (VchCode)")
    con.execute("CREATE INDEX ix_t2_mc ON Tran2 (MasterCode1, RecType)")
    con.commit()
    con.close()


def timed(label: str, fn, repeat: int = 1) -> float:  # type: ignore[no-untyped-def]
    best = float("inf")
    for _ in range(repeat):
        started = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - started)
    print(f"  {label:<46} {best * 1000:9.1f} ms")
    return best


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vouchers", type=int, default=50_000)
    args = ap.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        path = build(str(Path(tmp) / "db12024.sqlite"))
        populate(path, args.vouchers)
        s = settings_from({"backend": "sqlite", "database": path, "fy": 2024})
        print(f"Synthetic company: {args.vouchers:,} vouchers, {args.vouchers * 4:,} voucher lines")
        with BusyDB(discover_targets(s)[0], s) as db:
            db.masters()
            old_sql = ("SELECT t2.MasterCode1, SUM(IIF(t2.Value1 < 0, -t2.Value1, 0)), "
                       "SUM(IIF(t2.Value1 > 0, t2.Value1, 0)) FROM Tran2 t2 INNER JOIN Tran1 t1 "
                       "ON t1.VchCode = t2.VchCode WHERE t2.RecType = 1 AND t1.[Date] < ? "
                       "AND (t1.Cancelled = 0 OR t1.Cancelled IS NULL) GROUP BY t2.MasterCode1")
            one = timed("v1.0: one aggregate query (per report: x2)", lambda: db.query(old_sql, ("2025-04-01",)), 3)
            build_t = timed("v1.1: build movement index (once per change)",
                            lambda: (reports.ledger_index(db), reports.item_index(db)))
            warm = {
                "trial balance": lambda: reports.trial_balance(db, None, None, ledger_level=False),
                "balance sheet": lambda: reports.balance_sheet(db, None),
                "profit & loss": lambda: reports.profit_and_loss(db, None, None),
                "receivables (2,000 parties)": lambda: reports.outstanding(db, "receivable", None, aging=False),
                "trial balance for Q3 only": lambda: reports.trial_balance(db, "2024-10-01", "2024-12-31",
                                                                         ledger_level=False),
                "stock summary (1,000 items)": lambda: reports.stock_summary(db, None, None),
                "dashboard": lambda: reports.dashboard(db),
            }
            print("  v1.1 reports with the index warm:")
            total = sum(timed(f"  {k}", fn, 3) for k, fn in warm.items())
        print(f"\nv1.0 would run ~{2 * len(warm)} full aggregate queries for these {len(warm)} reports "
              f"(~{2 * len(warm) * one * 1000:,.0f} ms); v1.1 needs one index build ({build_t * 1000:,.0f} ms) "
              f"then {total * 1000:,.0f} ms in total.")


if __name__ == "__main__":
    main()
