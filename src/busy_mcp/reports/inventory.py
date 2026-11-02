"""Inventory: stock summary, item movement, stock by material centre (godown),
batch-wise stock with expiry, serial numbers."""

from __future__ import annotations

import datetime as _dt
from collections import defaultdict
from typing import Any

from ..db import BusyDB
from ..errors import ReportError
from ..schema import MT_ITEM, MT_ITEM_GROUP, MT_MATERIAL_CENTRE, RT3_BATCH, VT_PURCHASE, VT_SALES
from ._common import (
    ancestors,
    find_master,
    int_list,
    master_name,
    next_day,
    opening_balances,
    paginate,
    parent_name,
    period,
    r2,
    select_list,
    to_date,
    vt,
)
from ._index import MovementIndex

ITEM_INDEX = "index:item"


# ------------------------------------------------------------ data conventions


def item_rectypes(db: BusyDB) -> tuple[list[int], str]:
    """Which Tran2.RecType values are genuine stock movements in this database.

    RecType 2 is the documented item line. Some BUSY versions also put items in
    other RecTypes (7, 20 seen in the wild). A candidate is counted only if its
    rows are NOT mirrors of RecType 2 rows (same voucher, item and quantity),
    so nothing is double counted.
    """
    if db.settings.item_rectypes:
        return list(db.settings.item_rectypes), "configured (BUSY_ITEM_RECTYPES)"

    def detect() -> tuple[list[int], str]:
        rows = db.query(
            "SELECT t2.RecType AS rt, COUNT(*) AS n FROM Tran2 t2 INNER JOIN Master1 m ON m.Code = t2.MasterCode1 "
            f"WHERE m.MasterType = {MT_ITEM} GROUP BY t2.RecType"
        )
        counts = {int(r["rt"]): int(r["n"] or 0) for r in rows if r.get("rt") is not None}
        chosen, notes = [2], []
        for rt, n in sorted(counts.items()):
            if rt in (1, 2) or n == 0:
                continue
            dup = db.query(
                "SELECT COUNT(*) AS n FROM Tran2 a INNER JOIN Tran2 b "
                "ON (a.VchCode = b.VchCode AND a.MasterCode1 = b.MasterCode1) "
                f"WHERE a.RecType = {int(rt)} AND b.RecType = 2 AND ABS(a.Value1) = ABS(b.Value1)"
            )[0]["n"] or 0
            if int(dup) < n / 2:
                chosen.append(rt)
                notes.append(f"RecType {rt}: {n} item lines, independent of RecType 2 -> counted")
            else:
                notes.append(f"RecType {rt}: {n} item lines mirroring RecType 2 -> ignored")
        return sorted(chosen), "detected; " + ("; ".join(notes) if notes else "only RecType 2 carries items")

    return db.cached("item_rectypes", detect)


def item_inward_sign(db: BusyDB) -> tuple[int, str]:
    """Which sign of Tran2.Value1 means stock IN, verified on this database's own
    purchase (or sales) vouchers. Public evidence says positive = in."""
    if db.settings.item_inward_sign:
        return db.settings.item_inward_sign, "configured (BUSY_ITEM_INWARD_SIGN)"

    def detect() -> tuple[int, str]:
        for code, inward, label in ((VT_PURCHASE, True, "purchase"), (VT_SALES, False, "sales")):
            rows = db.query(
                "SELECT SUM(t2.Value1) AS s FROM Tran2 t2 INNER JOIN Tran1 t1 ON t1.VchCode = t2.VchCode "
                "WHERE t2.RecType = 2 AND t1.VchType = ?" + db.active_voucher_filter("t1"), (code,))
            s = float(rows[0]["s"] or 0) if rows else 0.0
            if s:
                return (1 if s > 0 else -1) * (1 if inward else -1), f"detected from {label} vouchers"
        return 1, "default (positive = inward); no purchase/sales item lines to verify against"

    return db.cached("inward_sign", detect)


