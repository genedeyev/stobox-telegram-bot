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

from typing import Any

from ...core.engine import AgentEngine
from ...core.types import AgentResponse, Author, ChatType, IncomingMessage
from ...logging import get_logger
from ..base import Channel

log = get_logger(__name__)


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
                    "source_url": c.source_url,
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
        body = await request.json()
        if not body.get("text") or not body.get("user_id"):
            return JSONResponse({"error": "user_id and text are required"}, status_code=400)
        result = await channel.chat(
            user_id=str(body["user_id"]),
            text=str(body["text"]),
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
        log.info("web.app_ready")
        yield

    async def chat_endpoint(request: Request):
        body = await request.json()
        if not body.get("text") or not body.get("user_id"):
            return JSONResponse({"error": "user_id and text are required"}, status_code=400)
        result = await request.app.state.channel.chat(
            user_id=str(body["user_id"]),
            text=str(body["text"]),
            session_id=body.get("session_id"),
            display_name=body.get("display_name"),
            language=body.get("language"),
        )
        return JSONResponse(result)

    async def health_endpoint(request: Request):
        n = await request.app.state.engine.retriever.store.count()
        return JSONResponse({"status": "ok", "indexed_chunks": n})

    async def digest_endpoint(request: Request):
        # NOTE: protect behind auth/gateway in production — analytics data.
        return JSONResponse(request.app.state.engine.daily_digest().build())

    async def faq_endpoint(request: Request):
        entries = await request.app.state.engine.weekly_faq().generate(top_n=10)
        from ...insights import WeeklyFAQ

        return JSONResponse({"faq": WeeklyFAQ.to_dict(entries)})

    return Starlette(
        lifespan=lifespan,
        routes=[
            Route("/chat", chat_endpoint, methods=["POST"]),
            Route("/health", health_endpoint, methods=["GET"]),
            Route("/insights/digest", digest_endpoint, methods=["GET"]),
            Route("/insights/faq", faq_endpoint, methods=["GET"]),
        ],
    )


def main() -> None:  # pragma: no cover - launches a server
    """``stobox-web`` console entry: serve the web chat API via uvicorn."""
    import os

    import uvicorn

    uvicorn.run(
        "stobox_ai.channels.web.adapter:create_app_from_config",
        factory=True,
        host=os.environ.get("WEB_HOST", "0.0.0.0"),
        port=int(os.environ.get("WEB_PORT", "8080")),
    )
