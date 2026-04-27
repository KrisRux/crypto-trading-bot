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
# profiles.json is read-only at runtime — active_profile and switch_history are
# stored in config/profile_state.json (gitignored), so no stash needed.
# guardrails.json deploy version always wins; API hot-reload reads from disk anyway.
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
