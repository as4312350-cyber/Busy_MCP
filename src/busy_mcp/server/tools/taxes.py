"""Tax tools: GST and TDS."""

from typing import Any

from ... import reports
from ..registry import FY, Company, FromDate, Limit, Offset, OptionalText, Registry, ToDate


def register(reg: Registry) -> None:
    state = reg.state

    @reg.tool("gst-summary", "GST summary",
              "Output vs input GST by head (CGST/SGST/IGST/Cess) from tax ledgers, net payable, and taxable "
              "turnover by Sale/Purchase Type; monthly=true adds a month-wise split.")
    def gst_summary(from_date: FromDate = None, to_date: ToDate = None, monthly: bool = False,
                    company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.gst_summary(state.db(company, fy), from_date, to_date, monthly)

    @reg.tool("tds-register", "TDS register",
              "TDS/TCS deducted: party, TDS account, amount, rate, tax, challan, quarter; totals per TDS account.")
    def tds_register(from_date: FromDate = None, to_date: ToDate = None, party: OptionalText = None,
                     offset: Offset = 0, limit: Limit = 200, company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.tds_register(state.db(company, fy), from_date, to_date, party, offset, limit)
