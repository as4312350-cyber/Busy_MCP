"""Generic access to every BUSY table: catalog, read-table, find, master details, SQL."""

import logging
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from ... import reports
from ...sqlguard import check_denied_tables, validate_select
from ..registry import FY, Company, Registry, TableName

audit = logging.getLogger("busy_mcp.audit")


class Filter(BaseModel):
    column: str = Field(min_length=1, max_length=128)
    op: Literal["eq", "ne", "gt", "ge", "lt", "le", "contains", "in", "is_null", "not_null"] = "eq"
    value: Any = None


def register(reg: Registry) -> None:
    state = reg.state
    settings = state.settings

    @reg.tool("catalog", "Data catalog",
              "Every table in the company database grouped by BUSY feature (masters, vouchers, inventory, taxes, "
              "banking, payroll, audit) with what it holds. Use with read-table to reach any data.")
    def catalog(with_row_counts: bool = False, company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.catalog(state.db(company, fy), with_row_counts)

    @reg.tool("read-table", "Read any table",
              "Read rows from ANY BUSY table without SQL: choose columns, filter (eq/ne/gt/ge/lt/le/contains/in/"
              "is_null/not_null; account or item names are accepted for code columns), date range, sort, paginate. "
              "Master codes are decoded to names automatically.")
    def read_table(table: TableName,
                   columns: Annotated[list[str] | None, Field(max_length=60)] = None,
                   filters: Annotated[list[Filter] | None, Field(max_length=20)] = None,
                   date_from: str | None = None, date_to: str | None = None, date_column: str | None = None,
                   order_by: str | None = None, descending: bool = False,
                   offset: Annotated[int, Field(ge=0)] = 0, limit: Annotated[int, Field(ge=1, le=500)] = 50,
                   compact: bool = True, company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.read_table(state.db(company, fy), table, columns,
                                  [f.model_dump() for f in filters or []], date_from, date_to, date_column,
                                  order_by, descending, offset, limit, compact)

    @reg.tool("find", "Find anything",
              "Search the whole company for a name, phone, GSTIN, PAN, e-mail, invoice/voucher number, narration, "
              "bill or batch number, or serial number. deep=true also scans line narrations (slower).")
    def find(text: Annotated[str, Field(min_length=2, max_length=100)],
             limit_per_source: Annotated[int, Field(ge=1, le=50)] = 10, deep: bool = False,
             company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.find(state.db(company, fy), text, limit_per_source, deep)

    @reg.tool("master-details", "Master details",
              "Everything BUSY stores about one master of ANY type (account, item, unit, godown, sale type, ...): "
              "decoded known fields (unit, MRP, prices, tax category, HSN, credit days), all other fields, "
              "opening and current balance or stock, contact details.")
    def master_details(name_or_code: Annotated[str, Field(min_length=1, max_length=200)],
                       master_type: Annotated[int | None, Field(ge=0, le=999)] = None,
                       company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.master_details(state.db(company, fy), name_or_code, master_type)

    @reg.tool("list-tables", "List tables", "Names of all tables in the selected BUSY database.")
    def list_tables(company: Company = None, fy: FY = None) -> dict[str, Any]:
        names = state.db(company, fy).table_names()
        return {"count": len(names), "tables": names}

    @reg.tool("describe-table", "Describe table", "Columns and types of one table, plus 3 sample rows.")
    def describe_table(table: TableName, company: Company = None, fy: FY = None) -> dict[str, Any]:
        db = state.db(company, fy)
        real = db.resolve_table(table)
        out: dict[str, Any] = {"table": real, "columns": db.columns(real)}
        if reports.explore.is_table_blocked(db, real):
            out["sample_rows"] = "hidden: table can contain stored credentials"
        else:
            sample = db.query(f"SELECT {db.top(3)}* FROM [{real}]{db.limit(3)}")
            out["sample_rows"] = [reports.explore.decode_row(db.masters(), r, compact=True) for r in sample]
        return out

    @reg.tool("query-database", "Run read-only SQL",
              "Last resort: ONE read-only SELECT (Access edition = Jet SQL, SQL edition = T-SQL). Prefer read-table. "
              "Writes, multiple statements, external databases and credential tables are rejected; results capped. "
              "Exclude cancelled vouchers with (Cancelled = 0 OR Cancelled IS NULL).")
    def query_database(sql: Annotated[str, Field(min_length=6, max_length=20000, description="A single SELECT")],
                       max_rows: Annotated[int, Field(ge=1, le=10000)] = 200,
                       company: Company = None, fy: FY = None) -> dict[str, Any]:
        clean = validate_select(sql)
        if not settings.allow_sensitive:
            check_denied_tables(clean, settings.denied_tables)
        db = state.db(company, fy)
        cap = min(max_rows, settings.max_rows)
        if settings.audit_queries:
            audit.info("query-database on %s: %s", db.target.key, " ".join(clean.split()))
        rows = db.query(clean, max_rows=cap + 1)
        masters = db.masters()
        decoded = [reports.explore.decode_row(masters, r, compact=False) for r in rows[:cap]]
        return {"row_count": len(decoded), "truncated": len(rows) > cap, "rows": decoded}
