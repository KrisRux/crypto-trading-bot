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
# Stash runtime-editable config files before pulling so user changes are not lost.
# guardrails.json and profiles.json can be modified via the API without a deploy.
sudo -u "$APP_USER" git stash -- config/guardrails.json config/profiles.json 2>/dev/null || true
sudo -u "$APP_USER" git pull
# Re-apply the stashed config on top of the pulled code (preserves user edits).
sudo -u "$APP_USER" git stash pop 2>/dev/null || true

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
