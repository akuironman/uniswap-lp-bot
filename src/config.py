"""Konfigurasi bot LP Uniswap V3 — Robinhood Chain only, strategi Yunus.

Bot ini SENGAJA khusus Robinhood Chain (chainId 4663, native = ETH). Multi-chain
support dibuang supaya konfigurasinya sederhana dan tidak ada risiko bot ngirim
tx ke chain yang salah.
"""
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)


def _i(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return int(default)


def _b(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).lower() in ("1", "true", "yes", "y")


def _list(name: str) -> list[int]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return []
    out: list[int] = []
    for x in raw.split(","):
        x = x.strip()
        if not x:
            continue
        try:
            out.append(int(x))
        except ValueError:
            pass
    return out


@dataclass
class Config:
    # ── Telegram ────────────────────────────────────────────────
    tg_token: str = os.getenv("TELEGRAM_TOKEN", "")
    tg_allowed: list[int] = None  # type: ignore  # filled in __post_init__
    tg_notify_chat: int = _i("NOTIFY_CHAT_ID", 0)

    # ── Robinhood Chain (chainId 4663) ──────────────────────────
    # Public RPC + native ETH. Chain ID diverifikasi dari chainid.network.
    rpc_url: str = os.getenv("RPC_URL", "https://rpc.mainnet.chain.robinhood.com")
    chain_id: int = _i("CHAIN_ID", 4663)

    # ── Wallet ──────────────────────────────────────────────────
    private_key: str = os.getenv("PRIVATE_KEY", "")
    wallet_address: str = os.getenv("WALLET_ADDRESS", "")

    # ── Uniswap V3 contracts di Robinhood Chain ────────────────
    # WETH terverifikasi dari pair Uniswap V3 real di DexScreener (base
    # token dari pool IF/WETH: 0x39A200...953).
    weth_address: str = os.getenv("WETH_ADDRESS", "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73")
    # NPM (NonfungiblePositionManager) & UniversalRouter untuk Uniswap V3
    # di Robinhood. Terverifikasi via on-chain lookup (Mint & swap event
    # analysis di block explorer). Bisa di-override lewat env.
    # UniversalRouter dipakai untuk swap (execute() + encoded commands),
    # menggantikan SwapRouter02 klasik.
    npm_address: str = os.getenv(
        "NPM_ADDRESS", "0x73991a25C818Bf1f1128dEAaB1492D45638DE0D3"
    )
    # Terima "UNIVERSAL_ROUTER_ADDRESS" ATAU legacy "SWAP_ROUTER_ADDRESS"
    universal_router_address: str = os.getenv(
        "UNIVERSAL_ROUTER_ADDRESS",
        os.getenv("SWAP_ROUTER_ADDRESS", "0x8876789976dEcBfCbBbe364623C63652db8C0904"),
    )

    # ── Screening (Yunus filters) ──────────────────────────────
    # Default longgar untuk Robinhood chain — kebanyakan microcap.
    min_mcap: float = _f("MIN_MARKET_CAP", 50_000)
    min_age_hours: float = _f("MIN_AGE_HOURS", 6)
    max_age_hours: float = _f("MAX_AGE_HOURS", 72)
    min_volume_24h: float = _f("MIN_VOLUME_24H", 50_000)
    min_liquidity_usd: float = _f("MIN_LIQUIDITY_USD", 10_000)
    min_holders: int = _i("MIN_HOLDERS", 200)

    # ── LP strategy ─────────────────────────────────────────────
    # Sizing: kalau POSITION_SIZE_ETH > 0 dipakai apa adanya. Kalau 0, bot
    # coba konversi POSITION_SIZE_USD lewat ETH/USD dari DexScreener.
    # Recommended untuk Robinhood: pakai POSITION_SIZE_ETH langsung, karena
    # native token = ETH dan user biasanya tahu jumlah ETH-nya.
    position_size_usd: float = _f("POSITION_SIZE_USD", 200)
    position_size_eth: float = _f("POSITION_SIZE_ETH", 0)
    max_active_positions: int = _i("MAX_ACTIVE_POSITIONS", 3)
    fee_tier: int = _i("FEE_TIER", 3000)  # 0.3%
    range_width_pct: float = _f("RANGE_WIDTH_PCT", 15)
    auto_rebalance: bool = _b("AUTO_REBALANCE", True)
    take_profit_pct: float = _f("TAKE_PROFIT_PCT", 30)
    stop_loss_pct: float = _f("STOP_LOSS_PCT", 25)
    # Slippage tolerance saat swap ETH → target token (basis points, 100 = 1%).
    swap_slippage_bps: int = _i("SWAP_SLIPPAGE_BPS", 200)

    # ── APIs ────────────────────────────────────────────────────
    dexscreener_api: str = os.getenv("DEXSCREENER_API", "https://api.dexscreener.com")

    # ── Loop ────────────────────────────────────────────────────
    scan_interval_sec: int = _i("SCAN_INTERVAL_SEC", 60)

    # ── Trading mode ────────────────────────────────────────────
    # dry_run=True → simulasi (tidak nyentuh chain). Set DRY_RUN=false di .env
    # untuk LIVE. Default True demi keamanan. Bot tetap dry-run kalau
    # PRIVATE_KEY / NPM_ADDRESS / UNIVERSAL_ROUTER_ADDRESS belum di-set.
    dry_run: bool = _b("DRY_RUN", True)

    # ── Web dashboard ───────────────────────────────────────────
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = _i("PORT", 8080)
    dashboard_enabled: bool = _b("DASHBOARD_ENABLED", True)
    dashboard_public_url: str = os.getenv("DASHBOARD_PUBLIC_URL", "")

    def __post_init__(self) -> None:
        if self.tg_allowed is None:
            self.tg_allowed = _list("ALLOWED_CHAT_IDS")


CONFIG = Config()
