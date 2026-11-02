"""Audit trail: who created, changed or deleted what, and when."""

from __future__ import annotations

from collections import Counter
from typing import Any

from ..db import BusyDB
from ._common import master_name, next_day, parse_date, period, r2, vt
from .explore import decode_row, is_table_blocked


def audit_trail(db: BusyDB, from_date: str | None, to_date: str | None, user: str | None = None,
                limit: int = 100) -> dict[str, Any]:
    masters = db.masters()
    fy_start, fy_end = period(db, None, None)
    f = parse_date(from_date, fy_start)
    t = parse_date(to_date, fy_end)
    lo, hi = db.dparam(f), db.dparam(next_day(t))
    who = (user or "").strip().lower() or None
    out: dict[str, Any] = {"from": f.isoformat(), "to": t.isoformat(), "user_filter": user}
    per_user: Counter[str] = Counter()
    sections = []

    if db.has_column("Tran1", "ModifiedBy") and db.has_column("Tran1", "ModificationTime"):
        cols = db.existing_columns("Tran1", ["VchCode", "VchType", "VchNo", "Date", "MasterCode1", "VchAmtBaseCur",
                                             "CreatedBy", "CreationTime", "ModifiedBy", "ModificationTime"])
        rows = db.query(f"SELECT {db.top(limit * 5)}{', '.join(f'[{c}]' for c in cols)} FROM Tran1 "
                        "WHERE ModificationTime >= ? AND ModificationTime < ? ORDER BY ModificationTime DESC"
                        + db.limit(limit * 5), (lo, hi))
        changed = []
        for r in rows:
            if who and who not in str(r.get("modifiedby") or "").lower():
                continue
            per_user[str(r.get("modifiedby") or "?")] += 1
            changed.append({"vch_type": vt(db, r.get("vchtype")), "vch_no": r.get("vchno"), "date": r.get("date"),
                            "party": master_name(masters, r.get("mastercode1")),
                            "amount": r2(abs(float(r.get("vchamtbasecur") or 0))),
                            "created_by": r.get("createdby"), "created_at": r.get("creationtime"),
                            "modified_by": r.get("modifiedby"), "modified_at": r.get("modificationtime")})
        out["vouchers_modified"] = changed[:limit]
        sections.append("Tran1 audit columns")

    if db.has_table("DeletedInfo") and not is_table_blocked(db, "DeletedInfo"):
        cols = db.existing_columns("DeletedInfo", ["Type", "VchMastType", "Identity", "DeletedBy", "DeletionTime",
                                                   "OrgVchAmtBaseCur", "ModVchAmtBaseCur", "ComputerName"])
        if "DeletionTime" in cols:
            rows = db.query(f"SELECT {db.top(limit)}{', '.join(f'[{c}]' for c in cols)} FROM DeletedInfo "
                            "WHERE DeletionTime >= ? AND DeletionTime < ? ORDER BY DeletionTime DESC" + db.limit(limit),
                            (lo, hi))
            deleted = [decode_row(masters, r) for r in rows if not who or who in str(r.get("deletedby") or "").lower()]
            for r in deleted:
                per_user[f"{r.get('deletedby') or '?'} (deletions)"] += 1
            out["deleted"] = deleted
            sections.append("DeletedInfo")

    if db.has_table("CheckList"):
        cols = db.existing_columns("CheckList", ["Type", "Code", "Action", "ActionTime", "UserName", "Notes",
                                                 "ComputerName"])
        if "ActionTime" in cols:
            rows = db.query(f"SELECT {db.top(limit)}{', '.join(f'[{c}]' for c in cols)} FROM CheckList "
                            "WHERE ActionTime >= ? AND ActionTime < ? ORDER BY ActionTime DESC" + db.limit(limit),
                            (lo, hi))
            out["change_log"] = [decode_row(masters, r) for r in rows
                                 if not who or who in str(r.get("username") or "").lower()]
            sections.append("CheckList")

    if db.has_table("EventLog"):
        cols = db.existing_columns("EventLog", ["UserName", "Date", "WorkedOn", "Description", "ComputerName"])
        if "Date" in cols:
            rows = db.query(f"SELECT {db.top(limit)}{', '.join(f'[{c}]' for c in cols)} FROM EventLog "
                            "WHERE [Date] >= ? AND [Date] < ? ORDER BY [Date] DESC" + db.limit(limit), (lo, hi))
            out["user_activity"] = [decode_row(masters, r) for r in rows
                                    if not who or who in str(r.get("username") or "").lower()]
            sections.append("EventLog")

    out["activity_by_user"] = dict(per_user.most_common())
    out["sources"] = sections or ["none: this BUSY version keeps no audit tables"]
    return out
