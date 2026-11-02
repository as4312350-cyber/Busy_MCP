"""Accounting tools: masters, ledgers, trial balance, P&L, balance sheet, trends."""

from typing import Annotated, Any

from pydantic import Field

from ... import reports
from ..registry import FY, AsOf, Company, FromDate, LedgerRef, Limit, MasterKind, Offset, Registry, ToDate


def register(reg: Registry) -> None:
    state = reg.state

    @reg.tool("search-masters", "Search masters", "Find accounts, groups or items by part of a name or alias.")
    def search_masters(text: Annotated[str, Field(min_length=1, max_length=200)], kind: MasterKind | None = None,
                       limit: Limit = 50, company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.search_masters(state.db(company, fy), text, kind, limit)

    @reg.tool("list-masters", "List masters", "List masters of one kind, optionally only those under a group.")
    def list_masters(kind: MasterKind, under_group: str | None = None, include_inactive: bool = True,
                     offset: Offset = 0, limit: Limit = 200, company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.list_masters(state.db(company, fy), kind, under_group, offset, limit, include_inactive)

    @reg.tool("chart-of-accounts", "Chart of accounts", "Account group tree (item group tree with item_groups=true).")
    def chart_of_accounts(include_ledgers: bool = False, item_groups: bool = False,
                          company: Company = None, fy: FY = None) -> list[dict[str, Any]]:
        return reports.chart_of_accounts(state.db(company, fy), include_ledgers, item_groups)

    @reg.tool("ledger-details", "Account details", "Account master: group path, opening balance, address, GSTIN, "
                                                   "PAN, phone, e-mail.")
    def ledger_details(ledger: LedgerRef, company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.ledger_details(state.db(company, fy), ledger)

    @reg.tool("ledger-balance", "Account balance", "Closing balance of one account on a date.")
    def ledger_balance(ledger: LedgerRef, as_of: AsOf = None, company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.ledger_balance(state.db(company, fy), ledger, as_of)

    @reg.tool("ledger-statement", "Account statement",
              "Account ledger: opening balance, every entry with contra account, narration and running balance.")
    def ledger_statement(ledger: LedgerRef, from_date: FromDate = None, to_date: ToDate = None,
                         offset: Offset = 0, limit: Limit = 200,
                         company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.ledger_statement(state.db(company, fy), ledger, from_date, to_date, offset, limit)

    @reg.tool("ledger-monthly", "Monthly trend",
              "Month-wise debit, credit and closing for one account OR a whole group (e.g. 'Sale', 'Expenses').")
    def ledger_monthly(account_or_group: LedgerRef, from_date: FromDate = None, to_date: ToDate = None,
                       company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.ledger_monthly(state.db(company, fy), account_or_group, from_date, to_date)

    @reg.tool("trial-balance", "Trial balance", "Opening, debit, credit, closing per account and per primary group.")
    def trial_balance(from_date: FromDate = None, to_date: ToDate = None,
                      group: Annotated[str | None, Field(description="Only accounts under this group")] = None,
                      include_zero: bool = False,
                      ledger_level: Annotated[bool, Field(description="false = group totals only")] = True,
                      offset: Offset = 0, limit: Limit = 500,
                      company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.trial_balance(state.db(company, fy), from_date, to_date, group, include_zero,
                                     ledger_level, offset, limit)

    @reg.tool("group-summary", "Group summary",
              "Balances of the sub-groups/accounts directly under a group (omit group for primary groups).")
    def group_summary(group: str | None = None, from_date: FromDate = None, to_date: ToDate = None,
                      company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.group_summary(state.db(company, fy), group, from_date, to_date)

    @reg.tool("profit-and-loss", "Profit & loss", "Income and expense heads for a period (before stock adjustment).")
    def profit_and_loss(from_date: FromDate = None, to_date: ToDate = None,
                        company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.profit_and_loss(state.db(company, fy), from_date, to_date)

    @reg.tool("balance-sheet", "Balance sheet", "Assets and liabilities by primary group on a date.")
    def balance_sheet(as_of: AsOf = None, company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.balance_sheet(state.db(company, fy), as_of)

    @reg.tool("cash-bank-summary", "Cash & bank",
              "Every cash and bank account: opening, received, paid, closing, plus month-wise cash flow.")
    def cash_bank_summary(from_date: FromDate = None, to_date: ToDate = None,
                          company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.cash_bank_summary(state.db(company, fy), from_date, to_date)
