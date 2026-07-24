#!/bin/bash
# ROBINHOOD LP HUNTER — Telegram Bot + Web Dashboard launcher
# Bot khusus Uniswap V3 di Robinhood Chain (chainId 4663, native ETH)
set -e
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "→ creating venv"
  python3 -m venv .venv
fi

source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt

if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "⚠  .env dibuat dari template."
  echo "   Isi TELEGRAM_TOKEN dari @BotFather sebelum jalanin lagi."
  exit 1
fi

PORT_LINE=$(grep -E '^PORT=' .env | cut -d= -f2)
echo "→ starting LP Hunter"
echo "  Telegram bot: polling"
echo "  Dashboard:    http://localhost:${PORT_LINE:-8080}  (set DASHBOARD_ENABLED=false to disable)"
exec python -B main.py
