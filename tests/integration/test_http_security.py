"""HTTP transport hardening: token, origin allow-list, body-size cap."""

from __future__ import annotations

import anyio
import httpx

from busy_mcp.server.http import secure_app

TOKEN = "t" * 32


async def _ok_app(scope, receive, send):  # minimal downstream ASGI app
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def _call(headers: dict[str, str], body: bytes = b"{}") -> int:
    async def run() -> int:
        app = secure_app(_ok_app, TOKEN, ["https://claude.ai"])
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
            return (await c.post("/mcp", headers=headers, content=body)).status_code

    return anyio.run(run)


def test_requires_token():
    assert _call({}) == 401
    assert _call({"Authorization": "Bearer wrong"}) == 401
    assert _call({"Authorization": f"Bearer {TOKEN}"}) == 200


def test_rejects_foreign_origin_even_with_token():
    auth = {"Authorization": f"Bearer {TOKEN}"}
    assert _call({**auth, "Origin": "https://evil.example"}) == 403
    assert _call({**auth, "Origin": "https://claude.ai"}) == 200


def test_rejects_oversized_body():
    assert _call({"Authorization": f"Bearer {TOKEN}"}, body=b"x" * 1_100_000) == 413