def _rectype_sql(db: BusyDB) -> str:
    return int_list(item_rectypes(db)[0])


def item_index(db: BusyDB) -> MovementIndex:
    """Daily (in-qty, out-qty, in-value, out-value) per item, built once per data version."""

    def build() -> MovementIndex:
        rows = db.query(
            "SELECT t2.MasterCode1 AS mc, t1.[Date] AS dt, "
            "SUM(IIF(t2.Value1 > 0, t2.Value1, 0)) AS pq, SUM(IIF(t2.Value1 < 0, -t2.Value1, 0)) AS nq, "
            "SUM(IIF(t2.Value1 > 0, ABS(t2.Value3), 0)) AS pv, SUM(IIF(t2.Value1 < 0, ABS(t2.Value3), 0)) AS nv "
            "FROM Tran2 t2 INNER JOIN Tran1 t1 ON t1.VchCode = t2.VchCode "
            f"WHERE t2.RecType IN ({_rectype_sql(db)}){db.active_voucher_filter('t1')} "
            "GROUP BY t2.MasterCode1, t1.[Date]"
        )
        positive_in = item_inward_sign(db)[0] > 0

        def metrics(r: dict[str, Any]) -> tuple[float, ...]:
            pq, nq, pv, nv = (float(r[k] or 0) for k in ("pq", "nq", "pv", "nv"))
            return (pq, nq, pv, nv) if positive_in else (nq, pq, nv, pv)

        return MovementIndex(4, ((int(r["mc"]), to_date(r["dt"]), metrics(r))
                                 for r in rows if r.get("mc") is not None and r.get("dt")))

    return db.cached(ITEM_INDEX, build)


def _item_move(db: BusyDB, code: int, start: _dt.date | None, end: _dt.date) -> dict[str, float]:
    iq, oq, iv, ov = item_index(db).range(code, start, end)
    return {"in_qty": iq, "out_qty": oq, "in_value": iv, "out_value": ov}


# --------------------------------------------------------------------- reports


def stock_summary(db: BusyDB, from_date: str | None, to_date_: str | None, item_group: str | None = None,
                  search: str | None = None, include_zero: bool = False, only_negative: bool = False,
                  offset: int = 0, limit: int = 200) -> dict[str, Any]:
    masters = db.masters()
    f, t = period(db, from_date, to_date_)
    ob = opening_balances(db, MT_ITEM)
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
        b = _item_move(db, code, None, f - _dt.timedelta(days=1))
        d = _item_move(db, code, f, t)
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
    rts, rt_how = item_rectypes(db)
    page = paginate(rows, offset, limit)
    return {
        "from": f.isoformat(), "to": t.isoformat(), **{k: v for k, v in page.items() if k != "items"},
        "items": page["items"],
        "notes": [f"Inward = Tran2.Value1 {'>' if sign > 0 else '<'} 0 ({how}).",
                  f"Item line RecTypes {rts} ({rt_how}).",
                  "Values exclude tax; quantities are in each item's main unit.",
                  "Closing stock VALUE depends on BUSY's valuation method and is not computed; "
                  "avg_inward_rate x closing_qty is a rough estimate only."],
    }


