"""Telegram bot — LP Hunter Yunus strategy.

Commands:
  /start      — welcome + main menu
  /scan       — trigger scan sekarang, tampilkan candidates
  /go         — start bot loop (dry-run default)
  /stop       — pause bot loop
  /status     — snapshot posisi + PNL + stats
  /positions  — list posisi aktif
  /config     — tampilkan strategi filter
  /set K V    — ubah config runtime (mis. /set position_size_usd 500)
  /help       — bantuan

Notif otomatis (kalau NOTIFY_CHAT_ID di-set):
  🎯 signal baru pass filter
  ✅ posisi opened
  💰 TP kena
  🩸 SL kena
  ⟳ rebalance
"""
from __future__ import annotations

import asyncio
import html
import logging
import time
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .config import CONFIG
from .strategy import LPBot

log = logging.getLogger("lp-hunter.tg")

# ---------- helpers ----------

def fmt_usd(n: float | int | None) -> str:
    if n is None:
        return "$0"
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "$0"
    a = abs(n)
    if a >= 1e9: return f"${n/1e9:.2f}B"
    if a >= 1e6: return f"${n/1e6:.2f}M"
    if a >= 1e3: return f"${n/1e3:.1f}K"
    return f"${n:.2f}"


def fmt_pct(n: float | None) -> str:
    if n is None:
        return "0.00%"
    sign = "+" if n >= 0 else ""
    return f"{sign}{n:.2f}%"


def ago(ts: float | None) -> str:
    if not ts:
        return "belum pernah"
    s = int(time.time() - ts)
    if s < 60: return f"{s}s lalu"
    if s < 3600: return f"{s//60}m lalu"
    return f"{s//3600}h lalu"


SEP = "━━━━━━━━━━━━━━━━━━━━"


def pnl_bar(pct: float, width: int = 10) -> str:
    """Compact filled/empty bar for progress toward a target (0..100%)."""
    p = max(0.0, min(100.0, abs(pct)))
    filled = round(p / 100 * width)
    return "▰" * filled + "▱" * (width - filled)


def main_menu() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("⚡ SCAN", callback_data="scan"),
         InlineKeyboardButton("📊 STATUS", callback_data="status")],
        [InlineKeyboardButton("▶ START", callback_data="go"),
         InlineKeyboardButton("■ STOP", callback_data="stop")],
        [InlineKeyboardButton("💼 POSITIONS", callback_data="positions"),
         InlineKeyboardButton("⚙ CONFIG", callback_data="config")],
    ]
    # Add a one-tap dashboard link when the web UI is enabled.
    if CONFIG.dashboard_enabled and CONFIG.dashboard_public_url:
        rows.append([InlineKeyboardButton("🖥 DASHBOARD", url=CONFIG.dashboard_public_url)])
    return InlineKeyboardMarkup(rows)


def _allowed(chat_id: int) -> bool:
    if not CONFIG.tg_allowed:
        return True
    return chat_id in CONFIG.tg_allowed


# ---------- global bot instance ----------

_lp_bot: LPBot | None = None
_app: Application | None = None


def _notify_event(evt: dict[str, Any]) -> None:
    """Callback dari LPBot → push notif ke Telegram."""
    if not _app or not CONFIG.tg_notify_chat:
        return
    kind = evt.get("kind", "")
    text = _format_event(evt)
    if not text:
        return
    try:
        asyncio.run_coroutine_threadsafe(
            _app.bot.send_message(
                chat_id=CONFIG.tg_notify_chat,
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            ),
            _app.update_queue._loop if hasattr(_app.update_queue, "_loop") else asyncio.get_event_loop(),
        )
    except Exception as e:
        log.warning("notify failed: %s", e)


