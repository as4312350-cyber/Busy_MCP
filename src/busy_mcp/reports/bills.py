"""Bill-by-bill outstanding (with due dates) and pending sales/purchase orders,
both from BUSY's reference table Tran3."""

from __future__ import annotations

import datetime as _dt
from collections import defaultdict
from typing import Any

from ..db import BusyDB
from ..errors import ReportError
from ..schema import (
    METHOD_NEW_REF,
    MT_ITEM,
    MT_LEDGER,
    RT3_BATCH,
    RT3_ORDER_LINE,
    VT_PURCHASE_ORDER,
    VT_SALES_ORDER,
)
from ._common import (
    EPS,
    balances,
    chunks,
    codes_under_groups,
    find_master,
    int_list,
    master_name,
    paginate,
    period,
    r2,
    select_list,
    to_date,
)
from .receivables import BUCKETS, _bucket

_SENTINEL_YEAR = 1990  # BUSY stores "no date" as an ancient date in some versions


def cancelled_vouchers(db: BusyDB) -> set[int]:
    def load() -> set[int]:
        if db.settings.include_cancelled:
            return set()
        cols = db.existing_columns("Tran1", ["Cancelled", "VchCancelled"])
        if not cols:
            return set()
        cond = " OR ".join(f"{c} <> 0" for c in cols)
        return {int(r["vchcode"]) for r in db.query(f"SELECT VchCode FROM Tran1 WHERE {cond}")}

    return db.cached("cancelled_vouchers", load)


def _valid_date(value: Any) -> _dt.date | None:
    if not value:
        return None
    try:
        d = to_date(value)
    except ValueError:
        return None
    return d if d.year > _SENTINEL_YEAR else None


def _credit_days(db: BusyDB, codes: list[int]) -> dict[int, int]:
    if not db.has_column("Master1", "I2") or not codes:
        return {}
    out: dict[int, int] = {}
    for part in chunks(codes):
        for r in db.query(f"SELECT Code, I2 FROM Master1 WHERE Code IN ({int_list(part)})"):
            if r.get("i2"):
                out[int(r["code"])] = int(r["i2"])
    return out


def _tran3_rows(db: BusyDB, codes: list[int], wanted: list[str], extra_where: str) -> list[dict[str, Any]]:
    cols = db.existing_columns("Tran3", wanted)
    rows: list[dict[str, Any]] = []
    for part in chunks(codes):
        rows += db.query(f"SELECT {select_list(cols)} FROM Tran3 WHERE MasterCode1 IN ({int_list(part)}){extra_where}")
    dead = cancelled_vouchers(db)
    return [r for r in rows if int(r.get("vchcode") or 0) not in dead]


