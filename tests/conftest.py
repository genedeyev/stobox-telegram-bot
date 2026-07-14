"""Test fixtures. Everything runs offline — no API keys, no Postgres.

We clear secret env vars so the factories fall back to the local hash embedder
and EchoLLM, and point config/prompts/docs at the repo defaults.
"""

from __future__ import annotations

import os

import pytest

os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("DATABASE_URL", None)
os.environ.setdefault("CONFIG_PATH", "config/config.yaml")
os.environ.setdefault("PROMPTS_PATH", "config/prompts")
os.environ.setdefault("DOCS_PATH", "docs")
os.environ.setdefault("STOBOX_ENV", "development")


@pytest.fixture(scope="session")
def config():
    from stobox_ai.config import load_config

    return load_config()
