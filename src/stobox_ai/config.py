"""Configuration loading.

Layered config:
  1. ``config/config.yaml``  — non-secret, versioned defaults (see spec "Configuration").
  2. environment variables    — secrets + ``${VAR:-default}`` interpolation inside the YAML.

Secrets (API keys, tokens, DB URL) NEVER live in the YAML — only in env / vault.
The parsed config is exposed as plain nested dicts wrapped by ``Config`` with
dotted access, so new keys added to the YAML need no code changes.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)(?::-(.*?))?\}")


def _interpolate(value: Any) -> Any:
    """Recursively replace ``${VAR}`` / ``${VAR:-default}`` in strings."""
    if isinstance(value, str):
        def repl(m: re.Match[str]) -> str:
            var, default = m.group(1), m.group(2)
            return os.environ.get(var, default if default is not None else "")
        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, dict):
        return {k: _interpolate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate(v) for v in value]
    return value


class Config:
    """Dotted, read-only view over the parsed config tree."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def get(self, path: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def section(self, path: str) -> Config:
        return Config(self.get(path, {}) or {})

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __contains__(self, key: str) -> bool:
        return key in self._data

    @property
    def raw(self) -> dict[str, Any]:
        return self._data


class Secrets:
    """Typed access to secret env vars. The single place secrets are read."""

    @property
    def telegram_token(self) -> str | None:
        return os.environ.get("TELEGRAM_BOT_TOKEN") or None

    @property
    def admin_user_ids(self) -> set[int]:
        raw = os.environ.get("TELEGRAM_ADMIN_USER_IDS", "")
        return {int(x) for x in raw.replace(" ", "").split(",") if x}

    @property
    def admin_usernames(self) -> set[str]:
        """Admins by @username (lowercased, no @). Less secure than IDs — usernames
        can change — but lets you add someone before you have their numeric ID."""
        raw = os.environ.get("TELEGRAM_ADMIN_USERNAMES", "")
        return {x.lower().lstrip("@") for x in raw.replace(" ", "").split(",") if x}

    @property
    def anthropic_key(self) -> str | None:
        return os.environ.get("ANTHROPIC_API_KEY") or None

    @property
    def openai_key(self) -> str | None:
        return os.environ.get("OPENAI_API_KEY") or None

    @property
    def database_url(self) -> str | None:
        return os.environ.get("DATABASE_URL") or None


@lru_cache(maxsize=1)
def load_config(path: str | None = None) -> Config:
    cfg_path = Path(path or os.environ.get("CONFIG_PATH", "config/config.yaml"))
    if not cfg_path.exists():
        raise FileNotFoundError(f"Config not found: {cfg_path.resolve()}")
    data = yaml.safe_load(cfg_path.read_text()) or {}
    return Config(_interpolate(data))


@lru_cache(maxsize=1)
def get_secrets() -> Secrets:
    return Secrets()
