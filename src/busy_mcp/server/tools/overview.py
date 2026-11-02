"""Discovery tools: where am I, what is in this company, how fresh is the data."""

# No `from __future__ import annotations`: the MCP SDK reads these annotations at runtime.
from typing import Any

from ... import reports
from ...schema import SCHEMA_GUIDE
from ..registry import FY, Company, FromDate, Registry, ToDate


def register(reg: Registry) -> None:
    state = reg.state

    @reg.tool("dashboard", "Business dashboard",
              "START HERE for 'how is the business doing'. One call returns sales, purchases, profit, receivables, "
              "payables, cash & bank, top debtors, negative-stock count and voucher counts for a period.")
    def dashboard(from_date: FromDate = None, to_date: ToDate = None,
                  company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.dashboard(state.db(company, fy), from_date, to_date)

    @reg.tool("server-info", "Server info",
              "Version, selected database, detected BUSY features and conventions, cache state, settings.")
    def server_info(company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.server_info(state.db(company, fy))

    @reg.tool("list-companies", "List companies", "Every BUSY company and financial-year database found.")
    def list_companies() -> dict[str, Any]:
        targets = state.targets(refresh=True)
        return {"selected": state.db().target.describe(), "available": [t.describe() for t in targets]}

    @reg.tool("select-company", "Select company", "Switch the default company and/or financial year.")
    def select_company(company: Company = None, fy: FY = None) -> dict[str, Any]:
        t = state.select(company, fy)
        return {"selected": t.describe(), "overview": reports.company_overview(state.db())}

    @reg.tool("company-overview", "Company overview", "Company name/details, master counts, voucher date range.")
    def company_overview(company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.company_overview(state.db(company, fy))

    @reg.tool("voucher-types", "Voucher types", "Voucher types used, with counts, date range and sample numbers.")
    def voucher_types(company: Company = None, fy: FY = None) -> list[dict[str, Any]]:
        return reports.voucher_types(state.db(company, fy))

    @reg.tool("master-type-summary", "Master types",
              "Count of masters per MasterType with examples (units, godowns, sale types, series, ...).")
    def master_type_summary(company: Company = None, fy: FY = None) -> list[dict[str, Any]]:
        return reports.master_type_summary(state.db(company, fy))

    @reg.tool("schema-guide", "Schema guide", "How BUSY stores data; read before writing SQL.")
    def schema_guide() -> str:
        return SCHEMA_GUIDE

    @reg.tool("refresh-cache", "Refresh cache",
              "Force a reload now (normally automatic: changes in BUSY are detected within seconds).")
    def refresh_cache(company: Company = None, fy: FY = None) -> dict[str, Any]:
        db = state.db(company, fy)
        n = len(db.masters(refresh=True))
        return {"reloaded_masters": n, "target": db.target.describe()}
