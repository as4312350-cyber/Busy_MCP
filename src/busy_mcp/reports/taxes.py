"""GST and TDS summaries."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from ..db import BusyDB
from ..schema import (
    MT_LEDGER,
    MT_PURCHASE_TYPE,
    MT_SALE_TYPE,
    VT_PURCHASE,
    VT_PURCHASE_RETURN,
    VT_SALES,
    VT_SALES_RETURN,
)
from ._common import (
    EPS,
    codes_under_groups,
    find_master,
    int_list,
    ledger_index,
    master_name,
    next_day,
    paginate,
    period,
    r2,
    vt,
)

_TAX_HEAD = re.compile(r"\b(cgst|sgst|utgst|igst|gst|cess)\b", re.IGNORECASE)


def _tax_head(name: str) -> str | None:
    found = {m.group(1).upper() for m in _TAX_HEAD.finditer(name)}
    for head in ("CGST", "SGST", "UTGST", "IGST", "CESS"):
        if head in found:
            return head
    return "GST" if "GST" in found else None


def gst_summary(db: BusyDB, from_date: str | None, to_date: str | None, monthly: bool = False) -> dict[str, Any]:
    """GST liability from tax ledgers, plus taxable turnover by Sale/Purchase Type."""
    masters = db.masters()
    f, t = period(db, from_date, to_date)
    idx = ledger_index(db)
    ledgers = []
    heads: dict[str, dict[str, float]] = defaultdict(lambda: {"output": 0.0, "input": 0.0})
    candidates = codes_under_groups(db, db.settings.tax_groups)
    if candidates:
        source = f"accounts under {db.settings.tax_groups}"
    else:  # no tax group: fall back to names, but never revenue (P&L) accounts such as 'Sales GST 18%'
        revenue = codes_under_groups(db, db.settings.pl_groups)
        candidates = {c for c, m in masters.items() if m.type == MT_LEDGER and c not in revenue}
        source = "accounts named like tax ledgers outside the P&L groups (no tax group found)"
    for code in sorted(candidates):
        m = masters[code]
        head = _tax_head(m.name)
        if not head:
            continue
        dr, cr = idx.range(code, f, t)
        if abs(dr) < EPS and abs(cr) < EPS:
            continue
        low = m.name.lower()
        side = "input" if ("input" in low or "itc" in low) else "output" if "output" in low else (
            "output" if cr >= dr else "input")
        net = (cr - dr) if side == "output" else (dr - cr)
        heads[head][side] += net
        entry: dict[str, Any] = {"ledger": m.name, "head": head, "side": side, "debit": r2(dr), "credit": r2(cr),
                                 "net": r2(net)}
        if monthly:
            entry["monthly_net"] = {k: r2((v[1] - v[0]) if side == "output" else (v[0] - v[1]))
                                    for k, v in idx.monthly([code], f, t).items()}
        ledgers.append(entry)
    out_tax = sum(h["output"] for h in heads.values())
    in_tax = sum(h["input"] for h in heads.values())
    result: dict[str, Any] = {
        "from": f.isoformat(), "to": t.isoformat(),
        "tax_by_head": {k: {"output": r2(v["output"]), "input": r2(v["input"]),
                            "net_payable": r2(v["output"] - v["input"])} for k, v in sorted(heads.items())},
        "total_output_tax": r2(out_tax), "total_input_tax": r2(in_tax), "net_gst_payable": r2(out_tax - in_tax),
        "tax_ledgers": sorted(ledgers, key=lambda x: (x["head"], x["ledger"])),
    }
    result["turnover_by_tax_type"] = _turnover_by_type(db, f, t)
    result["method"] = (f"Tax ledgers: {source}, headed by name (CGST/SGST/UTGST/IGST/Cess); 'input'/'output' in the "
                        "name, else balance side, decides the direction. Turnover grouped by the voucher's "
                        "Sale/Purchase Type (Tran1.CM1).")
    result["caveat"] = ("Ledger-based summary for review, not a return. Reversals, RCM and ITC eligibility follow "
                        "your BUSY postings; file returns from BUSY's own GST reports.")
    return result


def _turnover_by_type(db: BusyDB, f: Any, t: Any) -> dict[str, list[dict[str, Any]]]:
    if not db.has_column("Tran1", "CM1"):
        return {}
    masters = db.masters()
    types = int_list([VT_SALES, VT_SALES_RETURN, VT_PURCHASE, VT_PURCHASE_RETURN])
    rows = db.query(
        "SELECT t1.VchType AS vt, t1.CM1 AS st, COUNT(*) AS n, SUM(t1.VchSalePurcAmt) AS taxable, "
        f"SUM(t1.VchAmtBaseCur) AS total FROM Tran1 t1 WHERE t1.VchType IN ({types}) "
        "AND t1.[Date] >= ? AND t1.[Date] < ?" + db.active_voucher_filter("t1") + " GROUP BY t1.VchType, t1.CM1",
        (db.dparam(f), db.dparam(next_day(t))),
    )
    out: dict[str, dict[str, dict[str, float]]] = {"sales": defaultdict(lambda: defaultdict(float)),
                                                   "purchases": defaultdict(lambda: defaultdict(float))}
    for r in rows:
        code = int(r["vt"])
        side = "sales" if code in (VT_SALES, VT_SALES_RETURN) else "purchases"
        sign = -1 if code in (VT_SALES_RETURN, VT_PURCHASE_RETURN) else 1
        st = int(r.get("st") or 0)
        label = master_name(masters, st) if st in masters and masters[st].type in (MT_SALE_TYPE, MT_PURCHASE_TYPE) \
            else "(type not set)"
        bucket = out[side][label or "(type not set)"]
        bucket["vouchers"] += int(r["n"] or 0)
        bucket["taxable_value"] += sign * abs(float(r["taxable"] or 0))
        bucket["invoice_value"] += sign * abs(float(r["total"] or 0))
    return {side: [{"tax_type": k, "vouchers": int(v["vouchers"]), "taxable_value": r2(v["taxable_value"]),
                    "invoice_value": r2(v["invoice_value"])} for k, v in sorted(groups.items())]
            for side, groups in out.items() if groups}


def tds_register(db: BusyDB, from_date: str | None, to_date: str | None, party: str | None = None,
                 offset: int = 0, limit: int = 200) -> dict[str, Any]:
    """TDS/TCS deductions from BUSY's TDS table."""
    if not db.has_table("TDS"):
        return {"total": 0, "entries": [], "note": "No TDS table: TDS/TCS is not enabled in this company."}
    masters = db.masters()
    f, t = period(db, from_date, to_date)
    wanted = ["VchCode", "VchType", "MasterCode1", "MasterCode2", "EntryDate", "DateOfCredit", "VchAmt", "TDSRate",
              "TDSAmt", "SurchargeAmt", "CessAmt", "SHECessAmt", "ChallanNo", "ChequeNo", "ChequeDate", "Quarter",
              "Method", "RefNo"]
    cols = db.existing_columns("TDS", wanted)
    sql, params = f"SELECT {', '.join(cols)} FROM TDS WHERE 1 = 1", []
    date_col = next((c for c in ("EntryDate", "DateOfCredit") if c in cols), None)
    if date_col:
        sql += f" AND {date_col} >= ? AND {date_col} < ?"
        params += [db.dparam(f), db.dparam(next_day(t))]
    if party:
        sql += " AND MasterCode1 = ?"
        params.append(find_master(db, party, (MT_LEDGER,)).code)
    rows = db.query(sql, params)
    entries = []
    by_section: dict[str, dict[str, float]] = defaultdict(lambda: {"amount": 0.0, "tds": 0.0, "count": 0})
    for r in rows:
        e: dict[str, Any] = {"party": master_name(masters, r.get("mastercode1")),
             "tds_account": master_name(masters, r.get("mastercode2")),
             "date": r.get("entrydate") or r.get("dateofcredit"), "voucher_type": vt(db, r.get("vchtype")),
             "amount": r2(r.get("vchamt")), "rate": r2(r.get("tdsrate")), "tds": r2(r.get("tdsamt")),
             "surcharge_cess": r2(float(r.get("surchargeamt") or 0) + float(r.get("cessamt") or 0)
                                  + float(r.get("shecessamt") or 0)),
             "challan_no": r.get("challanno") or None, "quarter": r.get("quarter") or None}
        key = e["tds_account"] or "?"
        by_section[key]["amount"] += e["amount"]
        by_section[key]["tds"] += e["tds"]
        by_section[key]["count"] += 1
        entries.append(e)
    entries.sort(key=lambda x: str(x["date"] or ""))
    page = paginate(entries, offset, limit)
    return {"from": f.isoformat(), "to": t.isoformat(), "total_tds": r2(sum(e["tds"] for e in entries)),
            "by_tds_account": {k: {"amount": r2(v["amount"]), "tds": r2(v["tds"]), "entries": int(v["count"])}
                               for k, v in sorted(by_section.items())},
            "pending_challan": sum(1 for e in entries if not e["challan_no"]),
            **{k: v for k, v in page.items() if k != "items"}, "entries": page["items"]}
