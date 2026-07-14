"""ASGI-level test for the web channel. Skipped unless the `web` extra
(starlette) and httpx are installed."""

from __future__ import annotations

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx")


@pytest.mark.asyncio
async def test_web_asgi_chat_and_health():
    import httpx

    from stobox_ai.channels.web.adapter import create_app_from_config

    app = create_app_from_config()
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            health = await c.get("/health")
            assert health.status_code == 200
            assert health.json()["indexed_chunks"] > 0

            ok = await c.post("/chat", json={"user_id": "u", "text": "What is ERC-3643?"})
            assert ok.status_code == 200
            body = ok.json()
            assert body["answered"] is True
            assert any("ERC-3643" in c["title"] for c in body["citations"])

            bad = await c.post("/chat", json={"text": "no user id"})
            assert bad.status_code == 400
