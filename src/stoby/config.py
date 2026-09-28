"""Settings. Addresses of the sources, models, budgets, never a fact about Stobox."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _ids(raw: str) -> frozenset[int]:
    return frozenset(int(x) for x in raw.replace(" ", "").split(",") if x.strip().lstrip("-").isdigit())


@dataclass(frozen=True)
class Settings:
    telegram_token: str = ""
    admin_ids: frozenset[int] = field(default_factory=frozenset)
    site_url: str = "https://www.stobox.io/llms-full.txt"
    sig_url: str = "https://mcp.stobox.io/mcp"
    sig_token: str = ""
    base_rpc: str = "https://mainnet.base.org"
    answer_model: str = "claude-sonnet-5"
    classifier_model: str = "claude-haiku-4-5"
    answer_effort: str = "medium"
    state_dir: Path = Path("data")
    site_cache_minutes: int = 10
    site_stale_hours: int = 24
    sig_timeout_s: float = 8.0
    rpc_timeout_s: float = 8.0
    # Gene, 27.09.2026 (Stoby.md §6, item 7): mean <= $0.03 and p95 <= $0.06 per
    # answer; a hard daily stop of $5 is checked before every model call.
    cost_ceiling_mean_usd: float = 0.03
    cost_ceiling_p95_usd: float = 0.06
    daily_cap_usd: float = 5.0
    crm_webhook_url: str = ""
    crm_webhook_secret: str = ""
    crm_daily_cap: int = 20
    # Chats that get every new blog post (Gene, 28.09.2026): in production the
    # community group and the Stobox Announcements channel. Empty = no posting,
    # which is what a test bot must have.
    announce_chats: tuple[int, ...] = ()

    @classmethod
    def from_env(cls) -> Settings:
        e = os.environ.get
        return cls(
            telegram_token=e("TELEGRAM_BOT_TOKEN", ""),
            admin_ids=_ids(e("TELEGRAM_ADMIN_USER_IDS", "")),
            site_url=e("STOBY_SITE_URL", cls.site_url),
            sig_url=e("STOBY_SIG_URL", cls.sig_url),
            sig_token=e("STOBY_SIG_TOKEN", ""),
            base_rpc=e("STOBY_BASE_RPC", cls.base_rpc),
            answer_model=e("STOBY_ANSWER_MODEL", cls.answer_model),
            classifier_model=e("STOBY_CLASSIFIER_MODEL", cls.classifier_model),
            answer_effort=e("STOBY_ANSWER_EFFORT", cls.answer_effort),
            state_dir=Path(e("STOBY_STATE_DIR", e("RAILWAY_VOLUME_MOUNT_PATH", "data"))),
            daily_cap_usd=float(e("STOBY_DAILY_CAP_USD", cls.daily_cap_usd)),
            crm_webhook_url=e("CRM_WEBHOOK_URL", ""),
            crm_webhook_secret=e("CRM_WEBHOOK_SECRET", ""),
            announce_chats=tuple(sorted(_ids(e("STOBY_ANNOUNCE_CHATS", "")))),
        )