def _format_event(evt: dict[str, Any]) -> str | None:
    k = evt.get("kind", "")
    if k == "bot.start":
        return f"🟢 <b>BOT STARTED</b>\nmode: {'DRY-RUN' if evt.get('dry_run') else '⚠ LIVE'}"
    if k == "bot.stop":
        return "🔴 <b>BOT STOPPED</b>"
    if k == "scan.done":
        top = evt.get("top") or []
        if not top:
            return None
        lines = ["🎯 <b>SIGNAL SCAN</b>"]
        for t in top[:3]:
            sym = html.escape(str(t.get("symbol", "?")))
            chg = t.get("price_change_24h") or 0
            mc = t.get("market_cap") or 0
            lines.append(f"• <b>{sym}</b> · {fmt_usd(mc)} · Δ24h {fmt_pct(chg)}")
        return "\n".join(lines)
    if k == "position.swap.start":
        sym = html.escape(str(evt.get('symbol', '?')))
        eth_wei = int(evt.get('eth_wei') or 0)
        eth = eth_wei / 1e18
        return (f"🔄 <b>SWAPPING ETH → {sym}</b>\n"
                f"amount: <code>{eth:.6f} ETH</code>  ·  chain: <i>{evt.get('chain')}</i>")
    if k == "position.swap.done":
        sym = html.escape(str(evt.get('symbol', '?')))
        out = evt.get('amount_out')
        return (f"✅ <b>SWAP DONE</b> {sym}\n"
                f"got: <code>{out}</code> raw units")
    if k == "position.open":
        pos = evt.get("pos") or {}
        sym = html.escape(str(pos.get("symbol", "?")))
        mode = evt.get("mode", "live")
        tag = "[DRY]" if mode == "dry" else ""
        return (f"✅ <b>POSITION OPEN {tag}</b>\n"
                f"token: <b>{sym}</b> ({pos.get('chain')})\n"
                f"size: {fmt_usd(pos.get('size_usd'))}\n"
                f"entry: ${pos.get('entry_price', 0):.8f}")
    if k == "position.tp":
        return (f"💰 <b>TAKE PROFIT</b>\n"
                f"{html.escape(str(evt.get('symbol', '?')))} · {fmt_pct(evt.get('change'))} · "
                f"{evt.get('age_min', 0):.0f}m")
    if k == "position.sl":
        return (f"🩸 <b>STOP LOSS</b>\n"
                f"{html.escape(str(evt.get('symbol', '?')))} · {fmt_pct(evt.get('change'))} · "
                f"{evt.get('age_min', 0):.0f}m")
    if k == "position.rebalance":
        return (f"⟳ <b>REBALANCE</b>\n"
                f"{html.escape(str(evt.get('symbol', '?')))} → ${evt.get('new_price', 0):.8f}")
    if k == "position.close":
        pos = evt.get("pos") or {}
        sym = html.escape(str(pos.get("symbol", "?")))
        return f"✕ <b>CLOSED</b> {sym} · age {pos.get('age_min', 0):.0f}m"
    if k == "error":
        return f"⚠ <b>ERROR</b>\n<code>{html.escape(str(evt.get('msg', ''))[:200])}</code>"
    return None


# ---------- command handlers ----------

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not _allowed(update.effective_chat.id):
        return
    txt = (
        "🩸 <b>ROBINHOOD LP HUNTER</b>\n"
        "<i>Uniswap V3 · chain 4663 · single-side ETH · Yunus strategy</i>\n\n"
        "Bot auto-LP khusus <b>Robinhood Chain</b>. Otomatis: swap ETH → target token → "
        "LP tight range → auto rebalance → TP/SL exit.\n\n"
        "Filter default (Yunus):\n"
        f"• mcap > {fmt_usd(CONFIG.min_mcap)}\n"
        f"• umur {CONFIG.min_age_hours}–{CONFIG.max_age_hours}h\n"
        f"• vol 24h > {fmt_usd(CONFIG.min_volume_24h)}\n"
        f"• max {CONFIG.max_active_positions} pair aktif · size {fmt_usd(CONFIG.position_size_usd)}\n\n"
        "Pilih aksi:"
    )
    await update.effective_message.reply_html(txt, reply_markup=main_menu())


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update.effective_chat.id):
        return
    txt = (
        "<b>Commands:</b>\n"
        "/start — menu utama\n"
        "/scan — scan sekarang\n"
        "/go — start loop (ikut DRY_RUN di .env)\n"
        "/go live — paksa LIVE on-chain · /go dry — paksa simulasi\n"
        "/stop — stop loop\n"
        "/status — snapshot lengkap\n"
        "/positions — posisi aktif\n"
        "/config — filter & strategi\n"
        "/set K V — ubah runtime (mis. <code>/set position_size_usd 500</code>)\n"
        "/help — pesan ini\n\n"
        "<b>Setting bisa diubah:</b>\n"
        "min_mcap, min_age_hours, max_age_hours, min_volume_24h, min_liquidity_usd,\n"
        "position_size_usd, max_active_positions, range_width_pct,\n"
        "take_profit_pct, stop_loss_pct, scan_interval_sec"
    )
    await update.effective_message.reply_html(txt)


