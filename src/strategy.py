"""Strategy engine — orkestrasi screener → planner → executor.

Strategy Yunus:
  1. Screen 24h GMGN-style (mcap>500k, umur multi-hours, single-side stable)
  2. Ambil top-3 kandidat, "spray" masing-masing 1/3 modal
  3. LP range tight ±15% dari harga saat ini (multi-hours DCA-style)
  4. Auto rebalance kalau harga keluar range >5%
  5. Take profit 30% / stop loss 25% (fee earned + IL adjusted)
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from .config import CONFIG
from .screener import TokenCandidate, screen
from .uniswap import Position, UniswapExecutor

LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "trades.jsonl"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)


def _size_usd_to_eth_wei(size_usd: float) -> int:
    """Convert USD position size → wei ETH (Robinhood chain native = ETH).

    Prefers POSITION_SIZE_ETH from .env when set (most reliable — user knows the
    exact ETH amount, no oracle needed). Otherwise divides USD by an ETH/USD
    rate we fetch from DexScreener (best-effort). Raises with a clear message
    if neither works instead of silently guessing.
    """
    if CONFIG.position_size_eth and CONFIG.position_size_eth > 0:
        return int(CONFIG.position_size_eth * 10**18)

    # Fetch ETH/USD rate from Ethereum mainnet WETH pair (deepest liquidity).
    import httpx
    price = None
    try:
        r = httpx.get(
            "https://api.dexscreener.com/latest/dex/tokens/"
            "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
            timeout=10.0,
        )
        r.raise_for_status()
        pairs = r.json().get("pairs") or []
        pairs.sort(key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0), reverse=True)
        if pairs:
            price = float(pairs[0].get("priceUsd") or 0) or None
    except Exception:
        price = None

    if not price or price <= 0:
        raise RuntimeError(
            f"Tidak bisa dapat harga ETH/USD untuk konversi POSITION_SIZE_USD (${size_usd}) → wei ETH. "
            f"Solusi: set POSITION_SIZE_ETH=<jumlah> di .env (mis. 0.05)."
        )
    return int((size_usd / price) * 10**18)


@dataclass
class BotState:
    running: bool = False
    dry_run: bool = field(default_factory=lambda: CONFIG.dry_run)
    last_scan_ts: float = 0
    candidates: list[TokenCandidate] = field(default_factory=list)
    positions: dict[str, Position] = field(default_factory=dict)
    pnl_usd: float = 0.0
    total_deployed_usd: float = 0.0
    stats: dict[str, Any] = field(default_factory=lambda: {
        "scans": 0,
        "positions_opened": 0,
        "positions_closed": 0,
        "rebalances": 0,
        "errors": 0,
    })


class LPBot:
    """Bot engine. Panggil start() untuk loop kontinu; stop() untuk pause."""

    def __init__(self, on_event: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.state = BotState()
        # Single-chain (Robinhood) — cukup satu executor.
        self.executor = UniswapExecutor()
        self._on_event = on_event or (lambda _e: None)
        self._task: asyncio.Task | None = None

    # ---- pub-sub ----
    def emit(self, kind: str, payload: dict[str, Any]) -> None:
        evt = {"kind": kind, "ts": time.time(), **payload}
        try:
            with LOG_PATH.open("a") as f:
                f.write(json.dumps(evt, default=str) + "\n")
        except OSError:
            pass
        try:
            self._on_event(evt)
        except Exception:
            pass

    # ---- main loop ----
    async def start(self, dry_run: bool | None = None) -> None:
        if self.state.running:
            return
        # None → pakai default dari .env (CONFIG.dry_run). Telegram /go live &
        # dashboard toggle tetap bisa override eksplisit.
        if dry_run is None:
            dry_run = CONFIG.dry_run
        self.state.running = True
        self.state.dry_run = dry_run
        self.emit("bot.start", {"dry_run": dry_run, "config": {
            "min_mcap": CONFIG.min_mcap,
            "position_size_usd": CONFIG.position_size_usd,
            "max_positions": CONFIG.max_active_positions,
            "range_width_pct": CONFIG.range_width_pct,
        }})
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self.state.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.emit("bot.stop", {})

    async def _loop(self) -> None:
        while self.state.running:
            try:
                await self._tick()
            except Exception as e:
                self.state.stats["errors"] += 1
                self.emit("error", {"msg": str(e)})
            await asyncio.sleep(max(10, CONFIG.scan_interval_sec))

    async def _tick(self) -> None:
        # 1. Screen
        self.emit("scan.start", {})
        cands = await screen()
        self.state.candidates = cands
        self.state.last_scan_ts = time.time()
        self.state.stats["scans"] += 1
        self.emit("scan.done", {"count": len(cands), "top": [c.to_dict() for c in cands[:5]]})

        # 2. Manage existing positions (rebalance / close)
        await self._manage_positions()

        # 3. Open new positions kalau slot kosong
        open_slots = CONFIG.max_active_positions - len(self.state.positions)
        if open_slots <= 0:
            return

        for cand in cands[:open_slots * 2]:  # pilih top-N, skip yang sudah ada
            if cand.pair_address in self.state.positions:
                continue
            await self._open_position(cand)
            open_slots -= 1
            if open_slots <= 0:
                break

    async def _open_position(self, cand: TokenCandidate) -> None:
        size_usd = CONFIG.position_size_usd
        executor = self.executor

        # Dry-run OR executor not ready (missing addresses / no PK) → simulate.
        if self.state.dry_run or not executor.can_trade():
            reason = None
            if not self.state.dry_run:
                missing = executor.missing_addresses()
                reason = (
                    f"missing addresses: {missing} — isi di .env untuk enable live"
                    if missing else "PK/RPC not ready — falling back to dry-run"
                )
            pos = Position(
                token_id=int(time.time() * 1000),
                symbol=cand.symbol,
                pair_address=cand.pair_address,
                tick_lower=-887220,
                tick_upper=887220,
                liquidity=0,
                entry_price=cand.price_usd,
                entry_ts=time.time(),
                size_usd=size_usd,
                chain=cand.chain,
            )
            self.state.positions[cand.pair_address] = pos
            self.state.total_deployed_usd += size_usd
            self.state.stats["positions_opened"] += 1
            payload = {"mode": "dry", "pos": pos.to_dict(), "cand": cand.to_dict()}
            if reason:
                payload["skip_reason"] = reason
            self.emit("position.open", payload)
            return

        # ─── Live path (Yunus single-side): ETH → target token → mint ─────
        # 1. Convert USD size → wei ETH via candidate price hint (price of target in USD
        #    is not the ETH price; we need an ETH/USD source). Sederhananya: minta user
        #    set POSITION_SIZE_ETH langsung, atau infer via native symbol=ETH → assume
        #    1 ETH ≈ $3000 as a *very* rough fallback. Better: pull ETH/USD dari
        #    DexScreener WETH pair. Untuk sekarang pakai ENV override kalau ada.
        try:
            eth_amount_wei = _size_usd_to_eth_wei(size_usd)

            # 2. Swap ETH → target token
            self.emit("position.swap.start", {
                "symbol": cand.symbol, "chain": cand.chain,
                "eth_wei": eth_amount_wei, "target": cand.address,
            })
            swap = await asyncio.to_thread(
                executor.swap_native_to_token,
                target_token=cand.address,
                amount_in_wei=eth_amount_wei,
                fee=CONFIG.fee_tier,
                slippage_bps=CONFIG.swap_slippage_bps,
            )
            self.emit("position.swap.done", {
                "symbol": cand.symbol, "amount_out": swap.get("amount_out"),
                "swap_tx": swap.get("swap_tx"),
            })

            # 3. Plan + broadcast mint. Yunus = single-side, jadi kita provide the
            #    token (bukan WETH). token0/token1 ordering diambil dari pool state.
            plan = await asyncio.to_thread(
                executor.plan_mint,
                pool_address=cand.pair_address,
                amount0_desired=0,     # akan di-flip di bawah kalau target = token0
                amount1_desired=0,
                width_pct=CONFIG.range_width_pct,
            )
            token_out = int(swap["amount_out"])
            target_lc = cand.address.lower()
            if plan["token0"].lower() == target_lc:
                plan["amount0Desired"] = token_out
            else:
                plan["amount1Desired"] = token_out

            self.emit("position.plan", {"plan": plan, "cand": cand.to_dict()})
            result = await asyncio.to_thread(executor.execute_mint, plan)
            if result.get("status") != 1:
                raise RuntimeError(f"mint tx reverted (status={result.get('status')})")

            pos = Position(
                token_id=int(time.time() * 1000),
                symbol=cand.symbol,
                pair_address=cand.pair_address,
                tick_lower=int(plan["tickLower"]),
                tick_upper=int(plan["tickUpper"]),
                liquidity=0,
                entry_price=cand.price_usd,
                entry_ts=time.time(),
                size_usd=size_usd,
                chain=cand.chain,
            )
            self.state.positions[cand.pair_address] = pos
            self.state.total_deployed_usd += size_usd
            self.state.stats["positions_opened"] += 1
            self.emit("position.open", {
                "mode": "live", "pos": pos.to_dict(), "cand": cand.to_dict(),
                "swap_tx": swap.get("swap_tx"), "mint_tx": result.get("tx_hash"),
            })
        except Exception as e:
            self.state.stats["errors"] += 1
            self.emit("position.open.fail", {"symbol": cand.symbol, "err": str(e)})

    async def _manage_positions(self) -> None:
        to_close: list[str] = []
        for pair_addr, pos in self.state.positions.items():
            # Cari harga sekarang dari kandidat terbaru
            cur = next((c for c in self.state.candidates if c.pair_address == pair_addr), None)
            if not cur:
                continue

            change = (cur.price_usd - pos.entry_price) / max(pos.entry_price, 1e-12) * 100
            age_min = (time.time() - pos.entry_ts) / 60

            if change >= CONFIG.take_profit_pct:
                to_close.append(pair_addr)
                self.emit("position.tp", {"symbol": pos.symbol, "change": change, "age_min": age_min})
            elif change <= -CONFIG.stop_loss_pct:
                to_close.append(pair_addr)
                self.emit("position.sl", {"symbol": pos.symbol, "change": change, "age_min": age_min})
            elif abs(change) > CONFIG.range_width_pct and CONFIG.auto_rebalance:
                # Range breach → rebalance
                pos.entry_price = cur.price_usd
                pos.entry_ts = time.time()
                self.state.stats["rebalances"] += 1
                self.emit("position.rebalance", {"symbol": pos.symbol, "new_price": cur.price_usd})

        for k in to_close:
            pos = self.state.positions.pop(k, None)
            if pos:
                cur = next((c for c in self.state.candidates if c.pair_address == k), None)
                if cur:
                    pnl = (cur.price_usd - pos.entry_price) / pos.entry_price * pos.size_usd
                    self.state.pnl_usd += pnl
                self.state.stats["positions_closed"] += 1

                # Live: unwind the on-chain position (decrease + collect + burn).
                close_tx = None
                if not self.state.dry_run and self.executor.can_trade() and pos.token_id:
                    try:
                        res = await asyncio.to_thread(self.executor.close_position, pos.token_id)
                        close_tx = res.get("txs")
                    except Exception as e:
                        self.state.stats["errors"] += 1
                        self.emit("error", {"msg": f"close {pos.symbol} failed: {e}"})

                payload = {"pos": pos.to_dict()}
                if close_tx:
                    payload["tx"] = close_tx
                self.emit("position.close", payload)

    def snapshot(self) -> dict[str, Any]:
        return {
            "running": self.state.running,
            "dry_run": self.state.dry_run,
            "last_scan_ts": self.state.last_scan_ts,
            "pnl_usd": round(self.state.pnl_usd, 2),
            "total_deployed_usd": round(self.state.total_deployed_usd, 2),
            "candidates": [c.to_dict() for c in self.state.candidates[:10]],
            "positions": [p.to_dict() for p in self.state.positions.values()],
            "stats": self.state.stats,
            "config": {
                "min_mcap": CONFIG.min_mcap,
                "min_age_hours": CONFIG.min_age_hours,
                "max_age_hours": CONFIG.max_age_hours,
                "min_volume_24h": CONFIG.min_volume_24h,
                "position_size_usd": CONFIG.position_size_usd,
                "max_active_positions": CONFIG.max_active_positions,
                "range_width_pct": CONFIG.range_width_pct,
                "take_profit_pct": CONFIG.take_profit_pct,
                "stop_loss_pct": CONFIG.stop_loss_pct,
            },
        }
