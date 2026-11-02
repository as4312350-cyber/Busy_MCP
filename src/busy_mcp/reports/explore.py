"""Generic, safe access to every BUSY table: catalog, read-table, find, master-details.

Nothing here builds SQL from free text: table and column names are matched
against the database catalog, values are bind parameters, and tables or columns
that may hold credentials are blocked or masked.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any

from ..db import BusyDB, Master
from ..errors import ReportError
from ..schema import (
    CATEGORY_ORDER,
    MASTER_FIELD_INFO,
    MT_ITEM,
    MT_LEDGER,
    TABLE_INFO,
    is_reference_column,
    is_sensitive_column,
    master_type_label,
)
from ._common import (
    drcr,
    find_master,
    group_path,
    ledger_movements,
    master_name,
    next_day,
    opening_balances,
    parse_date,
    period,
    r2,
    vt,
)

MAX_WINDOW = 20_000
_OPS = {"eq": "=", "ne": "<>", "gt": ">", "ge": ">=", "lt": "<", "le": "<="}
_TABLE_INFO_CI = {k.lower(): (k, v) for k, v in TABLE_INFO.items()}


# ------------------------------------------------------------------- helpers


def is_table_blocked(db: BusyDB, table: str) -> bool:
    if db.settings.allow_sensitive:
        return False
    return table.lower() in {t.lower() for t in db.settings.denied_tables}


def _check_allowed(db: BusyDB, table: str) -> None:
    if is_table_blocked(db, table):
        raise ReportError(
            f"Table '{table}' is blocked because it can contain stored credentials. "
            "An administrator can allow it with BUSY_ALLOW_SENSITIVE=1."
        )


def decode_ref(masters: dict[int, Master], value: Any) -> str | None:
    """'Name (Type)' for a Master1 code, else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    code = int(value)
    if code <= 0 or code not in masters:
        return None
    m = masters[code]
    return f"{m.name} ({master_type_label(m.type)})"


def _is_empty(v: Any) -> bool:
    return v is None or v == "" or v is False or (isinstance(v, (int, float)) and not isinstance(v, bool) and v == 0)


def decode_row(masters: dict[int, Master], row: dict[str, Any], compact: bool = True,
               keep: set[str] | None = None) -> dict[str, Any]:
    """Mask sensitive columns, add '<col>_name' for master references, drop empty
    values (unless compact=False or the column is in `keep`)."""
    out: dict[str, Any] = {}
    keep = keep or set()
    for k, v in row.items():
        if is_sensitive_column(k):
            out[k] = "***"
            continue
        if compact and k not in keep and _is_empty(v):
            continue
        out[k] = v
        if is_reference_column(k):
            name = decode_ref(masters, v)
            if name:
                out[f"{k}_name"] = name
    return out


def _clean_text(text: str) -> str:
    cleaned = re.sub(r"[%_\[\]*?#]", " ", text or "").strip()
    if len(cleaned) < 2:
        raise ReportError("Search text must have at least 2 characters")
    return cleaned


# ------------------------------------------------------------------- catalog


def catalog(db: BusyDB, with_row_counts: bool = False) -> dict[str, Any]:
    """Every table in the company database, grouped by BUSY feature area."""
    counts: dict[str, int] = {}
    if with_row_counts:
        def count_all() -> dict[str, int]:
            out = {}
            for t in db.table_names():
                try:
                    out[t] = db.row_count(t)
                except Exception:  # noqa: BLE001 - a broken/locked table must not hide the rest
                    out[t] = -1
            return out
        counts = db.cached("row_counts", count_all)
    groups: dict[str, list[dict[str, Any]]] = {}
    for t in db.table_names():
        _, info = _TABLE_INFO_CI.get(t.lower(), (t, ("Other", "", "unknown")))
        category, desc, conf = info
        entry: dict[str, Any] = {"table": t}
        if desc:
            entry["description"] = desc
            entry["confidence"] = conf
        if with_row_counts:
            entry["rows"] = counts.get(t)
        if is_table_blocked(db, t):
            entry["blocked"] = True
        groups.setdefault(category, []).append(entry)
    ordered = {c: groups[c] for c in CATEGORY_ORDER if c in groups}
    return {"table_count": sum(len(v) for v in ordered.values()), "categories": ordered,
            "hint": "Read any table with read-table (codes are decoded to names). Blocked tables may hold credentials."}


# ---------------------------------------------------------------- read-table


def _resolve_columns(db: BusyDB, table: str) -> dict[str, str]:
    return {c["name"].lower(): c["name"] for c in db.columns(table)}


def _resolve_value(db: BusyDB, column: str, value: Any) -> Any:
    """Allow master names where a reference column expects a code."""
    if is_reference_column(column) and isinstance(value, str) and value.strip() and not value.strip().isdigit():
        return find_master(db, value, tuple(sorted({m.type for m in db.masters().values()}))).code
    return value


