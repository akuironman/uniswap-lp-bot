"""Konfigurasi bot LP Uniswap — strategi Yunus (GMGN screener + single-side stable)."""
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
    # Telegram
    tg_token: str = os.getenv("TELEGRAM_TOKEN", "")
    tg_allowed: list[int] = None  # type: ignore  # filled in __post_init__
    tg_notify_chat: int = _i("NOTIFY_CHAT_ID", 0)

    # RPC
    rpc_url: str = os.getenv("RPC_URL", "https://eth.llamarpc.com")
    base_rpc_url: str = os.getenv("BASE_RPC_URL", "https://mainnet.base.org")

    # Wallet
    private_key: str = os.getenv("PRIVATE_KEY", "")
    wallet_address: str = os.getenv("WALLET_ADDRESS", "")

    # Contracts
    npm_address: str = os.getenv("NPM_ADDRESS", "0xC36442b4a4522E871399CD717aBDD847Ab11FE88")

    # Screening (Yunus filters). Default longgar untuk Robinhood Chain (mostly microcap);
    # naikkan MIN_MARKET_CAP=500000 & MIN_VOLUME_24H=100000 kalau target Ethereum/Base.
    min_mcap: float = _f("MIN_MARKET_CAP", 50_000)
    min_age_hours: float = _f("MIN_AGE_HOURS", 6)
    max_age_hours: float = _f("MAX_AGE_HOURS", 72)
    min_volume_24h: float = _f("MIN_VOLUME_24H", 50_000)
    min_liquidity_usd: float = _f("MIN_LIQUIDITY_USD", 10_000)
    min_holders: int = _i("MIN_HOLDERS", 200)

    # Strategy
    position_size_usd: float = _f("POSITION_SIZE_USD", 200)
    max_active_positions: int = _i("MAX_ACTIVE_POSITIONS", 3)
    fee_tier: int = _i("FEE_TIER", 3000)  # 0.3%
    range_width_pct: float = _f("RANGE_WIDTH_PCT", 15)
    auto_rebalance: bool = _b("AUTO_REBALANCE", True)
    take_profit_pct: float = _f("TAKE_PROFIT_PCT", 30)
    stop_loss_pct: float = _f("STOP_LOSS_PCT", 25)

    # APIs
    gmgn_api: str = os.getenv("GMGN_API", "https://gmgn.ai/defi/quotation/v1")
    dexscreener_api: str = os.getenv("DEXSCREENER_API", "https://api.dexscreener.com/latest")

    # Loop
    scan_interval_sec: int = _i("SCAN_INTERVAL_SEC", 60)

    # Web dashboard (FastAPI + WebSocket)
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = _i("PORT", 8080)
    dashboard_enabled: bool = _b("DASHBOARD_ENABLED", True)
    # Public URL for the Telegram "DASHBOARD" button (e.g. https://xxx.ngrok.io).
    # Leave empty if you only access the dashboard locally (localhost isn't
    # clickable from a phone).
    dashboard_public_url: str = os.getenv("DASHBOARD_PUBLIC_URL", "")

    def __post_init__(self) -> None:
        if self.tg_allowed is None:
            self.tg_allowed = _list("ALLOWED_CHAT_IDS")


CONFIG = Config()