def _group(rows: list[dict[str, Any]], use_refcode: bool) -> dict[tuple[Any, ...], list[dict[str, Any]]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        party = int(r.get("mastercode1") or 0)
        if use_refcode and r.get("refcode"):
            key: tuple[Any, ...] = (party, "R", r["refcode"])
        else:
            key = (party, "N", str(r.get("no") or "").strip().upper())
        groups[key].append(r)
    return groups


def _orphans(groups: dict[tuple[Any, ...], list[dict[str, Any]]]) -> int:
    return sum(1 for g in groups.values() if not any(int(x.get("method") or 0) == METHOD_NEW_REF for x in g))


def bills_outstanding(db: BusyDB, nature: str = "receivable", party: str | None = None, as_of: str | None = None,
                      overdue_only: bool = False, offset: int = 0, limit: int = 100) -> dict[str, Any]:
    if nature not in ("receivable", "payable"):
        raise ReportError("nature must be 'receivable' or 'payable'")
    if not (db.has_table("Tran3") and {"Method", "MasterCode1", "Value1"} <= set(
            db.existing_columns("Tran3", ["Method", "MasterCode1", "Value1"]))):
        raise ReportError("This BUSY version has no bill-by-bill reference table (Tran3); use outstanding instead.")
    masters = db.masters()
    f, t = period(db, None, as_of)
    if party:
        codes = [find_master(db, party, (MT_LEDGER,)).code]
    else:
        groups_cfg = db.settings.debtor_groups if nature == "receivable" else db.settings.creditor_groups
        codes = sorted(codes_under_groups(db, groups_cfg))
        if not codes:
            raise ReportError(f"No accounts under {groups_cfg}.")
    extra = " AND Method BETWEEN 1 AND 4"
    if db.has_column("Tran3", "RecType"):
        extra += f" AND RecType NOT IN ({RT3_BATCH}, {RT3_ORDER_LINE})"
    rows = _tran3_rows(db, codes, ["RefCode", "RecType", "Method", "VchCode", "VchType", "MasterCode1", "No", "Date",
                                   "DueDate", "Value1"], extra)
    rows = [r for r in rows if not (_valid_date(r.get("date")) and to_date(r["date"]) > t)]
    if not rows:
        return {"nature": nature, "as_of": t.isoformat(), "total": 0, "parties": [],
                "note": "No bill-by-bill references found: bill-wise details are probably not maintained for these "
                        "accounts. Use the outstanding tool (FIFO aging) instead."}
    by_refcode = _group(rows, True) if db.has_column("Tran3", "RefCode") else None
    by_number = _group(rows, False)
    groups = by_refcode if by_refcode is not None and _orphans(by_refcode) <= _orphans(by_number) else by_number
    key_used = "RefCode" if groups is by_refcode else "bill number"
    sign = 1 if nature == "receivable" else -1
    credit = _credit_days(db, codes)
    bal = balances(db, f, t)
    per_party: dict[int, dict[str, Any]] = {}
    for (pcode, _, _), items in groups.items():
        pending = sign * -sum(float(x.get("value1") or 0) for x in items)
        new = [x for x in items if int(x.get("method") or 0) == METHOD_NEW_REF]
        head = new[0] if new else items[0]
        bill_date = _valid_date(head.get("date"))
        due = _valid_date(head.get("duedate"))
        if due is None and bill_date is not None:
            due = bill_date + _dt.timedelta(days=credit.get(pcode, 0))
        p = per_party.setdefault(pcode, {"bills": [], "unmatched": 0.0})
        if pending <= EPS:
            if pending < -EPS:
                p["unmatched"] += -pending
            continue
        overdue = (t - due).days if due else None
        if overdue_only and (overdue is None or overdue <= 0):
            continue
        p["bills"].append({
            "bill_no": str(head.get("no") or "").strip() or None, "date": bill_date.isoformat() if bill_date else None,
            "due_date": due.isoformat() if due else None,
            "bill_amount": r2(sign * -sum(float(x.get("value1") or 0) for x in new)) if new else None,
            "pending": r2(pending), "overdue_days": overdue if overdue and overdue > 0 else 0,
        })
    parties = []
    for pcode, p in per_party.items():
        if not p["bills"] and not party:
            continue
        p["bills"].sort(key=lambda b: (b["due_date"] or "", b["bill_no"] or ""))
        pending_total = sum(b["pending"] for b in p["bills"])
        ledger = sign * bal.get(pcode, {}).get("closing", 0.0)
        aging = dict.fromkeys(BUCKETS, 0.0)
        for b in p["bills"]:
            aging[_bucket(b["overdue_days"])] += b["pending"]
        parties.append({
            "party": master_name(masters, pcode), "pending_bills_total": r2(pending_total),
            "ledger_balance": r2(ledger), "on_account_or_unallocated": r2(ledger - pending_total),
            "overdue_total": r2(sum(b["pending"] for b in p["bills"] if b["overdue_days"] > 0)),
            "oldest_overdue_days": max((b["overdue_days"] for b in p["bills"]), default=0),
            "overdue_aging": {k: r2(v) for k, v in aging.items() if v > EPS},
            "credit_days": credit.get(pcode), "bill_count": len(p["bills"]), "bills": p["bills"][:50],
        })
    parties.sort(key=lambda x: -x["pending_bills_total"])
    page = paginate(parties, offset, limit)
    return {
        "nature": nature, "as_of": t.isoformat(),
        "total_pending": r2(sum(x["pending_bills_total"] for x in parties)),
        "total_overdue": r2(sum(x["overdue_total"] for x in parties)),
        **{k: v for k, v in page.items() if k != "items"}, "parties": page["items"],
        "method": (f"Tran3 bill references grouped by {key_used}; Method 1 = new bill, others = adjustments. "
                   "Due date from the bill, else bill date + party credit days (Master1.I2). "
                   "on_account_or_unallocated = ledger balance minus pending bills."),
        "confidence": "likely (production BUSY integration + schema dump); check one party against BUSY",
    }


def pending_orders(db: BusyDB, kind: str = "sales", party: str | None = None, item: str | None = None,
                   include_completed: bool = False, offset: int = 0, limit: int = 200) -> dict[str, Any]:
    if kind not in ("sales", "purchase"):
        raise ReportError("kind must be 'sales' or 'purchase'")
    if not db.has_table("Tran3"):
        raise ReportError("This BUSY version has no order reference table (Tran3).")
    masters = db.masters()
    order_type = VT_SALES_ORDER if kind == "sales" else VT_PURCHASE_ORDER
    cols = db.existing_columns("Tran3", ["RefCode", "Method", "VchCode", "VchType", "MasterCode1", "MasterCode2",
                                         "No", "Date", "DueDate", "Value1", "Value3", "ItemSrNo"])
    rows = db.query(f"SELECT {select_list(cols)} FROM Tran3 WHERE RecType = {RT3_ORDER_LINE}")
    dead = cancelled_vouchers(db)
    rows = [r for r in rows if int(r.get("vchcode") or 0) not in dead]
    party_code = find_master(db, party, (MT_LEDGER,)).code if party else None
    item_code = find_master(db, item, (MT_ITEM,)).code if item else None
    groups: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        key = ("R", r["refcode"]) if r.get("refcode") else (
            "N", str(r.get("no") or "").strip().upper(), r.get("mastercode1"), r.get("mastercode2"))
        groups[key].append(r)
    order_codes = sorted({int(r["vchcode"]) for r in rows if int(r.get("vchtype") or 0) == order_type})
    numbers: dict[int, Any] = {}
    for part in chunks(order_codes):
        for h in db.query(f"SELECT VchCode, VchNo FROM Tran1 WHERE VchCode IN ({int_list(part)})"):
            numbers[int(h["vchcode"])] = h["vchno"]
    lines = []
    for items in groups.values():
        orders = [x for x in items if int(x.get("vchtype") or 0) == order_type
                  and int(x.get("method") or METHOD_NEW_REF) == METHOD_NEW_REF]
        if not orders:
            continue
        head = orders[0]
        if party_code is not None and int(head.get("mastercode2") or 0) != party_code:
            continue
        if item_code is not None and int(head.get("mastercode1") or 0) != item_code:
            continue
        ordered = sum(float(x.get("value1") or 0) for x in orders)
        remaining = sum(float(x.get("value1") or 0) for x in items)
        pending = abs(remaining) if remaining * ordered > 0 else 0.0
        if pending < 1e-9 and not include_completed:
            continue
        rate = float(head.get("value3") or 0)
        lines.append({
            "order_no": numbers.get(int(head.get("vchcode") or 0)) or head.get("no"),
            "date": head.get("date"), "due_date": head.get("duedate"),
            "party": master_name(masters, head.get("mastercode2")),
            "item": master_name(masters, head.get("mastercode1")),
            "ordered_qty": r2(abs(ordered)), "executed_qty": r2(abs(ordered) - pending), "pending_qty": r2(pending),
            "rate": r2(rate), "pending_value": r2(pending * rate),
        })
    lines.sort(key=lambda x: (str(x["date"] or ""), str(x["order_no"] or "")))
    page = paginate(lines, offset, limit)
    return {
        "kind": kind, "pending_value_total": r2(sum(x["pending_value"] for x in lines)),
        **{k: v for k, v in page.items() if k != "items"}, "orders": page["items"],
        "method": "Tran3 RecType 4 order lines; executions linked by RefCode (else order number + item + party).",
        "confidence": "likely; verify one order against BUSY's pending-order report",
    }
