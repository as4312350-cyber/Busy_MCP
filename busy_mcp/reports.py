"""Accounting reports computed from BUSY's raw tables.

Sign convention inside BUSY: ledger amounts are stored NEGATIVE for Debit and
POSITIVE for Credit (Tran2.Value1, Folio1.D1). Every report here converts to
the human convention "positive = Debit" and also prints "Dr"/"Cr" labels.

All SQL here sticks to the subset that runs unchanged on MS-Access (Jet),
SQL Server and SQLite: single INNER JOINs, IIF, GROUP BY, bind parameters.
Name lookups and tree walks are done in Python against a cached Master1.
"""

from __future__ import annotations

import datetime as _dt
from collections import defaultdict
from typing import Any, Iterable

from .db import BusyDB, Master, fy_bounds, fy_label
from .schema import (
    MASTER_TYPES, MT_GROUP, MT_ITEM, MT_ITEM_GROUP, MT_LEDGER, RT3_ORDER_LINE,
    RT_ITEM, RT_LEDGER, VT_PURCHASE, VT_PURCHASE_RETURN, VT_SALES, VT_SALES_RETURN,
    vch_type_label,
)

_KIND_TO_TYPE = {
    "group": MT_GROUP, "account_group": MT_GROUP,
    "ledger": MT_LEDGER, "account": MT_LEDGER,
    "item_group": MT_ITEM_GROUP, "stock_group": MT_ITEM_GROUP,
    "item": MT_ITEM, "stock_item": MT_ITEM,
}


class ReportError(ValueError):
    pass


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def r2(x: float | None) -> float:
    return round(float(x or 0.0) + 0.0, 2)


def drcr(x: float) -> str:
    """Format a debit-positive number as '1,234.50 Dr'."""
    x = r2(x)
    if abs(x) < 0.005:
        return "0.00"
    return f"{abs(x):,.2f} {'Dr' if x > 0 else 'Cr'}"


def parse_date(value: str | None, default: _dt.date) -> _dt.date:
    if not value:
        return default
    try:
        return _dt.date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise ReportError(f"Bad date '{value}', use YYYY-MM-DD") from exc


def period(db: BusyDB, from_date: str | None, to_date: str | None) -> tuple[_dt.date, _dt.date]:
    bounds = fy_bounds(db.target.fy)
    if bounds:
        start, end = bounds
    else:
        today = _dt.date.today()
        fy = today.year if today.month >= 4 else today.year - 1
        start, end = _dt.date(fy, 4, 1), _dt.date(fy + 1, 3, 31)
    f = parse_date(from_date, start)
    t = parse_date(to_date, end)
    if f > t:
        raise ReportError("from_date is after to_date")
    return f, t


def _next_day(d: _dt.date) -> _dt.date:
    return d + _dt.timedelta(days=1)


def chunks(seq: list[Any], size: int = 400) -> Iterable[list[Any]]:
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def ancestors(masters: dict[int, Master], m: Master) -> list[Master]:
    out, seen, cur = [], {m.code}, m.parent
    while cur and cur in masters and cur not in seen:
        seen.add(cur)
        p = masters[cur]
        out.append(p)
        cur = p.parent
    return out


def group_path(masters: dict[int, Master], m: Master) -> str:
    return " > ".join(p.name for p in reversed(ancestors(masters, m)))


def find_master(db: BusyDB, ref: str | int, types: tuple[int, ...]) -> Master:
    masters = db.masters()
    pool = [m for m in masters.values() if m.type in types]
    if isinstance(ref, int) or (isinstance(ref, str) and ref.strip().isdigit()):
        code = int(ref)
        if code in masters and masters[code].type in types:
            return masters[code]
    text = str(ref).strip().lower()
    for m in pool:
        if m.name.lower() == text:
            return m
    for m in pool:
        if (m.alias or "").lower() == text or (m.print_name or "").lower() == text:
            return m
    near = [m for m in pool if text in m.name.lower() or text in (m.alias or "").lower()]
    if len(near) == 1:
        return near[0]
    kind = "/".join(MASTER_TYPES.get(t, str(t)) for t in types)
    if near:
        names = ", ".join(sorted(m.name for m in near)[:15])
        raise ReportError(f"'{ref}' matches several {kind} masters: {names}. Use the exact name or Code.")
    raise ReportError(f"No {kind} named '{ref}'. Try the search-masters tool.")


def codes_under_groups(db: BusyDB, group_names: list[str], member_type: int = MT_LEDGER) -> set[int]:
    masters = db.masters()
    wanted = {g.strip().lower() for g in group_names}
    out = set()
    for m in masters.values():
        if m.type != member_type:
            continue
        if any(p.name.lower() in wanted for p in ancestors(masters, m)):
            out.add(m.code)
    return out


def _vt(db: BusyDB, code: Any) -> str:
    return vch_type_label(int(code) if code is not None else None, db.settings.vch_type_names)


# ---------------------------------------------------------------------------
# balances
# ---------------------------------------------------------------------------


def opening_balances(db: BusyDB, master_type: int = MT_LEDGER) -> dict[int, dict[str, float]]:
    """Folio1 rows -> {code: {d1, d3}}. Cached per connection."""
    key = f"folio1:{master_type}"
    if key not in db.cache:
        rows = db.query("SELECT MasterCode, D1, D3 FROM Folio1 WHERE MasterType = ?", (master_type,))
        db.cache[key] = {
            int(r["mastercode"]): {"d1": float(r["d1"] or 0), "d3": float(r["d3"] or 0)}
            for r in rows if r.get("mastercode") is not None
        }
    return db.cache[key]


