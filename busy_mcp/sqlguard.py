"""Read-only guard for free-form SQL coming from the AI.

This is one layer of three (see README "Read-only guarantees"):
  1. this validator (rejects anything that is not a single SELECT/WITH),
  2. the connection is opened read-only (Access: ODBC ReadOnly; SQL Server:
     recommended dedicated db_datareader login),
  3. the server exposes no write tool at all.
"""

from __future__ import annotations

import re

_FORBIDDEN = {
    "INSERT", "UPDATE", "DELETE", "MERGE", "UPSERT",
    "DROP", "ALTER", "CREATE", "TRUNCATE", "RENAME",
    "EXEC", "EXECUTE", "CALL", "GRANT", "REVOKE", "DENY",
    "INTO",  # blocks SELECT ... INTO new_table
    "OPENROWSET", "OPENDATASOURCE", "OPENQUERY", "OPENXML", "BULK",
    "BACKUP", "RESTORE", "SHUTDOWN", "DBCC", "KILL", "RECONFIGURE",
    "WAITFOR", "DECLARE", "SET", "USE", "GO", "COMMIT", "ROLLBACK",
    "BEGIN", "TRANSACTION", "ATTACH", "DETACH", "PRAGMA", "VACUUM",
}
_FORBIDDEN_PREFIXES = ("XP_", "SP_")


class UnsafeQueryError(ValueError):
    pass


def _strip_literals_and_comments(sql: str) -> str:
    """Replace string literals, quoted identifiers and comments with spaces so
    keyword scanning cannot be fooled by e.g. a column named [Delete]."""
    out = []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        if c == "-" and nxt == "-":
            j = sql.find("\n", i)
            i = n if j == -1 else j
            out.append(" ")
        elif c == "/" and nxt == "*":
            j = sql.find("*/", i + 2)
            if j == -1:
                raise UnsafeQueryError("Unterminated /* comment */")
            i = j + 2
            out.append(" ")
        elif c in ("'", '"', "[", "`"):
            close = "]" if c == "[" else c
            j = i + 1
            while j < n:
                if sql[j] == close:
                    if j + 1 < n and sql[j + 1] == close:  # escaped '' or ]]
                        j += 2
                        continue
                    break
                j += 1
            if j >= n:
                raise UnsafeQueryError("Unterminated quoted literal or identifier")
            i = j + 1
            out.append(" x ")
        else:
            out.append(c)
            i += 1
    return "".join(out)


def validate_select(sql: str) -> str:
    """Return the cleaned SQL if it is a single read-only statement, else raise."""
    if not sql or not sql.strip():
        raise UnsafeQueryError("Empty query")
    cleaned = sql.strip()
    while cleaned.endswith(";"):
        cleaned = cleaned[:-1].rstrip()
    scrubbed = _strip_literals_and_comments(cleaned)
    if ";" in scrubbed:
        raise UnsafeQueryError("Only a single statement is allowed (found ';')")
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_$#@]*", scrubbed)
    if not words:
        raise UnsafeQueryError("Query has no SQL keywords")
    first = words[0].upper()
    if first not in ("SELECT", "WITH"):
        raise UnsafeQueryError("Only SELECT (or WITH ... SELECT) queries are allowed")
    for w in words:
        u = w.upper()
        if u in _FORBIDDEN:
            raise UnsafeQueryError(f"Keyword '{u}' is not allowed in read-only mode")
        if u.startswith(_FORBIDDEN_PREFIXES):
            raise UnsafeQueryError(f"Procedure call '{w}' is not allowed in read-only mode")
    return cleaned
