"""STBU live market data — CoinGecko (primary) + CoinMarketCap (fallback).

Read-only public market endpoints. CoinGecko works keyless (free tier); a
``COINGECKO_API_KEY`` upgrades to the pro host, and ``COINMARKETCAP_API_KEY``
enables the CMC fallback. Everything degrades gracefully: a failed fetch returns
the last good snapshot (or ``None``), never an exception — market data must never
break a reply.

The HTTP client is injectable (a ``HttpJson``) so the logic is unit-tested fully
offline, exactly like ``chain/wallet.py``.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from ..logging import get_logger

log = get_logger(__name__)

COINGECKO_BASE = "https://api.coingecko.com/api/v3"
COINGECKO_PRO_BASE = "https://pro-api.coingecko.com/api/v3"
CMC_BASE = "https://pro-api.coinmarketcap.com/v2"

COINGECKO_COIN_URL = "https://www.coingecko.com/en/coins/stobox-token"


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #
def _optf(v: Any) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")


def _iso_from_epoch(ts: Any) -> str:
    try:
        return datetime.fromtimestamp(int(ts), UTC).strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError, OSError, OverflowError):
        return _now_iso()


def _fmt_price(p: float) -> str:
    """Small-cap prices need more decimals; big ones fewer."""
    if p >= 1:
        return f"{p:,.4f}"
    if p >= 0.01:
        return f"{p:.4f}"
    s = f"{p:.8f}".rstrip("0").rstrip(".")
    return s or "0"


def _fmt_usd(v: float) -> str:
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(v) >= div:
            return f"{v / div:,.2f}{unit}"
    return f"{v:,.0f}"


# --------------------------------------------------------------------------- #
# Snapshot
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class MarketSnapshot:
    price_usd: float
    market_cap_usd: float | None
    volume_24h_usd: float | None
    change_24h_pct: float | None
    source: str            # "CoinGecko" | "CoinMarketCap"
    as_of: str             # human ISO-ish UTC string
    symbol: str = "STBU"
    coin_url: str = COINGECKO_COIN_URL

    def format_line(self) -> str:
        """One-line grounded fact for the [FRESHNESS] block."""
        parts = [f"${_fmt_price(self.price_usd)}"]
        if self.market_cap_usd is not None:
            parts.append(f"market cap ${_fmt_usd(self.market_cap_usd)}")
        if self.volume_24h_usd is not None:
            parts.append(f"24h vol ${_fmt_usd(self.volume_24h_usd)}")
        if self.change_24h_pct is not None:
            parts.append(f"24h {self.change_24h_pct:+.1f}%")
        body = ", ".join(parts)
        return (
            f"- {self.symbol} live market price ({self.source}): {body} — as of {self.as_of}. "
            "This is current secondary-market data (a FACT you may state when asked), NOT the "
            "Eqvista company valuation and NOT investment advice. Never give price predictions "
            "or targets."
        )

    def format_brief(self) -> str:
        """Compact one-liner for a community post (not the system prompt). The
        'not advice / not the company valuation' framing is added once by the
        caller around the whole updates briefing, so this stays short."""
        parts = [f"${_fmt_price(self.price_usd)}"]
        if self.change_24h_pct is not None:
            arrow = "🔺" if self.change_24h_pct >= 0 else "🔻"
            parts.append(f"{arrow} {self.change_24h_pct:+.1f}% (24h)")
        if self.market_cap_usd is not None:
            parts.append(f"mcap ${_fmt_usd(self.market_cap_usd)}")
        return " · ".join(parts)

    def format_report(self, contracts: dict[str, str] | None = None) -> str:
        """Full HTML block for the /price command. Carries its own compliance
        framing because command output bypasses the answer-path rails."""
        lines = [
            f"📈 <b>{self.symbol} market snapshot</b> — live, {self.source}",
            f"• Price: <b>${_fmt_price(self.price_usd)}</b>",
        ]
        if self.market_cap_usd is not None:
            lines.append(f"• Market cap: ${_fmt_usd(self.market_cap_usd)}")
        if self.volume_24h_usd is not None:
            lines.append(f"• 24h volume: ${_fmt_usd(self.volume_24h_usd)}")
        if self.change_24h_pct is not None:
            arrow = "🔺" if self.change_24h_pct >= 0 else "🔻"
            lines.append(f"• 24h change: {arrow} {self.change_24h_pct:+.2f}%")
        lines.append(f"• As of: {self.as_of}")
        lines.append(f"• Chart: {self.coin_url}")
        if contracts:
            labels = {
                "ethereum": "Ethereum", "bnb_chain": "BNB Chain",
                "polygon": "Polygon", "arbitrum": "Arbitrum", "base": "Base",
            }
            lines.append("\n<b>Official STBU contracts</b> (verify only via stobox.io):")
            for chain, addr in contracts.items():
                lines.append(f"• {labels.get(chain, chain)}: <code>{addr}</code>")
        lines.append(
            "\nThis is market data, <b>not investment advice</b>, and <b>not</b> the Stobox "
            "company valuation (that's a separate figure — /valuation)."
        )
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# HTTP client (injectable)
# --------------------------------------------------------------------------- #
class HttpJson(Protocol):
    async def get_json(
        self, url: str, params: dict | None = None, headers: dict | None = None
    ) -> Any | None: ...
    async def aclose(self) -> None: ...


class HttpxJson:
    def __init__(self, timeout: float = 8.0) -> None:
        import httpx

        self._client = httpx.AsyncClient(timeout=timeout)

    async def get_json(self, url, params=None, headers=None):
        try:
            r = await self._client.get(url, params=params, headers=headers)
        except Exception as exc:  # noqa: BLE001
            log.warning("market.http_failed", url=url, error=str(exc))
            return None
        if r.status_code != 200:
            log.warning("market.http_status", url=url, status=r.status_code)
            return None
        try:
            return r.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("market.http_badjson", url=url, error=str(exc))
            return None

    async def aclose(self):
        await self._client.aclose()


# --------------------------------------------------------------------------- #
# Provider
# --------------------------------------------------------------------------- #
class MarketData:
    """Cached STBU price provider. ``snapshot()`` returns the cached snapshot
    within ``ttl_seconds``, refreshing (behind a lock, no stampede) when stale,
    and backing off after a failure so a down API is never hammered per-message."""

    def __init__(
        self,
        *,
        coingecko_id: str = "stobox-token",
        cmc_symbol: str = "STBU",
        cmc_id: str | int | None = None,
        vs_currency: str = "usd",
        ttl_seconds: int = 90,
        symbol: str = "STBU",
        client: HttpJson | None = None,
        coingecko_key: str | None = None,
        cmc_key: str | None = None,
    ) -> None:
        self.coingecko_id = coingecko_id
        self.cmc_symbol = cmc_symbol
        self.cmc_id = str(cmc_id) if cmc_id not in (None, "") else None
        self.vs = (vs_currency or "usd").lower()
        self.ttl = max(15, int(ttl_seconds))
        self.symbol = symbol
        self._client = client                 # injected → owned by caller (tests)
        self.coingecko_key = coingecko_key or None
        self.cmc_key = cmc_key or None
        self._cache: MarketSnapshot | None = None
        self._fetched_at = 0.0                 # time.monotonic() of last good fetch
        self._neg_until = 0.0                  # backoff deadline after a failure
        self._lock = None                      # asyncio.Lock, created lazily

    @classmethod
    def from_config(cls, config, client: HttpJson | None = None) -> MarketData:
        def g(key, default):
            return config.get(f"market.{key}", default)

        return cls(
            coingecko_id=g("coingecko_id", "stobox-token"),
            cmc_symbol=g("cmc_symbol", "STBU"),
            cmc_id=g("cmc_id", None),
            vs_currency=g("vs_currency", "usd"),
            ttl_seconds=int(g("ttl_seconds", 90)),
            symbol=g("symbol", "STBU"),
            client=client,
            coingecko_key=os.environ.get("COINGECKO_API_KEY"),
            cmc_key=os.environ.get("COINMARKETCAP_API_KEY"),
        )

    async def snapshot(self, *, force: bool = False) -> MarketSnapshot | None:
        import asyncio

        now = time.monotonic()
        if not force and self._cache and (now - self._fetched_at) < self.ttl:
            return self._cache
        if not force and now < self._neg_until:
            return self._cache                 # in backoff — serve last good (maybe None)

        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            now = time.monotonic()
            if not force and self._cache and (now - self._fetched_at) < self.ttl:
                return self._cache             # another coroutine just refreshed
            client = self._client or HttpxJson()
            owns = self._client is None
            try:
                snap = await self._fetch(client)
            finally:
                if owns:
                    await client.aclose()
            if snap:
                self._cache = snap
                self._fetched_at = time.monotonic()
            else:
                self._neg_until = time.monotonic() + 60.0
            return self._cache

    # -- providers ---------------------------------------------------------- #
    async def _fetch(self, client: HttpJson) -> MarketSnapshot | None:
        snap = await self._fetch_coingecko(client)
        if snap:
            return snap
        if self.cmc_key:
            return await self._fetch_cmc(client)
        return None

    async def _fetch_coingecko(self, client: HttpJson) -> MarketSnapshot | None:
        base = COINGECKO_PRO_BASE if self.coingecko_key else COINGECKO_BASE
        headers = {"x-cg-pro-api-key": self.coingecko_key} if self.coingecko_key else None
        params = {
            "ids": self.coingecko_id,
            "vs_currencies": self.vs,
            "include_market_cap": "true",
            "include_24hr_vol": "true",
            "include_24hr_change": "true",
            "include_last_updated_at": "true",
        }
        data = await client.get_json(f"{base}/simple/price", params=params, headers=headers)
        if not isinstance(data, dict):
            return None
        row = data.get(self.coingecko_id)
        if not isinstance(row, dict):
            return None
        price = _optf(row.get(self.vs))
        if price is None:
            return None
        ts = row.get("last_updated_at")
        return MarketSnapshot(
            price_usd=price,
            market_cap_usd=_optf(row.get(f"{self.vs}_market_cap")),
            volume_24h_usd=_optf(row.get(f"{self.vs}_24h_vol")),
            change_24h_pct=_optf(row.get(f"{self.vs}_24h_change")),
            source="CoinGecko",
            as_of=_iso_from_epoch(ts) if ts else _now_iso(),
            symbol=self.symbol,
        )

    async def _fetch_cmc(self, client: HttpJson) -> MarketSnapshot | None:
        headers = {"X-CMC_PRO_API_KEY": self.cmc_key, "Accept": "application/json"}
        params: dict[str, str] = {"convert": self.vs.upper()}
        if self.cmc_id:
            params["id"] = self.cmc_id
        else:
            params["symbol"] = self.cmc_symbol
        data = await client.get_json(
            f"{CMC_BASE}/cryptocurrency/quotes/latest", params=params, headers=headers
        )
        if not isinstance(data, dict):
            return None
        payload = data.get("data")
        if not isinstance(payload, dict):
            return None
        # `data` is keyed by id or symbol; symbol values may be a list (dupes).
        entry: Any = None
        if self.cmc_id and self.cmc_id in payload:
            entry = payload[self.cmc_id]
        else:
            entry = payload.get(self.cmc_symbol) or next(iter(payload.values()), None)
        if isinstance(entry, list):
            entry = entry[0] if entry else None
        if not isinstance(entry, dict):
            return None
        quote = (entry.get("quote") or {}).get(self.vs.upper())
        if not isinstance(quote, dict):
            return None
        price = _optf(quote.get("price"))
        if price is None:
            return None
        return MarketSnapshot(
            price_usd=price,
            market_cap_usd=_optf(quote.get("market_cap")),
            volume_24h_usd=_optf(quote.get("volume_24h")),
            change_24h_pct=_optf(quote.get("percent_change_24h")),
            source="CoinMarketCap",
            as_of=quote.get("last_updated") or _now_iso(),
            symbol=self.symbol,
        )
