"""STBU wallet migration checker tests (offline, injected RPC)."""

from __future__ import annotations

import pytest

from stobox_ai.chain import WalletChecker, is_address, is_private_key

CONTRACTS = {
    "ethereum": "0xa6422e3e219ee6d4c1b18895275fe43556fd50ed",
    "polygon": "0xcf403036bc139d30080d2cf0f5b48066f98191bb",
}
WALLET = "0x1111111111111111111111111111111111111111"


class FakeRpc:
    """Maps (url, data-prefix) → hex result. balanceOf=0x70a0, decimals=0x313c."""

    def __init__(self, balances: dict[str, int], decimals: int = 18) -> None:
        self.balances = balances          # chain-url → raw integer balance
        self.decimals = decimals
        self.calls = 0

    async def call(self, url, to, data):
        self.calls += 1
        if data.startswith("0x313ce567"):                 # decimals()
            return hex(self.decimals)
        if data.startswith("0x70a08231"):                 # balanceOf()
            return hex(self.balances.get(url, 0))
        return "0x0"

    async def aclose(self):
        pass


def test_address_and_key_validation():
    assert is_address(WALLET)
    assert not is_address("0x123")               # too short
    assert not is_address("hello")
    # 64-hex is a private key, never an address.
    key = "0x" + "a" * 64
    assert is_private_key(key) and not is_address(key)
    assert not is_private_key(WALLET)


@pytest.mark.asyncio
async def test_checker_reports_balances_and_decimals():
    rpc = FakeRpc(balances={
        "https://eth.llamarpc.com": 5_000 * 10**18,
        "https://polygon-rpc.com": 0,
    })
    checker = WalletChecker(CONTRACTS, client=rpc)
    holdings = await checker.check(WALLET)
    by_chain = {h.chain: h for h in holdings}
    assert by_chain["ethereum"].balance == 5000.0 and by_chain["ethereum"].ok
    assert by_chain["polygon"].balance == 0.0
    # decimals fetched once per chain and cached.
    assert all(h.ok for h in holdings)


@pytest.mark.asyncio
async def test_checker_marks_rpc_errors():
    class ErrRpc(FakeRpc):
        async def call(self, url, to, data):
            if "polygon" in url:
                return None                        # simulate RPC failure
            return await super().call(url, to, data)
    rpc = ErrRpc(balances={"https://eth.llamarpc.com": 10**18})
    holdings = await WalletChecker(CONTRACTS, client=rpc).check(WALLET)
    poly = next(h for h in holdings if h.chain == "polygon")
    assert not poly.ok


@pytest.mark.asyncio
async def test_checker_rejects_bad_address():
    with pytest.raises(ValueError):
        await WalletChecker(CONTRACTS, client=FakeRpc({})).check("not-an-address")


@pytest.mark.asyncio
async def test_engine_check_wallet_composes_report(config):
    from stobox_ai.core.engine import AgentEngine

    engine = await AgentEngine.create(config)
    # Private key → compromise warning, never a balance read.
    warn = await engine.check_wallet("0x" + "b" * 64)
    assert "private key" in warn.lower() and "compromised" in warn.lower()
    # Garbage → guidance.
    assert "0x" in (await engine.check_wallet("nope")).lower()
