"""MCP tool definitions, one module per BUSY feature area."""

from ..registry import Registry
from . import accounts, explore, inventory, overview, receivables, taxes, vouchers


def register_all(reg: Registry) -> None:
    for module in (overview, explore, accounts, receivables, vouchers, inventory, taxes):
        module.register(reg)