async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update.effective_chat.id):
        return
    m = await update.effective_message.reply_text("⏳ scanning…")
    try:
        from .screener import screen
        cands = await screen()
        if _lp_bot:
            _lp_bot.state.candidates = cands
            _lp_bot.state.last_scan_ts = time.time()
        if not cands:
            await m.edit_text("🚫 no candidates lolos filter. coba longgarin filter via /set.")
            return
        lines = [f"🎯 <b>{len(cands)} CANDIDATES</b>\n"]
        for c in cands[:10]:
            chg24 = c.price_change_24h or 0
            chg6 = c.price_change_6h or 0
            sym = html.escape(c.symbol[:10])
            emoji = "🚀" if c.score >= 100 else "📈" if c.score >= 30 else "•"
            lines.append(
                f"{emoji} <b>{sym}</b> · {c.chain[:6]}\n"
                f"   score <b>{c.score:.1f}</b> · mcap {fmt_usd(c.market_cap)} · "
                f"liq {fmt_usd(c.liquidity_usd)}\n"
                f"   vol24 {fmt_usd(c.volume_24h)} · age {c.age_hours:.1f}h · "
                f"Δ6h {fmt_pct(chg6)} · Δ24h {fmt_pct(chg24)}\n"
                f"   <a href='{html.escape(c.url)}'>chart ↗</a>"
            )
        await m.edit_text("\n".join(lines), parse_mode=ParseMode.HTML,
                          disable_web_page_preview=True, reply_markup=main_menu())
    except Exception as e:
        log.exception("scan failed")
        await m.edit_text(f"❌ error: <code>{html.escape(str(e))[:200]}</code>",
                          parse_mode=ParseMode.HTML)


async def cmd_go(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update.effective_chat.id) or not _lp_bot:
        return
    # Tanpa argumen → pakai default DRY_RUN dari .env.
    # /go live → paksa live · /go dry → paksa dry-run.
    arg = ctx.args[0].lower() if ctx.args else ""
    if arg == "live":
        dry = False
    elif arg in ("dry", "dryrun", "dry-run", "sim"):
        dry = True
    else:
        dry = CONFIG.dry_run
    if not dry and not CONFIG.private_key:
        await update.effective_message.reply_text(
            "⚠ live mode butuh PRIVATE_KEY di .env. jalan dry-run dulu.")
        dry = True
    await _lp_bot.start(dry_run=dry)
    await update.effective_message.reply_html(
        f"{'🟢 BOT STARTED (DRY-RUN)' if dry else '⚠️ BOT STARTED (LIVE)'}\n"
        f"scan tiap {CONFIG.scan_interval_sec}s · max {CONFIG.max_active_positions} pair · "
        f"size {fmt_usd(CONFIG.position_size_usd)}",
        reply_markup=main_menu(),
    )


