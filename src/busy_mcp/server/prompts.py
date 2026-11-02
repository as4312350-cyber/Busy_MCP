"""Reusable prompts that chain the tools into common accountant workflows."""

from __future__ import annotations

from typing import Any


def register_prompts(mcp: Any) -> None:
    @mcp.prompt(name="financial-health-check", description="Review trial balance, P&L, balance sheet and "
                                                           "receivables for the selected year.")
    def financial_health_check(as_of: str = "") -> str:
        when = f" as on {as_of}" if as_of else " for the selected financial year"
        return (
            f"Do a financial health check of the selected BUSY company{when}.\n"
            "1. company-overview, then trial-balance (ledger_level=false) - confirm it tallies.\n"
            "2. profit-and-loss and balance-sheet - summarise margins and the biggest heads.\n"
            "3. outstanding receivable and payable - flag parties over 90 days.\n"
            "4. stock-summary only_negative=true - flag negative stock.\n"
            "Report findings as a short list with numbers, and repeat every caveat/warning the tools return."
        )

    @mcp.prompt(name="receivables-follow-up", description="Prioritised list of customers to chase.")
    def receivables_follow_up(min_amount: str = "10000") -> str:
        return (
            f"Using outstanding (nature=receivable, min_amount={min_amount}), list customers to follow up, "
            "oldest and largest first. For the top 5 call ledger-details for phone/email and "
            "ledger-statement for the last receipt date. Produce a table: party, amount, oldest days, "
            "last receipt, contact."
        )

    @mcp.prompt(name="month-end-review", description="Month-end review of sales, purchases and cash.")
    def month_end_review(month: str = "") -> str:
        scope = f"the month {month} (YYYY-MM)" if month else "the latest complete month"
        return (
            f"Review {scope} in BUSY: sales-purchase-analysis (sales and purchase, group_by=month and "
            "party), day-book for unusual large vouchers, and cash/bank balances via group-summary of "
            "'Current Assets'. Compare with the previous month and highlight changes over 20%."
        )
