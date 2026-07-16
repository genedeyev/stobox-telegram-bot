"""Live STBU market-data provider tests (offline, injected HTTP client)."""

from __future__ import annotations

import pytest

from stobox_ai.market import MarketData, MarketSnapshot

CG_OK = {
    "stobox-token": {
        "usd": 0.00104287,
        "usd_market_cap": 130358.9,
        "usd_24h_vol": 63.68,
        "usd_24h_change": -3.98,
        "last_updated_at": 1784190614,
    }
}


class FakeHttp:
    """Maps a URL substring → JSON payload; records call count. Returns None
    (a failed fetch) for anything unmatched."""

    def __init__(self, routes: dict[str, object]) -> None:
        self.routes = routes
        self.calls = 0

    async def get_json(self, url, params=None, headers=None):
        self.calls += 1
        for frag, payload in self.routes.items():
            if frag in url:
                return payload
        return None

    async def aclose(self):
        pass


@pytest.mark.asyncio
async def test_coingecko_snapshot_parsed():
    http = FakeHttp({"/simple/price": CG_OK})
    md = MarketData(client=http, ttl_seconds=90)
    snap = await md.snapshot()
    assert isinstance(snap, MarketSnapshot)
    assert snap.source == "CoinGecko"
    assert snap.price_usd == pytest.approx(0.00104287)
    assert snap.market_cap_usd == pytest.approx(130358.9)
    assert snap.volume_24h_usd == pytest.approx(63.68)
    assert snap.change_24h_pct == pytest.approx(-3.98)
    # 2026 epoch → sane year in the as-of string.
    assert snap.as_of.startswith("2026")


@pytest.mark.asyncio
async def test_snapshot_is_cached_within_ttl():
    http = FakeHttp({"/simple/price": CG_OK})
    md = MarketData(client=http, ttl_seconds=90)
    await md.snapshot()
    await md.snapshot()
    await md.snapshot()
    assert http.calls == 1                 # served from cache, no re-fetch


@pytest.mark.asyncio
async def test_format_line_is_grounded_and_disclaimed():
    snap = (await MarketData(client=FakeHttp({"/simple/price": CG_OK})).snapshot())
    line = snap.format_line()
    assert "STBU live market price" in line and "CoinGecko" in line
    assert "not investment advice" in line.lower()
    assert "eqvista company valuation" in line.lower()   # must distinguish the two


@pytest.mark.asyncio
async def test_format_report_includes_contracts_and_framing():
    snap = await MarketData(client=FakeHttp({"/simple/price": CG_OK})).snapshot()
    report = snap.format_report(contracts={"ethereum": "0xabc", "base": "0xdef"})
    assert "0xabc" in report and "0xdef" in report
    assert "not investment advice" in report.lower()
    assert "not</b> the Stobox" in report or "not the Stobox" in report.lower()


@pytest.mark.asyncio
async def test_failed_fetch_returns_none_and_backs_off():
    http = FakeHttp({})                    # nothing matches → every fetch fails
    md = MarketData(client=http, ttl_seconds=90)
    assert await md.snapshot() is None
    # Second call is inside the 60s negative-cache window → no new HTTP call.
    assert await md.snapshot() is None
    assert http.calls == 1


@pytest.mark.asyncio
async def test_cmc_fallback_when_coingecko_empty():
    cmc_payload = {
        "data": {
            "STBU": {
                "quote": {
                    "USD": {
                        "price": 0.00105,
                        "market_cap": 131000.0,
                        "volume_24h": 70.0,
                        "percent_change_24h": 1.25,
                        "last_updated": "2026-07-16T08:30:00Z",
                    }
                }
            }
        }
    }
    # CoinGecko returns None (no /simple/price route); CMC route matches.
    http = FakeHttp({"cryptocurrency/quotes/latest": cmc_payload})
    md = MarketData(client=http, cmc_key="test-key", ttl_seconds=90)
    snap = await md.snapshot()
    assert snap is not None
    assert snap.source == "CoinMarketCap"
    assert snap.price_usd == pytest.approx(0.00105)
    assert snap.change_24h_pct == pytest.approx(1.25)


@pytest.mark.asyncio
async def test_no_cmc_fallback_without_key():
    # CoinGecko empty and no CMC key → None (never calls CMC).
    http = FakeHttp({"cryptocurrency/quotes/latest": {"data": {"STBU": {}}}})
    md = MarketData(client=http, ttl_seconds=90)
    assert await md.snapshot() is None
