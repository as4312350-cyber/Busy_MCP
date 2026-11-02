"""Logging. MCP stdio uses stdout for protocol traffic, so logs go to stderr
(and optionally a rotating file)."""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def configure_logging() -> None:
    level = (os.environ.get("BUSY_LOG_LEVEL") or "WARNING").upper()
    root = logging.getLogger("busy_mcp")
    root.setLevel(level)
    root.handlers.clear()
    stderr = logging.StreamHandler(sys.stderr)
    stderr.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(stderr)
    log_file = os.environ.get("BUSY_LOG_FILE")
    if log_file:
        fh = logging.handlers.RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(fh)
    root.propagate = False
