"""Read-only guard for free-form SQL coming from the AI.

This is one of four layers (see SECURITY.md):
  1. no write tool exists at all,
  2. this validator rejects anything that is not a single SELECT/WITH,
  3. the connection is opened read-only (Access ``ReadOnly=1``; SQL Server
     ``ApplicationIntent=ReadOnly`` + autocommit),
  4. (SQL edition) a dedicated ``db_datareader`` login with explicit DENYs.
"""

from __future__ import annotations

import re

from .errors import UnsafeQueryError

MAX_SQL_LENGTH = 20_000

_FORBIDDEN_KEYWORDS = frozenset({
    # DML / DDL / DCL
    "INSERT", "UPDATE", "DELETE", "MERGE", "UPSERT", "DROP", "ALTER", "CREATE",
    "TRUNCATE", "RENAME", "GRANT", "REVOKE", "DENY",
    "INTO",  # SELECT ... INTO new_table
    # procedure / dynamic execution
    "EXEC", "EXECUTE", "CALL", "SP_EXECUTESQL",
    # external data access (T-SQL)
    "OPENROWSET", "OPENDATASOURCE", "OPENQUERY", "OPENXML", "BULK",
    # server / session control
    "BACKUP", "RESTORE", "SHUTDOWN", "DBCC", "KILL", "RECONFIGURE", "WAITFOR",
    "DECLARE", "SET", "USE", "GO", "COMMIT", "ROLLBACK", "BEGIN", "TRANSACTION",
    "SAVE", "CHECKPOINT",
    # SQLite admin
    "ATTACH", "DETACH", "PRAGMA", "VACUUM", "REINDEX",
    # Jet/ACE (Access) VBA expression-service functions that touch the OS
    "SHELL", "EVAL", "DIR", "CURDIR", "ENVIRON", "KILLFILE", "FILECOPY",
})
_FORBIDDEN_PREFIXES = ("XP_", "SP_")
# Access: SELECT * FROM T IN 'C:\other.mdb'  /  FROM [;DATABASE=...].T  /  [Text;...]
_EXTERNAL_IN = re.compile(r"\bIN\s+x\b", re.IGNORECASE)
_BRACKET_CONNECT = re.compile(r"\[[^\]]*;[^\]]*\]")
_NEXT_VALUE = re.compile(r"\bNEXT\s+VALUE\s+FOR\b", re.IGNORECASE)


def _strip_literals_and_comments(sql: str) -> str:
    """Replace string literals, quoted identifiers and comments with a neutral
    token so keyword scanning cannot be fooled by e.g. a column named [Delete]."""
    out: list[str] = []
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
    if len(sql) > MAX_SQL_LENGTH:
        raise UnsafeQueryError(f"Query longer than {MAX_SQL_LENGTH} characters")
    if "\x00" in sql:
        raise UnsafeQueryError("NUL byte in query")
    cleaned = sql.strip()
    while cleaned.endswith(";"):
        cleaned = cleaned[:-1].rstrip()
    if _BRACKET_CONNECT.search(cleaned):
        raise UnsafeQueryError("External database / connect-string references are not allowed")
    scrubbed = _strip_literals_and_comments(cleaned)
    if ";" in scrubbed:
        raise UnsafeQueryError("Only a single statement is allowed (found ';')")
    if _EXTERNAL_IN.search(scrubbed):
        raise UnsafeQueryError("'IN <external database>' is not allowed")
    if _NEXT_VALUE.search(scrubbed):
        raise UnsafeQueryError("NEXT VALUE FOR modifies a sequence and is not allowed")
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_$#@]*", scrubbed)
    if not words:
        raise UnsafeQueryError("Query has no SQL keywords")
    if words[0].upper() not in ("SELECT", "WITH"):
        raise UnsafeQueryError("Only SELECT (or WITH ... SELECT) queries are allowed")
    for w in words:
        u = w.upper()
        if u in _FORBIDDEN_KEYWORDS:
            raise UnsafeQueryError(f"Keyword '{u}' is not allowed in read-only mode")
        if u.startswith(_FORBIDDEN_PREFIXES):
            raise UnsafeQueryError(f"Procedure call '{w}' is not allowed in read-only mode")
    return cleaned