def stock_item_movement(db: BusyDB, item: str, from_date: str | None, to_date_: str | None,
                        offset: int = 0, limit: int = 200) -> dict[str, Any]:
    masters = db.masters()
    m = find_master(db, item, (MT_ITEM,))
    f, t = period(db, from_date, to_date_)
    sign, how = item_inward_sign(db)
    before = _item_move(db, m.code, None, f - _dt.timedelta(days=1))
    opening = opening_balances(db, MT_ITEM).get(m.code, {"d1": 0.0})["d1"] + before["in_qty"] - before["out_qty"]
    mc_col = ", t2.MasterCode2 AS mc2" if db.has_column("Tran2", "MasterCode2") else ""
    rows = db.query(
        f"SELECT t1.VchCode, t1.VchType, t1.VchNo, t1.[Date], t1.MasterCode1 AS party, t2.Value1, t2.Value3{mc_col} "
        "FROM Tran2 t2 INNER JOIN Tran1 t1 ON t1.VchCode = t2.VchCode "
        f"WHERE t2.RecType IN ({_rectype_sql(db)}) AND t2.MasterCode1 = ? AND t1.[Date] >= ? AND t1.[Date] < ?"
        + db.active_voucher_filter("t1") + " ORDER BY t1.[Date], t1.VchCode",
        (m.code, db.dparam(f), db.dparam(next_day(t))),
    )
    running = opening
    entries = []
    for r in rows:
        q = float(r["value1"] or 0)
        inward = (q > 0) == (sign > 0)
        running += abs(q) if inward else -abs(q)
        e: dict[str, Any] = {
            "date": r["date"], "vch_type": vt(db, r["vchtype"]), "vch_no": r["vchno"],
            "party": master_name(masters, r.get("party")),
            "in_qty": r2(abs(q)) if inward else 0.0, "out_qty": 0.0 if inward else r2(abs(q)),
            "value": r2(abs(float(r["value3"] or 0))), "balance_qty": r2(running),
        }
        mc = r.get("mc2")
        if mc and int(mc) in masters and masters[int(mc)].type == MT_MATERIAL_CENTRE:
            e["material_centre"] = masters[int(mc)].name
        entries.append(e)
    page = paginate(entries, offset, limit)
    return {"item": m.name, "group": parent_name(masters, m), "from": f.isoformat(), "to": t.isoformat(),
            "opening_qty": r2(opening), "closing_qty": r2(running),
            **{k: v for k, v in page.items() if k != "items"}, "entries": page["items"],
            "inward_sign_rule": how}


def stock_by_location(db: BusyDB, as_of: str | None, item: str | None = None, location: str | None = None,
                      include_zero: bool = False, offset: int = 0, limit: int = 300) -> dict[str, Any]:
    """Closing quantity per item per material centre (godown).

    Opening per centre comes from Tran4 (RecType 0) when present; movements from
    Tran2 item lines grouped by MasterCode2. Stock with no centre recorded is
    reported under "(not allocated)".
    """
    masters = db.masters()
    _, t = period(db, None, as_of)
    if not db.has_column("Tran2", "MasterCode2"):
        raise ReportError("This BUSY version stores no material centre on item lines (Tran2.MasterCode2).")
    item_code = find_master(db, item, (MT_ITEM,)).code if item else None
    loc_code = find_master(db, location, (MT_MATERIAL_CENTRE,)).code if location else None
    qty: dict[tuple[int, int], float] = defaultdict(float)
    method = ["movements: Tran2 item lines by MasterCode2"]
    tran4_cols = ["MasterCode1", "MasterCode2", "D1", "RecType"]
    located: set[int] = set()
    if db.has_table("Tran4") and db.existing_columns("Tran4", tran4_cols) == tran4_cols:
        for r in db.query("SELECT MasterCode1 AS ic, MasterCode2 AS mc, SUM(D1) AS q FROM Tran4 "
                          "WHERE RecType = 0 GROUP BY MasterCode1, MasterCode2"):
            ic, mc = int(r["ic"] or 0), int(r["mc"] or 0)
            if ic in masters and masters[ic].type == MT_ITEM:
                qty[(ic, mc)] += float(r["q"] or 0)
                located.add(ic)
        method.append("opening: Tran4 RecType 0 by centre")
    unlocated = 0
    for ic, ob in opening_balances(db, MT_ITEM).items():
        if ic not in located and abs(ob.get("d1", 0.0)) > 1e-9:
            qty[(ic, 0)] += ob["d1"]
            unlocated += 1
    if unlocated:
        method.append(f"opening of {unlocated} item(s) without centre-wise opening shown as '(not allocated)'")
    sign = item_inward_sign(db)[0]
    rows = db.query(
        "SELECT t2.MasterCode1 AS ic, t2.MasterCode2 AS mc, SUM(t2.Value1) AS q "
        "FROM Tran2 t2 INNER JOIN Tran1 t1 ON t1.VchCode = t2.VchCode "
        f"WHERE t2.RecType IN ({_rectype_sql(db)}) AND t1.[Date] < ?{db.active_voucher_filter('t1')} "
        "GROUP BY t2.MasterCode1, t2.MasterCode2", (db.dparam(next_day(t)),))
    for r in rows:
        qty[(int(r["ic"] or 0), int(r["mc"] or 0))] += sign * float(r["q"] or 0)
    out: list[dict[str, Any]] = []
    for (ic, mc), q in qty.items():
        if item_code is not None and ic != item_code:
            continue
        if loc_code is not None and mc != loc_code:
            continue
        if not include_zero and abs(q) < 1e-9:
            continue
        loc = masters[mc].name if mc in masters and masters[mc].type == MT_MATERIAL_CENTRE else "(not allocated)"
        out.append({"item": master_name(masters, ic), "location": loc, "closing_qty": r2(q)})
    out.sort(key=lambda x: (x["item"] or "", x["location"]))
    totals: dict[str, float] = defaultdict(float)
    for row in out:
        totals[row["location"]] += row["closing_qty"]
    page = paginate(out, offset, limit)
    return {"as_of": t.isoformat(), "totals_by_location": {k: r2(v) for k, v in sorted(totals.items())},
            **{k: v for k, v in page.items() if k != "items"}, "stock": page["items"], "method": "; ".join(method),
            "confidence": "likely (material centre = Tran2.MasterCode2, per a production BUSY integration)"}


