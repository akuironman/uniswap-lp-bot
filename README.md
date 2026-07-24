# 🩸 ROBINHOOD LP HUNTER — Telegram Bot + Web Dashboard

Bot auto-LP **khusus Uniswap V3 di Robinhood Chain** (chainId 4663, native ETH), pakai strategi [@0xyunss](https://x.com/0xyunss/status/2078140396182651195):

> screening 24h → single-side ETH → multi-hours → spray 3 pair → auto rebalance

Bot ini **single-chain** — dirancang khusus untuk pengguna yang punya ETH di Robinhood Chain. Bot otomatis:
1. Screen token mover dari DexScreener (chain=robinhood, DEX=Uniswap V3)
2. Swap ETH → target token via Uniswap V3 SwapRouter (slippage guard)
3. LP posisi single-side dengan range tight ±`RANGE_WIDTH_PCT`
4. TP/SL/rebalance otomatis, close = decreaseLiquidity + collect + burn

Dua muka kontrol, **satu mesin bersama**:
- **Telegram** — command + inline buttons + push notif otomatis
- **Web dashboard** — realtime WebSocket: KPI, tabel candidate, kartu posisi + PnL live

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

**4. Chat bot di Telegram** — kirim `/start`.

**5. Buka dashboard** — otomatis di `http://localhost:8080`.

---

## Commands (Telegram)

| Command | Fungsi |
|---------|--------|
| `/start` | Menu utama dengan tombol inline |
| `/scan` | Scan sekarang, tampilkan top 10 candidate |
| `/go` | Start loop (ikut `DRY_RUN` di `.env`) |
| `/go live` | Paksa LIVE on-chain (butuh PRIVATE_KEY + NPM + SwapRouter) |
| `/go dry` | Paksa dry-run (simulasi) |
| `/stop` | Stop loop |
| `/status` | PNL, deployed, stats lengkap |
| `/positions` | List posisi aktif + Δ dari entry + PnL bar |
| `/config` | Filter & strategi saat ini |
| `/set K V` | Ubah config runtime (contoh: `/set position_size_eth 0.05`) |
| `/help` | Daftar command + tunable keys |

**Config tunable via `/set`:**
`min_mcap`, `min_age_hours`, `max_age_hours`, `min_volume_24h`, `min_liquidity_usd`,
`position_size_usd`, `position_size_eth`, `max_active_positions`, `range_width_pct`,
`take_profit_pct`, `stop_loss_pct`, `scan_interval_sec`, `auto_rebalance`

---

## Web dashboard

Realtime via WebSocket — nyala otomatis bareng Telegram bot (satu proses, satu state).

- **KPI cards**: PnL 24h, deployed, scans, signals, rebalances
- **Candidates**: tabel top signal dengan score, mcap, liq, momentum, link chart
- **Positions**: kartu posisi + PnL live vs entry + progress bar ke TP/SL
- **Live feed**: stream event (scan, swap, open, TP, SL, rebalance, close)

Kontrol dari dashboard: SCAN NOW, START/STOP, toggle DRY RUN.
API: `GET /api/state`, `GET /api/health`, `POST /api/scan`, `POST /api/start?dry_run=…`, `POST /api/stop`, `POST /api/config`.

Buat tombol **🖥 DASHBOARD** di menu Telegram muncul, set `DASHBOARD_PUBLIC_URL` ke URL publik (ngrok/tailscale/domain).

---

## Config penting (`.env`)

```
# Chain (khusus Robinhood — hardcoded)
RPC_URL=https://rpc.mainnet.chain.robinhood.com
CHAIN_ID=4663

# Wallet
PRIVATE_KEY=          # kosong = dry-run only
WALLET_ADDRESS=

# Uniswap V3 di Robinhood
WETH_ADDRESS=0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73  # verified
NPM_ADDRESS=          # ← WAJIB isi buat LIVE
SWAP_ROUTER_ADDRESS=  # ← WAJIB isi buat LIVE

# Screening (Yunus filters)
MIN_MARKET_CAP=50000
MIN_AGE_HOURS=6
MAX_AGE_HOURS=72
MIN_VOLUME_24H=50000

# Sizing
POSITION_SIZE_ETH=0     # > 0 pakai ETH langsung (recommended untuk Robinhood)
POSITION_SIZE_USD=200   # fallback pakai oracle ETH/USD
MAX_ACTIVE_POSITIONS=3

# Range & exit
RANGE_WIDTH_PCT=15
TAKE_PROFIT_PCT=30
STOP_LOSS_PCT=25
SWAP_SLIPPAGE_BPS=200   # 2% slippage cap saat swap

# Mode
DRY_RUN=true            # true=simulasi, false=live
```

---

## Live trading — bot khusus Robinhood

Bot **default dry-run**. Untuk aktifkan LIVE:

1. Isi `.env`:
   ```
   DRY_RUN=false
   PRIVATE_KEY=0x...
   WALLET_ADDRESS=0x...
   POSITION_SIZE_ETH=0.05     # rekomendasi kecil dulu buat uji
   NPM_ADDRESS=0x...          # ← cari di robinhoodchain.blockscout.com
   SWAP_ROUTER_ADDRESS=0x...  # ← cari di robinhoodchain.blockscout.com
   ```
2. Fund wallet-mu di Robinhood Chain dengan ETH (buat modal LP + gas).
3. `bash run.sh` → bot mulai live otomatis.

**Ganti mode**:
- Lewat `.env` (persisten): `DRY_RUN=true/false` + restart
- Lewat Telegram (sekali jalan): `/go live` atau `/go dry`
- Lewat dashboard: toggle `DRY RUN` → `START`

### Alur live yang bot jalankan tiap open posisi
1. **Sizing**: `POSITION_SIZE_ETH` (kalau di-set) atau `POSITION_SIZE_USD` dikonversi ke wei ETH via ETH/USD DexScreener
2. **Swap**: `ETH → WETH → target token` via SwapRouter (slippage `SWAP_SLIPPAGE_BPS`)
3. **Mint**: LP posisi single-side dengan token yang baru di-swap, range ±`RANGE_WIDTH_PCT`
4. **Manage**: TP/SL/rebalance seperti biasa; close = `decreaseLiquidity+collect+burn`

### Cara cari NPM & SwapRouter Uniswap V3 di Robinhood Chain

1. Buka https://robinhoodchain.blockscout.com
2. Cari transaksi swap Uniswap V3 (bisa dari pair Uniswap V3 di DexScreener → klik tx hash)
3. Kontrak yang menerima call `exactInputSingle` = **SwapRouter**
4. Kontrak yang menerima call `mint` = **NonfungiblePositionManager**
5. Salin address-nya ke `.env`

### 🔒 Pengaman berlapis
- Kalau `NPM_ADDRESS` / `SWAP_ROUTER_ADDRESS` kosong → bot **otomatis skip live** dan fallback ke dry-run (dengan alasan jelas di feed).
- Kalau `PRIVATE_KEY` kosong → sama, fallback dry-run.
- Slippage guard: `amountOutMinimum` dihitung dari simulasi swap × (1 - `SWAP_SLIPPAGE_BPS/10000`), bukan 0. Bot **menolak** swap dengan expected out = 0 (no pool).

> ⚠️ **BELUM diuji end-to-end dengan dana nyata** — jalur live butuh alamat NPM/SwapRouter yang benar + wallet berdana + RPC live. **Wajib** coba dengan `POSITION_SIZE_ETH=0.01` di **satu pair** dulu sebelum naikin ukuran.

---

## Struktur

```
main.py               # entry — Telegram bot + dashboard di 1 event loop, 1 LPBot
src/
  config.py           # env → dataclass (Robinhood-only)
  screener.py         # DexScreener → filter chain=robinhood
  uniswap.py          # V3 pool + swap + mint/close executor
  strategy.py         # scan → swap → open → manage → close
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

## Extend

- **Custom exit strategy**: edit `LPBot._manage_positions` di `src/strategy.py`
- **Ganti slippage/gas defaults**: `SWAP_SLIPPAGE_BPS` di `.env`, atau edit `_build_tx` di `uniswap.py`
- **Fee tier lain**: `FEE_TIER=500` (0.05%), `10000` (1%)
