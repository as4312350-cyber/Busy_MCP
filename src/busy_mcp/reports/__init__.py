"""Accounting reports computed from BUSY's raw tables.

Every function takes a :class:`~busy_mcp.db.BusyDB` and returns plain
JSON-serialisable data; nothing here depends on MCP.
"""

from ._common import drcr, ledger_index, paginate, r2
from .accounts import (
    balance_sheet,
    group_summary,
    ledger_balance,
    ledger_details,
    ledger_statement,
    profit_and_loss,
    trial_balance,
)
from .analytics import cash_bank_summary, dashboard, ledger_monthly
from .audit import audit_trail
from .banking import bank_reconciliation, post_dated_cheques
from .bills import bills_outstanding, pending_orders
from .explore import catalog, find, master_details, read_table
from .inventory import (
    batch_stock,
    item_index,
    item_inward_sign,
    item_rectypes,
    serial_numbers,
    stock_by_location,
    stock_item_movement,
    stock_summary,
)
from .overview import (
    KIND_TO_TYPE,
    capabilities,
    chart_of_accounts,
    company_info,
    company_overview,
    list_masters,
    master_type_summary,
    search_masters,
    server_info,
    voucher_types,
)
from .receivables import outstanding
from .taxes import gst_summary, tds_register
from .trade import trade_analysis
from .vouchers import day_book, voucher_details

__all__ = [
    "KIND_TO_TYPE", "audit_trail", "balance_sheet", "bank_reconciliation", "batch_stock", "bills_outstanding",
    "capabilities", "cash_bank_summary", "catalog", "chart_of_accounts", "company_info", "company_overview",
    "dashboard", "day_book", "drcr", "find", "group_summary", "gst_summary", "item_index", "item_inward_sign",
    "item_rectypes", "ledger_balance", "ledger_details", "ledger_index", "ledger_monthly", "ledger_statement",
    "list_masters", "master_details", "master_type_summary", "outstanding", "paginate", "pending_orders",
    "post_dated_cheques", "profit_and_loss", "r2", "read_table", "search_masters", "serial_numbers", "server_info",
    "stock_by_location", "stock_item_movement", "stock_summary", "tds_register", "trade_analysis",
    "trial_balance", "voucher_details", "voucher_types",
]
