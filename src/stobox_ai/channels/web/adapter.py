"""Web / HTTP channel — the same engine, a different transport.

This adapter demonstrates the platform is genuinely channel-agnostic: it maps an
HTTP chat request onto the identical :class:`IncomingMessage` → engine →
:class:`AgentResponse` path that Telegram uses, then serializes the response as
JSON (text + citations + confidence) for a website widget or API client.

``WebChannel.chat`` is framework-free so it runs in unit tests with no server.
``create_app`` lazily builds a Starlette/FastAPI-style ASGI app only if a web
framework is installed, keeping it an optional dependency.
"""

from __future__ import annotations

import json
import os
from typing import Any

from ...core.engine import AgentEngine
from ...core.types import AgentResponse, Author, ChatType, IncomingMessage
from ...logging import get_logger
from ..base import Channel, public_citation_url

log = get_logger(__name__)

# Hard cap on /chat request bodies — Starlette does not bound body size, and an
# unauthenticated endpoint that feeds an LLM must not accept megabyte payloads.
MAX_BODY_BYTES = 16_384
MAX_TEXT_CHARS = 4_000


async def _read_json_capped(request) -> dict | None:
    """Parse the request body as JSON, rejecting oversized or invalid payloads."""
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > MAX_BODY_BYTES:
        return None
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        return None
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _client_ip(request) -> str:
    """Best-effort client IP for rate limiting (first X-Forwarded-For hop on PaaS)."""
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "?"


def _insights_authorized(request) -> bool:
    """Bearer-token gate for the analytics endpoints (community PII).

    Secure by default: with no INSIGHTS_TOKEN configured the routes are OFF —
    they leak member questions and lead data if left open on a public service.
    """
    token = os.environ.get("INSIGHTS_TOKEN", "")
    if not token:
        return False
    import hmac

    supplied = request.headers.get("authorization", "")
    supplied = supplied.removeprefix("Bearer ").strip()
    return bool(supplied) and hmac.compare_digest(supplied, token)


class WebChannel(Channel):
    name = "web"

    def __init__(self, engine: AgentEngine) -> None:
        super().__init__(engine)
        self._counter = 0

    async def start(self) -> None:  # pragma: no cover - HTTP server is external
        log.info("web.ready")

    async def stop(self) -> None:  # pragma: no cover
        pass

    async def chat(
        self,
        *,
        user_id: str,
        text: str,
        session_id: str | None = None,
        display_name: str | None = None,
        language: str | None = None,
    ) -> dict[str, Any]:
        """Process one web chat message; return a JSON-serializable response."""
        self._counter += 1
        incoming = IncomingMessage(
            author=Author(external_id=user_id, channel="web", display_name=display_name),
            text=text,
            chat_id=session_id or f"web:{user_id}",
            chat_type=ChatType.PRIVATE,   # web chat is 1:1
            message_id=str(self._counter),
            channel="web",
            raw={"addressed": True, "language_hint": language},
        )
        response = await self.engine.handle(incoming)
        return self._serialize(response)

    @staticmethod
    def _serialize(response: AgentResponse | None) -> dict[str, Any]:
        if response is None or not response.should_reply:
            return {"reply": None, "answered": False}
        return {
            "answered": True,
            "reply": response.text,
            "confidence": response.confidence.value,
            "confidence_score": response.confidence_score,
            "language": response.language,
            "mode": response.mode.value,
            "escalate": response.escalate,
            "lead_captured": response.lead_captured,
            "citations": [
                {
                    "title": c.title,
                    "section": c.section,
                    "version": c.version,
                    "source_url": public_citation_url(c.source_url),
                    "label": c.render(),
                }
                for c in response.citations
            ],
        }


