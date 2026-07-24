"""Entry point — run Telegram bot + web dashboard on ONE event loop.

Both surfaces share a single ``LPBot`` instance, so a scan triggered from the
web UI shows up in Telegram and vice-versa. Events fan out to:
  * the dashboard WebSocket hub (via server.attach_bot)
  * Telegram push notifications (NOTIFY_CHAT_ID)

Dashboard can be disabled with DASHBOARD_ENABLED=false — then only the
Telegram bot runs.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

from src.config import CONFIG
from src.strategy import LPBot
from src.telegram_bot import build_app, _format_event

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("lp-hunter")


def main() -> None:
    if not CONFIG.tg_token:
        print("❌ TELEGRAM_TOKEN belum di-set di .env")
        print("→ chat @BotFather di Telegram → /newbot → copy token ke .env")
        sys.exit(1)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # ── ONE shared bot instance ────────────────────────────────────────
    lp_bot = LPBot()
    tg_app: Any = build_app(lp_bot)

    # Telegram push sink: LPBot event → send_message to NOTIFY_CHAT_ID
    def _tg_push(evt: dict) -> None:
        text = _format_event(evt)
        if not text or not CONFIG.tg_notify_chat:
            return
        try:
            asyncio.run_coroutine_threadsafe(
                tg_app.bot.send_message(
                    chat_id=CONFIG.tg_notify_chat, text=text,
                    parse_mode="HTML", disable_web_page_preview=True,
                ),
                loop,
            )
        except Exception as e:
            log.warning("tg push failed: %s", e)

    # ── Wire the dashboard (shared state) ──────────────────────────────
    dashboard_task = None
    if CONFIG.dashboard_enabled:
        try:
            import uvicorn
            from src import server as srv
            # Bot drives both the WS hub AND Telegram push.
            srv.attach_bot(lp_bot, extra_sinks=[_tg_push])
            uv_config = uvicorn.Config(
                srv.app, host=CONFIG.host, port=CONFIG.port,
                log_level="warning", loop="asyncio",
            )
            dashboard_task = uvicorn.Server(uv_config)
        except ImportError:
            log.warning("uvicorn/fastapi not installed — dashboard disabled. "
                        "run: pip install -r requirements.txt")
    if not dashboard_task:
        # Telegram-only mode: still push events.
        lp_bot._on_event = _tg_push

    async def _run() -> None:
        await tg_app.initialize()
        await tg_app.start()
        await tg_app.updater.start_polling(drop_pending_updates=True)
        log.info("✅ Telegram bot polling started")

        if dashboard_task:
            asyncio.create_task(dashboard_task.serve())
            log.info("✅ Dashboard: http://%s:%s", CONFIG.host, CONFIG.port)

        if CONFIG.tg_notify_chat:
            dash_line = (
                f"\n📊 dashboard: http://{CONFIG.host}:{CONFIG.port}"
                if dashboard_task else ""
            )
            try:
                await tg_app.bot.send_message(
                    chat_id=CONFIG.tg_notify_chat,
                    text=f"🟢 <b>LP HUNTER online</b>\nkirim /start buat menu{dash_line}",
                    parse_mode="HTML",
                )
            except Exception as e:
                log.warning("boot notif failed: %s", e)

        # Idle until Ctrl+C
        stop = asyncio.Event()
        await stop.wait()

    try:
        loop.run_until_complete(_run())
    except KeyboardInterrupt:
        log.info("shutdown requested")
    finally:
        async def _shutdown() -> None:
            try:
                await lp_bot.stop()
            except Exception:
                pass
            if dashboard_task:
                try:
                    dashboard_task.should_exit = True
                except Exception:
                    pass
            try:
                await tg_app.updater.stop()
                await tg_app.stop()
                await tg_app.shutdown()
            except Exception:
                pass
        try:
            loop.run_until_complete(_shutdown())
        finally:
            loop.close()


if __name__ == "__main__":
    main()
