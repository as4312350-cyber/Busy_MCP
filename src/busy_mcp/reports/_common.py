"""Shared helpers for all reports.

Sign convention inside BUSY: account amounts are stored NEGATIVE for Debit and
POSITIVE for Credit (Tran2.Value1, Folio1.D1). Reports convert to "positive =
Debit" and print 'Dr'/'Cr' labels.

All SQL sticks to the subset that runs unchanged on MS-Access (Jet), SQL Server
and SQLite: single INNER JOINs, IIF, GROUP BY, bind parameters, [Date] quoting.
Tree walks and name lookups happen in Python against the cached Master1.
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Iterable, Sequence
from typing import Any

from ..db import BusyDB, Master, fy_bounds
from ..errors import ReportError
from ..schema import MASTER_TYPES, MT_GROUP, MT_LEDGER, RT_LEDGER, vch_type_label

EPS = 0.005


def r2(x: Any) -> float:
    return round(float(x or 0.0) + 0.0, 2)


def drcr(x: float) -> str:
    """Format a debit-positive number as '1,234.50 Dr'."""
    x = r2(x)
    if abs(x) < EPS:
        return "0.00"
    return f"{abs(x):,.2f} {'Dr' if x > 0 else 'Cr'}"


def parse_date(value: str | None, default: _dt.date) -> _dt.date:
    if not value:
        return default
    try:
        return _dt.date.fromisoformat(str(value).strip()[:10])
    except ValueError as exc:
        raise ReportError(f"Bad date '{value}', use YYYY-MM-DD") from exc


def fy_range(db: BusyDB) -> tuple[_dt.date, _dt.date]:
    bounds = fy_bounds(db.target.fy)
    if bounds:
        return bounds
    today = _dt.date.today()
    fy = today.year if today.month >= 4 else today.year - 1
    return _dt.date(fy, 4, 1), _dt.date(fy + 1, 3, 31)


def period(db: BusyDB, from_date: str | None, to_date: str | None) -> tuple[_dt.date, _dt.date]:
    start, end = fy_range(db)
    f = parse_date(from_date, start)
    t = parse_date(to_date, end)
    if f > t:
        raise ReportError("from_date is after to_date")
    return f, t


def next_day(d: _dt.date) -> _dt.date:
    return d + _dt.timedelta(days=1)


def to_date(value: Any) -> _dt.date:
    return _dt.date.fromisoformat(str(value)[:10])


def chunks(seq: Sequence[Any], size: int = 400) -> Iterable[Sequence[Any]]:
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def int_list(values: Iterable[Any]) -> str:
    """Comma list of ints for an IN clause (values are always our own ints)."""
    return ",".join(str(int(v)) for v in values)


def paginate(items: list[Any], offset: int, limit: int) -> dict[str, Any]:
    offset = max(0, int(offset))
    limit = max(1, int(limit))
    window = items[offset:offset + limit]
    more = offset + len(window) < len(items)
    return {"total": len(items), "offset": offset, "returned": len(window), "has_more": more,
            "next_offset": offset + len(window) if more else None, "items": window}


# --------------------------------------------------------------------- masters


def ancestors(masters: dict[int, Master], m: Master) -> list[Master]:
    out: list[Master] = []
    seen, cur = {m.code}, m.parent
    while cur and cur in masters and cur not in seen:
        seen.add(cur)
        p = masters[cur]
        out.append(p)
        cur = p.parent
    return out


def group_path(masters: dict[int, Master], m: Master) -> str:
    return " > ".join(p.name for p in reversed(ancestors(masters, m)))


def parent_name(masters: dict[int, Master], m: Master) -> str | None:
    return masters[m.parent].name if m.parent in masters else None


def master_name(masters: dict[int, Master], code: Any) -> str | None:
    try:
        c = int(code or 0)
    except (TypeError, ValueError):
        return None
    return masters[c].name if c in masters else (None if c == 0 else f"#{c}")


def find_master(db: BusyDB, ref: str | int, types: tuple[int, ...]) -> Master:
    masters = db.masters()
    kind = "/".join(MASTER_TYPES.get(t, str(t)) for t in types)
    if ref is None or str(ref).strip() == "":
        raise ReportError(f"A {kind} name or code is required")
    pool = [m for m in masters.values() if m.type in types]
    text = str(ref).strip()
    if text.isdigit() and int(text) in masters and masters[int(text)].type in types:
        return masters[int(text)]
    low = text.lower()
    for m in pool:
        if m.name.lower() == low:
            return m
    for m in pool:
        if (m.alias or "").lower() == low or (m.print_name or "").lower() == low:
            return m
    near = [m for m in pool if low in m.name.lower() or low in (m.alias or "").lower()]
    if len(near) == 1:
        return near[0]
    if near:
        names = ", ".join(sorted(m.name for m in near)[:15])
        raise ReportError(f"'{ref}' matches several {kind} masters: {names}. Use the exact name or Code.")
    raise ReportError(f"No {kind} named '{ref}'. Try search-masters.")


def codes_under_groups(db: BusyDB, group_names: list[str], member_type: int = MT_LEDGER) -> set[int]:
    masters = db.masters()
    wanted = {g.strip().lower() for g in group_names}
    return {
        m.code for m in masters.values()
        if m.type == member_type and any(p.name.lower() in wanted for p in ancestors(masters, m))
    }


_PL_HINTS = ("revenue", "income", "expense", "sale", "purchase", "trading", "profit & loss a/c")


def pl_ledger_codes(db: BusyDB) -> tuple[set[int], str]:
    """Ledgers that belong to the P&L, and how that was decided."""
    configured = codes_under_groups(db, db.settings.pl_groups)
    if configured:
        return configured, f"accounts under group(s) {db.settings.pl_groups}"
    masters = db.masters()
    roots = {m.code for m in masters.values() if m.type == MT_GROUP and m.parent not in masters
             and any(h in m.name.lower() for h in _PL_HINTS)}
    codes = {m.code for m in masters.values()
             if m.type == MT_LEDGER and any(p.code in roots for p in ancestors(masters, m))}
    if not codes:
        raise ReportError(
            f"No accounts found under P&L group(s) {db.settings.pl_groups}. Set BUSY_PL_GROUPS "
            "to your revenue/income/expense group names (see chart-of-accounts)."
        )
    names = sorted(masters[c].name for c in roots)
    return codes, f"heuristic: top-level groups {names} (set BUSY_PL_GROUPS to make this explicit)"


def vt(db: BusyDB, code: Any) -> str:
    return vch_type_label(int(code) if code is not None else None, db.settings.vch_type_names)


# -------------------------------------------------------------------- balances


def opening_balances(db: BusyDB, master_type: int = MT_LEDGER) -> dict[int, dict[str, float]]:
    """Folio1 rows -> {code: {d1, d3}}. Cached until masters refresh."""
    def load() -> dict[int, dict[str, float]]:
        if not db.has_table("Folio1"):
            return {}
        rows = db.query("SELECT MasterCode, D1, D3 FROM Folio1 WHERE MasterType = ?", (master_type,))
        out: dict[int, dict[str, float]] = {}
        for r in rows:
            if r.get("mastercode") is None:
                continue
            slot = out.setdefault(int(r["mastercode"]), {"d1": 0.0, "d3": 0.0})
            slot["d1"] += float(r["d1"] or 0)
            slot["d3"] += float(r["d3"] or 0)
        return out
    db.masters()  # ensure cache generation is current
    return db.cached(f"folio1:{master_type}", load)


def ledger_movements(db: BusyDB, start: _dt.date | None, end: _dt.date, ledger: int | None = None
                     ) -> dict[int, tuple[float, float]]:
    """Debits and credits per account for start..end inclusive -> {code: (dr, cr)}."""
    sql = (
        "SELECT t2.MasterCode1 AS mc, "
        "SUM(IIF(t2.Value1 < 0, -t2.Value1, 0)) AS dr, "
        "SUM(IIF(t2.Value1 > 0, t2.Value1, 0)) AS cr "
        "FROM Tran2 t2 INNER JOIN Tran1 t1 ON t1.VchCode = t2.VchCode "
        "WHERE t2.RecType = ? AND t1.[Date] < ?" + db.active_voucher_filter("t1")
    )
    params: list[Any] = [RT_LEDGER, db.dparam(next_day(end))]
    if start:
        sql += " AND t1.[Date] >= ?"
        params.append(db.dparam(start))
    if ledger is not None:
        sql += " AND t2.MasterCode1 = ?"
        params.append(ledger)
    sql += " GROUP BY t2.MasterCode1"
    return {int(r["mc"]): (float(r["dr"] or 0), float(r["cr"] or 0))
            for r in db.query(sql, params) if r.get("mc") is not None}


def fy_start(db: BusyDB) -> _dt.date | None:
    b = fy_bounds(db.target.fy)
    return b[0] if b else None


def balance_at_start_of(db: BusyDB, code: int, day: _dt.date) -> float:
    """Debit-positive balance of one account at the start of `day`."""
    opening = -opening_balances(db).get(code, {}).get("d1", 0.0)
    start = fy_start(db)
    if start is None or day > start:
        dr, cr = ledger_movements(db, start, day - _dt.timedelta(days=1), ledger=code).get(code, (0.0, 0.0))
        opening += dr - cr
    return opening


def balances(db: BusyDB, f: _dt.date, t: _dt.date) -> dict[int, dict[str, float]]:
    """Per account: opening (as at f), debit, credit, closing (as at t); debit positive."""
    masters = db.masters()
    ob = opening_balances(db)
    start = fy_start(db)
    before = ledger_movements(db, start, f - _dt.timedelta(days=1)) if (start is None or f > start) else {}
    during = ledger_movements(db, f, t)
    out: dict[int, dict[str, float]] = {}
    for code, m in masters.items():
        if m.type != MT_LEDGER:
            continue
        opening = -ob.get(code, {}).get("d1", 0.0)
        bdr, bcr = before.get(code, (0.0, 0.0))
        opening += bdr - bcr
        dr, cr = during.get(code, (0.0, 0.0))
        out[code] = {"opening": opening, "debit": dr, "credit": cr, "closing": opening + dr - cr}
    return out
