"""Voucher reports: day book and full voucher details."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..db import BusyDB
from ..errors import ReportError
from ..schema import (
    METHOD_NEW_REF,
    MT_LEDGER,
    MT_MATERIAL_CENTRE,
    MT_PURCHASE_TYPE,
    MT_SALE_TYPE,
    RT3_BATCH,
    RT3_ORDER_LINE,
    RT_BILL_SUNDRY,
    RT_LEDGER,
)
from ._common import chunks, find_master, int_list, master_name, next_day, paginate, period, r2, vt
from .explore import decode_row, is_table_blocked
from .inventory import item_rectypes
from .overview import narration_source


def _narrations(db: BusyDB, vch_codes: list[int]) -> dict[int, str]:
    src = narration_source(db)
    if not src or not vch_codes:
        return {}
    table, col = src.split(".")
    has2 = db.has_column(table, "Narration2")
    out: dict[int, str] = {}
    for part in chunks(sorted(set(vch_codes))):
        cols = f"VchCode, {col}" + (", Narration2" if has2 else "")
        for r in db.query(f"SELECT {cols} FROM {table} WHERE VchCode IN ({int_list(part)})"):
            text = " ".join(x for x in (r.get("narration1"), r.get("narration2")) if x)
            if text:
                out[int(r["vchcode"])] = text
    return out


def _ledger_totals(db: BusyDB, vch_codes: list[int]) -> dict[int, float]:
    """Sum of debits per voucher (the voucher amount when the header total is empty)."""
    out: dict[int, float] = defaultdict(float)
    for part in chunks(sorted(set(vch_codes))):
        rows = db.query(
            "SELECT VchCode, SUM(IIF(Value1 < 0, -Value1, 0)) AS dr FROM Tran2 "
            f"WHERE RecType = {RT_LEDGER} AND VchCode IN ({int_list(part)}) GROUP BY VchCode")
        for r in rows:
            out[int(r["vchcode"])] = float(r["dr"] or 0)
    return out


def day_book(db: BusyDB, from_date: str | None, to_date: str | None, vch_type: int | None = None,
             party: str | None = None, offset: int = 0, limit: int = 100) -> dict[str, Any]:
    masters = db.masters()
    f, t = period(db, from_date, to_date)
    series = ", t1.VchSeriesCode" if db.has_column("Tran1", "VchSeriesCode") else ""
    sql = (f"SELECT t1.VchCode, t1.VchType, t1.VchNo, t1.[Date], t1.MasterCode1, t1.VchAmtBaseCur{series} "
           "FROM Tran1 t1 WHERE t1.[Date] >= ? AND t1.[Date] < ?" + db.active_voucher_filter("t1"))
    params: list[Any] = [db.dparam(f), db.dparam(next_day(t))]
    if vch_type is not None:
        sql += " AND t1.VchType = ?"
        params.append(int(vch_type))
    if party:
        sql += " AND t1.MasterCode1 = ?"
        params.append(find_master(db, party, (MT_LEDGER,)).code)
    rows = db.query(sql + " ORDER BY t1.[Date], t1.VchCode", params)
    page = paginate(rows, offset, limit)
    codes = [int(r["vchcode"]) for r in page["items"]]
    fallback = _ledger_totals(db, [c for c, r in zip(codes, page["items"], strict=True) if not r.get("vchamtbasecur")])
    narr = _narrations(db, codes)
    counts: dict[str, int] = defaultdict(int)
    for r in rows:
        counts[vt(db, r["vchtype"])] += 1
    vouchers = []
    for r in page["items"]:
        code = int(r["vchcode"])
        amt = abs(float(r.get("vchamtbasecur") or 0)) or fallback.get(code, 0.0)
        v = {"vch_code": code, "date": r["date"], "vch_type": vt(db, r["vchtype"]), "vch_no": r["vchno"],
             "party": master_name(masters, r.get("mastercode1")), "amount": r2(amt)}
        if r.get("vchseriescode"):
            v["series"] = master_name(masters, r["vchseriescode"])
        if code in narr:
            v["narration"] = narr[code]
        vouchers.append(v)
    return {"from": f.isoformat(), "to": t.isoformat(), "count_by_type": dict(sorted(counts.items())),
            **{k: v for k, v in page.items() if k != "items"}, "vouchers": vouchers}


def voucher_details(db: BusyDB, vch_code: int | None = None, vch_no: str | None = None,
                    vch_type: int | None = None, include_raw: bool = False) -> dict[str, Any]:
    masters = db.masters()
    if vch_code is None:
        if not vch_no or not vch_no.strip():
            raise ReportError("Give vch_code or vch_no")
        sql = "SELECT VchCode, VchType, [Date] FROM Tran1 WHERE VchNo = ?"
        params: list[Any] = [vch_no.strip()]
        if vch_type is not None:
            sql += " AND VchType = ?"
            params.append(int(vch_type))
        hits = db.query(sql, params)
        if not hits:
            raise ReportError(f"No voucher numbered '{vch_no}'")
        if len(hits) > 1:
            return {"ambiguous": True, "message": "Several vouchers share this number; pass vch_code.",
                    "matches": [{"vch_code": h["vchcode"], "vch_type": vt(db, h["vchtype"]), "date": h["date"]}
                                for h in hits[:50]]}
        vch_code = int(hits[0]["vchcode"])
    head = db.query("SELECT * FROM Tran1 WHERE VchCode = ?", (int(vch_code),))
    if not head:
        raise ReportError(f"No voucher with VchCode {vch_code}")
    h = head[0]
    legs = db.query("SELECT * FROM Tran2 WHERE VchCode = ? ORDER BY RecType, SrNo", (int(vch_code),))
    item_types = set(item_rectypes(db)[0])
    ledger_lines, item_lines, sundries, other = [], [], [], []
    for leg in legs:
        name = master_name(masters, leg.get("mastercode1"))
        rt = int(leg.get("rectype") or 0)
        v1 = float(leg.get("value1") or 0)
        if rt == RT_LEDGER:
            line: dict[str, Any] = {"account": name, "debit": r2(-v1) if v1 < 0 else 0.0,
                                    "credit": r2(v1) if v1 > 0 else 0.0}
            if leg.get("shortnar"):
                line["narration"] = leg["shortnar"]
            ledger_lines.append(line)
        elif rt in item_types:
            qty = abs(v1)
            value = abs(float(leg.get("value3") or 0))
            line = {"item": name, "qty": r2(qty), "value_excl_tax": r2(value),
                    "rate_excl_tax": r2(value / qty) if qty else None}
            for src, key in (("d4", "list_price"), ("d9", "discount_pct"), ("d2", "net_rate_incl_tax")):
                if leg.get(src):
                    line[key] = r2(leg[src])
            loc = leg.get("mastercode2")
            if loc and int(loc) in masters and masters[int(loc)].type == MT_MATERIAL_CENTRE:
                line["material_centre"] = masters[int(loc)].name
            item_lines.append(line)
        elif rt == RT_BILL_SUNDRY:
            sundries.append({"bill_sundry": name, "amount": r2(v1)})
        else:
            other.append({"rectype": rt, "master": name, "value1": r2(v1)})
    out: dict[str, Any] = {
        "vch_code": int(vch_code), "vch_type": vt(db, h.get("vchtype")), "vch_type_code": h.get("vchtype"),
        "vch_no": h.get("vchno"), "date": h.get("date"),
        "party": master_name(masters, h.get("mastercode1")),
        "total_incl_tax": r2(abs(float(h.get("vchamtbasecur") or 0))),
        "goods_value_excl_tax": r2(abs(float(h.get("vchsalepurcamt") or 0))),
        "cancelled": bool(h.get("cancelled") or h.get("vchcancelled")),
    }
    header_refs: tuple[tuple[str, str, tuple[int, ...] | None], ...] = (
        ("vchseriescode", "series", None),
        ("cm1", "sale_purchase_type", (MT_SALE_TYPE, MT_PURCHASE_TYPE)),
        ("mastercode2", "material_centre", (MT_MATERIAL_CENTRE,)),
    )
    for col, key, types in header_refs:
        code = h.get(col)
        if code and int(code) in masters and (types is None or masters[int(code)].type in types):
            out[key] = masters[int(code)].name
    for key in ("createdby", "creationtime", "modifiedby", "modificationtime"):
        if h.get(key):
            out[key] = h[key]
    narr = _narrations(db, [int(vch_code)]).get(int(vch_code))
    if narr:
        out["narration"] = narr
    out["ledger_entries"] = ledger_lines
    out["item_entries"] = item_lines
    if sundries:
        out["bill_sundries"] = sundries
    if db.has_table("Tran3"):
        refs = db.query("SELECT * FROM Tran3 WHERE VchCode = ?", (int(vch_code),))
        orders = [r for r in refs if int(r.get("rectype") or 0) == RT3_ORDER_LINE]
        if orders:
            out["order_lines"] = [{"item": master_name(masters, r.get("mastercode1")),
                                   "qty": r2(abs(float(r.get("value1") or 0))), "rate": r2(r.get("value3")),
                                   "amount": r2(abs(float(r.get("newrefamount") or 0)))} for r in orders]
        bills = [r for r in refs if int(r.get("rectype") or 0) not in (RT3_ORDER_LINE, RT3_BATCH)]
        if bills:
            out["bill_references"] = [{
                "method": "new bill" if int(r.get("method") or 0) == METHOD_NEW_REF else "against bill",
                "bill_no": r.get("no"), "party": master_name(masters, r.get("mastercode1")),
                "due_date": r.get("duedate"), "amount": r2(r.get("value1"))} for r in bills]
    out.update(_attachments(db, int(vch_code)))
    if other:
        out["other_lines"] = other
    if ledger_lines:
        diff = sum(x["debit"] for x in ledger_lines) - sum(x["credit"] for x in ledger_lines)
        out["balanced"] = abs(diff) < 0.01
    if include_raw:
        out["raw_header"] = decode_row(masters, h)
    return out


_ATTACHMENTS = (
    ("other_info", "VchOtherInfo"), ("billing_details", "BillingDet"), ("item_descriptions", "ItemDesc"),
    ("gst_lines", "VchGSTSumItemWise"), ("tds", "TDS"), ("serial_numbers", "ItemSerialNo"),
    ("batches", "ItemParamDet"), ("pos_payment", "POSDet"), ("packing", "PackingDetails"),
)


def _attachments(db: BusyDB, vch_code: int) -> dict[str, Any]:
    """Every per-voucher detail table this BUSY version has, decoded and compacted."""
    masters = db.masters()
    out: dict[str, Any] = {}
    for key, table in _ATTACHMENTS:
        if not db.has_table(table) or not db.has_column(table, "VchCode") or is_table_blocked(db, table):
            continue
        real = db.resolve_table(table)
        rows = db.query(f"SELECT {db.top(200)}* FROM [{real}] WHERE VchCode = ?{db.limit(200)}", (vch_code,))
        decoded = [d for d in (decode_row(masters, {k: v for k, v in r.items() if k != "vchcode"}) for r in rows) if d]
        if decoded:
            out[key] = decoded[0] if key in ("other_info", "billing_details", "pos_payment") and len(decoded) == 1 \
                else decoded
    return out
