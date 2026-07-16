"""Live market-data feed for STBU (price, market cap, 24h volume).

Off-chain price feeds (CoinGecko primary, CoinMarketCap fallback) — distinct from
``chain/`` which does on-chain balance reads. The snapshot is cached and injected
into the [FRESHNESS] block so Stoby can state STBU's *current* market price as a
grounded fact, and powers the /price command.
"""

from __future__ import annotations

from .prices import HttpxJson, MarketData, MarketSnapshot

__all__ = ["MarketData", "MarketSnapshot", "HttpxJson"]
