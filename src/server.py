"""FastAPI server — REST API + WebSocket + serve dashboard.

Designed to share a SINGLE ``LPBot`` instance with the Telegram bot so both
surfaces reflect the same state. ``main.py`` builds the bot and calls
``attach_bot(bot)`` before starting uvicorn; when the server module is run
standalone it lazily creates its own bot as a fallback.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import CONFIG
from .strategy import LPBot

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="LP Hunter — Yunus Strategy", version="1.1")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"], allow_credentials=True,
)


class WSHub:
    """Fan-out event bus for connected dashboard clients."""

    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def add(self, ws: WebSocket) -> None:
        async with self._lock:
            self.clients.add(ws)

    async def remove(self, ws: WebSocket) -> None:
        async with self._lock:
            self.clients.discard(ws)

    async def broadcast(self, msg: dict[str, Any]) -> None:
        data = json.dumps(msg, default=str)
        dead: list[WebSocket] = []
        for c in list(self.clients):
            try:
                await c.send_text(data)
            except Exception:
                dead.append(c)
        for c in dead:
            await self.remove(c)


hub = WSHub()
_loop: asyncio.AbstractEventLoop | None = None
_bot: LPBot | None = None
# Extra event sinks (e.g. Telegram push) chained after the WS broadcast.
_extra_sinks: list = []


def _ws_sink(evt: dict[str, Any]) -> None:
    """LPBot event → broadcast to all dashboard websockets (thread-safe)."""
    if _loop and not _loop.is_closed():
        asyncio.run_coroutine_threadsafe(hub.broadcast(evt), _loop)


def _fanout(evt: dict[str, Any]) -> None:
    _ws_sink(evt)
    for sink in _extra_sinks:
        try:
            sink(evt)
        except Exception:
            pass


def attach_bot(bot: LPBot, extra_sinks: list | None = None) -> LPBot:
    """Wire an externally-owned LPBot into the server (shared-state mode).

    Called by main.py so the dashboard and Telegram bot drive the same engine.
    ``extra_sinks`` are additional ``on_event`` callables (e.g. Telegram push).
    """
    global _bot, _extra_sinks
    _bot = bot
    _extra_sinks = list(extra_sinks or [])
    bot._on_event = _fanout
    return bot


def get_bot() -> LPBot:
    """Return the shared bot, lazily creating a standalone one if unattached."""
    global _bot
    if _bot is None:
        _bot = LPBot(on_event=_ws_sink)
    return _bot


@app.on_event("startup")
async def _startup() -> None:
    global _loop
    _loop = asyncio.get_running_loop()


@app.get("/api/state")
async def get_state() -> JSONResponse:
    return JSONResponse(get_bot().snapshot())


@app.get("/api/health")
async def health() -> JSONResponse:
    bot = get_bot()
    return JSONResponse({
        "ok": True,
        "running": bot.state.running,
        "dry_run": bot.state.dry_run,
        "ts": time.time(),
    })


@app.post("/api/start")
async def start_bot(dry_run: bool = True) -> JSONResponse:
    await get_bot().start(dry_run=dry_run)
    return JSONResponse({"ok": True, "running": True, "dry_run": dry_run})


@app.post("/api/stop")
async def stop_bot() -> JSONResponse:
    await get_bot().stop()
    return JSONResponse({"ok": True, "running": False})


@app.post("/api/scan")
async def force_scan() -> JSONResponse:
    """Trigger a single scan without starting the loop."""
    from .screener import screen
    bot = get_bot()
    cands = await screen()
    bot.state.candidates = cands
    bot.state.last_scan_ts = time.time()
    bot.state.stats["scans"] += 1
    return JSONResponse({"count": len(cands), "candidates": [c.to_dict() for c in cands[:20]]})


@app.post("/api/config")
async def update_config(payload: dict[str, Any]) -> JSONResponse:
    """Update runtime-tunable config keys from the dashboard."""
    from .telegram_bot import _TUNABLE
    applied: dict[str, Any] = {}
    rejected: list[str] = []
    for key, val in (payload or {}).items():
        key = str(key).lower()
        if key not in _TUNABLE:
            rejected.append(key)
            continue
        try:
            current = getattr(CONFIG, key)
            if isinstance(current, bool):
                new = str(val).lower() in ("1", "true", "yes", "on")
            elif isinstance(current, int):
                new = int(float(val))
            else:
                new = float(val)
            setattr(CONFIG, key, new)
            applied[key] = new
        except (TypeError, ValueError):
            rejected.append(key)
    return JSONResponse({"ok": not rejected, "applied": applied, "rejected": rejected})


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    await hub.add(ws)
    try:
        await ws.send_text(json.dumps({"kind": "snapshot", **get_bot().snapshot()}, default=str))
        while True:
            # heartbeat / keep-alive
            await asyncio.sleep(30)
            try:
                await ws.send_text(json.dumps({"kind": "ping"}))
            except Exception:
                break
    except WebSocketDisconnect:
        pass
    finally:
        await hub.remove(ws)


# Static frontend
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "src.server:app",
        host=CONFIG.host,
        port=CONFIG.port,
        reload=False,
        log_level="info",
    )
