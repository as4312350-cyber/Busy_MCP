"""Sales / purchase analysis."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..db import BusyDB
from ..errors import ReportError
from ..schema import RT_ITEM, VT_PURCHASE, VT_PURCHASE_RETURN, VT_SALES, VT_SALES_RETURN
from ._common import int_list, master_name, next_day, period, r2, vt


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
    rets = return_types if return_types is not None else (
        [VT_SALES_RETURN] if kind == "sales" else [VT_PURCHASE_RETURN])
    types_sql = int_list(sorted(set(main) | set(rets)))
    dates = (db.dparam(f), db.dparam(next_day(t)))
    flt = db.active_voucher_filter("t1")
    agg: dict[Any, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    if group_by == "item":
        rows = db.query(
            "SELECT t1.VchType AS vt, t2.MasterCode1 AS mc, SUM(ABS(t2.Value1)) AS q, SUM(ABS(t2.Value3)) AS v "
            "FROM Tran2 t2 INNER JOIN Tran1 t1 ON t1.VchCode = t2.VchCode "
            f"WHERE t2.RecType = {RT_ITEM} AND t1.VchType IN ({types_sql}) AND t1.[Date] >= ? AND t1.[Date] < ?"
            f"{flt} GROUP BY t1.VchType, t2.MasterCode1", dates)
        for r in rows:
            name = master_name(masters, r["mc"]) or "?"
            side = "gross" if int(r["vt"]) in main else "returns"
            agg[name][f"{side}_qty"] += float(r["q"] or 0)
            agg[name][f"{side}_value"] += float(r["v"] or 0)
        lines: list[dict[str, Any]] = [
            {"item": k, "qty": r2(v["gross_qty"]), "value_excl_tax": r2(v["gross_value"]),
             "returns_qty": r2(v["returns_qty"]), "returns_value": r2(v["returns_value"]),
             "net_qty": r2(v["gross_qty"] - v["returns_qty"]),
             "net_value_excl_tax": r2(v["gross_value"] - v["returns_value"])}
            for k, v in agg.items()]
        lines.sort(key=lambda x: -x["net_value_excl_tax"])
        total = {"net_value_excl_tax": r2(sum(x["net_value_excl_tax"] for x in lines))}
    else:
        rows = db.query(
            "SELECT t1.VchCode, t1.VchType, t1.VchNo, t1.[Date], t1.MasterCode1, t1.VchAmtBaseCur, "
            f"t1.VchSalePurcAmt FROM Tran1 t1 WHERE t1.VchType IN ({types_sql}) "
            f"AND t1.[Date] >= ? AND t1.[Date] < ?{flt}", dates)
        for r in rows:
            amt = abs(float(r["vchamtbasecur"] or 0))
            taxable = abs(float(r["vchsalepurcamt"] or 0))
            side = "gross" if int(r["vchtype"]) in main else "returns"
            if group_by == "party":
                key: Any = master_name(masters, r["mastercode1"]) or "?"
            elif group_by == "month":
                key = str(r["date"])[:7]
            else:
                key = (r["date"], r["vchno"], int(r["mastercode1"] or 0), int(r["vchtype"]))
            agg[key][side] += amt
            agg[key][f"{side}_taxable"] += taxable
            if side == "gross":
                agg[key]["count"] += 1
        lines = []
        for k, v in agg.items():
            if group_by == "voucher":
                d, no, mc, typ = k
                row: dict[str, Any] = {"date": d, "vch_no": no, "vch_type": vt(db, typ),
                                       "party": master_name(masters, mc)}
            else:
                row = {group_by: k}
            row.update({"invoices": int(v["count"]),
                        "gross_incl_tax": r2(v["gross"]), "returns_incl_tax": r2(v["returns"]),
                        "net_incl_tax": r2(v["gross"] - v["returns"]),
                        "net_taxable_value": r2(v["gross_taxable"] - v["returns_taxable"])})
            lines.append(row)
        if group_by == "month":
            lines.sort(key=lambda x: x["month"])
        else:
            lines.sort(key=lambda x: -x["net_incl_tax"])
        total = {"net_incl_tax": r2(sum(x["net_incl_tax"] for x in lines)),
                 "net_taxable_value": r2(sum(x["net_taxable_value"] for x in lines))}
    return {
        "kind": kind, "group_by": group_by, "from": f.isoformat(), "to": t.isoformat(),
        "voucher_types": [vt(db, x) for x in main], "return_types": [vt(db, x) for x in rets],
        "totals": total, "line_count": len(lines),
        "lines": lines if group_by == "month" else lines[:top],
    }