def create_app(engine: AgentEngine):
    """Build an ASGI app exposing ``POST /chat`` and ``GET /health``.

    Uses Starlette if available (FastAPI is a superset). Import is lazy so the
    web framework stays an optional dependency.
    """
    try:
        from starlette.applications import Starlette
        from starlette.requests import Request
        from starlette.responses import JSONResponse
        from starlette.routing import Route
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "Web channel needs Starlette/FastAPI: pip install 'starlette uvicorn'"
        ) from exc

    channel = WebChannel(engine)

    async def chat_endpoint(request: Request):
        body = await _read_json_capped(request)
        if body is None:
            return JSONResponse({"error": "invalid or oversized body"}, status_code=413)
        if not body.get("text") or not body.get("user_id"):
            return JSONResponse({"error": "user_id and text are required"}, status_code=400)
        result = await channel.chat(
            user_id=str(body["user_id"])[:128],
            text=str(body["text"])[:MAX_TEXT_CHARS],
            session_id=body.get("session_id"),
            display_name=body.get("display_name"),
            language=body.get("language"),
        )
        return JSONResponse(result)

    async def health_endpoint(_request: Request):
        n = await engine.retriever.store.count()
        return JSONResponse({"status": "ok", "indexed_chunks": n})

    return Starlette(
        routes=[
            Route("/chat", chat_endpoint, methods=["POST"]),
            Route("/health", health_endpoint, methods=["GET"]),
        ]
    )


