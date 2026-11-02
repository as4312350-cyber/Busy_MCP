"""Account reports: ledger details/balance/statement, trial balance, group
summary, profit & loss, balance sheet."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..db import BusyDB, Master
from ..schema import MT_GROUP, MT_LEDGER, RT_LEDGER
from ._common import (
    EPS,
    ancestors,
    balance_at_start_of,
    balances,
    chunks,
    drcr,
    find_master,
    group_path,
    int_list,
    ledger_movements,
    master_name,
    next_day,
    opening_balances,
    paginate,
    parent_name,
    period,
    pl_ledger_codes,
    r2,
    vt,
)

_FIELDS = ("opening", "debit", "credit", "closing")


def ledger_details(db: BusyDB, ledger: str) -> dict[str, Any]:
    masters = db.masters()
    m = find_master(db, ledger, (MT_LEDGER,))
    out: dict[str, Any] = {
        "code": m.code, "name": m.name, "alias": m.alias, "print_name": m.print_name,
        "group": parent_name(masters, m), "group_path": group_path(masters, m),
        "fy_opening_balance": drcr(-opening_balances(db).get(m.code, {}).get("d1", 0.0)),
    }
    if db.has_table("MasterAddressInfo"):
        addr = db.query("SELECT * FROM MasterAddressInfo WHERE MasterCode = ?", (m.code,))
        if addr:
            out["address_info"] = {k: v for k, v in addr[0].items()
                                   if v not in (None, "", 0, 0.0, False) and k != "mastercode"}
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


def _contra_accounts(db: BusyDB, vch_codes: list[int], self_code: int,
                     masters: dict[int, Master]) -> dict[int, str]:
    legs: dict[int, list[tuple[float, str]]] = defaultdict(list)
    for part in chunks(sorted(set(vch_codes))):
        rows = db.query(
            f"SELECT VchCode, MasterCode1, Value1 FROM Tran2 WHERE RecType = {RT_LEDGER} "
            f"AND VchCode IN ({int_list(part)})"
        )
        for r in rows:
            mc = int(r["mastercode1"] or 0)
            if mc != self_code:
                legs[int(r["vchcode"])].append((abs(float(r["value1"] or 0)), master_name(masters, mc) or "?"))
    result = {}
    for vc, items in legs.items():
        items.sort(reverse=True)
        names: list[str] = []
        for _, n in items:
            if n not in names:
                names.append(n)
        result[vc] = names[0] + (f" (+{len(names) - 1} more)" if len(names) > 1 else "")
    return result


def ledger_statement(db: BusyDB, ledger: str, from_date: str | None, to_date: str | None,
                     offset: int = 0, limit: int = 200) -> dict[str, Any]:
    masters = db.masters()
    m = find_master(db, ledger, (MT_LEDGER,))
    f, t = period(db, from_date, to_date)
    opening = balance_at_start_of(db, m.code, f)
    nar = ", t2.ShortNar" if db.has_column("Tran2", "ShortNar") else ""
    rows = db.query(
        f"SELECT t1.VchCode, t1.VchType, t1.VchNo, t1.[Date], t2.Value1{nar} "
        "FROM Tran2 t2 INNER JOIN Tran1 t1 ON t1.VchCode = t2.VchCode "
        "WHERE t2.RecType = ? AND t2.MasterCode1 = ? AND t1.[Date] >= ? AND t1.[Date] < ?"
        + db.active_voucher_filter("t1") + " ORDER BY t1.[Date], t1.VchCode",
        (RT_LEDGER, m.code, db.dparam(f), db.dparam(next_day(t))),
    )
    running, tdr, tcr = opening, 0.0, 0.0
    entries: list[dict[str, Any]] = []
    for r in rows:
        v = float(r["value1"] or 0)
        dr, cr = (-v, 0.0) if v < 0 else (0.0, v)
        tdr, tcr = tdr + dr, tcr + cr
        running += dr - cr
        e = {"date": r["date"], "vch_type": vt(db, r["vchtype"]), "vch_no": r["vchno"],
             "vch_code": r["vchcode"], "debit": r2(dr), "credit": r2(cr), "balance": drcr(running)}
        if r.get("shortnar"):
            e["narration"] = r["shortnar"]
        entries.append(e)
    page = paginate(entries, offset, limit)
    contra = _contra_accounts(db, [e["vch_code"] for e in page["items"]], m.code, masters)
    for e in page["items"]:
        e["particulars"] = contra.get(e["vch_code"], "")
    return {
        "ledger": m.name, "group": parent_name(masters, m), "from": f.isoformat(), "to": t.isoformat(),
        "opening_balance": drcr(opening), "total_debit": r2(tdr), "total_credit": r2(tcr),
        "closing_balance": drcr(running), **{k: v for k, v in page.items() if k != "items"},
        "entries": page["items"],
    }


def trial_balance(db: BusyDB, from_date: str | None, to_date: str | None, group: str | None = None,
                  include_zero: bool = False, ledger_level: bool = True,
                  offset: int = 0, limit: int = 500) -> dict[str, Any]:
    masters = db.masters()
    f, t = period(db, from_date, to_date)
    bal = balances(db, f, t)
    scope = find_master(db, group, (MT_GROUP,)).code if group else None
    rows: list[dict[str, Any]] = []
    totals: dict[str, float] = defaultdict(float)
    by_top: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    cl_dr = cl_cr = 0.0
    for code, b in bal.items():
        anc = ancestors(masters, masters[code])
        if scope is not None and all(p.code != scope for p in anc):
            continue
        if not include_zero and all(abs(b[k]) < EPS for k in _FIELDS):
            continue
        top = anc[-1].name if anc else "(no group)"
        for k in _FIELDS:
            totals[k] += b[k]
            by_top[top][k] += b[k]
        cl_dr += max(b["closing"], 0)
        cl_cr += max(-b["closing"], 0)
        rows.append({
            "ledger": masters[code].name, "group": anc[0].name if anc else None, "primary_group": top,
            "opening": drcr(b["opening"]), "debit": r2(b["debit"]), "credit": r2(b["credit"]),
            "closing": drcr(b["closing"]),
        })
    rows.sort(key=lambda x: (x["primary_group"], x["group"] or "", x["ledger"]))
    out: dict[str, Any] = {
        "from": f.isoformat(), "to": t.isoformat(), "group_filter": group,
        "by_primary_group": [
            {"primary_group": k, "opening": drcr(v["opening"]), "debit": r2(v["debit"]),
             "credit": r2(v["credit"]), "closing": drcr(v["closing"])}
            for k, v in sorted(by_top.items())
        ],
        "totals": {"debit": r2(totals["debit"]), "credit": r2(totals["credit"]),
                   "closing_dr": r2(cl_dr), "closing_cr": r2(cl_cr),
                   "net_difference": drcr(totals["closing"])},
    }
    if group is None and abs(totals["closing"]) >= EPS:
        out["warning"] = ("Trial balance does not tally. Usual causes: opening balances were not carried "
                          "forward into this FY database, or an opening-stock difference.")
    if ledger_level:
        page = paginate(rows, offset, limit)
        out.update({k: v for k, v in page.items() if k != "items"})
        out["ledgers"] = page["items"]
    return out


def group_summary(db: BusyDB, group: str | None, from_date: str | None, to_date: str | None) -> dict[str, Any]:
    masters = db.masters()
    f, t = period(db, from_date, to_date)
    bal = balances(db, f, t)
    root = find_master(db, group, (MT_GROUP,)).code if group else 0
    lines: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for code, b in bal.items():
        chain = [masters[code], *ancestors(masters, masters[code])]
        child = next((n for n in chain if (n.parent if n.parent in masters else 0) == root), None)
        if child is None:
            continue
        for k in _FIELDS:
            lines[child.code][k] += b[k]
    out: list[dict[str, Any]] = []
    for code, v in lines.items():
        if all(abs(v[k]) < EPS for k in _FIELDS):
            continue
        m = masters[code]
        out.append({"name": m.name, "kind": "group" if m.type == MT_GROUP else "ledger",
                    "opening": drcr(v["opening"]), "debit": r2(v["debit"]), "credit": r2(v["credit"]),
                    "closing": drcr(v["closing"]), "closing_signed": r2(v["closing"])})
    out.sort(key=lambda x: -abs(x["closing_signed"]))
    return {"group": masters[root].name if root else "(all primary groups)", "from": f.isoformat(),
            "to": t.isoformat(), "lines": out,
            "total_closing": drcr(sum(x["closing_signed"] for x in out))}


def profit_and_loss(db: BusyDB, from_date: str | None, to_date: str | None, top_ledgers: int = 15) -> dict[str, Any]:
    masters = db.masters()
    f, t = period(db, from_date, to_date)
    pl_codes, method = pl_ledger_codes(db)
    mv = ledger_movements(db, f, t)
    roots = {g.lower() for g in db.settings.pl_groups}
    heads: dict[str, dict[str, Any]] = {}
    for code in pl_codes:
        dr, cr = mv.get(code, (0.0, 0.0))
        net = dr - cr
        if abs(net) < EPS:
            continue
        m = masters[code]
        chain = ancestors(masters, m)
        head = chain[-1].name if chain else m.name
        for i, p in enumerate(chain):
            if p.name.lower() in roots:
                head = chain[i - 1].name if i > 0 else m.name
                break
        h = heads.setdefault(head, {"net": 0.0, "ledgers": []})
        h["net"] += net
        h["ledgers"].append({"ledger": m.name, "amount": drcr(net), "signed": r2(net)})
    income: list[dict[str, Any]] = []
    expense: list[dict[str, Any]] = []
    for name, h in heads.items():
        h["ledgers"].sort(key=lambda x: -abs(x["signed"]))
        entry = {"head": name, "amount": r2(abs(h["net"])), "ledger_count": len(h["ledgers"]),
                 "top_ledgers": [{"ledger": x["ledger"], "amount": x["amount"]} for x in h["ledgers"][:top_ledgers]]}
        (expense if h["net"] > 0 else income).append(entry)
    ti = sum(x["amount"] for x in income)
    te = sum(x["amount"] for x in expense)
    return {
        "from": f.isoformat(), "to": t.isoformat(), "classification": method,
        "income": sorted(income, key=lambda x: -x["amount"]),
        "expenses": sorted(expense, key=lambda x: -x["amount"]),
        "total_income": r2(ti), "total_expenses": r2(te),
        "net_profit_before_stock_adjustment": r2(ti - te), "result": "Profit" if ti >= te else "Loss",
        "caveat": ("Ledger-based. Opening/closing stock valuation (added by BUSY's Trading account) is NOT "
                   "included: profit += closing stock - opening stock. Use stock-summary for quantities."),
    }


def balance_sheet(db: BusyDB, as_of: str | None) -> dict[str, Any]:
    masters = db.masters()
    f, t = period(db, None, as_of)
    bal = balances(db, f, t)
    pl_codes, method = pl_ledger_codes(db)
    groups: dict[str, float] = defaultdict(float)
    pl_net = 0.0
    for code, b in bal.items():
        if code in pl_codes:
            pl_net += b["closing"]
            continue
        anc = ancestors(masters, masters[code])
        groups[anc[-1].name if anc else "(no group)"] += b["closing"]
    assets: list[dict[str, Any]] = [{"group": g, "amount": r2(v)} for g, v in groups.items() if v > EPS]
    liabilities: list[dict[str, Any]] = [{"group": g, "amount": r2(-v)} for g, v in groups.items() if v < -EPS]
    profit = -pl_net
    label = "Profit & Loss A/c (current period, before stock adjustment)"
    (liabilities if profit >= 0 else assets).append({"group": label, "amount": r2(abs(profit))})
    ta, tl = sum(x["amount"] for x in assets), sum(x["amount"] for x in liabilities)
    return {
        "as_of": t.isoformat(), "pl_classification": method,
        "liabilities": sorted(liabilities, key=lambda x: -x["amount"]),
        "assets": sorted(assets, key=lambda x: -x["amount"]),
        "total_liabilities": r2(tl), "total_assets": r2(ta), "difference": r2(ta - tl),
        "caveat": ("Ledger-based. Closing stock is not included unless posted to a ledger; a difference "
                   "usually equals closing-stock value or un-carried-forward opening balances."),
    }
