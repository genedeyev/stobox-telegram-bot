"""Structured logging setup (structlog).

Every AI decision is logged as a structured event so the analytics layer and
audits can consume it (see spec: "Every AI decision logged").
"""

from __future__ import annotations

import logging
import os
import sys

import structlog


def configure_logging(level: str | None = None) -> None:
    log_level = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=log_level)

    # SECURITY: httpx logs full request URLs at INFO — for the Telegram API the
    # bot token is IN the URL, so those lines would leak it into logs/screens.
    # Keep third-party HTTP client logs at WARNING and above.
    for noisy in ("httpx", "httpcore", "telegram", "openai", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    dev = os.environ.get("STOBOX_ENV", "development") == "development"
    renderer = (
        structlog.dev.ConsoleRenderer()
        if dev
        else structlog.processors.JSONRenderer()
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level, logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "stobox_ai") -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
