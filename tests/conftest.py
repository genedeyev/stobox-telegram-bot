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


@pytest.fixture(autouse=True)
def _isolate_state_files(tmp_path, monkeypatch):
    """Redirect channel/proactive state defaults into tmp so tests never write
    ledger files into the repo's live data/ directory."""
    from stobox_ai.channels.telegram import adapter as tg_adapter
    from stobox_ai.channels.telegram import proactive as tg_proactive

    monkeypatch.setattr(tg_adapter, "DEFAULT_STATE_PATH",
                        str(tmp_path / "telegram_state.json"))
    monkeypatch.setattr(tg_proactive, "DEFAULT_STATE_PATH",
                        str(tmp_path / "proactive_state.json"))


@pytest.fixture(scope="session", autouse=True)
def config(tmp_path_factory):
    """The real config, with every data/ state path redirected into a session
    tmp dir — engine-based tests must never write into the repo's live data/
    (they used to mutate xp.json, reminders.json, etc. on every run).
    Autouse + session-scoped: load_config() is lru_cached, so the redirect
    lands before any test grabs the shared instance directly."""
    from stobox_ai.config import load_config

    cfg = load_config()
    state_dir = tmp_path_factory.mktemp("state")

    def _redirect(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, str) and v.startswith("data/"):
                    node[k] = str(state_dir / v[len("data/"):])
                else:
                    _redirect(v)
        elif isinstance(node, list):
            for item in node:
                _redirect(item)

    _redirect(cfg.raw)
    return cfg
