"""Fast analytics on top of the movement index: business dashboard, monthly
trends and cash & bank position. These answer the most common questions in a
single call without extra database round trips."""

from __future__ import annotations

import datetime as _dt
from collections import defaultdict
from typing import Any

from ..db import BusyDB
from ..errors import ReportError
from ..schema import MT_GROUP, MT_ITEM, MT_LEDGER, VT_PURCHASE, VT_PURCHASE_RETURN, VT_SALES, VT_SALES_RETURN
from ._common import (
    EPS,
    ancestors,
    balances,
    codes_under_groups,
    drcr,
    find_master,
    int_list,
    ledger_index,
    master_name,
    next_day,
    opening_balances,
    period,
    pl_ledger_codes,
    r2,
    vt,
)
from .inventory import item_index


def ledger_monthly(db: BusyDB, account_or_group: str, from_date: str | None = None,
                   to_date: str | None = None) -> dict[str, Any]:
    """Month-wise debit, credit and closing balance for an account or a whole group."""
    masters = db.masters()
    f, t = period(db, from_date, to_date)
    m = find_master(db, account_or_group, (MT_LEDGER, MT_GROUP))
    if m.type == MT_GROUP:
        codes = [c.code for c in masters.values()
                 if c.type == MT_LEDGER and any(p.code == m.code for p in ancestors(masters, c))]
        if not codes:
            raise ReportError(f"Group '{m.name}' has no accounts")
    else:
        codes = [m.code]
    idx = ledger_index(db)
    ob = opening_balances(db)
    opening = sum(-ob.get(c, {}).get("d1", 0.0) for c in codes)
    day_before = f - _dt.timedelta(days=1)
    opening += sum(dr - cr for dr, cr in (idx.range(c, None, day_before) for c in codes))
    running = opening
    months = []
    for month, (dr, cr) in idx.monthly(codes, f, t).items():
        running += dr - cr
        months.append({"month": month, "debit": r2(dr), "credit": r2(cr), "net": drcr(dr - cr),
                       "closing": drcr(running)})
    return {"name": m.name, "kind": "group" if m.type == MT_GROUP else "account", "accounts": len(codes),
            "from": f.isoformat(), "to": t.isoformat(), "opening": drcr(opening), "months": months,
            "closing": drcr(running)}


def cash_bank_summary(db: BusyDB, from_date: str | None = None, to_date: str | None = None) -> dict[str, Any]:
    masters = db.masters()
    f, t = period(db, from_date, to_date)
    codes = codes_under_groups(db, db.settings.cash_bank_groups)
    if not codes:
        raise ReportError(f"No accounts under {db.settings.cash_bank_groups}; set BUSY_CASH_BANK_GROUPS.")
    bal = balances(db, f, t)
    rows: list[dict[str, Any]] = []
    for c in sorted(codes, key=lambda x: masters[x].name.lower()):
        b = bal.get(c, {"opening": 0.0, "debit": 0.0, "credit": 0.0, "closing": 0.0})
        if all(abs(b[k]) < EPS for k in b):
            continue
        rows.append({"account": masters[c].name, "group": master_name(masters, masters[c].parent),
                     "opening": drcr(b["opening"]), "received": r2(b["debit"]), "paid": r2(b["credit"]),
                     "closing": drcr(b["closing"]), "closing_signed": r2(b["closing"])})
    flow = {k: {"in": r2(dr), "out": r2(cr), "net": r2(dr - cr)}
            for k, (dr, cr) in ledger_index(db).monthly(codes, f, t).items()}
    return {"from": f.isoformat(), "to": t.isoformat(), "accounts": rows,
            "total_closing": drcr(sum(r["closing_signed"] for r in rows)),
            "total_received": r2(sum(r["received"] for r in rows)), "total_paid": r2(sum(r["paid"] for r in rows)),
            "monthly_flow": flow,
            "note": "Transfers between your own cash/bank accounts (contra) appear on both sides."}