def batch_stock(db: BusyDB, item: str | None = None, expiring_within_days: int | None = None,
                as_of: str | None = None, offset: int = 0, limit: int = 300) -> dict[str, Any]:
    """Batch-wise closing quantity with manufacturing and expiry dates."""
    masters = db.masters()
    _, t = period(db, None, as_of)
    item_code = find_master(db, item, (MT_ITEM,)).code if item else None
    stock: dict[tuple[int, str], dict[str, Any]] = {}
    sources = []
    if db.has_table("ItemParamDet") and len(db.existing_columns("ItemParamDet", ["ItemCode", "C1", "Value1"])) == 3:
        sql = "SELECT ItemCode AS ic, C1 AS batch, SUM(Value1) AS q FROM ItemParamDet"
        params: list[Any] = []
        if db.has_column("ItemParamDet", "Date"):
            sql += " WHERE ([Date] < ? OR [Date] IS NULL)"
            params.append(db.dparam(next_day(t)))
        for r in db.query(sql + " GROUP BY ItemCode, C1", params):
            key = (int(r["ic"] or 0), str(r["batch"] or "").strip())
            stock.setdefault(key, {"qty": 0.0})["qty"] += float(r["q"] or 0)
        sources.append("quantities: ItemParamDet (C1 = batch)")
    dates: dict[tuple[int, str], dict[str, Any]] = {}
    if db.has_table("Tran3"):
        cols = db.existing_columns("Tran3", ["MasterCode1", "No", "DueDate", "MfgDate", "Balance1", "Balance2",
                                             "Balance3", "Value1"])
        if {"MasterCode1", "No"} <= set(cols):
            rows = db.query(f"SELECT {select_list(cols)} FROM Tran3 WHERE RecType = {RT3_BATCH}")
            from_tran3_qty = not stock
            for r in rows:
                key = (int(r.get("mastercode1") or 0), str(r.get("no") or "").strip())
                if from_tran3_qty:
                    stock.setdefault(key, {"qty": 0.0})["qty"] += float(r.get("value1") or 0)
                d = dates.setdefault(key, {})
                for src, dst in (("duedate", "expiry"), ("mfgdate", "mfg_date"), ("balance3", "mrp"),
                                 ("balance1", "purchase_rate"), ("balance2", "sale_rate")):
                    if r.get(src) and dst not in d:
                        d[dst] = r[src] if dst in ("expiry", "mfg_date") else r2(r[src])
            sources.append("dates/rates: Tran3 RecType 2" + (" (+ quantities)" if from_tran3_qty else ""))
    if not stock:
        return {"as_of": t.isoformat(), "total": 0, "batches": [],
                "note": "No batch data found (batch-wise inventory is probably not enabled in this company)."}
    out = []
    for (ic, batch), v in stock.items():
        if item_code is not None and ic != item_code:
            continue
        if abs(v["qty"]) < 1e-9:
            continue
        row: dict[str, Any] = {"item": master_name(masters, ic), "batch": batch or "(blank)", "qty": r2(v["qty"])}
        row.update(dates.get((ic, batch), {}))
        exp = row.get("expiry")
        if exp and str(exp)[:4].isdigit() and int(str(exp)[:4]) > 1990:
            days = (to_date(exp) - t).days
            row["days_to_expiry"] = days
            if expiring_within_days is not None and days > expiring_within_days:
                continue
        elif expiring_within_days is not None:
            continue
        out.append(row)
    out.sort(key=lambda x: (x.get("days_to_expiry", 10**6), x["item"] or ""))
    page = paginate(out, offset, limit)
    return {"as_of": t.isoformat(), **{k: v for k, v in page.items() if k != "items"}, "batches": page["items"],
            "method": "; ".join(sources), "confidence": "likely (two production integrations)"}


