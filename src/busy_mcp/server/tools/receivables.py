"""Receivables, payables, bills, banking."""

from typing import Annotated, Any

from pydantic import Field

from ... import reports
from ..registry import FY, AsOf, Company, LedgerRef, Limit, Nature, Offset, Registry


def register(reg: Registry) -> None:
    state = reg.state

    @reg.tool("outstanding", "Receivables / payables",
              "Party balances under Sundry Debtors (receivable) or Sundry Creditors (payable) with FIFO aging. "
              "Works for every company; for bill-wise due dates use bills-outstanding.")
    def outstanding(nature: Nature, as_of: AsOf = None, min_amount: Annotated[float, Field(ge=0)] = 1.0,
                    aging: bool = True, offset: Offset = 0, limit: Limit = 200,
                    company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.outstanding(state.db(company, fy), nature, as_of, min_amount, aging, offset, limit)

    @reg.tool("bills-outstanding", "Bill-wise outstanding",
              "Pending bills per party from BUSY's bill-by-bill references: bill no, date, due date, pending amount, "
              "overdue days and aging, reconciled with the ledger balance (on-account amounts shown separately).")
    def bills_outstanding(nature: Nature = "receivable", party: str | None = None, as_of: AsOf = None,
                          overdue_only: bool = False, offset: Offset = 0, limit: Limit = 100,
                          company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.bills_outstanding(state.db(company, fy), nature, party, as_of, overdue_only, offset, limit)

    @reg.tool("bank-reconciliation", "Bank reconciliation",
              "Balance as per books vs as per bank for one bank account, listing deposits not yet credited and "
              "cheques not yet presented (from BUSY's clearing dates).")
    def bank_reconciliation(bank: LedgerRef, as_of: AsOf = None, offset: Offset = 0, limit: Limit = 200,
                            company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.bank_reconciliation(state.db(company, fy), bank, as_of, offset, limit)

    @reg.tool("post-dated-cheques", "Post-dated cheques",
              "Receipts and payments whose cheque date is after the given date (default: today).")
    def post_dated_cheques(as_of: AsOf = None, offset: Offset = 0, limit: Limit = 200,
                           company: Company = None, fy: FY = None) -> dict[str, Any]:
        return reports.post_dated_cheques(state.db(company, fy), as_of, offset, limit)
