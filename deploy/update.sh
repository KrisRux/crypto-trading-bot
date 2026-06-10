#!/bin/bash
# =============================================================================
# CryptoBot — Update script (pull latest code and restart)
# Usage: sudo ./update.sh
# =============================================================================

set -e

APP_DIR="/opt/cryptobot"
APP_USER="cryptobot"

echo "Pulling latest code..."
cd "$APP_DIR"
# All runtime state lives in GITIGNORED files (config/profile_state.json,
# strategy_params.json, trading_bot.db, logs). Tracked files must always match
# the repo: discard any local drift before pulling so a runtime process that
# touched a tracked file can never block the deploy (this happened with the
# old skills sync rewriting SKILL.md files).
sudo -u "$APP_USER" git checkout -- .
sudo -u "$APP_USER" git pull

echo "Updating Python dependencies..."
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install -r requirements.txt

echo "Rebuilding frontend..."
cd "$APP_DIR/frontend"
sudo -u "$APP_USER" npm install
sudo -u "$APP_USER" npm run build

echo "Restarting services..."
systemctl restart cryptobot
systemctl restart nginx

echo "Done! CryptoBot updated and running."
echo "Check status: sudo systemctl status cryptobot"