def serial_numbers(db: BusyDB, item: str | None = None, serial: str | None = None, in_stock_only: bool = True,
                   offset: int = 0, limit: int = 300) -> dict[str, Any]:
    """Serial numbers per item with their stock status and last movement."""
    if not db.has_table("ItemSerialNo"):
        return {"total": 0, "serials": [], "note": "No serial-number data (feature not enabled in this company)."}
    masters = db.masters()
    cols = db.existing_columns("ItemSerialNo", ["ItemCode", "SerialNo", "Value1", "Date", "VchCode", "VchType",
                                                "VchNo", "MCCode", "WarrantyMonth"])
    if not {"ItemCode", "SerialNo", "Value1"} <= set(cols):
        raise ReportError("ItemSerialNo table has an unexpected layout in this BUSY version; use read-table.")
    sql = f"SELECT {select_list(cols)} FROM ItemSerialNo WHERE 1 = 1"
    params: list[Any] = []
    if item:
        sql += " AND ItemCode = ?"
        params.append(find_master(db, item, (MT_ITEM,)).code)
    if serial:
        sql += " AND SerialNo = ?"
        params.append(serial.strip())
    order = " ORDER BY [Date]" if "Date" in cols else ""
    state: dict[tuple[int, str], dict[str, Any]] = {}
    for r in db.query(sql + order, params):
        key = (int(r.get("itemcode") or 0), str(r.get("serialno") or "").strip())
        s = state.setdefault(key, {"qty": 0.0})
        s["qty"] += float(r.get("value1") or 0)
        s["last_date"] = r.get("date")
        s["last_voucher"] = r.get("vchno")
        if r.get("vchtype") is not None:
            s["last_vch_type"] = vt(db, r["vchtype"])
        if r.get("mccode"):
            s["location"] = master_name(masters, r["mccode"])
        if r.get("warrantymonth"):
            s["warranty_months"] = r["warrantymonth"]
    out = []
    for (ic, sn), s in state.items():
        in_stock = s.pop("qty") > 0
        if in_stock_only and not in_stock:
            continue
        out.append({"item": master_name(masters, ic), "serial_no": sn, "in_stock": in_stock, **s})
    out.sort(key=lambda x: (x["item"] or "", x["serial_no"]))
    page = paginate(out, offset, limit)
    return {**{k: v for k, v in page.items() if k != "items"}, "serials": page["items"],
            "method": "ItemSerialNo: sum of Value1 per (item, serial) > 0 means in stock"}
