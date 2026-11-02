"""Inventory reports: stock summary and item movement."""

from __future__ import annotations

import datetime as _dt
from typing import Any

from ..db import BusyDB
from ..schema import MT_ITEM, MT_ITEM_GROUP, RT_ITEM, VT_PURCHASE, VT_SALES
from ._common import (
    ancestors,
    find_master,
    fy_start,
    master_name,
    next_day,
    opening_balances,
    paginate,
    parent_name,
    period,
    r2,
    vt,
)


def item_inward_sign(db: BusyDB) -> tuple[int, str]:
    """Which sign of Tran2.Value1 means stock IN, detected from real vouchers.

    Public evidence (an independent stock-report tool and Folio1 opening stock)
    says positive = in; we still verify against this database's own purchases.
    """
    if db.settings.item_inward_sign:
        return db.settings.item_inward_sign, "configured (BUSY_ITEM_INWARD_SIGN)"

    def detect() -> tuple[int, str]:
        for code, inward, label in ((VT_PURCHASE, True, "purchase"), (VT_SALES, False, "sales")):
            rows = db.query(
                "SELECT SUM(t2.Value1) AS s FROM Tran2 t2 INNER JOIN Tran1 t1 ON t1.VchCode = t2.VchCode "
                "WHERE t2.RecType = ? AND t1.VchType = ?" + db.active_voucher_filter("t1"), (RT_ITEM, code))
            s = float(rows[0]["s"] or 0) if rows else 0.0
            if s:
                sign = (1 if s > 0 else -1) * (1 if inward else -1)
                return sign, f"detected from {label} vouchers"
        return 1, "default (positive = inward); no purchase/sales item lines to verify against"

    db.masters()
    return db.cached("inward_sign", detect)


def _item_movements(db: BusyDB, start: _dt.date | None, end: _dt.date, item: int | None = None
                    ) -> dict[int, dict[str, float]]:
    sql = ("SELECT t2.MasterCode1 AS mc, "
           "SUM(IIF(t2.Value1 > 0, t2.Value1, 0)) AS pq, SUM(IIF(t2.Value1 < 0, -t2.Value1, 0)) AS nq, "
           "SUM(IIF(t2.Value1 > 0, ABS(t2.Value3), 0)) AS pv, SUM(IIF(t2.Value1 < 0, ABS(t2.Value3), 0)) AS nv "
           "FROM Tran2 t2 INNER JOIN Tran1 t1 ON t1.VchCode = t2.VchCode "
           "WHERE t2.RecType = ? AND t1.[Date] < ?" + db.active_voucher_filter("t1"))
    params: list[Any] = [RT_ITEM, db.dparam(next_day(end))]
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


_ZERO = {"in_qty": 0.0, "out_qty": 0.0, "in_value": 0.0, "out_value": 0.0}


def stock_summary(db: BusyDB, from_date: str | None, to_date: str | None, item_group: str | None = None,
                  search: str | None = None, include_zero: bool = False, only_negative: bool = False,
                  offset: int = 0, limit: int = 200) -> dict[str, Any]:
    masters = db.masters()
    f, t = period(db, from_date, to_date)
    ob = opening_balances(db, MT_ITEM)
    start = fy_start(db)
    before = _item_movements(db, start, f - _dt.timedelta(days=1)) if (start is None or f > start) else {}
    during = _item_movements(db, f, t)
    scope = find_master(db, item_group, (MT_ITEM_GROUP,)).code if item_group else None
    needle = (search or "").strip().lower()
    rows: list[dict[str, Any]] = []
    for code, m in masters.items():
        if m.type != MT_ITEM:
            continue
        if scope is not None and all(p.code != scope for p in ancestors(masters, m)):
            continue
        if needle and needle not in m.name.lower() and needle not in (m.alias or "").lower():
            continue
        o = ob.get(code, {"d1": 0.0, "d3": 0.0})
        b = before.get(code, _ZERO)
        d = during.get(code, _ZERO)
        opening = o["d1"] + b["in_qty"] - b["out_qty"]
        closing = opening + d["in_qty"] - d["out_qty"]
        if only_negative and closing >= 0:
            continue
        if not include_zero and all(abs(x) < 1e-9 for x in (opening, d["in_qty"], d["out_qty"], closing)):
            continue
        avg_in = d["in_value"] / d["in_qty"] if d["in_qty"] else None
        rows.append({
            "item": m.name, "group": parent_name(masters, m),
            "opening_qty": r2(opening), "inward_qty": r2(d["in_qty"]), "outward_qty": r2(d["out_qty"]),
            "closing_qty": r2(closing), "inward_value": r2(d["in_value"]), "outward_value": r2(d["out_value"]),
            "avg_inward_rate": r2(avg_in) if avg_in else None, "fy_opening_value": r2(o["d3"]),
        })
    rows.sort(key=lambda x: x["item"].lower())
    sign, how = item_inward_sign(db)
    page = paginate(rows, offset, limit)
    return {
        "from": f.isoformat(), "to": t.isoformat(), **{k: v for k, v in page.items() if k != "items"},
        "items": page["items"],
        "notes": [f"Inward = Tran2.Value1 {'>' if sign > 0 else '<'} 0 ({how}).",
                  "Values exclude tax; quantities are in each item's main unit.",
                  "Closing stock VALUE depends on BUSY's valuation method and is not computed; "
                  "avg_inward_rate x closing_qty is a rough estimate only."],
    }


def stock_item_movement(db: BusyDB, item: str, from_date: str | None, to_date: str | None,
                        offset: int = 0, limit: int = 200) -> dict[str, Any]:
    masters = db.masters()
    m = find_master(db, item, (MT_ITEM,))
    f, t = period(db, from_date, to_date)
    sign, how = item_inward_sign(db)
    opening = opening_balances(db, MT_ITEM).get(m.code, {"d1": 0.0})["d1"]
    start = fy_start(db)
    if start is None or f > start:
        b = _item_movements(db, start, f - _dt.timedelta(days=1), item=m.code).get(m.code)
        if b:
            opening += b["in_qty"] - b["out_qty"]
    rows = db.query(
        "SELECT t1.VchCode, t1.VchType, t1.VchNo, t1.[Date], t1.MasterCode1 AS party, t2.Value1, t2.Value3 "
        "FROM Tran2 t2 INNER JOIN Tran1 t1 ON t1.VchCode = t2.VchCode "
        "WHERE t2.RecType = ? AND t2.MasterCode1 = ? AND t1.[Date] >= ? AND t1.[Date] < ?"
        + db.active_voucher_filter("t1") + " ORDER BY t1.[Date], t1.VchCode",
        (RT_ITEM, m.code, db.dparam(f), db.dparam(next_day(t))),
    )
    running = opening
    entries = []
    for r in rows:
        q = float(r["value1"] or 0)
        inward = (q > 0) == (sign > 0)
        running += abs(q) if inward else -abs(q)
        entries.append({
            "date": r["date"], "vch_type": vt(db, r["vchtype"]), "vch_no": r["vchno"],
            "party": master_name(masters, r.get("party")),
            "in_qty": r2(abs(q)) if inward else 0.0, "out_qty": 0.0 if inward else r2(abs(q)),
            "value": r2(abs(float(r["value3"] or 0))), "balance_qty": r2(running),
        })
    page = paginate(entries, offset, limit)
    return {"item": m.name, "group": parent_name(masters, m), "from": f.isoformat(), "to": t.isoformat(),
            "opening_qty": r2(opening), "closing_qty": r2(running),
            **{k: v for k, v in page.items() if k != "items"}, "entries": page["items"],
            "inward_sign_rule": how}
