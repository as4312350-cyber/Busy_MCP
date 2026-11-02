"""Exception hierarchy. Every error the AI should see as a clean message derives
from :class:`BusyMCPError`; anything else is logged and reported generically."""

from __future__ import annotations


class BusyMCPError(Exception):
    """Base class for errors that are safe to show to the MCP client."""


class BusyConnectionError(BusyMCPError):
    """The BUSY data could not be located or opened."""


class ReportError(BusyMCPError, ValueError):
    """Bad report input (unknown ledger, bad date, ...)."""


class UnsafeQueryError(BusyMCPError, ValueError):
    """A free-form SQL statement was rejected by the read-only guard."""
