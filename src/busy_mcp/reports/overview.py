"""Discovery reports: company overview, master/voucher type summaries, master lookup."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .. import __version__
from ..db import BusyDB, Master
from ..errors import ReportError
from ..schema import MASTER_TYPES, MT_GROUP, MT_ITEM, MT_ITEM_GROUP, MT_LEDGER
from ._common import ancestors, find_master, group_path, paginate, parent_name, vt

KIND_TO_TYPE = {
    "group": MT_GROUP, "ledger": MT_LEDGER, "item_group": MT_ITEM_GROUP, "item": MT_ITEM,
}


def company_info(db: BusyDB) -> dict[str, Any]:
    """Best effort: company name/details live in different tables per BUSY version."""
    info: dict[str, Any] = {}
    if db.has_column("Locks", "CompanyName"):
        rows = db.query(f"SELECT {db.top(1)}CompanyName FROM Locks{db.limit(1)}")
        if rows and rows[0].get("companyname"):
            info["name"] = rows[0]["companyname"]
    for table in ("Company", "CompanyInfo"):
        if db.has_table(table):
            rows = db.query(f"SELECT {db.top(1)}* FROM {db.resolve_table(table)}{db.limit(1)}")
            if rows:
                info.update({k: v for k, v in rows[0].items() if v not in (None, "", 0, 0.0)})
            break
    return info


def capabilities(db: BusyDB) -> dict[str, Any]:
    """What this BUSY version's schema supports (detected, not assumed)."""
    from .inventory import item_inward_sign

    sign, how = item_inward_sign(db)
    return {
        "cancelled_voucher_columns": [c for c in ("Cancelled", "VchCancelled") if db.has_column("Tran1", c)],
        "cancelled_vouchers_excluded": not db.settings.include_cancelled,
        "narration_source": narration_source(db),
        "item_inward_sign": sign,
        "item_inward_sign_rule": how,
        "has_order_lines_table": db.has_table("Tran3"),
        "has_address_table": db.has_table("MasterAddressInfo"),
        "has_gst_item_table": db.has_table("VchGSTSumItemWise"),
    }


def narration_source(db: BusyDB) -> str | None:
    if db.has_column("VchOtherInfo", "Narration1"):
        return "VchOtherInfo.Narration1"
    if db.has_column("Tran1", "Narration1"):
        return "Tran1.Narration1"
    return None


def server_info(db: BusyDB) -> dict[str, Any]:
    return {
        "server": "busy-mcp", "version": __version__, "target": db.target.describe(),
        "dialect": db.dialect, "capabilities": capabilities(db),
        "settings": db.settings.public_view(),
    }


def company_overview(db: BusyDB) -> dict[str, Any]:
    masters = db.masters()
    counts: dict[int, int] = defaultdict(int)
    for m in masters.values():
        counts[m.type] += 1
    where = "WHERE 1 = 1" + db.active_voucher_filter("t1")
    vch = db.query(f"SELECT COUNT(*) AS n, MIN(t1.[Date]) AS first_date, MAX(t1.[Date]) AS last_date "
                   f"FROM Tran1 t1 {where}")
    return {
        "target": db.target.describe(),
        "company": company_info(db) or None,
        "masters": {MASTER_TYPES.get(k, f"MasterType {k}"): v for k, v in sorted(counts.items())},
        "vouchers": vch[0] if vch else {},
    }


def master_type_summary(db: BusyDB) -> list[dict[str, Any]]:
    by_type: dict[int, list[Master]] = defaultdict(list)
    for m in db.masters().values():
        by_type[m.type].append(m)
    return [
        {"master_type": t, "label": MASTER_TYPES.get(t, "unmapped"), "count": len(ms),
         "examples": [m.name for m in sorted(ms, key=lambda x: x.code)[:5]]}
        for t, ms in sorted(by_type.items())
    ]


