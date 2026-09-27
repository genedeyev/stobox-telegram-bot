"""Read-only chain reads for /check. Which contracts to read comes from the site.

The live STBU contract and the discontinued legacy contracts are parsed from the
site's "STBU, the token" section at request time; nothing is hardcoded here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

from .site import SiteFacts

ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
PRIVATE_KEY = re.compile(r"^(0x)?[0-9a-fA-F]{64}$")
RPC = {
    "Base": "https://mainnet.base.org",
    "Ethereum": "https://ethereum-rpc.publicnode.com",
    "BNB Chain": "https://bsc-dataseed.binance.org",
    "Polygon": "https://polygon-bor-rpc.publicnode.com",
    "Arbitrum": "https://arb1.arbitrum.io/rpc",
}
_BALANCE_OF, _DECIMALS = "0x70a08231", "0x313ce567"
_LEGACY = re.compile(r"(Ethereum|BNB Chain|Polygon|Arbitrum)\s+`(0x[0-9a-fA-F]{40})`")
_LIVE = re.compile(r"Canonical contract:\s+`(0x[0-9a-fA-F]{40})`\s+on Base")


class ChainDown(Exception):
    pass


@dataclass
class Holding:
    chain: str
    contract: str
    balance: float | None          # None = the RPC could not be read


def contracts_from_site(site: SiteFacts) -> tuple[str | None, dict[str, str]]:
    text = site.sections.get("STBU, the token", "")
    live = _LIVE.search(text)
    legacy = {name: addr for name, addr in _LEGACY.findall(text)}
    return (live.group(1) if live else None), legacy


def is_address(s: str) -> bool:
    return bool(ADDRESS.match(s.strip()))


def is_private_key(s: str) -> bool:
    t = s.strip()
    return bool(PRIVATE_KEY.match(t)) and not is_address(t)


class ChainReader:
    def __init__(self, rpc: dict[str, str] | None = None, timeout_s: float = 8.0,
                 client: httpx.AsyncClient | None = None) -> None:
        self.rpc = {**RPC, **(rpc or {})}
        self.timeout = timeout_s
        self.client = client

    async def _call(self, client: httpx.AsyncClient, chain: str, to: str, data: str) -> int | None:
        try:
            r = await client.post(self.rpc[chain], timeout=self.timeout, json={
                "jsonrpc": "2.0", "id": 1, "method": "eth_call",
                "params": [{"to": to, "data": data}, "latest"]})
            res = r.json().get("result")
        except (httpx.HTTPError, ValueError, KeyError):
            return None
        if res in (None, ""):
            return None
        return 0 if res in ("0x", "0x0") else int(res, 16)

    async def balances(self, wallet: str, live: str, legacy: dict[str, str]) -> list[Holding]:
        client = self.client or httpx.AsyncClient()
        out: list[Holding] = []
        try:
            for chain, contract in [("Base", live), *legacy.items()]:
                raw = await self._call(client, chain, contract,
                                       _BALANCE_OF + wallet[2:].lower().rjust(64, "0"))
                if raw is None:
                    out.append(Holding(chain, contract, None))
                    continue
                dec = await self._call(client, chain, contract, _DECIMALS)
                dec = dec if dec and 0 < dec <= 36 else 18
                out.append(Holding(chain, contract, raw / 10**dec))
        finally:
            if self.client is None:
                await client.aclose()
        return out
