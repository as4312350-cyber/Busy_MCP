"""Voucher tools: day book, full voucher, sales/purchase analysis, orders, audit trail."""

from typing import Annotated, Any, Literal

from pydantic import Field

from ... import reports
from ..registry import FY, Company, FromDate, Limit, Offset, OptionalText, Registry, ToDate

VchTypeList = Annotated[list[int] | None, Field(max_length=20, description="Override voucher type codes, e.g. [9]")]


def register(reg: Registry) -> None:
    state = reg.state

    @reg.tool("day-book", "Day book",
              "Vouchers in a date range with party, amount, series and narration; filter by voucher type code "
              "(see voucher-types) or party.")
    def day_book(from_date: FromDate = None, to_date: ToDate = None,
                 vch_type: Annotated[int | None, Field(ge=0, le=999, description="e.g. 9 = Sales")] = None,
                 party: OptionalText = None, offset: Offset = 0, limit: Limit = 100,
                 company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.day_book(state.db(company, fy), from_date, to_date, vch_type, party, offset, limit)

    @reg.tool("voucher-details", "Voucher details",
              "Complete voucher by vch_code or vch_no: header (series, sale type, godown, audit fields), narration, "
              "accounting entries, items (qty, rate, discount, godown), bill sundries, bill references, order lines, "
              "GST and TDS lines, serial numbers, batches, billing and transport details, POS payment split.")
    def voucher_details(vch_code: Annotated[int | None, Field(ge=0)] = None,
                        vch_no: Annotated[str | None, Field(max_length=100)] = None,
                        vch_type: Annotated[int | None, Field(ge=0, le=999)] = None,
                        include_raw: bool = False, company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.voucher_details(state.db(company, fy), vch_code, vch_no, vch_type, include_raw)

    @reg.tool("sales-purchase-analysis", "Sales / purchase analysis",
              "Sales or purchase totals by party, item, month or voucher, net of returns, incl. and excl. tax.")
    def sales_purchase_analysis(kind: Literal["sales", "purchase"],
                                group_by: Literal["party", "item", "month", "voucher"] = "party",
                                from_date: FromDate = None, to_date: ToDate = None,
                                top: Annotated[int, Field(ge=1, le=1000)] = 50, vch_types: VchTypeList = None,
                                company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.trade_analysis(state.db(company, fy), kind, group_by, from_date, to_date, top, vch_types)

    @reg.tool("pending-orders", "Pending orders",
              "Open sales or purchase orders: ordered, executed and pending quantity and value per order line.")
    def pending_orders(kind: Literal["sales", "purchase"] = "sales", party: OptionalText = None,
                       item: OptionalText = None, include_completed: bool = False,
                       offset: Offset = 0, limit: Limit = 200,
                       company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.pending_orders(state.db(company, fy), kind, party, item, include_completed, offset, limit)

    @reg.tool("audit-trail", "Audit trail",
              "Who created, modified or deleted vouchers and masters, and when (BUSY's audit tables).")
    def audit_trail(from_date: FromDate = None, to_date: ToDate = None, user: OptionalText = None,
                    limit: Annotated[int, Field(ge=1, le=500)] = 100,
                    company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.audit_trail(state.db(company, fy), from_date, to_date, user, limit)