def read_table(db: BusyDB, table: str, columns: list[str] | None = None, filters: list[dict[str, Any]] | None = None,
               date_from: str | None = None, date_to: str | None = None, date_column: str | None = None,
               order_by: str | None = None, descending: bool = False, offset: int = 0, limit: int = 50,
               compact: bool = True) -> dict[str, Any]:
    real = db.resolve_table(table)
    _check_allowed(db, real)
    colmap = _resolve_columns(db, real)

    def col(name: str, for_output: bool = False) -> str:
        key = name.strip().lower()
        if key not in colmap:
            raise ReportError(f"Table {real} has no column '{name}'. Use describe-table.")
        if not for_output and is_sensitive_column(colmap[key]) and not db.settings.allow_sensitive:
            # filtering/sorting on a masked column would leak it through row counts
            raise ReportError(f"Column '{colmap[key]}' may hold credentials and cannot be filtered or sorted on.")
        return colmap[key]

    selected = [col(c, for_output=True) for c in columns] if columns else list(colmap.values())
    if not db.settings.allow_sensitive:
        selected = [c for c in selected if not is_sensitive_column(c)]
        if not selected:
            raise ReportError("Only credential-like columns were requested; they are never returned.")
    where, params = [], []
    for flt in filters or []:
        c = col(str(flt.get("column", "")))
        op = str(flt.get("op", "eq")).lower()
        value = flt.get("value")
        if op in _OPS:
            where.append(f"[{c}] {_OPS[op]} ?")
            params.append(_resolve_value(db, c, value))
        elif op == "contains":
            where.append(f"[{c}] LIKE ?")
            params.append(f"%{_clean_text(str(value))}%")
        elif op == "in":
            values = value if isinstance(value, list) else [value]
            if not values or len(values) > 200:
                raise ReportError("'in' needs 1-200 values")
            where.append(f"[{c}] IN ({', '.join('?' for _ in values)})")
            params.extend(_resolve_value(db, c, v) for v in values)
        elif op == "is_null":
            where.append(f"[{c}] IS NULL")
        elif op == "not_null":
            where.append(f"[{c}] IS NOT NULL")
        else:
            raise ReportError(f"Unknown filter op '{op}'. Use eq, ne, gt, ge, lt, le, contains, in, is_null, not_null.")
    dcol = col(date_column) if date_column else colmap.get("date")
    if (date_from or date_to) and not dcol:
        raise ReportError(f"Table {real} has no Date column; pass date_column.")
    if date_from:
        where.append(f"[{dcol}] >= ?")
        params.append(db.dparam(parse_date(date_from, _dt.date.min)))
    if date_to:
        where.append(f"[{dcol}] < ?")
        params.append(db.dparam(next_day(parse_date(date_to, _dt.date.max))))
    window = offset + limit + 1
    if window > MAX_WINDOW:
        raise ReportError(f"offset + limit must stay below {MAX_WINDOW}; narrow the filters instead.")
    order = col(order_by) if order_by else next((colmap[k] for k in ("vchcode", "code", "mastercode") if k in colmap),
                                                None)
    sql = f"SELECT {db.top(window)}{', '.join(f'[{c}]' for c in selected)} FROM [{real}]"
    if where:
        sql += " WHERE " + " AND ".join(where)
    if order:
        sql += f" ORDER BY [{order}]{' DESC' if descending else ''}"
    sql += db.limit(window)
    rows = db.query(sql, params)
    masters = db.masters()
    page = rows[offset:offset + limit]
    more = len(rows) > offset + limit
    _, info = _TABLE_INFO_CI.get(real.lower(), (real, ("Other", "", "unknown")))
    return {
        "table": real, "category": info[0], "description": info[1] or None,
        "offset": offset, "returned": len(page), "has_more": more, "next_offset": offset + len(page) if more else None,
        "rows": [decode_row(masters, r, compact) for r in page],
        "note": "Empty/zero values are omitted (compact=true). '<column>_name' fields decode master codes.",
    }


# ---------------------------------------------------------------------- find


