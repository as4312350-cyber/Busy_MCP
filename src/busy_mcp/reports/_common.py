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
from ..schema import MT_GROUP, MT_LEDGER, RT_LEDGER, master_type_label, vch_type_label
from ._index import MovementIndex

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


def select_list(columns: Iterable[str]) -> str:
    """A bracketed column list. BUSY uses column names such as No and Date that are reserved words in
    Access SQL (a bare ``No`` is the constant False there); brackets work in Access, SQL Server and SQLite."""
    return ", ".join(f"[{c}]" for c in columns)


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
    kind = "/".join(master_type_label(t) for t in types)
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
    return db.cached(f"folio1:{master_type}", load)


LEDGER_INDEX = "index:ledger"


def ledger_index(db: BusyDB) -> MovementIndex:
    """Daily debit/credit totals for every account (built once per data version)."""

    def build() -> MovementIndex:
        rows = db.query(
            "SELECT t2.MasterCode1 AS mc, t1.[Date] AS dt, "
            "SUM(IIF(t2.Value1 < 0, -t2.Value1, 0)) AS dr, SUM(IIF(t2.Value1 > 0, t2.Value1, 0)) AS cr "
            "FROM Tran2 t2 INNER JOIN Tran1 t1 ON t1.VchCode = t2.VchCode "
            f"WHERE t2.RecType = {RT_LEDGER}{db.active_voucher_filter('t1')} "
            "GROUP BY t2.MasterCode1, t1.[Date]"
        )
        return MovementIndex(2, (
            (int(r["mc"]), to_date(r["dt"]), (float(r["dr"] or 0), float(r["cr"] or 0)))
            for r in rows if r.get("mc") is not None and r.get("dt")
        ))

    return db.cached(LEDGER_INDEX, build)


def ledger_movements(db: BusyDB, start: _dt.date | None, end: _dt.date, ledger: int | None = None
                     ) -> dict[int, tuple[float, float]]:
    """Debits and credits per account for start..end inclusive -> {code: (dr, cr)}.

    Whole-company requests build (or reuse) the movement index; a single-account
    request uses the index when it already exists, otherwise one indexed query.
    """
    if ledger is None or db.is_cached(LEDGER_INDEX):
        idx = ledger_index(db)
        if ledger is None:
            return {c: (v[0], v[1]) for c, v in idx.all_ranges(start, end).items()}
        dr, cr = idx.range(ledger, start, end)
        return {ledger: (dr, cr)} if (dr or cr) else {}
    sql = (
        "SELECT SUM(IIF(t2.Value1 < 0, -t2.Value1, 0)) AS dr, SUM(IIF(t2.Value1 > 0, t2.Value1, 0)) AS cr "
        "FROM Tran2 t2 INNER JOIN Tran1 t1 ON t1.VchCode = t2.VchCode "
        "WHERE t2.RecType = ? AND t2.MasterCode1 = ? AND t1.[Date] < ?" + db.active_voucher_filter("t1")
    )
    params: list[Any] = [RT_LEDGER, ledger, db.dparam(next_day(end))]
    if start:
        sql += " AND t1.[Date] >= ?"
        params.append(db.dparam(start))
    row = db.query(sql, params)[0]
    dr, cr = float(row.get("dr") or 0), float(row.get("cr") or 0)
    return {ledger: (dr, cr)} if (dr or cr) else {}


def fy_start(db: BusyDB) -> _dt.date | None:
    b = fy_bounds(db.target.fy)
    return b[0] if b else None


def balance_at_start_of(db: BusyDB, code: int, day: _dt.date) -> float:
    """Debit-positive balance of one account at the start of `day`."""
    opening = -opening_balances(db).get(code, {}).get("d1", 0.0)
    dr, cr = ledger_movements(db, None, day - _dt.timedelta(days=1), ledger=code).get(code, (0.0, 0.0))
    return opening + dr - cr


def balances(db: BusyDB, f: _dt.date, t: _dt.date) -> dict[int, dict[str, float]]:
    """Per account: opening (as at f), debit, credit, closing (as at t); debit positive."""
    masters = db.masters()
    ob = opening_balances(db)
    before = ledger_movements(db, None, f - _dt.timedelta(days=1))
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
