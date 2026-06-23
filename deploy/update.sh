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

# Repo hygiene (idempotent): a git command accidentally run as root leaves
# root-owned files under .git, which then makes `sudo -u $APP_USER git` abort
# with "detected dubious ownership". Re-assert ownership and register the
# safe.directory exception for the APP_USER so the pull never gets blocked.
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
if ! sudo -u "$APP_USER" git config --global --get-all safe.directory 2>/dev/null \
        | grep -qx "$APP_DIR"; then
  sudo -u "$APP_USER" git config --global --add safe.directory "$APP_DIR"
fi

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
