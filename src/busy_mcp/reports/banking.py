"""Banking: bank reconciliation (cleared / uncleared entries) and post-dated cheques."""

from __future__ import annotations

import datetime as _dt
from typing import Any

from ..db import BusyDB
from ..errors import ReportError
from ..schema import MT_LEDGER, RT_LEDGER, VT_PAYMENT, VT_RECEIPT
from ._common import (
    balance_at_start_of,
    chunks,
    codes_under_groups,
    drcr,
    find_master,
    int_list,
    master_name,
    next_day,
    paginate,
    period,
    r2,
    select_list,
    vt,
)
from .bills import _valid_date


def bank_reconciliation(db: BusyDB, bank: str, as_of: str | None = None, offset: int = 0,
                        limit: int = 200) -> dict[str, Any]:
    """Book balance vs bank balance: entries not cleared (no clearing date, or
    cleared after `as_of`) explain the difference."""
    if not db.has_column("Tran2", "ClrDate"):
        raise ReportError("This BUSY version stores no bank clearing dates (Tran2.ClrDate).")
    m = find_master(db, bank, (MT_LEDGER,))
    _, t = period(db, None, as_of)
    book = balance_at_start_of(db, m.code, next_day(t))
    rows = db.query(
        "SELECT t1.VchCode, t1.VchType, t1.VchNo, t1.[Date], t2.Value1, t2.ClrDate "
        "FROM Tran2 t2 INNER JOIN Tran1 t1 ON t1.VchCode = t2.VchCode "
        "WHERE t2.RecType = ? AND t2.MasterCode1 = ? AND t1.[Date] < ?" + db.active_voucher_filter("t1")
        + " ORDER BY t1.[Date], t1.VchCode",
        (RT_LEDGER, m.code, db.dparam(next_day(t))),
    )
    pending = []
    for r in rows:
        clr = _valid_date(r.get("clrdate"))
        if clr is not None and clr <= t:
            continue
        v = float(r["value1"] or 0)
        pending.append({"date": r["date"], "vch_type": vt(db, r["vchtype"]), "vch_no": r["vchno"],
                        "vch_code": r["vchcode"], "deposit": r2(-v) if v < 0 else 0.0,
                        "withdrawal": r2(v) if v > 0 else 0.0,
                        "cleared_on": clr.isoformat() if clr else None})
    if db.has_table("BRSOpBal") and {"MasterCode1", "OpAmt", "ClrDate"} <= set(
            db.existing_columns("BRSOpBal", ["MasterCode1", "OpAmt", "ClrDate"])):
        cols = db.existing_columns("BRSOpBal", ["EntryDate", "VchNo", "OpAmt", "ClrDate", "ShortNar"])
        for r in db.query(f"SELECT {select_list(cols)} FROM BRSOpBal WHERE MasterCode1 = ?", (m.code,)):
            clr = _valid_date(r.get("clrdate"))
            if clr is not None and clr <= t:
                continue
            v = float(r.get("opamt") or 0)
            pending.append({"date": r.get("entrydate"), "vch_type": "Opening (uncleared)", "vch_no": r.get("vchno"),
                            "deposit": r2(-v) if v < 0 else 0.0, "withdrawal": r2(v) if v > 0 else 0.0,
                            "cleared_on": clr.isoformat() if clr else None, "narration": r.get("shortnar")})
    contra = _counterparties(db, [p["vch_code"] for p in pending if p.get("vch_code")], m.code)
    for p in pending:
        if p.get("vch_code") in contra:
            p["party"] = contra[p["vch_code"]]
    dep = sum(p["deposit"] for p in pending)
    wdr = sum(p["withdrawal"] for p in pending)
    page = paginate(pending, offset, limit)
    return {
        "bank": m.name, "as_of": t.isoformat(), "balance_as_per_books": drcr(book),
        "deposits_not_yet_credited_by_bank": r2(dep), "cheques_issued_not_yet_presented": r2(wdr),
        "balance_as_per_bank": drcr(book - dep + wdr),
        **{k: v for k, v in page.items() if k != "items"}, "uncleared_entries": page["items"],
        "method": "Tran2.ClrDate (bank clearing date set in BUSY's reconciliation screen) plus BRSOpBal openings.",
        "confidence": "likely (column semantics from a production reconciliation tool)",
    }


def _counterparties(db: BusyDB, vch_codes: list[int], self_code: int) -> dict[int, str]:
    masters = db.masters()
    out: dict[int, str] = {}
    for part in chunks(sorted(set(vch_codes))):
        rows = db.query(f"SELECT VchCode, MasterCode1 FROM Tran2 WHERE RecType = {RT_LEDGER} "
                        f"AND VchCode IN ({int_list(part)}) AND MasterCode1 <> ?", (self_code,))
        for r in rows:
            out.setdefault(int(r["vchcode"]), master_name(masters, r["mastercode1"]) or "?")
    return out


def post_dated_cheques(db: BusyDB, as_of: str | None = None, offset: int = 0, limit: int = 200) -> dict[str, Any]:
    """Receipts/payments whose effective date (Tran1.StockUpdationDate) is after `as_of`."""
    if not db.has_column("Tran1", "StockUpdationDate"):
        raise ReportError("This BUSY version has no effective-date column for post-dated cheques.")
    masters = db.masters()
    fy_end = period(db, None, None)[1]
    t = period(db, None, as_of)[1] if as_of else min(_dt.date.today(), fy_end)
    rows = db.query(
        "SELECT t1.VchCode, t1.VchType, t1.VchNo, t1.[Date], t1.StockUpdationDate AS eff, t1.MasterCode1, "
        f"t1.VchAmtBaseCur FROM Tran1 t1 WHERE t1.VchType IN ({VT_RECEIPT}, {VT_PAYMENT}) "
        "AND t1.StockUpdationDate > ?" + db.active_voucher_filter("t1") + " ORDER BY t1.StockUpdationDate",
        (db.dparam(next_day(t)),),
    )
    out = [{"vch_type": vt(db, r["vchtype"]), "vch_no": r["vchno"], "entered_on": r["date"],
            "cheque_date": r["eff"], "party": master_name(masters, r.get("mastercode1")),
            "amount": r2(abs(float(r.get("vchamtbasecur") or 0)))} for r in rows]
    receipts = sum(x["amount"] for x in out if x["vch_type"] == "Receipt")
    page = paginate(out, offset, limit)
    return {"as_of": t.isoformat(), "pdc_receipts_total": r2(receipts),
            "pdc_payments_total": r2(sum(x["amount"] for x in out) - receipts),
            **{k: v for k, v in page.items() if k != "items"}, "cheques": page["items"],
            "confidence": "likely (single production source: StockUpdationDate = PDC effective date)"}


def cash_bank_codes(db: BusyDB) -> set[int]:
    return codes_under_groups(db, db.settings.cash_bank_groups)
