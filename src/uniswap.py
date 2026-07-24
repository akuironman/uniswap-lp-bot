"""Executor Uniswap V3 — single-side stable LP dengan range tight.

Fungsi utama:
  * build_position(): siapkan tick range dari harga saat ini ± range_width_pct
  * mint_position(): panggil NonfungiblePositionManager.mint()
  * close_position(): decreaseLiquidity + collect + burn
  * rebalance(): close + re-mint pada range baru

Semua transaksi disiapkan lalu di-sign lokal (never leak PK ke RPC).
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

from web3 import Web3
from web3.contract import Contract

from .config import CONFIG

# ── Minimal ERC20 ABI (approve / allowance / decimals / balanceOf) ──
ERC20_ABI = [
    {"constant": False, "inputs": [{"name": "spender", "type": "address"}, {"name": "value", "type": "uint256"}],
     "name": "approve", "outputs": [{"name": "", "type": "bool"}], "stateMutability": "nonpayable", "type": "function"},
    {"constant": True, "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
     "name": "allowance", "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"constant": True, "inputs": [], "name": "decimals",
     "outputs": [{"name": "", "type": "uint8"}], "stateMutability": "view", "type": "function"},
    {"constant": True, "inputs": [{"name": "owner", "type": "address"}], "name": "balanceOf",
     "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
]

# Minimal ABI untuk NonfungiblePositionManager: mint + decreaseLiquidity + collect + burn + positions
NPM_ABI = [
    {
        "inputs": [{
            "components": [
                {"internalType": "address", "name": "token0", "type": "address"},
                {"internalType": "address", "name": "token1", "type": "address"},
                {"internalType": "uint24", "name": "fee", "type": "uint24"},
                {"internalType": "int24", "name": "tickLower", "type": "int24"},
                {"internalType": "int24", "name": "tickUpper", "type": "int24"},
                {"internalType": "uint256", "name": "amount0Desired", "type": "uint256"},
                {"internalType": "uint256", "name": "amount1Desired", "type": "uint256"},
                {"internalType": "uint256", "name": "amount0Min", "type": "uint256"},
                {"internalType": "uint256", "name": "amount1Min", "type": "uint256"},
                {"internalType": "address", "name": "recipient", "type": "address"},
                {"internalType": "uint256", "name": "deadline", "type": "uint256"},
            ],
            "internalType": "struct INonfungiblePositionManager.MintParams",
            "name": "params",
            "type": "tuple",
        }],
        "name": "mint",
        "outputs": [
            {"internalType": "uint256", "name": "tokenId", "type": "uint256"},
            {"internalType": "uint128", "name": "liquidity", "type": "uint128"},
            {"internalType": "uint256", "name": "amount0", "type": "uint256"},
            {"internalType": "uint256", "name": "amount1", "type": "uint256"},
        ],
        "stateMutability": "payable",
        "type": "function",
    },
    {
        "inputs": [{
            "components": [
                {"internalType": "uint256", "name": "tokenId", "type": "uint256"},
                {"internalType": "uint128", "name": "liquidity", "type": "uint128"},
                {"internalType": "uint256", "name": "amount0Min", "type": "uint256"},
                {"internalType": "uint256", "name": "amount1Min", "type": "uint256"},
                {"internalType": "uint256", "name": "deadline", "type": "uint256"},
            ],
            "internalType": "struct INonfungiblePositionManager.DecreaseLiquidityParams",
            "name": "params",
            "type": "tuple",
        }],
        "name": "decreaseLiquidity",
        "outputs": [
            {"internalType": "uint256", "name": "amount0", "type": "uint256"},
            {"internalType": "uint256", "name": "amount1", "type": "uint256"},
        ],
        "stateMutability": "payable",
        "type": "function",
    },
    {
        "inputs": [{
            "components": [
                {"internalType": "uint256", "name": "tokenId", "type": "uint256"},
                {"internalType": "address", "name": "recipient", "type": "address"},
                {"internalType": "uint128", "name": "amount0Max", "type": "uint128"},
                {"internalType": "uint128", "name": "amount1Max", "type": "uint128"},
            ],
            "internalType": "struct INonfungiblePositionManager.CollectParams",
            "name": "params",
            "type": "tuple",
        }],
        "name": "collect",
        "outputs": [
            {"internalType": "uint256", "name": "amount0", "type": "uint256"},
            {"internalType": "uint256", "name": "amount1", "type": "uint256"},
        ],
        "stateMutability": "payable",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "uint256", "name": "tokenId", "type": "uint256"}],
        "name": "burn", "outputs": [], "stateMutability": "payable", "type": "function",
    },
    {
        "inputs": [{"internalType": "uint256", "name": "tokenId", "type": "uint256"}],
        "name": "positions",
        "outputs": [
            {"internalType": "uint96", "name": "nonce", "type": "uint96"},
            {"internalType": "address", "name": "operator", "type": "address"},
            {"internalType": "address", "name": "token0", "type": "address"},
            {"internalType": "address", "name": "token1", "type": "address"},
            {"internalType": "uint24", "name": "fee", "type": "uint24"},
            {"internalType": "int24", "name": "tickLower", "type": "int24"},
            {"internalType": "int24", "name": "tickUpper", "type": "int24"},
            {"internalType": "uint128", "name": "liquidity", "type": "uint128"},
            {"internalType": "uint256", "name": "feeGrowthInside0LastX128", "type": "uint256"},
            {"internalType": "uint256", "name": "feeGrowthInside1LastX128", "type": "uint256"},
            {"internalType": "uint128", "name": "tokensOwed0", "type": "uint128"},
            {"internalType": "uint128", "name": "tokensOwed1", "type": "uint128"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]

_UINT128_MAX = (1 << 128) - 1

POOL_ABI = [
    {
        "inputs": [],
        "name": "slot0",
        "outputs": [
            {"internalType": "uint160", "name": "sqrtPriceX96", "type": "uint160"},
            {"internalType": "int24", "name": "tick", "type": "int24"},
            {"internalType": "uint16", "name": "observationIndex", "type": "uint16"},
            {"internalType": "uint16", "name": "observationCardinality", "type": "uint16"},
            {"internalType": "uint16", "name": "observationCardinalityNext", "type": "uint16"},
            {"internalType": "uint8", "name": "feeProtocol", "type": "uint8"},
            {"internalType": "bool", "name": "unlocked", "type": "bool"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "tickSpacing",
        "outputs": [{"internalType": "int24", "name": "", "type": "int24"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "token0",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "token1",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
]


@dataclass
class Position:
    token_id: int
    symbol: str
    pair_address: str
    tick_lower: int
    tick_upper: int
    liquidity: int
    entry_price: float
    entry_ts: float
    size_usd: float
    chain: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_id": self.token_id,
            "symbol": self.symbol,
            "pair_address": self.pair_address,
            "tick_lower": self.tick_lower,
            "tick_upper": self.tick_upper,
            "liquidity": str(self.liquidity),
            "entry_price": self.entry_price,
            "entry_ts": self.entry_ts,
            "age_min": round((time.time() - self.entry_ts) / 60, 1),
            "size_usd": self.size_usd,
            "chain": self.chain,
        }


def _price_to_tick(price: float, decimals0: int, decimals1: int) -> int:
    """Konversi harga (token1 per token0) ke tick Uniswap V3."""
    adj = price * (10 ** decimals1) / (10 ** decimals0)
    if adj <= 0:
        return 0
    return int(math.log(adj) / math.log(1.0001))


def _nearest_valid_tick(tick: int, spacing: int) -> int:
    return (tick // spacing) * spacing


def compute_range(current_tick: int, tick_spacing: int, width_pct: float) -> tuple[int, int]:
    """Hitung tickLower & tickUpper untuk range ±width_pct% dari harga sekarang."""
    # 1 tick = 1.0001x harga → n ticks untuk width%
    n_ticks = int(math.log(1 + width_pct / 100) / math.log(1.0001))
    lower = _nearest_valid_tick(current_tick - n_ticks, tick_spacing)
    upper = _nearest_valid_tick(current_tick + n_ticks, tick_spacing)
    if upper <= lower:
        upper = lower + tick_spacing
    return lower, upper


class UniswapExecutor:
    """Executor V3 — dry-run friendly, hanya broadcast kalau PK di-set."""

    def __init__(self, rpc_url: str | None = None) -> None:
        self.w3 = Web3(Web3.HTTPProvider(rpc_url or CONFIG.rpc_url))
        self.npm: Contract | None = None
        if CONFIG.npm_address:
            self.npm = self.w3.eth.contract(
                address=Web3.to_checksum_address(CONFIG.npm_address),
                abi=NPM_ABI,
            )
        # Derive account from private key (never logged / never sent to RPC).
        self.account = None
        if CONFIG.private_key:
            try:
                self.account = self.w3.eth.account.from_key(CONFIG.private_key)
            except Exception:
                self.account = None

    def connected(self) -> bool:
        try:
            return self.w3.is_connected()
        except Exception:
            return False

    def can_trade(self) -> bool:
        """True only when we have an unlocked account + live RPC + NPM contract."""
        return bool(self.account and self.npm and self.connected())

    @property
    def address(self) -> str | None:
        if self.account:
            return self.account.address
        if CONFIG.wallet_address:
            return Web3.to_checksum_address(CONFIG.wallet_address)
        return None

    def get_pool_state(self, pool_address: str) -> dict[str, Any]:
        pool = self.w3.eth.contract(
            address=Web3.to_checksum_address(pool_address),
            abi=POOL_ABI,
        )
        slot0 = pool.functions.slot0().call()
        spacing = pool.functions.tickSpacing().call()
        token0 = pool.functions.token0().call()
        token1 = pool.functions.token1().call()
        return {
            "sqrt_price_x96": slot0[0],
            "tick": slot0[1],
            "tick_spacing": spacing,
            "token0": token0,
            "token1": token1,
        }

    def plan_mint(
        self,
        pool_address: str,
        amount0_desired: int,
        amount1_desired: int,
        width_pct: float | None = None,
        recipient: str | None = None,
    ) -> dict[str, Any]:
        """Persiapkan mint params (tidak broadcast). Return dict siap tanda-tangan."""
        width = width_pct if width_pct is not None else CONFIG.range_width_pct
        state = self.get_pool_state(pool_address)
        lower, upper = compute_range(state["tick"], state["tick_spacing"], width)
        return {
            "pool": pool_address,
            "token0": state["token0"],
            "token1": state["token1"],
            "fee": CONFIG.fee_tier,
            "tickLower": lower,
            "tickUpper": upper,
            "amount0Desired": amount0_desired,
            "amount1Desired": amount1_desired,
            "amount0Min": 0,
            "amount1Min": 0,
            "recipient": recipient or self.address,
            "deadline": int(time.time()) + 600,
            "currentTick": state["tick"],
        }

    # ── Live execution (only runs when can_trade() is True) ──────────────
    #
    # ⚠️ These broadcast real on-chain transactions. Test with a SMALL
    #    position_size_usd first. The bot stays in dry-run unless you start
    #    it with `/go live` and PRIVATE_KEY is set.

    def _build_tx(self, fn, value: int = 0) -> dict[str, Any]:
        """Assemble a signed-ready tx dict from a contract function call."""
        addr = self.address
        base = {
            "from": addr,
            "nonce": self.w3.eth.get_transaction_count(addr),
            "chainId": self.w3.eth.chain_id,
            "value": value,
        }
        # EIP-1559 when the node supports it, else legacy gasPrice.
        try:
            latest = self.w3.eth.get_block("latest")
            base_fee = latest.get("baseFeePerGas")
            if base_fee is not None:
                prio = self.w3.to_wei(1.5, "gwei")
                base["maxPriorityFeePerGas"] = prio
                base["maxFeePerGas"] = base_fee * 2 + prio
            else:
                base["gasPrice"] = self.w3.eth.gas_price
        except Exception:
            base["gasPrice"] = self.w3.eth.gas_price
        tx = fn.build_transaction(base)
        try:
            tx["gas"] = int(self.w3.eth.estimate_gas(tx) * 1.25)
        except Exception:
            tx["gas"] = 600_000  # safe fallback for mint
        return tx

    def _sign_send(self, tx: dict[str, Any]) -> str:
        """Sign locally with the private key and broadcast. Returns tx hash hex."""
        signed = self.w3.eth.account.sign_transaction(tx, CONFIG.private_key)
        raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
        tx_hash = self.w3.eth.send_raw_transaction(raw)
        return tx_hash.hex()

    def ensure_allowance(self, token: str, amount: int) -> str | None:
        """Approve NPM to pull `amount` of `token` if current allowance is short."""
        if not self.can_trade() or amount <= 0:
            return None
        erc20 = self.w3.eth.contract(address=Web3.to_checksum_address(token), abi=ERC20_ABI)
        spender = Web3.to_checksum_address(CONFIG.npm_address)
        current = erc20.functions.allowance(self.address, spender).call()
        if current >= amount:
            return None
        tx = self._build_tx(erc20.functions.approve(spender, 2**256 - 1))
        return self._sign_send(tx)

    def execute_mint(self, plan: dict[str, Any]) -> dict[str, Any]:
        """Broadcast a mint from a plan produced by plan_mint(). Returns receipt info."""
        if not self.can_trade():
            raise RuntimeError("executor not ready for live trades (need PRIVATE_KEY + live RPC)")
        approvals = []
        if plan["amount0Desired"] > 0:
            h = self.ensure_allowance(plan["token0"], plan["amount0Desired"])
            if h:
                approvals.append(h)
        if plan["amount1Desired"] > 0:
            h = self.ensure_allowance(plan["token1"], plan["amount1Desired"])
            if h:
                approvals.append(h)
        # Wait for approvals to land before minting.
        for h in approvals:
            self.w3.eth.wait_for_transaction_receipt(h, timeout=180)

        params = (
            Web3.to_checksum_address(plan["token0"]),
            Web3.to_checksum_address(plan["token1"]),
            int(plan["fee"]),
            int(plan["tickLower"]),
            int(plan["tickUpper"]),
            int(plan["amount0Desired"]),
            int(plan["amount1Desired"]),
            int(plan.get("amount0Min", 0)),
            int(plan.get("amount1Min", 0)),
            Web3.to_checksum_address(plan["recipient"]),
            int(plan["deadline"]),
        )
        tx = self._build_tx(self.npm.functions.mint(params))
        tx_hash = self._sign_send(tx)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=240)
        return {
            "tx_hash": tx_hash,
            "status": int(receipt.get("status", 0)),
            "block": receipt.get("blockNumber"),
            "approvals": approvals,
        }

    def close_position(self, token_id: int) -> dict[str, Any]:
        """decreaseLiquidity(full) → collect(all) → burn. Returns tx hashes."""
        if not self.can_trade():
            raise RuntimeError("executor not ready for live trades (need PRIVATE_KEY + live RPC)")
        pos = self.npm.functions.positions(int(token_id)).call()
        liquidity = int(pos[7])
        deadline = int(time.time()) + 600
        txs: dict[str, str] = {}

        if liquidity > 0:
            dec = self.npm.functions.decreaseLiquidity(
                (int(token_id), liquidity, 0, 0, deadline)
            )
            h = self._sign_send(self._build_tx(dec))
            self.w3.eth.wait_for_transaction_receipt(h, timeout=240)
            txs["decrease"] = h

        # Collect owed tokens (principal + fees) to our wallet.
        col = self.npm.functions.collect(
            (int(token_id), self.address, _UINT128_MAX, _UINT128_MAX)
        )
        h = self._sign_send(self._build_tx(col))
        self.w3.eth.wait_for_transaction_receipt(h, timeout=240)
        txs["collect"] = h

        # Burn the now-empty NFT.
        try:
            h = self._sign_send(self._build_tx(self.npm.functions.burn(int(token_id))))
            txs["burn"] = h
        except Exception:
            pass  # burn fails if not fully empty; non-fatal
        return {"txs": txs}
