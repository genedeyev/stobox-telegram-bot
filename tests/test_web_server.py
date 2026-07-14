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


@pytest.mark.asyncio
async def test_reingest_webhook_requires_valid_signature(monkeypatch):
    import httpx

    from stobox_ai.channels.web.adapter import create_app_from_config
    from stobox_ai.ops.webhook import sign

    monkeypatch.setenv("WEBHOOK_SECRET", "topsecret")

    # Keep the test offline: stub the remote sync the webhook triggers.
    async def _fake_sync(indexer, config, fetcher=None):
        return {"web": 0, "github": 0}

    monkeypatch.setattr("stobox_ai.knowledge.sync.sync_sources", _fake_sync)
    app = create_app_from_config()
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            body = b'{"source":"test"}'
            # Missing/invalid signature → 401.
            bad = await c.post("/api/reingest", content=body,
                               headers={"X-Hub-Signature-256": "sha256=nope"})
            assert bad.status_code == 401
            # Valid signature → 200 and a sync runs (offline: 0+ chunks).
            ok = await c.post("/api/reingest", content=body,
                              headers={"X-Hub-Signature-256": sign("topsecret", body)})
            assert ok.status_code == 200
            assert "synced" in ok.json()
