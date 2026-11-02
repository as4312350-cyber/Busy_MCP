"""Streamable-HTTP transport hardening: bearer-token auth (constant-time),
Origin allow-list (blocks browser / DNS-rebinding attacks) and a body-size cap."""

from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

MAX_BODY = 1_000_000


async def _reject(send: Send, status: int, text: str) -> None:
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"text/plain"), (b"www-authenticate", b"Bearer")]})
    await send({"type": "http.response.body", "body": text.encode()})


def secure_app(app: ASGIApp, token: str, allowed_origins: list[str] | None = None) -> ASGIApp:
    expected = f"Bearer {token}".encode()
    origins = {o.rstrip("/").lower() for o in (allowed_origins or [])}

    async def guarded(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await app(scope, receive, send)
            return
        headers = {k.lower(): v for k, v in scope.get("headers") or []}
        origin = headers.get(b"origin", b"").decode("latin-1").rstrip("/").lower()
        if origin and origin not in origins:
            await _reject(send, 403, "Origin not allowed")
            return
        if not hmac.compare_digest(headers.get(b"authorization", b""), expected):
            await _reject(send, 401, "Unauthorized")
            return
        length = headers.get(b"content-length")
        if length is not None and (not length.isdigit() or int(length) > MAX_BODY):
            await _reject(send, 413, "Request too large")
            return
        await app(scope, receive, send)

    return guarded