def voucher_types(db: BusyDB) -> list[dict[str, Any]]:
    flt = db.active_voucher_filter("t1")
    rows = db.query(
        "SELECT t1.VchType AS vt, COUNT(*) AS n, MIN(t1.[Date]) AS first_date, MAX(t1.[Date]) AS last_date "
        f"FROM Tran1 t1 WHERE 1 = 1{flt} GROUP BY t1.VchType"
    )
    out = []
    for r in sorted(rows, key=lambda x: -(x["n"] or 0)):
        code = int(r["vt"])
        samples = db.query(
            f"SELECT {db.top(3)}t1.VchNo FROM Tran1 t1 WHERE t1.VchType = ?{flt} "
            f"ORDER BY t1.VchCode DESC{db.limit(3)}", (code,))
        out.append({
            "vch_type": code, "label": vt(db, code), "count": r["n"],
            "first_date": r["first_date"], "last_date": r["last_date"],
            "sample_numbers": [s["vchno"] for s in samples],
        })
    if db.has_column("Tran1", "Cancelled"):
        c = db.query("SELECT COUNT(*) AS n FROM Tran1 WHERE Cancelled <> 0")
        if c and c[0]["n"]:
            out.append({"note": f"{c[0]['n']} cancelled vouchers exist and are excluded from all reports"})
    return out


def search_masters(db: BusyDB, text: str, kind: str | None = None, limit: int = 50) -> dict[str, Any]:
    masters = db.masters()
    types = (KIND_TO_TYPE[kind],) if kind else None
    needle = (text or "").strip().lower()
    if not needle:
        raise ReportError("Search text is required")
    hits = [m for m in masters.values()
            if (not types or m.type in types)
            and needle in f"{m.name} {m.alias or ''} {m.print_name or ''}".lower()]
    hits.sort(key=lambda m: (m.name.lower() != needle, not m.name.lower().startswith(needle), m.name.lower()))
    return {
        "total": len(hits),
        "matches": [{"code": m.code, "name": m.name, "alias": m.alias, "master_type": m.type,
                     "type": MASTER_TYPES.get(m.type, f"MasterType {m.type}"), "under": group_path(masters, m)}
                    for m in hits[:limit]],
    }


def list_masters(db: BusyDB, kind: str, under_group: str | None = None, offset: int = 0,
                 limit: int = 200) -> dict[str, Any]:
    mt = KIND_TO_TYPE[kind]
    masters = db.masters()
    rows = [m for m in masters.values() if m.type == mt]
    if under_group:
        grp_type = MT_GROUP if mt in (MT_GROUP, MT_LEDGER) else MT_ITEM_GROUP
        g = find_master(db, under_group, (grp_type,))
        rows = [m for m in rows if any(p.code == g.code for p in ancestors(masters, m))]
    rows.sort(key=lambda m: m.name.lower())
    page = paginate([{"code": m.code, "name": m.name, "alias": m.alias, "parent": parent_name(masters, m)}
                     for m in rows], offset, limit)
    return {"type": MASTER_TYPES[mt], **page}


def chart_of_accounts(db: BusyDB, include_ledgers: bool = False, item_side: bool = False,
                      max_depth: int = 12) -> list[dict[str, Any]]:
    masters = db.masters()
    gtype, ltype = (MT_ITEM_GROUP, MT_ITEM) if item_side else (MT_GROUP, MT_LEDGER)
    children: dict[int, list[Master]] = defaultdict(list)
    for m in masters.values():
        if m.type == gtype or (include_ledgers and m.type == ltype):
            children[m.parent if m.parent in masters else 0].append(m)

    def node(m: Master, depth: int, seen: frozenset[int]) -> dict[str, Any]:
        n: dict[str, Any] = {"name": m.name, "code": m.code}
        if m.type == gtype:
            kids = sorted(children.get(m.code, []), key=lambda x: (x.type != gtype, x.name.lower()))
            if include_ledgers:
                n["member_count"] = sum(1 for k in kids if k.type == ltype)
            if depth < max_depth:
                n["children"] = [node(k, depth + 1, seen | {m.code}) for k in kids if k.code not in seen]
        return n

    roots = sorted((m for m in children.get(0, []) if m.type == gtype), key=lambda x: x.name.lower())
    return [node(r, 0, frozenset()) for r in roots]


__all__ = [
    "KIND_TO_TYPE", "capabilities", "chart_of_accounts", "company_info", "company_overview",
    "list_masters", "master_type_summary", "narration_source", "search_masters", "server_info",
    "voucher_types",
]
