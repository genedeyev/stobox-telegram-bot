"""STBU wallet migration checker — read-only balance lookup.

Given a PUBLIC wallet address, calls ``balanceOf`` on the eligible STBU token
contracts across each supported chain via public JSON-RPC (no keys, no writes,
never touches a private key). Returns where the user holds STBU so Stoby can
give the exact migration path. The RPC client is injectable so the logic is
unit-tested offline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from ..logging import get_logger

log = get_logger(__name__)

_ADDR = re.compile(r"^0x[0-9a-fA-F]{40}$")
_PRIVKEY = re.compile(r"^0x?[0-9a-fA-F]{64}$")

_BALANCEOF = "0x70a08231"      # balanceOf(address)
_DECIMALS = "0x313ce567"       # decimals()

CHAIN_LABELS = {
    "ethereum": "Ethereum", "bnb_chain": "BNB Chain",
    "polygon": "Polygon", "arbitrum": "Arbitrum", "base": "Base",
}
DEFAULT_RPC = {
    "ethereum": "https://eth.llamarpc.com",
    "bnb_chain": "https://bsc-dataseed.binance.org",
    "polygon": "https://polygon-rpc.com",
    "arbitrum": "https://arb1.arbitrum.io/rpc",
}


def is_address(s: str) -> bool:
    return bool(_ADDR.match(s.strip()))


def is_private_key(s: str) -> bool:
    """A 64-hex string is a private key, NOT an address — never accept it."""
    t = s.strip()
    return bool(_PRIVKEY.match(t)) and not is_address(t)


@dataclass(slots=True)
class Holding:
    chain: str
    label: str
    balance: float
    contract: str
    ok: bool = True          # False if the RPC read failed


class RpcClient(Protocol):
    async def call(self, url: str, to: str, data: str) -> str | None: ...
    async def aclose(self) -> None: ...


class HttpxRpc:
    def __init__(self, timeout: float = 12.0) -> None:
        import httpx

        self._client = httpx.AsyncClient(timeout=timeout)

    async def call(self, url: str, to: str, data: str) -> str | None:
        payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                   "params": [{"to": to, "data": data}, "latest"]}
        try:
            r = await self._client.post(url, json=payload)
            return (r.json() or {}).get("result")
        except Exception as exc:  # noqa: BLE001
            log.warning("chain.rpc_failed", url=url, error=str(exc))
            return None

    async def aclose(self) -> None:
        await self._client.aclose()


def _balanceof_data(address: str) -> str:
    return _BALANCEOF + address[2:].lower().rjust(64, "0")


def _to_int(hexstr: str | None) -> int | None:
    if hexstr is None:
        return None                       # RPC error — distinct from zero balance
    if hexstr in ("0x", "0x0", ""):
        return 0
    try:
        return int(hexstr, 16)
    except ValueError:
        return None


class WalletChecker:
    def __init__(self, contracts: dict[str, str], rpc: dict[str, str] | None = None,
                 client: RpcClient | None = None) -> None:
        self.contracts = {k: v for k, v in (contracts or {}).items() if v}
        self.rpc = {**DEFAULT_RPC, **(rpc or {})}
        self.client = client
        self._decimals: dict[str, int] = {}

    async def check(self, address: str) -> list[Holding]:
        if not is_address(address):
            raise ValueError("not a valid wallet address")
        client = self.client or HttpxRpc()
        owns = self.client is None
        holdings: list[Holding] = []
        try:
            for chain, contract in self.contracts.items():
                rpc = self.rpc.get(chain)
                label = CHAIN_LABELS.get(chain, chain)
                if not rpc:
                    continue
                raw = await client.call(rpc, contract, _balanceof_data(address))
                bal = _to_int(raw)
                if bal is None:
                    holdings.append(Holding(chain, label, 0.0, contract, ok=False))
                    continue
                dec = await self._get_decimals(client, chain, contract, rpc)
                holdings.append(Holding(chain, label, bal / (10 ** dec), contract, ok=True))
        finally:
            if owns:
                await client.aclose()
        return holdings

    async def _get_decimals(self, client, chain, contract, rpc) -> int:
        if chain in self._decimals:
            return self._decimals[chain]
        raw = await client.call(rpc, contract, _DECIMALS)
        val = _to_int(raw)
        dec = val if (val and 0 < val <= 36) else 18   # STBU is 18; default safe
        self._decimals[chain] = dec
        return dec