def ledger_movements(db: BusyDB, start: _dt.date | None, end: _dt.date, ledger: int | None = None
                     ) -> dict[int, tuple[float, float]]:
    """Sum of debits and credits per ledger for start..end (inclusive). Returns {code: (dr, cr)}."""
    sql = (
        "SELECT t2.MasterCode1 AS mc, "
        "SUM(IIF(t2.Value1 < 0, -t2.Value1, 0)) AS dr, "
        "SUM(IIF(t2.Value1 > 0, t2.Value1, 0)) AS cr "
        "FROM Tran2 t2 INNER JOIN Tran1 t1 ON t1.VchCode = t2.VchCode "
        "WHERE t2.RecType = ? AND t1.[Date] < ?"
    )
    params: list[Any] = [RT_LEDGER, db.dparam(_next_day(end))]
    if start:
        sql += " AND t1.[Date] >= ?"
        params.append(db.dparam(start))
    if ledger is not None:
        sql += " AND t2.MasterCode1 = ?"
        params.append(ledger)
    sql += " GROUP BY t2.MasterCode1"
    return {int(r["mc"]): (float(r["dr"] or 0), float(r["cr"] or 0))
            for r in db.query(sql, params) if r.get("mc") is not None}


def _fy_start(db: BusyDB, f: _dt.date) -> _dt.date | None:
    b = fy_bounds(db.target.fy)
    return b[0] if b else None


def balances(db: BusyDB, f: _dt.date, t: _dt.date) -> dict[int, dict[str, float]]:
    """Per-ledger opening (as at f), period dr/cr, closing (as at t); debit positive."""
    masters = db.masters()
    ob = opening_balances(db, MT_LEDGER)
    fy_start = _fy_start(db, f)
    before = ledger_movements(db, fy_start, f - _dt.timedelta(days=1)) if (fy_start is None or f > fy_start) else {}
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


# ---------------------------------------------------------------------------
# overview & discovery tools
# ---------------------------------------------------------------------------


def company_overview(db: BusyDB) -> dict[str, Any]:
    masters = db.masters()
    counts: dict[int, int] = defaultdict(int)
    for m in masters.values():
        counts[m.type] += 1
    vch = db.query("SELECT COUNT(*) AS n, MIN([Date]) AS first_date, MAX([Date]) AS last_date FROM Tran1")
    info = company_info(db)
    return {
        "target": db.target.describe(),
        "company_info": info,
        "masters": {MASTER_TYPES.get(k, f"MasterType {k}"): v for k, v in sorted(counts.items())},
        "vouchers": vch[0] if vch else {},
    }


def company_info(db: BusyDB) -> dict[str, Any] | None:
    """Best effort: BUSY keeps company details in a table whose name varies by version."""
    for table in ("Company", "CompanyInfo", "CompInfo"):
        try:
            rows = db.query(f"SELECT {db.top(1)}* FROM {table}{db.limit(1)}")
        except Exception:  # noqa: BLE001 - table may not exist
            continue
        if rows:
            return {k: v for k, v in rows[0].items() if v not in (None, "", 0, 0.0)}
    return None


def master_type_summary(db: BusyDB) -> list[dict[str, Any]]:
    by_type: dict[int, list[Master]] = defaultdict(list)
    for m in db.masters().values():
        by_type[m.type].append(m)
    return [
        {"master_type": t, "label": MASTER_TYPES.get(t, "unmapped"), "count": len(ms),
         "examples": [m.name for m in sorted(ms, key=lambda x: x.code)[:5]]}
        for t, ms in sorted(by_type.items())
    ]


def voucher_types(db: BusyDB) -> list[dict[str, Any]]:
    rows = db.query(
        "SELECT VchType, COUNT(*) AS n, MIN([Date]) AS first_date, MAX([Date]) AS last_date "
        "FROM Tran1 GROUP BY VchType"
    )
    out = []
    for r in sorted(rows, key=lambda x: -(x["n"] or 0)):
        code = int(r["vchtype"])
        samples = db.query(
            f"SELECT {db.top(3)}VchNo FROM Tran1 WHERE VchType = ? ORDER BY VchCode DESC{db.limit(3)}",
            (code,),
        )
        out.append({
            "vch_type": code, "label": _vt(db, code), "count": r["n"],
            "first_date": r["first_date"], "last_date": r["last_date"],
            "sample_numbers": [s["vchno"] for s in samples],
        })
    return out


