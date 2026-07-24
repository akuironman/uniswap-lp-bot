# 💎 LP HUNTER — Telegram Bot + Web Dashboard

Bot auto-LP Uniswap V3 :

> screening 24h → single-side stable → multi-hours → spray 3 pair → auto rebalance

Dua muka kontrol, **satu mesin bersama**:
- **Telegram** — command + inline buttons + push notif otomatis
- **Web dashboard** — realtime (WebSocket): KPI, tabel candidate, kartu posisi + PnL live, live feed

Scan yang kamu trigger dari web langsung kelihatan di Telegram, dan sebaliknya — keduanya pakai instance `LPBot` yang sama.

---

## Quickstart

**1. Bikin bot Telegram**

Chat [@BotFather](https://t.me/BotFather) → `/newbot` → kasih nama → copy token.

**2. Ambil chat_id kamu**

Chat [@userinfobot](https://t.me/userinfobot) → copy ID kamu.

**3. Setup**

```bash
cd uniswap-lp-bot
cp .env.example .env
# edit .env:
#   TELEGRAM_TOKEN=<token dari botfather>
#   NOTIFY_CHAT_ID=<chat_id kamu>
#   ALLOWED_CHAT_IDS=<chat_id kamu>   (opsional, batasi akses)
bash run.sh
```

**4. Chat bot kamu di Telegram**

Kirim `/start` → langsung muncul menu inline.

**5. Buka dashboard (opsional)**

`bash run.sh` otomatis nyalain dashboard di `http://localhost:8080`.
Akses dari HP lewat Tailscale / ngrok / LAN IP, atau matikan dengan `DASHBOARD_ENABLED=false`.

---

## Web dashboard

Realtime via WebSocket — nyala otomatis bareng Telegram bot (satu proses, satu state).

- **KPI cards**: PnL 24h, deployed, scans, signals, rebalances
- **Candidates**: tabel top signal dengan score, mcap, liq, momentum, link chart
- **Positions**: kartu posisi + PnL live vs entry + progress bar ke TP/SL
- **Live feed**: stream event (scan, open, TP, SL, rebalance, close)

Kontrol dari dashboard: SCAN NOW, START/STOP, toggle DRY RUN.
API: `GET /api/state`, `GET /api/health`, `POST /api/scan`, `POST /api/start?dry_run=…`, `POST /api/stop`, `POST /api/config`.

Buat tombol **🖥 DASHBOARD** di menu Telegram muncul, set `DASHBOARD_PUBLIC_URL` ke URL publik (ngrok/tailscale/domain).

---

## Commands

| Command | Fungsi |
|---------|--------|
| `/start` | Menu utama dengan tombol inline |
| `/scan` | Scan sekarang, tampilkan top 10 candidate |
| `/go` | Start bot loop (dry-run default) |
| `/go live` | Start loop dengan on-chain execution (butuh PRIVATE_KEY) |
| `/stop` | Stop loop |
| `/status` | PNL, deployed, stats lengkap |
| `/positions` | List posisi aktif + Δ dari entry |
| `/config` | Filter & strategi saat ini |
| `/set K V` | Ubah config runtime (contoh: `/set position_size_usd 500`) |
| `/help` | Daftar command + tunable keys |

**Config tunable via `/set`:**
`min_mcap`, `min_age_hours`, `max_age_hours`, `min_volume_24h`, `min_liquidity_usd`,
`position_size_usd`, `max_active_positions`, `range_width_pct`,
`take_profit_pct`, `stop_loss_pct`, `scan_interval_sec`, `auto_rebalance`

---

## Push notifications

Kalau `NOTIFY_CHAT_ID` di-set, bot auto-kirim ke chat itu:

- 🎯 setiap scan (top 3 signal)
- ✅ position opened
- ⟳ rebalance
- 💰 take profit
- 🩸 stop loss
- ✕ position closed
- ⚠ error

---

## Config penting (.env)

```
TELEGRAM_TOKEN=...
NOTIFY_CHAT_ID=123456789
ALLOWED_CHAT_IDS=123456789      # kosongin = akses bebas (tidak disarankan)

MIN_MARKET_CAP=50000            # Robinhood Chain / microcap
                                # Ethereum/Base: naikkan ke 500000
MIN_AGE_HOURS=6
MAX_AGE_HOURS=72
MIN_VOLUME_24H=50000
MIN_LIQUIDITY_USD=10000

POSITION_SIZE_USD=200
MAX_ACTIVE_POSITIONS=3          # spray 3 pair
RANGE_WIDTH_PCT=15
TAKE_PROFIT_PCT=30
STOP_LOSS_PCT=25

SCAN_INTERVAL_SEC=60
```

---

## Struktur

```
main.py               # entry — Telegram bot + dashboard di 1 event loop, 1 LPBot
src/
  config.py           # env → dataclass, hot-tunable (+ host/port/dashboard)
  screener.py         # DexScreener → filter GMGN-style
  uniswap.py          # V3 pool + tick math + mint/close executor (live-ready)
  strategy.py         # scan → open → manage → close
  telegram_bot.py     # command handlers + inline UI + push notif
  server.py           # FastAPI: REST API + WebSocket + serve dashboard
static/
  index.html          # dashboard markup
  style.css           # neon dark theme
  app.js              # WebSocket realtime + render
run.sh                # setup venv + install + launch
logs/trades.jsonl     # event log lengkap
```

---

## Live trading

Bot default **dry-run** (simulasi, tidak nyentuh chain). Untuk live:

1. Isi `PRIVATE_KEY` + `WALLET_ADDRESS` di `.env`
2. Fund wallet dengan USDC/WETH di jaringan target
3. Chat bot: `/go live`
4. Monitor lewat push notif / dashboard

**Executor live** (`src/uniswap.py`) sekarang benar-benar broadcast on-chain:
- `execute_mint()` — auto-approve token → `mint()` NonfungiblePositionManager
- `close_position()` — `decreaseLiquidity` → `collect` → `burn`
- Sign lokal (private key tidak pernah dikirim ke RPC), EIP-1559 gas otomatis

> ⚠️ **BELUM diuji end-to-end dengan dana nyata di dalam sesi ini** — jalur live butuh
> private key + wallet berdana + RPC hidup untuk diverifikasi. **Wajib** coba dengan
> `POSITION_SIZE_USD` kecil (mis. $5–10) di satu pair dulu sebelum naikin ukuran.
> Catatan: konversi single-side (swap USDC→token sesuai side) belum dilakukan otomatis —
> saat ini mint pakai amount0 (USDC) langsung. New pair Uniswap = risiko rug/dump tinggi.

---

## Extend

- **Tambah chain**: edit `TARGET_CHAINS` di `src/screener.py`
- **Ganti data source**: implement client baru di `src/screener.py`
- **Custom exit strategy**: edit `LPBot._manage_positions` di `src/strategy.py`
- **Single-side swap**: tambahkan langkah swap USDC→token di `execute_mint` (lihat komentar `src/uniswap.py`)