def dashboard(db: BusyDB, from_date: str | None = None, to_date: str | None = None) -> dict[str, Any]:
    """Key business figures for a period in one call."""
    masters = db.masters()
    f, t = period(db, from_date, to_date)
    types = int_list([VT_SALES, VT_SALES_RETURN, VT_PURCHASE, VT_PURCHASE_RETURN])
    trade = {int(r["vt"]): (float(r["total"] or 0), float(r["taxable"] or 0), int(r["n"] or 0)) for r in db.query(
        "SELECT t1.VchType AS vt, COUNT(*) AS n, SUM(ABS(t1.VchAmtBaseCur)) AS total, "
        f"SUM(ABS(t1.VchSalePurcAmt)) AS taxable FROM Tran1 t1 WHERE t1.VchType IN ({types}) "
        "AND t1.[Date] >= ? AND t1.[Date] < ?" + db.active_voucher_filter("t1") + " GROUP BY t1.VchType",
        (db.dparam(f), db.dparam(next_day(t))))}

    def net(main: int, ret: int, k: int) -> float:
        return trade.get(main, (0.0, 0.0, 0))[k] - trade.get(ret, (0.0, 0.0, 0))[k]

    bal = balances(db, f, t)
    pl_codes, _ = pl_ledger_codes(db)
    profit = -sum(bal[c]["debit"] - bal[c]["credit"] for c in pl_codes if c in bal)
    debtors = codes_under_groups(db, db.settings.debtor_groups)
    creditors = codes_under_groups(db, db.settings.creditor_groups)
    cash_bank = codes_under_groups(db, db.settings.cash_bank_groups)
    receivable = sum(max(bal.get(c, {}).get("closing", 0.0), 0) for c in debtors)
    payable = sum(max(-bal.get(c, {}).get("closing", 0.0), 0) for c in creditors)
    top_debtors = sorted(((masters[c].name, bal.get(c, {}).get("closing", 0.0)) for c in debtors),
                         key=lambda x: -x[1])[:5]
    items = item_index(db)
    ob_items = opening_balances(db, MT_ITEM)
    negative = 0
    for code, m in masters.items():
        if m.type == MT_ITEM:
            iq, oq, _, _ = items.range(code, None, t)
            if ob_items.get(code, {}).get("d1", 0.0) + iq - oq < -1e-9:
                negative += 1
    counts: dict[str, int] = defaultdict(int)
    for r in db.query("SELECT t1.VchType AS vt, COUNT(*) AS n FROM Tran1 t1 WHERE t1.[Date] >= ? AND t1.[Date] < ?"
                      + db.active_voucher_filter("t1") + " GROUP BY t1.VchType",
                      (db.dparam(f), db.dparam(next_day(t)))):
        counts[vt(db, r["vt"])] += int(r["n"] or 0)
    return {
        "from": f.isoformat(), "to": t.isoformat(),
        "sales": {"net_incl_tax": r2(net(VT_SALES, VT_SALES_RETURN, 0)),
                  "net_taxable": r2(net(VT_SALES, VT_SALES_RETURN, 1)), "invoices": trade.get(VT_SALES, (0, 0, 0))[2]},
        "purchases": {"net_incl_tax": r2(net(VT_PURCHASE, VT_PURCHASE_RETURN, 0)),
                      "net_taxable": r2(net(VT_PURCHASE, VT_PURCHASE_RETURN, 1)),
                      "bills": trade.get(VT_PURCHASE, (0, 0, 0))[2]},
        "net_profit_before_stock_adjustment": r2(profit),
        "receivables": r2(receivable), "payables": r2(payable),
        "cash_and_bank": drcr(sum(bal.get(c, {}).get("closing", 0.0) for c in cash_bank)),
        "top_debtors": [{"party": n, "balance": drcr(b)} for n, b in top_debtors if b > EPS],
        "items_with_negative_stock": negative,
        "vouchers_by_type": dict(sorted(counts.items(), key=lambda x: -x[1])),
        "caveat": "Profit is ledger-based (before stock adjustment). Use the detailed tools to drill down.",
    }