def search_masters(db: BusyDB, text: str, kind: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    masters = db.masters()
    types = (_KIND_TO_TYPE[kind],) if kind and kind in _KIND_TO_TYPE else None
    needle = (text or "").strip().lower()
    hits = []
    for m in masters.values():
        if types and m.type not in types:
            continue
        hay = f"{m.name} {m.alias or ''} {m.print_name or ''}".lower()
        if needle in hay:
            hits.append(m)
    hits.sort(key=lambda m: (m.name.lower() != needle, not m.name.lower().startswith(needle), m.name))
    return [
        {"code": m.code, "name": m.name, "alias": m.alias, "master_type": m.type,
         "type": MASTER_TYPES.get(m.type, f"MasterType {m.type}"), "under": group_path(masters, m)}
        for m in hits[:limit]
    ]


def list_masters(db: BusyDB, kind: str, under_group: str | None = None, limit: int = 500) -> dict[str, Any]:
    if kind not in _KIND_TO_TYPE:
        raise ReportError(f"kind must be one of {sorted(set(_KIND_TO_TYPE))}")
    mt = _KIND_TO_TYPE[kind]
    masters = db.masters()
    rows = [m for m in masters.values() if m.type == mt]
    if under_group:
        grp_type = MT_GROUP if mt in (MT_GROUP, MT_LEDGER) else MT_ITEM_GROUP
        g = find_master(db, under_group, (grp_type,))
        rows = [m for m in rows if any(p.code == g.code for p in ancestors(masters, m))]
    rows.sort(key=lambda m: m.name.lower())
    return {
        "type": MASTER_TYPES[mt], "total": len(rows), "returned": min(len(rows), limit),
        "items": [{"code": m.code, "name": m.name, "alias": m.alias,
                   "parent": masters[m.parent].name if m.parent in masters else None}
                  for m in rows[:limit]],
    }


def chart_of_accounts(db: BusyDB, include_ledgers: bool = False, item_side: bool = False) -> list[dict[str, Any]]:
    masters = db.masters()
    gtype, ltype = (MT_ITEM_GROUP, MT_ITEM) if item_side else (MT_GROUP, MT_LEDGER)
    children: dict[int, list[Master]] = defaultdict(list)
    for m in masters.values():
        if m.type == gtype or (include_ledgers and m.type == ltype):
            parent = m.parent if m.parent in masters else 0
            children[parent].append(m)

    def node(m: Master, depth: int) -> dict[str, Any]:
        n: dict[str, Any] = {"name": m.name, "code": m.code}
        if m.type == gtype:
            kids = sorted(children.get(m.code, []), key=lambda x: (x.type != gtype, x.name.lower()))
            if depth < 15:
                n["children"] = [node(k, depth + 1) for k in kids]
            if include_ledgers:
                n["ledger_count"] = sum(1 for k in kids if k.type == ltype)
        return n

    roots = sorted((m for m in children.get(0, []) if m.type == gtype), key=lambda x: x.name.lower())
    return [node(r, 0) for r in roots]


# ---------------------------------------------------------------------------
# ledger reports
# ---------------------------------------------------------------------------


def ledger_details(db: BusyDB, ledger: str) -> dict[str, Any]:
    masters = db.masters()
    m = find_master(db, ledger, (MT_LEDGER,))
    out: dict[str, Any] = {
        "code": m.code, "name": m.name, "alias": m.alias, "print_name": m.print_name,
        "group": masters[m.parent].name if m.parent in masters else None,
        "group_path": group_path(masters, m),
        "opening_balance": drcr(-opening_balances(db).get(m.code, {}).get("d1", 0.0)),
    }
    try:
        addr = db.query("SELECT * FROM MasterAddressInfo WHERE MasterCode = ?", (m.code,))
        if addr:
            out["address_info"] = {k: v for k, v in addr[0].items()
                                   if v not in (None, "", 0, 0.0) and k != "mastercode"}
    except Exception as exc:  # noqa: BLE001
        out["address_info_error"] = str(exc)
    return out


def ledger_balance(db: BusyDB, ledger: str, as_of: str | None) -> dict[str, Any]:
    m = find_master(db, ledger, (MT_LEDGER,))
    f, t = period(db, None, as_of)
    opening = -opening_balances(db).get(m.code, {}).get("d1", 0.0)
    dr, cr = ledger_movements(db, f, t, ledger=m.code).get(m.code, (0.0, 0.0))
    closing = opening + dr - cr
    return {
        "ledger": m.name, "as_of": t.isoformat(), "fy_opening": drcr(opening),
        "total_debit": r2(dr), "total_credit": r2(cr), "closing_balance": drcr(closing),
        "closing_signed_dr_positive": r2(closing),
    }


def ledger_statement(db: BusyDB, ledger: str, from_date: str | None, to_date: str | None,
                     limit: int = 1000) -> dict[str, Any]:
    masters = db.masters()
    m = find_master(db, ledger, (MT_LEDGER,))
    f, t = period(db, from_date, to_date)
    opening = balances_for_one(db, m.code, f)
    rows = db.query(
        "SELECT t1.VchCode, t1.VchType, t1.VchNo, t1.[Date], t2.Value1 "
        "FROM Tran2 t2 INNER JOIN Tran1 t1 ON t1.VchCode = t2.VchCode "
        "WHERE t2.RecType = ? AND t2.MasterCode1 = ? AND t1.[Date] >= ? AND t1.[Date] < ? "
        "ORDER BY t1.[Date], t1.VchCode",
        (RT_LEDGER, m.code, db.dparam(f), db.dparam(_next_day(t))),
    )
    contra = _contra_accounts(db, [r["vchcode"] for r in rows[:limit]], m.code, masters)
    running = opening
    entries, tdr, tcr = [], 0.0, 0.0
    for r in rows:
        v = float(r["value1"] or 0)
        dr, cr = (-v, 0.0) if v < 0 else (0.0, v)
        tdr += dr
        tcr += cr
        running += dr - cr
        if len(entries) < limit:
            entries.append({
                "date": r["date"], "vch_type": _vt(db, r["vchtype"]), "vch_no": r["vchno"],
                "vch_code": r["vchcode"], "particulars": contra.get(r["vchcode"], ""),
                "debit": r2(dr), "credit": r2(cr), "balance": drcr(running),
            })
    return {
        "ledger": m.name, "group": masters[m.parent].name if m.parent in masters else None,
        "from": f.isoformat(), "to": t.isoformat(), "opening_balance": drcr(opening),
        "total_debit": r2(tdr), "total_credit": r2(tcr), "closing_balance": drcr(running),
        "entry_count": len(rows), "entries_returned": len(entries), "entries": entries,
    }


def balances_for_one(db: BusyDB, code: int, as_at_start_of: _dt.date) -> float:
    opening = -opening_balances(db).get(code, {}).get("d1", 0.0)
    fy_start = _fy_start(db, as_at_start_of)
    if fy_start is None or as_at_start_of > fy_start:
        mv = ledger_movements(db, fy_start, as_at_start_of - _dt.timedelta(days=1), ledger=code)
        dr, cr = mv.get(code, (0.0, 0.0))
        opening += dr - cr
    return opening


def _contra_accounts(db: BusyDB, vch_codes: list[int], self_code: int,
                     masters: dict[int, Master]) -> dict[int, str]:
    out: dict[int, list[tuple[float, str]]] = defaultdict(list)
    codes = sorted({int(c) for c in vch_codes})
    for part in chunks(codes):
        rows = db.query(
            f"SELECT VchCode, MasterCode1, Value1 FROM Tran2 WHERE RecType = {RT_LEDGER} "
            f"AND VchCode IN ({','.join(str(c) for c in part)})"
        )
        for r in rows:
            mc = int(r["mastercode1"] or 0)
            if mc == self_code:
                continue
            name = masters[mc].name if mc in masters else f"#{mc}"
            out[int(r["vchcode"])].append((abs(float(r["value1"] or 0)), name))
    result = {}
    for vc, legs in out.items():
        legs.sort(reverse=True)
        names = []
        for _, n in legs:
            if n not in names:
                names.append(n)
        result[vc] = names[0] + (f" (+{len(names) - 1} more)" if len(names) > 1 else "")
    return result


def trial_balance(db: BusyDB, from_date: str | None, to_date: str | None, group: str | None = None,
                  include_zero: bool = False, ledger_level: bool = True) -> dict[str, Any]:
    masters = db.masters()
    f, t = period(db, from_date, to_date)
    bal = balances(db, f, t)
    scope = None
    if group:
        g = find_master(db, group, (MT_GROUP,))
        scope = g.code
    rows, totals = [], defaultdict(float)
    top_totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for code, b in bal.items():
        m = masters[code]
        anc = ancestors(masters, m)
        if scope is not None and all(p.code != scope for p in anc):
            continue
        if not include_zero and all(abs(b[k]) < 0.005 for k in ("opening", "debit", "credit", "closing")):
            continue
        top = anc[-1].name if anc else "(no group)"
        for k in ("opening", "debit", "credit", "closing"):
            totals[k] += b[k]
            top_totals[top][k] += b[k]
        if ledger_level:
            rows.append({
                "ledger": m.name, "group": anc[0].name if anc else None, "primary_group": top,
                "opening": drcr(b["opening"]), "debit": r2(b["debit"]), "credit": r2(b["credit"]),
                "closing": drcr(b["closing"]), "closing_dr": r2(max(b["closing"], 0)),
                "closing_cr": r2(max(-b["closing"], 0)),
            })
    rows.sort(key=lambda x: (x["primary_group"], x["group"] or "", x["ledger"]))
    closing_dr = sum(x["closing_dr"] for x in rows) if ledger_level else None
    closing_cr = sum(x["closing_cr"] for x in rows) if ledger_level else None
    return {
        "from": f.isoformat(), "to": t.isoformat(), "group_filter": group,
        "by_primary_group": [
            {"primary_group": k, "opening": drcr(v["opening"]), "debit": r2(v["debit"]),
             "credit": r2(v["credit"]), "closing": drcr(v["closing"])}
            for k, v in sorted(top_totals.items())
        ],
        "totals": {"debit": r2(totals["debit"]), "credit": r2(totals["credit"]),
                   "closing_dr": r2(closing_dr) if closing_dr is not None else None,
                   "closing_cr": r2(closing_cr) if closing_cr is not None else None,
                   "net_difference": drcr(totals["closing"])},
        "note": ("net_difference should be 0.00 for the whole company. A non-zero value usually "
                 "means opening balances were not carried forward into this FY database, or "
                 "opening stock is held outside ledgers."),
        "ledgers": rows if ledger_level else None,
    }


def group_summary(db: BusyDB, group: str | None, from_date: str | None, to_date: str | None) -> dict[str, Any]:
    masters = db.masters()
    f, t = period(db, from_date, to_date)
    bal = balances(db, f, t)
    root = find_master(db, group, (MT_GROUP,)).code if group else 0
    lines: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for code, b in bal.items():
        m = masters[code]
        chain = [m] + ancestors(masters, m)
        # find the child of `root` on this ledger's chain
        child = None
        for i, node in enumerate(chain):
            parent = node.parent if node.parent in masters else 0
            if parent == root:
                child = node
                break
        if child is None:
            continue
        for k in ("opening", "debit", "credit", "closing"):
            lines[child.code][k] += b[k]
    out = []
    for code, v in lines.items():
        if all(abs(v[k]) < 0.005 for k in ("opening", "debit", "credit", "closing")):
            continue
        m = masters[code]
        out.append({"name": m.name, "kind": "group" if m.type == MT_GROUP else "ledger",
                    "opening": drcr(v["opening"]), "debit": r2(v["debit"]), "credit": r2(v["credit"]),
                    "closing": drcr(v["closing"]), "closing_signed": r2(v["closing"])})
    out.sort(key=lambda x: -abs(x["closing_signed"]))
    total = sum(x["closing_signed"] for x in out)
    return {"group": masters[root].name if root else "(all primary groups)", "from": f.isoformat(),
            "to": t.isoformat(), "lines": out, "total_closing": drcr(total)}


def profit_and_loss(db: BusyDB, from_date: str | None, to_date: str | None) -> dict[str, Any]:
    masters = db.masters()
    f, t = period(db, from_date, to_date)
    pl_codes = codes_under_groups(db, db.settings.pl_groups)
    if not pl_codes:
        raise ReportError(
            f"No ledgers found under P&L root group(s) {db.settings.pl_groups}. "
            "Set BUSY_PL_GROUPS to your revenue group names (see chart-of-accounts)."
        )
    mv = ledger_movements(db, f, t)
    pl_roots = {g.lower() for g in db.settings.pl_groups}
    heads: dict[str, dict[str, Any]] = {}
    for code in pl_codes:
        dr, cr = mv.get(code, (0.0, 0.0))
        net = dr - cr  # debit positive
        if abs(net) < 0.005:
            continue
        m = masters[code]
        chain = ancestors(masters, m)
        head = m.name
        for i, p in enumerate(chain):
            if p.name.lower() in pl_roots:
                head = chain[i - 1].name if i > 0 else m.name
                break
        h = heads.setdefault(head, {"head": head, "net": 0.0, "ledgers": []})
        h["net"] += net
        h["ledgers"].append({"ledger": m.name, "amount": drcr(net), "signed": r2(net)})
    income, expense = [], []
    for h in heads.values():
        h["ledgers"].sort(key=lambda x: -abs(x["signed"]))
        h["ledgers"] = h["ledgers"][:50]
        entry = {"head": h["head"], "amount": r2(abs(h["net"])), "top_ledgers": h["ledgers"]}
        (expense if h["net"] > 0 else income).append(entry)
    total_income = sum(x["amount"] for x in income)
    total_expense = sum(x["amount"] for x in expense)
    net = total_income - total_expense
    return {
        "from": f.isoformat(), "to": t.isoformat(), "pl_root_groups": db.settings.pl_groups,
        "income": sorted(income, key=lambda x: -x["amount"]),
        "expenses": sorted(expense, key=lambda x: -x["amount"]),
        "total_income": r2(total_income), "total_expenses": r2(total_expense),
        "net_profit_before_stock_adjustment": r2(net),
        "result": "Profit" if net >= 0 else "Loss",
        "caveat": ("Computed from ledger postings only. Opening/closing stock valuation (which BUSY "
                   "adds to its Trading account) is NOT included — use stock-summary for stock "
                   "figures and adjust: Gross/Net profit += closing stock - opening stock."),
    }


def balance_sheet(db: BusyDB, as_of: str | None) -> dict[str, Any]:
    masters = db.masters()
    f, t = period(db, None, as_of)
    bal = balances(db, f, t)
    pl_codes = codes_under_groups(db, db.settings.pl_groups)
    groups: dict[str, float] = defaultdict(float)
    pl_net = 0.0
    for code, b in bal.items():
        if code in pl_codes:
            pl_net += b["closing"]
            continue
        anc = ancestors(masters, masters[code])
        groups[anc[-1].name if anc else "(no group)"] += b["closing"]
    assets = [{"group": g, "amount": r2(v)} for g, v in groups.items() if v > 0.005]
    liabilities = [{"group": g, "amount": r2(-v)} for g, v in groups.items() if v < -0.005]
    profit = -pl_net  # credit balance in revenue = profit
    if profit >= 0:
        liabilities.append({"group": "Profit & Loss A/c (current period, before stock)", "amount": r2(profit)})
    else:
        assets.append({"group": "Profit & Loss A/c (current period loss, before stock)", "amount": r2(-profit)})
    ta, tl = sum(x["amount"] for x in assets), sum(x["amount"] for x in liabilities)
    return {
        "as_of": t.isoformat(),
        "liabilities": sorted(liabilities, key=lambda x: -x["amount"]),
        "assets": sorted(assets, key=lambda x: -x["amount"]),
        "total_liabilities": r2(tl), "total_assets": r2(ta), "difference": r2(ta - tl),
        "caveat": ("Built from ledger balances. Closing stock is not included unless your BUSY "
                   "company posts it to a ledger. A difference usually equals closing stock value or "
                   "un-carried-forward opening balances."),
    }


def outstanding(db: BusyDB, nature: str, as_of: str | None, min_amount: float = 1.0,
                aging: bool = True, limit: int = 500) -> dict[str, Any]:
    if nature not in ("receivable", "payable"):
        raise ReportError("nature must be 'receivable' or 'payable'")
    masters = db.masters()
    groups = db.settings.debtor_groups if nature == "receivable" else db.settings.creditor_groups
    codes = codes_under_groups(db, groups)
    if not codes:
        raise ReportError(f"No ledgers under {groups}. Set BUSY_DEBTOR_GROUPS / BUSY_CREDITOR_GROUPS.")
    f, t = period(db, None, as_of)
    bal = balances(db, f, t)
    sign = 1 if nature == "receivable" else -1
    parties = []
    for code in codes:
        amt = sign * bal.get(code, {}).get("closing", 0.0)
        if amt >= min_amount:
            parties.append((code, amt))
    parties.sort(key=lambda x: -x[1])
    ages = _fifo_aging(db, [c for c, _ in parties[:limit]], dict(parties), sign, f, t) if aging else {}
    out = []
    for code, amt in parties[:limit]:
        m = masters[code]
        row = {"party": m.name, "group": masters[m.parent].name if m.parent in masters else None,
               "outstanding": r2(amt)}
        if aging:
            row.update(ages.get(code, {}))
        out.append(row)
    advances = sum(1 for c in codes if sign * bal.get(c, {}).get("closing", 0.0) <= -min_amount)
    return {
        "nature": nature, "as_of": t.isoformat(), "groups": groups,
        "party_count": len(parties), "total_outstanding": r2(sum(a for _, a in parties)),
        "parties_with_advance_balance": advances, "parties": out,
        "method": ("Ledger balances under the debtor/creditor groups. Aging (days) is a FIFO "
                   "estimate: the balance is matched against the most recent invoices; BUSY "
                   "bill-by-bill references are not used."),
    }


def _fifo_aging(db: BusyDB, codes: list[int], amounts: dict[int, float], sign: int,
                fy_start: _dt.date, as_of: _dt.date) -> dict[int, dict[str, Any]]:
    buckets = ("0-30", "31-60", "61-90", "91-180", "180+")
    out: dict[int, dict[str, Any]] = {}
    if not codes:
        return out
    txns: dict[int, list[tuple[_dt.date, float]]] = defaultdict(list)
    for part in chunks(codes):
        rows = db.query(
            "SELECT t2.MasterCode1 AS mc, t1.[Date] AS dt, t2.Value1 AS v "
            "FROM Tran2 t2 INNER JOIN Tran1 t1 ON t1.VchCode = t2.VchCode "
            f"WHERE t2.RecType = {RT_LEDGER} AND t1.[Date] < ? "
            f"AND t2.MasterCode1 IN ({','.join(str(c) for c in part)})",
            (db.dparam(_next_day(as_of)),),
        )
        for r in rows:
            v = -float(r["v"] or 0) * sign  # positive = increases outstanding
            if v > 0:
                txns[int(r["mc"])].append((_dt.date.fromisoformat(str(r["dt"])[:10]), v))
    for code in codes:
        remaining = amounts[code]
        b = dict.fromkeys(buckets, 0.0)
        oldest = None
        for d, v in sorted(txns.get(code, []), reverse=True):
            if remaining <= 0.005:
                break
            take = min(v, remaining)
            remaining -= take
            age = (as_of - d).days
            oldest = age
            key = ("0-30" if age <= 30 else "31-60" if age <= 60 else "61-90" if age <= 90
                   else "91-180" if age <= 180 else "180+")
            b[key] += take
        if remaining > 0.005:  # comes from opening balance
            age = (as_of - fy_start).days
            oldest = age
            b["180+" if age > 180 else "91-180" if age > 90 else "61-90" if age > 60
              else "31-60" if age > 30 else "0-30"] += remaining
        out[code] = {"aging": {k: r2(v) for k, v in b.items() if v > 0.005}, "oldest_days": oldest}
    return out


# ---------------------------------------------------------------------------
# vouchers
# ---------------------------------------------------------------------------


def day_book(db: BusyDB, from_date: str | None, to_date: str | None, vch_type: int | None = None,
             party: str | None = None, limit: int = 200) -> dict[str, Any]:
    masters = db.masters()
    f, t = period(db, from_date, to_date)
    sql = ("SELECT VchCode, VchType, VchNo, [Date], MasterCode1, VchAmtBaseCur FROM Tran1 "
           "WHERE [Date] >= ? AND [Date] < ?")
    params: list[Any] = [db.dparam(f), db.dparam(_next_day(t))]
    if vch_type is not None:
        sql += " AND VchType = ?"
        params.append(int(vch_type))
    if party:
        p = find_master(db, party, (MT_LEDGER,))
        sql += " AND MasterCode1 = ?"
        params.append(p.code)
    sql += " ORDER BY [Date], VchCode"
    rows = db.query(sql, params)
    totals: dict[str, float] = defaultdict(float)
    for r in rows:
        totals[_vt(db, r["vchtype"])] += abs(float(r["vchamtbasecur"] or 0))
    return {
        "from": f.isoformat(), "to": t.isoformat(), "voucher_count": len(rows),
        "totals_by_type": {k: r2(v) for k, v in sorted(totals.items())},
        "returned": min(limit, len(rows)),
        "vouchers": [{
            "vch_code": r["vchcode"], "date": r["date"], "vch_type": _vt(db, r["vchtype"]),
            "vch_no": r["vchno"],
            "party": masters[int(r["mastercode1"])].name if r.get("mastercode1") and int(r["mastercode1"]) in masters else None,
            "amount": r2(abs(float(r["vchamtbasecur"] or 0))),
        } for r in rows[:limit]],
    }


def voucher_details(db: BusyDB, vch_code: int | None = None, vch_no: str | None = None,
                    vch_type: int | None = None, include_raw: bool = False) -> dict[str, Any]:
    masters = db.masters()
    if vch_code is None:
        if not vch_no:
            raise ReportError("Give vch_code or vch_no")
        sql, params = "SELECT VchCode, VchType, [Date] FROM Tran1 WHERE VchNo = ?", [vch_no.strip()]
        if vch_type is not None:
            sql += " AND VchType = ?"
            params.append(int(vch_type))
        hits = db.query(sql, params)
        if not hits:
            raise ReportError(f"No voucher numbered '{vch_no}'")
        if len(hits) > 1:
            return {"ambiguous": True, "matches": [
                {"vch_code": h["vchcode"], "vch_type": _vt(db, h["vchtype"]), "date": h["date"]} for h in hits]}
        vch_code = int(hits[0]["vchcode"])
    head = db.query("SELECT * FROM Tran1 WHERE VchCode = ?", (int(vch_code),))
    if not head:
        raise ReportError(f"No voucher with VchCode {vch_code}")
    h = head[0]
    party_code = int(h.get("mastercode1") or 0)
    legs = db.query("SELECT * FROM Tran2 WHERE VchCode = ? ORDER BY RecType, SrNo", (int(vch_code),))
    ledger_lines, item_lines, other = [], [], []
    for leg in legs:
        mc = int(leg.get("mastercode1") or 0)
        name = masters[mc].name if mc in masters else f"#{mc}"
        rt = int(leg.get("rectype") or 0)
        v1 = float(leg.get("value1") or 0)
        if rt == RT_LEDGER:
            ledger_lines.append({"ledger": name, "debit": r2(-v1) if v1 < 0 else 0.0,
                                 "credit": r2(v1) if v1 > 0 else 0.0})
        elif rt == RT_ITEM:
            qty = abs(v1)
            value = abs(float(leg.get("value3") or 0))
            line = {"item": name, "qty": r2(qty), "value_excl_tax": r2(value),
                    "rate_excl_tax": r2(value / qty) if qty else None}
            if leg.get("d4"):
                line["list_price"] = r2(leg.get("d4"))
            if leg.get("d9"):
                line["discount_pct"] = r2(leg.get("d9"))
            if leg.get("d2"):
                line["net_rate_incl_tax"] = r2(leg.get("d2"))
            item_lines.append(line)
        else:
            other.append({"rectype": rt, "master": name, "value1": v1})
    order_lines = []
    try:
        for r in db.query("SELECT * FROM Tran3 WHERE VchCode = ? AND RecType = ?", (int(vch_code), RT3_ORDER_LINE)):
            mc = int(r.get("mastercode1") or 0)
            order_lines.append({"item": masters[mc].name if mc in masters else f"#{mc}",
                                "qty": r2(abs(float(r.get("value1") or 0))), "rate": r2(r.get("value3")),
                                "amount": r2(abs(float(r.get("newrefamount") or 0)))})
    except Exception:  # noqa: BLE001 - Tran3 layout differs between versions
        pass
    out = {
        "vch_code": int(vch_code), "vch_type": _vt(db, h.get("vchtype")), "vch_type_code": h.get("vchtype"),
        "vch_no": h.get("vchno"), "date": h.get("date"),
        "party": masters[party_code].name if party_code in masters else None,
        "total_incl_tax": r2(abs(float(h.get("vchamtbasecur") or 0))),
        "goods_value_excl_tax": r2(abs(float(h.get("vchsalepurcamt") or 0))),
        "ledger_entries": ledger_lines, "item_entries": item_lines,
    }
    if order_lines:
        out["order_lines"] = order_lines
    if other:
        out["other_lines"] = other
    if include_raw:
        out["raw_header"] = {k: v for k, v in h.items() if v not in (None, "", 0, 0.0, False)}
    return out


# ---------------------------------------------------------------------------
# inventory
# ---------------------------------------------------------------------------


def item_inward_sign(db: BusyDB) -> tuple[int, str]:
    """Which sign of Tran2.Value1 means stock IN. Detected from purchase vouchers."""
    if db.settings.item_inward_sign:
        return db.settings.item_inward_sign, "configured (BUSY_ITEM_INWARD_SIGN)"
    if "inward_sign" in db.cache:
        return db.cache["inward_sign"]
    result = (-1, "assumed (no purchase/sales item lines to detect from)")
    for vt, is_inward, label in ((VT_PURCHASE, True, "purchase"), (VT_SALES, False, "sales")):
        rows = db.query(
            "SELECT SUM(t2.Value1) AS s FROM Tran2 t2 INNER JOIN Tran1 t1 ON t1.VchCode = t2.VchCode "
            "WHERE t2.RecType = ? AND t1.VchType = ?", (RT_ITEM, vt))
        s = float(rows[0]["s"] or 0) if rows else 0.0
        if abs(s) > 0:
            sign = (1 if s > 0 else -1) * (1 if is_inward else -1)
            result = (sign, f"detected from {label} vouchers")
            break
    db.cache["inward_sign"] = result
    return result


def _item_movements(db: BusyDB, start: _dt.date | None, end: _dt.date, item: int | None = None
                    ) -> dict[int, dict[str, float]]:
    sql = ("SELECT t2.MasterCode1 AS mc, "
           "SUM(IIF(t2.Value1 > 0, t2.Value1, 0)) AS pq, SUM(IIF(t2.Value1 < 0, -t2.Value1, 0)) AS nq, "
           "SUM(IIF(t2.Value1 > 0, ABS(t2.Value3), 0)) AS pv, SUM(IIF(t2.Value1 < 0, ABS(t2.Value3), 0)) AS nv "
           "FROM Tran2 t2 INNER JOIN Tran1 t1 ON t1.VchCode = t2.VchCode "
           "WHERE t2.RecType = ? AND t1.[Date] < ?")
    params: list[Any] = [RT_ITEM, db.dparam(_next_day(end))]
    if start:
        sql += " AND t1.[Date] >= ?"
        params.append(db.dparam(start))
    if item is not None:
        sql += " AND t2.MasterCode1 = ?"
        params.append(item)
    sql += " GROUP BY t2.MasterCode1"
    sign, _ = item_inward_sign(db)
    out = {}
    for r in db.query(sql, params):
        pq, nq, pv, nv = (float(r[k] or 0) for k in ("pq", "nq", "pv", "nv"))
        if sign > 0:
            out[int(r["mc"])] = {"in_qty": pq, "out_qty": nq, "in_value": pv, "out_value": nv}
        else:
            out[int(r["mc"])] = {"in_qty": nq, "out_qty": pq, "in_value": nv, "out_value": pv}
    return out


def stock_summary(db: BusyDB, from_date: str | None, to_date: str | None, item_group: str | None = None,
                  search: str | None = None, include_zero: bool = False, limit: int = 500) -> dict[str, Any]:
    masters = db.masters()
    f, t = period(db, from_date, to_date)
    ob = opening_balances(db, MT_ITEM)
    fy_start = _fy_start(db, f)
    before = _item_movements(db, fy_start, f - _dt.timedelta(days=1)) if (fy_start is None or f > fy_start) else {}
    during = _item_movements(db, f, t)
    scope = find_master(db, item_group, (MT_ITEM_GROUP,)).code if item_group else None
    needle = (search or "").lower()
    rows = []
    for code, m in masters.items():
        if m.type != MT_ITEM:
            continue
        if scope is not None and all(p.code != scope for p in ancestors(masters, m)):
            continue
        if needle and needle not in m.name.lower() and needle not in (m.alias or "").lower():
            continue
        o = ob.get(code, {"d1": 0.0, "d3": 0.0})
        b = before.get(code, {"in_qty": 0, "out_qty": 0})
        d = during.get(code, {"in_qty": 0, "out_qty": 0, "in_value": 0, "out_value": 0})
        opening = o["d1"] + b["in_qty"] - b["out_qty"]
        closing = opening + d["in_qty"] - d["out_qty"]
        if not include_zero and all(abs(x) < 1e-9 for x in (opening, d["in_qty"], d["out_qty"], closing)):
            continue
        avg_in = d["in_value"] / d["in_qty"] if d["in_qty"] else None
        rows.append({
            "item": m.name, "group": masters[m.parent].name if m.parent in masters else None,
            "opening_qty": r2(opening), "inward_qty": r2(d["in_qty"]), "outward_qty": r2(d["out_qty"]),
            "closing_qty": r2(closing), "inward_value": r2(d["in_value"]), "outward_value": r2(d["out_value"]),
            "avg_inward_rate": r2(avg_in) if avg_in else None,
            "fy_opening_value": r2(o["d3"]),
        })
    rows.sort(key=lambda x: x["item"].lower())
    sign, how = item_inward_sign(db)
    return {
        "from": f.isoformat(), "to": t.isoformat(), "item_count": len(rows), "returned": min(limit, len(rows)),
        "items": rows[:limit],
        "notes": [f"Inward direction: Tran2.Value1 {'>' if sign > 0 else '<'} 0 ({how}).",
                  "Values are excl. tax line values; quantities are in each item's main unit.",
                  "Closing stock VALUE is not computed (depends on BUSY's valuation method); "
                  "avg_inward_rate x closing_qty is a rough estimate."],
    }


def stock_item_movement(db: BusyDB, item: str, from_date: str | None, to_date: str | None,
                        limit: int = 1000) -> dict[str, Any]:
    masters = db.masters()
    m = find_master(db, item, (MT_ITEM,))
    f, t = period(db, from_date, to_date)
    sign, how = item_inward_sign(db)
    ob = opening_balances(db, MT_ITEM).get(m.code, {"d1": 0.0})
    fy_start = _fy_start(db, f)
    opening = ob["d1"]
    if fy_start is None or f > fy_start:
        b = _item_movements(db, fy_start, f - _dt.timedelta(days=1), item=m.code).get(m.code)
        if b:
            opening += b["in_qty"] - b["out_qty"]
    rows = db.query(
        "SELECT t1.VchCode, t1.VchType, t1.VchNo, t1.[Date], t1.MasterCode1 AS party, t2.Value1, t2.Value3 "
        "FROM Tran2 t2 INNER JOIN Tran1 t1 ON t1.VchCode = t2.VchCode "
        "WHERE t2.RecType = ? AND t2.MasterCode1 = ? AND t1.[Date] >= ? AND t1.[Date] < ? "
        "ORDER BY t1.[Date], t1.VchCode",
        (RT_ITEM, m.code, db.dparam(f), db.dparam(_next_day(t))),
    )
    running, entries = opening, []
    for r in rows:
        q = float(r["value1"] or 0)
        inward = (q > 0) == (sign > 0)
        running += abs(q) if inward else -abs(q)
        if len(entries) < limit:
            pc = int(r.get("party") or 0)
            entries.append({
                "date": r["date"], "vch_type": _vt(db, r["vchtype"]), "vch_no": r["vchno"],
                "party": masters[pc].name if pc in masters else None,
                "in_qty": r2(abs(q)) if inward else 0.0, "out_qty": 0.0 if inward else r2(abs(q)),
                "value": r2(abs(float(r["value3"] or 0))), "balance_qty": r2(running),
            })
    return {"item": m.name, "from": f.isoformat(), "to": t.isoformat(), "opening_qty": r2(opening),
            "closing_qty": r2(running), "entry_count": len(rows), "entries": entries,
            "inward_sign_rule": how}


# ---------------------------------------------------------------------------
# sales / purchase analysis
# ---------------------------------------------------------------------------


def trade_analysis(db: BusyDB, kind: str, group_by: str, from_date: str | None, to_date: str | None,
                   top: int = 50, vch_types: list[int] | None = None,
                   return_types: list[int] | None = None) -> dict[str, Any]:
    if kind not in ("sales", "purchase"):
        raise ReportError("kind must be 'sales' or 'purchase'")
    if group_by not in ("party", "item", "month", "voucher"):
        raise ReportError("group_by must be party, item, month or voucher")
    masters = db.masters()
    f, t = period(db, from_date, to_date)
    main = vch_types or ([VT_SALES] if kind == "sales" else [VT_PURCHASE])
    rets = return_types if return_types is not None else ([VT_SALES_RETURN] if kind == "sales" else [VT_PURCHASE_RETURN])
    all_types = sorted(set(main) | set(rets))
    tlist = ",".join(str(int(x)) for x in all_types)
    dates = (db.dparam(f), db.dparam(_next_day(t)))
    agg: dict[Any, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    if group_by == "item":
        rows = db.query(
            "SELECT t1.VchType AS vt, t2.MasterCode1 AS mc, SUM(ABS(t2.Value1)) AS q, SUM(ABS(t2.Value3)) AS v "
            "FROM Tran2 t2 INNER JOIN Tran1 t1 ON t1.VchCode = t2.VchCode "
            f"WHERE t2.RecType = {RT_ITEM} AND t1.VchType IN ({tlist}) AND t1.[Date] >= ? AND t1.[Date] < ? "
            "GROUP BY t1.VchType, t2.MasterCode1", dates)
        for r in rows:
            mc = int(r["mc"] or 0)
            key = masters[mc].name if mc in masters else f"#{mc}"
            side = "gross" if int(r["vt"]) in main else "returns"
            agg[key][f"{side}_qty"] += float(r["q"] or 0)
            agg[key][f"{side}_value"] += float(r["v"] or 0)
        lines = [{"item": k, "qty": r2(v["gross_qty"]), "value_excl_tax": r2(v["gross_value"]),
                  "returns_qty": r2(v["returns_qty"]), "returns_value": r2(v["returns_value"]),
                  "net_qty": r2(v["gross_qty"] - v["returns_qty"]),
                  "net_value": r2(v["gross_value"] - v["returns_value"])} for k, v in agg.items()]
        lines.sort(key=lambda x: -x["net_value"])
        total = sum(x["net_value"] for x in lines)
    else:
        rows = db.query(
            f"SELECT VchCode, VchType, VchNo, [Date], MasterCode1, VchAmtBaseCur FROM Tran1 "
            f"WHERE VchType IN ({tlist}) AND [Date] >= ? AND [Date] < ?", dates)
        for r in rows:
            amt = abs(float(r["vchamtbasecur"] or 0))
            side = "gross" if int(r["vchtype"]) in main else "returns"
            if group_by == "party":
                mc = int(r["mastercode1"] or 0)
                key = masters[mc].name if mc in masters else f"#{mc}"
            elif group_by == "month":
                key = str(r["date"])[:7]
            else:
                key = (r["date"], r["vchno"], int(r["mastercode1"] or 0), int(r["vchtype"]))
            agg[key][side] += amt
            agg[key]["count"] += 1 if side == "gross" else 0
        lines = []
        for k, v in agg.items():
            row: dict[str, Any]
            if group_by == "voucher":
                d, no, mc, vt = k
                row = {"date": d, "vch_no": no, "vch_type": _vt(db, vt),
                       "party": masters[mc].name if mc in masters else None}
            else:
                row = {group_by: k}
            row.update({"invoices": int(v["count"]), "gross_incl_tax": r2(v["gross"]),
                        "returns_incl_tax": r2(v["returns"]), "net_incl_tax": r2(v["gross"] - v["returns"])})
            lines.append(row)
        if group_by == "month":
            lines.sort(key=lambda x: x["month"])
        else:
            lines.sort(key=lambda x: -x["net_incl_tax"])
        total = sum(x["net_incl_tax"] for x in lines)
    return {
        "kind": kind, "group_by": group_by, "from": f.isoformat(), "to": t.isoformat(),
        "voucher_types": [_vt(db, x) for x in main], "return_types": [_vt(db, x) for x in rets],
        "net_total": r2(total), "line_count": len(lines),
        "lines": lines if group_by == "month" else lines[:top],
    }