async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update.effective_chat.id) or not _lp_bot:
        return
    await _lp_bot.stop()
    await update.effective_message.reply_html("🔴 <b>BOT STOPPED</b>", reply_markup=main_menu())


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update.effective_chat.id) or not _lp_bot:
        return
    s = _lp_bot.snapshot()
    pnl = s["pnl_usd"]
    pnl_pct = (pnl / s["total_deployed_usd"] * 100) if s["total_deployed_usd"] else 0
    pnl_icon = "🟢" if pnl >= 0 else "🔴"
    st = s["stats"]
    txt = (
        f"📊 <b>STATUS</b>\n"
        f"{SEP}\n"
        f"{'🟢 RUNNING' if s['running'] else '⚫ IDLE'}  ·  "
        f"{'🧪 DRY-RUN' if s['dry_run'] else '⚠️ LIVE'}\n\n"
        f"💰 <b>PNL</b>\n"
        f"   {pnl_icon} {fmt_usd(pnl)}  ({fmt_pct(pnl_pct)})\n"
        f"   💵 deployed: {fmt_usd(s['total_deployed_usd'])}\n\n"
        f"📈 <b>ACTIVITY</b>\n"
        f"   💼 positions: <b>{len(s['positions'])}</b>  ·  "
        f"🎯 signals: <b>{len(s['candidates'])}</b>\n"
        f"   🔍 last scan: {ago(s['last_scan_ts'])}\n"
        f"   scans <b>{st['scans']}</b> · opens <b>{st['positions_opened']}</b> · "
        f"closes <b>{st['positions_closed']}</b> · rebal <b>{st['rebalances']}</b>\n"
    )
    if st["errors"]:
        txt += f"   ⚠️ errors: <b>{st['errors']}</b>\n"
    await update.effective_message.reply_html(txt, reply_markup=main_menu())


async def cmd_positions(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update.effective_chat.id) or not _lp_bot:
        return
    s = _lp_bot.snapshot()
    if not s["positions"]:
        await update.effective_message.reply_html(
            "💼 tidak ada posisi aktif.", reply_markup=main_menu())
        return
    tp = s["config"].get("take_profit_pct", 30)
    sl = s["config"].get("stop_loss_pct", 25)
    lines = [f"💼 <b>{len(s['positions'])} POSITIONS</b>", SEP]
    for p in s["positions"]:
        sym = html.escape(p["symbol"][:10])
        cur = next((c for c in s["candidates"] if c["pair_address"] == p["pair_address"]), None)
        cur_price = cur["price_usd"] if cur else p["entry_price"]
        change = (cur_price - p["entry_price"]) / max(p["entry_price"], 1e-12) * 100
        pnl_usd = change / 100 * p["size_usd"]
        icon = "🟢" if change >= 0 else "🔴"
        target = tp if change >= 0 else sl
        bar = pnl_bar(change / max(target, 1e-9) * 100)
        lines.append(
            f"{icon} <b>{sym}</b> · {p['chain']}  <code>{bar}</code>\n"
            f"   💵 {fmt_usd(p['size_usd'])} · ⏱️ {p['age_min']:.0f}m\n"
            f"   Δ {fmt_pct(change)}  ({fmt_usd(pnl_usd)})\n"
            f"   entry <code>${p['entry_price']:.8f}</code>\n"
            f"   now   <code>${cur_price:.8f}</code>"
        )
    await update.effective_message.reply_html("\n\n".join(lines), reply_markup=main_menu())


async def cmd_config(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update.effective_chat.id):
        return
    txt = (
        "⚙ <b>STRATEGY CONFIG</b>\n\n"
        "<b>Filter</b>\n"
        f"mcap ≥ <b>{fmt_usd(CONFIG.min_mcap)}</b>\n"
        f"age <b>{CONFIG.min_age_hours}h – {CONFIG.max_age_hours}h</b>\n"
        f"vol 24h ≥ <b>{fmt_usd(CONFIG.min_volume_24h)}</b>\n"
        f"liq ≥ <b>{fmt_usd(CONFIG.min_liquidity_usd)}</b>\n\n"
        "<b>Positions</b>\n"
        f"size / pos: <b>{fmt_usd(CONFIG.position_size_usd)}</b>\n"
        f"max active: <b>{CONFIG.max_active_positions}</b>\n"
        f"range ±<b>{CONFIG.range_width_pct}%</b>\n"
        f"fee tier: <b>{CONFIG.fee_tier/10000}%</b>\n\n"
        "<b>Exit</b>\n"
        f"TP: <b>+{CONFIG.take_profit_pct}%</b>\n"
        f"SL: <b>-{CONFIG.stop_loss_pct}%</b>\n"
        f"rebalance: <b>{'on' if CONFIG.auto_rebalance else 'off'}</b>\n\n"
        f"scan interval: <b>{CONFIG.scan_interval_sec}s</b>\n\n"
        "Ubah runtime: <code>/set key value</code>"
    )
    await update.effective_message.reply_html(txt, reply_markup=main_menu())


