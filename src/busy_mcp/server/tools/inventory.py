"""Inventory tools: stock summary, movements, godowns, batches, serial numbers."""

from typing import Annotated, Any

from pydantic import Field

from ... import reports
from ..registry import FY, AsOf, Company, FromDate, ItemRef, Limit, Offset, OptionalText, Registry, ToDate


def register(reg: Registry) -> None:
    state = reg.state

    @reg.tool("stock-summary", "Stock summary",
              "Item-wise opening, inward, outward and closing quantity; only_negative=true lists negative stock.")
    def stock_summary(from_date: FromDate = None, to_date: ToDate = None, item_group: OptionalText = None,
                      search: OptionalText = None, include_zero: bool = False, only_negative: bool = False,
                      offset: Offset = 0, limit: Limit = 200, company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.stock_summary(state.db(company, fy), from_date, to_date, item_group, search,
                                     include_zero, only_negative, offset, limit)

    @reg.tool("stock-item-movement", "Item movement",
              "Every inward/outward movement of one item with running quantity and godown.")
    def stock_item_movement(item: ItemRef, from_date: FromDate = None, to_date: ToDate = None,
                            offset: Offset = 0, limit: Limit = 200,
                            company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.stock_item_movement(state.db(company, fy), item, from_date, to_date, offset, limit)

    @reg.tool("stock-by-location", "Stock by godown",
              "Closing quantity per item per material centre (godown/branch), with totals per location.")
    def stock_by_location(as_of: AsOf = None, item: OptionalText = None, location: OptionalText = None,
                          include_zero: bool = False, offset: Offset = 0, limit: Limit = 300,
                          company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.stock_by_location(state.db(company, fy), as_of, item, location, include_zero, offset, limit)

    @reg.tool("batch-stock", "Batch-wise stock",
              "Batch-wise closing quantity with manufacturing and expiry dates, MRP and rates; "
              "expiring_within_days lists near-expiry stock.")
    def batch_stock(item: OptionalText = None,
                    expiring_within_days: Annotated[int | None, Field(ge=-3650, le=3650)] = None,
                    as_of: AsOf = None, offset: Offset = 0, limit: Limit = 300,
                    company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.batch_stock(state.db(company, fy), item, expiring_within_days, as_of, offset, limit)

    @reg.tool("serial-numbers", "Serial numbers",
              "Serial numbers per item: in stock or sold, last voucher, godown and warranty.")
    def serial_numbers(item: OptionalText = None, serial: OptionalText = None, in_stock_only: bool = True,
                       offset: Offset = 0, limit: Limit = 300,
                       company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.serial_numbers(state.db(company, fy), item, serial, in_stock_only, offset, limit)