def create_app_from_config():
    """ASGI app that builds the engine on startup (inside the server's event
    loop, so pooled DB connections bind correctly). Entry point for uvicorn."""
    from contextlib import asynccontextmanager

    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    from ...config import load_config
    from ...logging import configure_logging

    @asynccontextmanager
    async def lifespan(app):
        configure_logging()
        engine = await AgentEngine.create(load_config())
        app.state.channel = WebChannel(engine)
        app.state.engine = engine
        # Per-IP limiter for /chat: the body's user_id is client-supplied and
        # trivially rotated, so the engine's per-user limits can't stop a
        # scripted cost-DoS on their own. IPs are much harder to rotate.
        from ...ops.ratelimit import RateLimiter

        app.state.ip_limiter = RateLimiter(
            per_minute=20, per_day=2000, global_daily_output_tokens=None)
        log.info("web.app_ready")
        yield

    async def chat_endpoint(request: Request):
        decision = request.app.state.ip_limiter.check(f"ip:{_client_ip(request)}")
        if not decision.allowed:
            return JSONResponse({"error": "rate limited", "detail": decision.retry_hint},
                                status_code=429)
        body = await _read_json_capped(request)
        if body is None:
            return JSONResponse({"error": "invalid or oversized body"}, status_code=413)
        if not body.get("text") or not body.get("user_id"):
            return JSONResponse({"error": "user_id and text are required"}, status_code=400)
        result = await request.app.state.channel.chat(
            user_id=str(body["user_id"])[:128],
            text=str(body["text"])[:MAX_TEXT_CHARS],
            session_id=body.get("session_id"),
            display_name=body.get("display_name"),
            language=body.get("language"),
        )
        return JSONResponse(result)

    async def health_endpoint(request: Request):
        n = await request.app.state.engine.retriever.store.count()
        return JSONResponse({"status": "ok", "indexed_chunks": n})

    def _insights_denied() -> JSONResponse:
        return JSONResponse(
            {"error": "insights endpoints require a Bearer INSIGHTS_TOKEN "
                      "(disabled when the token is unset)"},
            status_code=403,
        )

    async def digest_endpoint(request: Request):
        # Analytics data (member questions, leads) — token-gated, off by default.
        if not _insights_authorized(request):
            return _insights_denied()
        return JSONResponse(request.app.state.engine.daily_digest().build())

    async def dashboard_endpoint(request: Request):
        if not _insights_authorized(request):
            return _insights_denied()
        from starlette.responses import HTMLResponse

        from ...insights.dashboard import render_dashboard

        digest = request.app.state.engine.daily_digest().build()
        return HTMLResponse(render_dashboard(digest))

    async def faq_endpoint(request: Request):
        # Also triggers LLM spend on demand — must never be open to the internet.
        if not _insights_authorized(request):
            return _insights_denied()
        entries = await request.app.state.engine.weekly_faq().generate(top_n=10)
        from ...insights import WeeklyFAQ

        return JSONResponse({"faq": WeeklyFAQ.to_dict(entries)})

    async def metrics_endpoint(request: Request):
        """Prometheus text exposition — token-gated like the other analytics
        surfaces (latency percentiles and volumes are business-sensitive)."""
        if not _insights_authorized(request):
            return _insights_denied()
        from starlette.responses import PlainTextResponse

        engine = request.app.state.engine
        chunks = await engine.retriever.store.count()
        snap = engine.decisions.snapshot() or {}
        lines = [
            "# HELP stobox_indexed_chunks Chunks in the knowledge index",
            "# TYPE stobox_indexed_chunks gauge",
            f"stobox_indexed_chunks {chunks}",
            "# TYPE stobox_decisions_window gauge",
            f"stobox_decisions_window {snap.get('count', 0)}",
            "# TYPE stobox_unknown_rate gauge",
            f"stobox_unknown_rate {snap.get('unknown_rate', 0)}",
            "# TYPE stobox_escalations_window gauge",
            f"stobox_escalations_window {snap.get('escalations', 0)}",
            "# TYPE stobox_leads_window gauge",
            f"stobox_leads_window {snap.get('leads', 0)}",
            "# TYPE stobox_p95_latency_ms gauge",
            f"stobox_p95_latency_ms {snap.get('p95_latency_ms', 0)}",
            "# TYPE stobox_tokens_in_window gauge",
            f"stobox_tokens_in_window {snap.get('tokens_in', 0)}",
            "# TYPE stobox_tokens_out_window gauge",
            f"stobox_tokens_out_window {snap.get('tokens_out', 0)}",
            "# TYPE stobox_paused gauge",
            f"stobox_paused {int(getattr(engine, 'paused', False))}",
        ]
        return PlainTextResponse("\n".join(lines) + "\n")

    async def reingest_endpoint(request: Request):
        """HMAC-signed webhook: re-ingest stobox.io + GitHub (self-update loop).
        Signature required — see ops/webhook.py."""
        import os

        from ...ops.webhook import verify_signature

        secret = os.environ.get("WEBHOOK_SECRET")
        if not secret:
            return JSONResponse({"error": "reingest webhook disabled (no WEBHOOK_SECRET)"}, status_code=503)
        body = await request.body()
        if not verify_signature(secret, body, request.headers.get("X-Hub-Signature-256")):
            return JSONResponse({"error": "invalid signature"}, status_code=401)
        results = await request.app.state.engine.sync_knowledge()
        return JSONResponse({"synced": results, "total": sum(results.values())})

    return Starlette(
        lifespan=lifespan,
        routes=[
            Route("/chat", chat_endpoint, methods=["POST"]),
            Route("/health", health_endpoint, methods=["GET"]),
            Route("/insights", dashboard_endpoint, methods=["GET"]),
            Route("/insights/digest", digest_endpoint, methods=["GET"]),
            Route("/insights/faq", faq_endpoint, methods=["GET"]),
            Route("/metrics", metrics_endpoint, methods=["GET"]),
            Route("/api/reingest", reingest_endpoint, methods=["POST"]),
        ],
    )


def main() -> None:  # pragma: no cover - launches a server
    """``stobox-web`` console entry: serve the web chat API via uvicorn."""
    import os

    import uvicorn

    # Honor the platform-injected $PORT (Railway/Render/Heroku/Fly) first.
    port = int(os.environ.get("WEB_PORT") or os.environ.get("PORT") or "8080")
    uvicorn.run(
        "stobox_ai.channels.web.adapter:create_app_from_config",
        factory=True,
        host=os.environ.get("WEB_HOST", "0.0.0.0"),
        port=port,
    )