_TUNABLE = {
    "min_mcap", "min_age_hours", "max_age_hours", "min_volume_24h",
    "min_liquidity_usd", "position_size_usd", "max_active_positions",
    "range_width_pct", "take_profit_pct", "stop_loss_pct", "scan_interval_sec",
    "auto_rebalance",
}


async def cmd_set(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update.effective_chat.id):
        return
    if not ctx.args or len(ctx.args) < 2:
        await update.effective_message.reply_html(
            "usage: <code>/set key value</code>\n/help lihat daftar key")
        return
    key, val = ctx.args[0].lower(), " ".join(ctx.args[1:])
    if key not in _TUNABLE:
        await update.effective_message.reply_html(
            f"❌ key tidak valid: <code>{html.escape(key)}</code>\ncek /help")
        return
    try:
        current = getattr(CONFIG, key)
        if isinstance(current, bool):
            new = val.lower() in ("1", "true", "yes", "on")
        elif isinstance(current, int):
            new = int(float(val))
        else:
            new = float(val)
        setattr(CONFIG, key, new)
        await update.effective_message.reply_html(
            f"✅ <code>{key}</code> = <b>{new}</b> (was {current})")
    except (TypeError, ValueError) as e:
        await update.effective_message.reply_text(f"❌ invalid value: {e}")


# ---------- callbacks (inline buttons) ----------

async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.data:
        return
    await q.answer()
    if not _allowed(q.message.chat.id):
        return
    fake = Update(update.update_id, message=q.message)
    # dispatch
    router = {
        "scan": cmd_scan,
        "status": cmd_status,
        "go": cmd_go,
        "stop": cmd_stop,
        "positions": cmd_positions,
        "config": cmd_config,
    }
    fn = router.get(q.data)
    if fn:
        await fn(fake, ctx)


async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("tg error: %s", ctx.error)


# ---------- runner ----------

def build_app(lp_bot: LPBot) -> Application:
    global _lp_bot, _app
    _lp_bot = lp_bot
    _app = ApplicationBuilder().token(CONFIG.tg_token).build()

    _app.add_handler(CommandHandler("start", cmd_start))
    _app.add_handler(CommandHandler("help", cmd_help))
    _app.add_handler(CommandHandler("scan", cmd_scan))
    _app.add_handler(CommandHandler("go", cmd_go))
    _app.add_handler(CommandHandler("stop", cmd_stop))
    _app.add_handler(CommandHandler("status", cmd_status))
    _app.add_handler(CommandHandler("positions", cmd_positions))
    _app.add_handler(CommandHandler("config", cmd_config))
    _app.add_handler(CommandHandler("set", cmd_set))
    _app.add_handler(CallbackQueryHandler(on_callback))
    _app.add_handler(MessageHandler(filters.COMMAND, cmd_help))
    _app.add_error_handler(on_error)
    return _app


async def send_notify(text: str) -> None:
    """Kirim notif ke NOTIFY_CHAT_ID (dipanggil dari strategy loop)."""
    if not _app or not CONFIG.tg_notify_chat:
        return
    try:
        await _app.bot.send_message(
            chat_id=CONFIG.tg_notify_chat, text=text,
            parse_mode=ParseMode.HTML, disable_web_page_preview=True,
        )
    except Exception as e:
        log.warning("notify send failed: %s", e)
