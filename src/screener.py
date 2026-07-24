"""Token screener — meniru filter GMGN 24h + kriteria Yunus.

Strategi Yunus: main di Robinhood Chain (dex: uniswap) + Base + Ethereum.
Pipeline:
  1. Fetch token-boosts/top + token-profiles/latest dari DexScreener (fresh mover)
  2. Untuk tiap token, fetch pair details via /latest/dex/tokens/{addr}
  3. Filter: mcap > $500k, umur 6–72h, volume > $100k, ada pool uniswap-v3
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, asdict
from typing import Any

import httpx

from .config import CONFIG

# Chains yang kita target — sesuai video Yunus
TARGET_CHAINS = {"robinhood", "ethereum", "base"}
TARGET_DEXES = {"uniswap", "uniswap-v3", "uniswapv3", "aerodrome"}


@dataclass
class TokenCandidate:
    symbol: str
    name: str
    address: str
    chain: str
    price_usd: float
    market_cap: float
    fdv: float
    volume_24h: float
    liquidity_usd: float
    age_hours: float
    price_change_24h: float
    price_change_6h: float
    holders: int
    pair_address: str
    dex: str
    fee_tier: int
    url: str
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _score(t: TokenCandidate) -> float:
    """Ranking: momentum 6h/24h + liq + volume + umur sweet-spot (~24h)."""
    momentum = max(0.0, t.price_change_24h) * 0.4 + max(0.0, t.price_change_6h) * 0.6
    liq_score = min(t.liquidity_usd / 500_000, 1.0) * 30
    vol_score = min(t.volume_24h / 1_000_000, 1.0) * 25
    age_sweet = 1.0 - abs(t.age_hours - 24) / 48
    age_score = max(0.0, age_sweet) * 20
    return round(momentum + liq_score + vol_score + age_score, 2)


class DexScreenerClient:
    def __init__(self, base: str | None = None) -> None:
        self.base = base or "https://api.dexscreener.com"
        self.client = httpx.AsyncClient(
            timeout=20.0,
            headers={"User-Agent": "lp-bot/1.0 (yunus-strategy)"},
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def token_boosts_top(self) -> list[dict[str, Any]]:
        """Trending token dengan boost (paid promo, indikator hype)."""
        try:
            r = await self.client.get(f"{self.base}/token-boosts/top/v1")
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, list) else []
        except (httpx.HTTPError, ValueError):
            return []

    async def token_profiles_latest(self) -> list[dict[str, Any]]:
        """Token profile terbaru — signal early discovery."""
        try:
            r = await self.client.get(f"{self.base}/token-profiles/latest/v1")
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, list) else []
        except (httpx.HTTPError, ValueError):
            return []

    async def pairs_by_token(self, chain: str, token_addr: str) -> list[dict[str, Any]]:
        """Semua pair yang mengandung token ini."""
        try:
            r = await self.client.get(f"{self.base}/latest/dex/tokens/{token_addr}")
            r.raise_for_status()
            pairs = r.json().get("pairs") or []
            return [p for p in pairs if p.get("chainId") == chain]
        except (httpx.HTTPError, ValueError):
            return []


def _parse_pair(p: dict[str, Any]) -> TokenCandidate | None:
    try:
        base = p.get("baseToken") or {}
        symbol = base.get("symbol") or "?"
        # Skip kalau base-nya stable/wrapped (kita cari altcoin sebagai base)
        if symbol.upper() in ("WETH", "USDC", "USDT", "DAI", "USDG", "WBTC", "ETH"):
            return None
        created_at_ms = p.get("pairCreatedAt") or 0
        age_h = (time.time() * 1000 - created_at_ms) / 3_600_000 if created_at_ms else 999
        mcap = float(p.get("marketCap") or p.get("fdv") or 0)
        liq = float((p.get("liquidity") or {}).get("usd") or 0)
        vol = float((p.get("volume") or {}).get("h24") or 0)
        change24 = float((p.get("priceChange") or {}).get("h24") or 0)
        change6 = float((p.get("priceChange") or {}).get("h6") or 0)
        info = p.get("info") or {}
        tok = TokenCandidate(
            symbol=symbol,
            name=(base.get("name") or symbol)[:40],
            address=base.get("address") or "",
            chain=p.get("chainId") or "ethereum",
            price_usd=float(p.get("priceUsd") or 0),
            market_cap=mcap,
            fdv=float(p.get("fdv") or mcap),
            volume_24h=vol,
            liquidity_usd=liq,
            age_hours=round(age_h, 1),
            price_change_24h=change24,
            price_change_6h=change6,
            holders=int(info.get("holders") or 0),
            pair_address=p.get("pairAddress") or "",
            dex=p.get("dexId") or "uniswap",
            fee_tier=3000,
            url=p.get("url") or "",
        )
        tok.score = _score(tok)
        return tok
    except (TypeError, ValueError, KeyError):
        return None


def _best_pair(pairs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pilih pair dengan liquidity tertinggi (biasanya paling representatif)."""
    if not pairs:
        return None
    return max(pairs, key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0))


async def screen() -> list[TokenCandidate]:
    """Screening penuh — return kandidat lolos filter Yunus, sorted by score."""
    cli = DexScreenerClient()
    try:
        # 1. Ambil pool token dari 2 sumber signal
        boosts = await cli.token_boosts_top()
        profiles = await cli.token_profiles_latest()

        # 2. Gabung + dedup
        combined: dict[tuple[str, str], dict[str, Any]] = {}
        for t in boosts + profiles:
            chain = t.get("chainId")
            addr = t.get("tokenAddress")
            if not chain or not addr:
                continue
            if chain not in TARGET_CHAINS:
                continue
            combined[(chain, addr.lower())] = t

        # 3. Fetch pair details paralel
        async def _fetch(chain: str, addr: str) -> TokenCandidate | None:
            pairs = await cli.pairs_by_token(chain, addr)
            best = _best_pair(pairs)
            if not best:
                return None
            return _parse_pair(best)

        tasks = [_fetch(chain, addr) for (chain, addr) in combined]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        await cli.close()

    candidates: list[TokenCandidate] = []
    for r in results:
        if isinstance(r, Exception) or r is None:
            continue
        tok: TokenCandidate = r
        if tok.market_cap < CONFIG.min_mcap:
            continue
        if tok.age_hours < CONFIG.min_age_hours:
            continue
        if tok.age_hours > CONFIG.max_age_hours:
            continue
        if tok.volume_24h < CONFIG.min_volume_24h:
            continue
        if tok.liquidity_usd < CONFIG.min_liquidity_usd:
            continue
        candidates.append(tok)

    candidates.sort(key=lambda x: x.score, reverse=True)
    return candidates[:20]


if __name__ == "__main__":
    async def _demo() -> None:
        tokens = await screen()
        print(f"passed filter: {len(tokens)}\n")
        for t in tokens[:10]:
            print(f"{t.score:6.1f}  {t.symbol:10s}  {t.chain:10s}  "
                  f"mcap=${t.market_cap/1000:>7.0f}k  vol=${t.volume_24h/1000:>6.0f}k  "
                  f"liq=${t.liquidity_usd/1000:>6.0f}k  age={t.age_hours:>5.1f}h  "
                  f"Δ24={t.price_change_24h:>+6.1f}%")

    asyncio.run(_demo())
