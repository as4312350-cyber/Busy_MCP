"""Receivables / payables with FIFO aging."""

from __future__ import annotations

import datetime as _dt
from collections import defaultdict
from typing import Any

from ..db import BusyDB
from ..errors import ReportError
from ..schema import RT_LEDGER
from ._common import (
    EPS,
    balances,
    chunks,
    codes_under_groups,
    int_list,
    next_day,
    paginate,
    parent_name,
    period,
    r2,
    to_date,
)

BUCKETS = ("0-30", "31-60", "61-90", "91-180", "180+")


def _bucket(age: int) -> str:
    if age <= 30:
        return "0-30"
    if age <= 60:
        return "31-60"
    if age <= 90:
        return "61-90"
    if age <= 180:
        return "91-180"
    return "180+"


def outstanding(db: BusyDB, nature: str, as_of: str | None, min_amount: float = 1.0,
                aging: bool = True, offset: int = 0, limit: int = 200) -> dict[str, Any]:
    if nature not in ("receivable", "payable"):
        raise ReportError("nature must be 'receivable' or 'payable'")
    masters = db.masters()
    groups = db.settings.debtor_groups if nature == "receivable" else db.settings.creditor_groups
    codes = codes_under_groups(db, groups)
    if not codes:
        raise ReportError(f"No accounts under {groups}. Set BUSY_DEBTOR_GROUPS / BUSY_CREDITOR_GROUPS.")
    f, t = period(db, None, as_of)
    bal = balances(db, f, t)
    sign = 1 if nature == "receivable" else -1
    parties = sorted(((c, sign * bal.get(c, {}).get("closing", 0.0)) for c in codes),
                     key=lambda x: -x[1])
    due = [(c, a) for c, a in parties if a >= min_amount]
    advances = [(c, -a) for c, a in parties if a <= -min_amount]
    page = paginate(due, offset, limit)
    ages = _fifo_aging(db, [c for c, _ in page["items"]], dict(due), sign, f, t) if aging else {}
    totals = dict.fromkeys(BUCKETS, 0.0)
    rows = []
    for code, amt in page["items"]:
        m = masters[code]
        row: dict[str, Any] = {"party": m.name, "group": parent_name(masters, m), "outstanding": r2(amt)}
        if aging:
            row.update(ages.get(code, {}))
            for k, v in row.get("aging", {}).items():
                totals[k] += v
        rows.append(row)
    return {
        "nature": nature, "as_of": t.isoformat(), "groups": groups,
        "total_outstanding": r2(sum(a for _, a in due)),
        "advance_balances": {"party_count": len(advances), "total": r2(sum(a for _, a in advances))},
        **{k: v for k, v in page.items() if k != "items"},
        "aging_totals_this_page": {k: r2(v) for k, v in totals.items() if v > EPS} if aging else None,
        "parties": rows,
        "method": ("Balances of accounts under the debtor/creditor groups. Aging is a FIFO estimate: the "
                   "balance is matched against the most recent bills; BUSY bill-by-bill references are not used."),
    }


def _fifo_aging(db: BusyDB, codes: list[int], amounts: dict[int, float], sign: int,
                fy_start: _dt.date, as_of: _dt.date) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    if not codes:
        return out
    txns: dict[int, list[tuple[_dt.date, float]]] = defaultdict(list)
    for part in chunks(codes):
        rows = db.query(
            "SELECT t2.MasterCode1 AS mc, t1.[Date] AS dt, t2.Value1 AS v "
            "FROM Tran2 t2 INNER JOIN Tran1 t1 ON t1.VchCode = t2.VchCode "
            f"WHERE t2.RecType = {RT_LEDGER} AND t1.[Date] < ?{db.active_voucher_filter('t1')} "
            f"AND t2.MasterCode1 IN ({int_list(part)})",
            (db.dparam(next_day(as_of)),),
        )
        for r in rows:
            v = -float(r["v"] or 0) * sign  # positive = increases what is owed
            if v > 0:
                txns[int(r["mc"])].append((to_date(r["dt"]), v))
    for code in codes:
        remaining = amounts[code]
        b = dict.fromkeys(BUCKETS, 0.0)
        oldest = None
        for d, v in sorted(txns.get(code, []), reverse=True):
            if remaining <= EPS:
                break
            take = min(v, remaining)
            remaining -= take
            oldest = (as_of - d).days
            b[_bucket(oldest)] += take
        if remaining > EPS:  # carried in from the opening balance
            oldest = (as_of - fy_start).days
            b[_bucket(oldest)] += remaining
        out[code] = {"aging": {k: r2(v) for k, v in b.items() if v > EPS}, "oldest_days": oldest}
    return out
