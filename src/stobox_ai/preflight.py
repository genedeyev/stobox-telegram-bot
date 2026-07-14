"""Preflight readiness check — run before launching, or via ``stobox-doctor``.

Tells you exactly what is configured and what is missing, so a Telegram test
"just works" instead of failing cryptically. Distinguishes hard blockers
(bot won't start / can't answer) from soft warnings (degraded quality).

Loads ``.env`` first so it reflects what a real run will see.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path


def load_dotenv_if_present() -> bool:
    """Load .env into the process env (no-op if the file or lib is absent)."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return False
    env = Path(".env")
    if env.exists():
        load_dotenv(env)
        return True
    return False


class Level(str, enum.Enum):
    OK = "ok"
    WARN = "warn"       # degraded but runnable
    BLOCK = "block"     # cannot run / cannot answer


@dataclass
class Check:
    name: str
    level: Level
    detail: str
    fix: str = ""


@dataclass
class Preflight:
    checks: list[Check] = field(default_factory=list)

    @property
    def blockers(self) -> list[Check]:
        return [c for c in self.checks if c.level == Level.BLOCK]

    @property
    def ready(self) -> bool:
        return not self.blockers

    def render(self) -> str:
        icon = {Level.OK: "✅", Level.WARN: "⚠️ ", Level.BLOCK: "⛔"}
        lines = ["Stobox bot preflight", "─" * 40]
        for c in self.checks:
            lines.append(f"{icon[c.level]} {c.name}: {c.detail}")
            if c.fix and c.level != Level.OK:
                lines.append(f"     → {c.fix}")
        lines.append("─" * 40)
        lines.append("READY TO START ✅" if self.ready else "NOT READY — resolve ⛔ blockers above")
        return "\n".join(lines)


def run_preflight() -> Preflight:
    load_dotenv_if_present()
    from .config import get_secrets, load_config

    pf = Preflight()
    secrets = get_secrets()

    # --- Telegram (hard blocker) ---
    if secrets.telegram_token:
        pf.checks.append(Check("Telegram token", Level.OK, "TELEGRAM_BOT_TOKEN set"))
    else:
        pf.checks.append(Check(
            "Telegram token", Level.BLOCK, "TELEGRAM_BOT_TOKEN missing",
            "Create a bot with @BotFather, then set TELEGRAM_BOT_TOKEN in .env",
        ))

    if secrets.admin_user_ids:
        pf.checks.append(Check("Admins", Level.OK, f"{len(secrets.admin_user_ids)} admin id(s)"))
    else:
        pf.checks.append(Check(
            "Admins", Level.WARN, "TELEGRAM_ADMIN_USER_IDS empty",
            "Set your numeric Telegram id so /sync, /stats, escalations reach you (get it from @userinfobot)",
        ))

    # --- Reasoning model (blocker for real answers) ---
    if secrets.anthropic_key or secrets.openai_key:
        which = "Anthropic" if secrets.anthropic_key else "OpenAI"
        pf.checks.append(Check("Reasoning LLM", Level.OK, f"{which} key present"))
    else:
        pf.checks.append(Check(
            "Reasoning LLM", Level.BLOCK, "no ANTHROPIC_API_KEY or OPENAI_API_KEY",
            "Set ANTHROPIC_API_KEY (recommended) — without it the bot only echoes and can't answer",
        ))

    # --- Embeddings (quality warning) ---
    if secrets.openai_key:
        pf.checks.append(Check("Embeddings", Level.OK, "OpenAI embeddings (semantic retrieval)"))
    else:
        pf.checks.append(Check(
            "Embeddings", Level.WARN, "no OPENAI_API_KEY → local hash embeddings",
            "Set OPENAI_API_KEY for real semantic retrieval; hash embeddings give weak results",
        ))

    # --- Persistence (info) ---
    if secrets.database_url:
        pf.checks.append(Check("Storage", Level.OK, "Postgres+pgvector (persistent)"))
    else:
        pf.checks.append(Check(
            "Storage", Level.WARN, "no DATABASE_URL → in-memory (not persistent)",
            "Fine for testing; set DATABASE_URL (pgvector) for production persistence",
        ))

    # --- Guardrail files (blocker for compliance) ---
    try:
        config = load_config()
    except Exception as exc:  # noqa: BLE001
        pf.checks.append(Check("Config", Level.BLOCK, f"config load failed: {exc}",
                               "Check config/config.yaml exists and is valid YAML"))
        return pf

    sp = Path(config.get("guardrails.system_prompt", "SYSTEM-PROMPT.md"))
    canon = Path(config.get("guardrails.canonicals", "canonicals.yaml"))
    if sp.exists() and canon.exists():
        pf.checks.append(Check("Guardrails", Level.OK, f"{sp.name} + {canon.name} present"))
    else:
        missing = ", ".join(p.name for p in (sp, canon) if not p.exists())
        pf.checks.append(Check(
            "Guardrails", Level.BLOCK, f"missing {missing}",
            "Run from the repo root so SYSTEM-PROMPT.md and canonicals.yaml are found",
        ))

    # --- Knowledge (warning) ---
    docs = Path(config.get("knowledge.docs_path", "docs"))
    doc_count = len(list(docs.glob("*"))) if docs.exists() else 0
    sync_on = config.get("knowledge.sources.web.enabled") or config.get("knowledge.sources.github.enabled")
    if doc_count or sync_on:
        detail = f"{doc_count} local doc(s)"
        detail += "; run `stobox-sync` to pull stobox.io + GitHub" if sync_on else ""
        pf.checks.append(Check("Knowledge", Level.OK if (doc_count or secrets.openai_key) else Level.WARN, detail,
                               "Run `stobox-sync` before/after launch to load live Stobox knowledge"))
    else:
        pf.checks.append(Check("Knowledge", Level.WARN, "no local docs and no remote sources enabled",
                               "Add files to docs/ or enable knowledge.sources, then `stobox-sync`"))

    return pf


def main() -> None:
    pf = run_preflight()
    print(pf.render())
    raise SystemExit(0 if pf.ready else 1)


if __name__ == "__main__":
    main()