def find(db: BusyDB, text: str, limit_per_source: int = 10, deep: bool = False) -> dict[str, Any]:
    """Search names, phone numbers, GSTINs, e-mails, voucher numbers, narrations,
    bill/batch numbers and serial numbers across the company."""
    needle = _clean_text(text)
    low = needle.lower()
    masters = db.masters()
    results: dict[str, Any] = {}
    hits = [m for m in masters.values() if low in f"{m.name} {m.alias or ''} {m.print_name or ''}".lower()]
    hits.sort(key=lambda m: (m.name.lower() != low, m.name.lower()))
    if hits:
        results["masters"] = [{"code": m.code, "name": m.name, "type": master_type_label(m.type),
                               "under": group_path(masters, m)} for m in hits[:limit_per_source]]
    like = f"%{needle}%"

    def probe(key: str, table: str, search_cols: list[str], show_cols: list[str]) -> None:
        if not db.has_table(table) or is_table_blocked(db, table):
            return
        scols = db.existing_columns(table, search_cols)
        if not scols:
            return
        shown = db.existing_columns(table, show_cols + search_cols)
        sql = (f"SELECT {db.top(limit_per_source)}{', '.join(f'[{c}]' for c in dict.fromkeys(shown))} "
               f"FROM [{db.resolve_table(table)}] WHERE " + " OR ".join(f"[{c}] LIKE ?" for c in scols)
               + db.limit(limit_per_source))
        rows = db.query(sql, [like] * len(scols))
        if rows:
            results[key] = [decode_row(masters, r) for r in rows]

    probe("party_contacts", "MasterAddressInfo",
          ["Mobile", "WhatsAppNo", "TelNo", "Email", "GSTNo", "ITPAN", "Contact", "Address1", "Address2", "PINCode"],
          ["MasterCode"])
    probe("voucher_numbers", "Tran1", ["VchNo"], ["VchCode", "VchType", "Date", "MasterCode1", "VchAmtBaseCur"])
    probe("voucher_other_info", "VchOtherInfo",
          ["Narration1", "Narration2", "PurchaseBillNo", "VehicleNo", "GrNo", "Transport"], ["VchCode"])
    probe("billing_details", "BillingDet", ["PartyName", "MobileNo", "GSTNo", "Email"], ["VchCode"])
    probe("bill_and_batch_refs", "Tran3", ["No"], ["VchCode", "VchType", "MasterCode1", "Date", "DueDate", "Value1"])
    probe("serial_numbers", "ItemSerialNo", ["SerialNo"], ["ItemCode", "VchCode", "VchNo", "Date"])
    if deep:
        probe("line_narrations", "Tran2", ["ShortNar"], ["VchCode", "VchNo", "Date", "MasterCode1", "Value1"])
    for rows in results.values():
        for r in rows:
            if "vchtype" in r:
                r["vch_type_name"] = vt(db, r["vchtype"])
    return {"text": needle, "sources_with_hits": list(results), "results": results,
            "hint": "Use voucher-details (vch_code), master-details (code) or ledger-statement for the full record."}


# ------------------------------------------------------------ master-details


def master_details(db: BusyDB, ref: str, master_type: int | None = None) -> dict[str, Any]:
    """Everything BUSY stores about one master (any type), decoded."""
    masters = db.masters()
    types = (master_type,) if master_type else tuple(sorted({m.type for m in masters.values()}))
    m = find_master(db, ref, types)
    row = db.query("SELECT * FROM Master1 WHERE Code = ?", (m.code,))
    raw = row[0] if row else {}
    out: dict[str, Any] = {
        "code": m.code, "name": m.name, "alias": m.alias, "print_name": m.print_name,
        "type": master_type_label(m.type), "master_type": m.type, "under": group_path(masters, m) or None,
        "inactive": m.inactive,
    }
    known: dict[str, Any] = {}
    for column, (meaning, conf) in MASTER_FIELD_INFO.get(m.type, {}).items():
        val = raw.get(column.lower())
        if _is_empty(val):
            continue
        ref_name = decode_ref(masters, val) if column.upper().startswith("CM") else None
        known[meaning] = {"value": ref_name or val, "column": column, "confidence": conf}
    if known:
        out["known_fields"] = known
    shown = {"code", "mastertype", "name", "alias", "printname", "parentgrp", "deactivemaster"}
    shown |= {c.lower() for c in MASTER_FIELD_INFO.get(m.type, {})}
    out["other_fields"] = decode_row(masters, {k: v for k, v in raw.items() if k not in shown})
    fy_end = period(db, None, None)[1]
    if m.type == MT_LEDGER:
        if db.has_table("MasterAddressInfo"):
            addr = db.query("SELECT * FROM MasterAddressInfo WHERE MasterCode = ?", (m.code,))
            if addr:
                out["address_info"] = decode_row(masters, {k: v for k, v in addr[0].items() if k != "mastercode"})
        opening = -opening_balances(db).get(m.code, {}).get("d1", 0.0)
        dr, cr = ledger_movements(db, None, fy_end, ledger=m.code).get(m.code, (0.0, 0.0))
        out["fy_opening_balance"] = drcr(opening)
        out["balance_at_fy_end"] = drcr(opening + dr - cr)
    elif m.type == MT_ITEM:
        from .inventory import _item_move

        ob = opening_balances(db, MT_ITEM).get(m.code, {"d1": 0.0, "d3": 0.0})
        mv = _item_move(db, m.code, None, fy_end)
        out["stock"] = {"fy_opening_qty": r2(ob["d1"]), "fy_opening_value": r2(ob["d3"]),
                        "inward_qty": r2(mv["in_qty"]), "outward_qty": r2(mv["out_qty"]),
                        "closing_qty_at_fy_end": r2(ob["d1"] + mv["in_qty"] - mv["out_qty"])}
    children = [c for c in masters.values() if c.parent == m.code]
    if children:
        out["children"] = {"count": len(children),
                           "first": [master_name(masters, c.code) for c in sorted(children, key=lambda x: x.name)[:25]]}
    return out
