"""Discovery reports: company overview, capabilities, master/voucher type
summaries, master search and the chart of accounts."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .. import __version__
from ..db import BusyDB, Master
from ..errors import ReportError
from ..schema import MASTER_TYPE_INFO, MT_GROUP, MT_ITEM, MT_ITEM_GROUP, MT_LEDGER, master_type_label
from ._common import ancestors, find_master, group_path, paginate, parent_name, vt

KIND_TO_TYPE = {"group": MT_GROUP, "ledger": MT_LEDGER, "item_group": MT_ITEM_GROUP, "item": MT_ITEM}


def company_info(db: BusyDB) -> dict[str, Any]:
    """Best effort: company details live in different tables per BUSY version."""
    info: dict[str, Any] = {}
    if db.has_table("Company"):
        cols = db.existing_columns("Company", ["Name", "Address1", "Address2", "Address3", "Address4", "GSTNo",
                                               "PANNo", "Email", "Mobile", "TelNo"])
        if cols:
            rows = db.query(f"SELECT {db.top(1)}{', '.join(cols)} FROM Company{db.limit(1)}")
            if rows:
                info.update({k: v for k, v in rows[0].items() if v not in (None, "")})
    if "name" not in info and db.has_column("Locks", "CompanyName"):
        rows = db.query(f"SELECT {db.top(1)}CompanyName FROM Locks{db.limit(1)}")
        if rows and rows[0].get("companyname"):
            info["name"] = rows[0]["companyname"]
    return info


def narration_source(db: BusyDB) -> str | None:
    if db.has_column("VchOtherInfo", "Narration1"):
        return "VchOtherInfo.Narration1"
    if db.has_column("Tran1", "Narration1"):
        return "Tran1.Narration1"
    return None


def capabilities(db: BusyDB) -> dict[str, Any]:
    """What this BUSY version's schema supports (detected, not assumed)."""
    from .inventory import item_inward_sign, item_rectypes

    sign, how = item_inward_sign(db)
    rts, rt_how = item_rectypes(db)
    features = {
        "bill_by_bill": db.has_column("Tran3", "Method"),
        "bank_reconciliation": db.has_column("Tran2", "ClrDate"),
        "post_dated_cheques": db.has_column("Tran1", "StockUpdationDate"),
        "material_centres": any(m.type == 11 for m in db.masters().values()),
        "batches": db.has_table("ItemParamDet") or db.has_column("Tran3", "MfgDate"),
        "serial_numbers": db.has_table("ItemSerialNo"),
        "tds": db.has_table("TDS"),
        "gst_item_details": db.has_table("VchGSTSumItemWise"),
        "audit_trail": db.has_table("DeletedInfo") or db.has_column("Tran1", "ModifiedBy"),
    }
    return {
        "cancelled_voucher_columns": db.existing_columns("Tran1", ["Cancelled", "VchCancelled"]),
        "cancelled_vouchers_excluded": not db.settings.include_cancelled,
        "narration_source": narration_source(db),
        "item_inward_sign": sign, "item_inward_sign_rule": how,
        "item_line_rectypes": rts, "item_line_rectypes_rule": rt_how,
        "features_present": features,
    }


def server_info(db: BusyDB) -> dict[str, Any]:
    return {
        "server": "busy-mcp", "version": __version__, "target": db.target.describe(), "dialect": db.dialect,
        "capabilities": capabilities(db), "cache": db.cache_info(), "settings": db.settings.public_view(),
        "settings_origin": dict(db.settings.origin),
    }


def company_overview(db: BusyDB) -> dict[str, Any]:
    masters = db.masters()
    counts: dict[int, int] = defaultdict(int)
    for m in masters.values():
        counts[m.type] += 1
    vch = db.query("SELECT COUNT(*) AS n, MIN(t1.[Date]) AS first_date, MAX(t1.[Date]) AS last_date "
                   "FROM Tran1 t1 WHERE 1 = 1" + db.active_voucher_filter("t1"))
    return {
        "target": db.target.describe(),
        "company": company_info(db) or None,
        "masters": {master_type_label(k): v for k, v in sorted(counts.items())},
        "vouchers": vch[0] if vch else {},
    }


def master_type_summary(db: BusyDB) -> list[dict[str, Any]]:
    by_type: dict[int, list[Master]] = defaultdict(list)
    for m in db.masters().values():
        by_type[m.type].append(m)
    return [
        {"master_type": t, "label": master_type_label(t),
         "confidence": MASTER_TYPE_INFO.get(t, ("", "unknown"))[1], "count": len(ms),
         "examples": [m.name for m in sorted(ms, key=lambda x: x.code)[:5]]}
        for t, ms in sorted(by_type.items())
    ]


def voucher_types(db: BusyDB) -> list[dict[str, Any]]:
    flt = db.active_voucher_filter("t1")
    rows = db.query(
        "SELECT t1.VchType AS vt, COUNT(*) AS n, MIN(t1.[Date]) AS first_date, MAX(t1.[Date]) AS last_date "
        f"FROM Tran1 t1 WHERE 1 = 1{flt} GROUP BY t1.VchType"
    )
    samples: dict[int, list[Any]] = defaultdict(list)
    for r in db.query(f"SELECT {db.top(2000)}t1.VchType AS vt, t1.VchNo FROM Tran1 t1 WHERE 1 = 1{flt} "
                      f"ORDER BY t1.VchCode DESC{db.limit(2000)}"):
        bucket = samples[int(r["vt"])]
        if len(bucket) < 3 and r.get("vchno"):
            bucket.append(r["vchno"])
    out: list[dict[str, Any]] = [
        {"vch_type": int(r["vt"]), "label": vt(db, r["vt"]), "count": r["n"], "first_date": r["first_date"],
         "last_date": r["last_date"], "sample_numbers": samples.get(int(r["vt"]), [])}
        for r in sorted(rows, key=lambda x: -(x["n"] or 0))
    ]
    cols = db.existing_columns("Tran1", ["Cancelled", "VchCancelled"])
    if cols:
        n = db.query(f"SELECT COUNT(*) AS n FROM Tran1 WHERE {' OR '.join(f'{c} <> 0' for c in cols)}")[0]["n"]
        if n:
            out.append({"note": f"{n} cancelled vouchers exist and are excluded from all reports"})
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
                     "type": master_type_label(m.type), "under": group_path(masters, m)}
                    for m in hits[:limit]],
    }


def list_masters(db: BusyDB, kind: str, under_group: str | None = None, offset: int = 0,
                 limit: int = 200, include_inactive: bool = True) -> dict[str, Any]:
    mt = KIND_TO_TYPE[kind]
    masters = db.masters()
    rows = [m for m in masters.values() if m.type == mt and (include_inactive or not m.inactive)]
    if under_group:
        grp_type = MT_GROUP if mt in (MT_GROUP, MT_LEDGER) else MT_ITEM_GROUP
        g = find_master(db, under_group, (grp_type,))
        rows = [m for m in rows if any(p.code == g.code for p in ancestors(masters, m))]
    rows.sort(key=lambda m: m.name.lower())
    page = paginate([{"code": m.code, "name": m.name, "alias": m.alias, "parent": parent_name(masters, m),
                      **({"inactive": True} if m.inactive else {})} for m in rows], offset, limit)
    return {"type": master_type_label(mt), **page}


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
