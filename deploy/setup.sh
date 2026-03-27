#!/bin/bash
# =============================================================================
# CryptoBot — Deployment script for Ubuntu 22.04 (Oracle Cloud)
# =============================================================================
# Usage:
#   1. SSH into your Oracle instance
#   2. Copy this script: scp deploy/setup.sh ubuntu@<IP>:~/setup.sh
#   3. Run: chmod +x setup.sh && sudo ./setup.sh
# =============================================================================

set -e

APP_USER="cryptobot"
APP_DIR="/opt/cryptobot"
REPO_URL="https://github.com/KrisRux/crypto-trading-bot.git"

echo "========================================="
echo "  CryptoBot — Server Setup"
echo "========================================="

# ------------------------------------------------------------------
# 1. System packages
# ------------------------------------------------------------------
echo "[1/8] Installing system packages..."
apt-get update -y
apt-get install -y \
  python3 python3-pip python3-venv \
  nginx certbot python3-certbot-nginx \
  git curl software-properties-common \
  iptables-persistent

# Node.js 20.x — remove old version and install fresh
echo "Installing Node.js 20..."
apt-get remove -y nodejs npm 2>/dev/null || true
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt-get install -y nodejs

echo "  Python: $(python3 --version)"
echo "  Node:   $(node --version)"
echo "  npm:    $(npm --version)"

# ------------------------------------------------------------------
# 2. Create app user and directory
# ------------------------------------------------------------------
echo "[2/8] Creating app user and directory..."
if ! id "$APP_USER" &>/dev/null; then
  useradd -r -m -s /bin/bash "$APP_USER"
fi
mkdir -p "$APP_DIR"
chown "$APP_USER:$APP_USER" "$APP_DIR"

# ------------------------------------------------------------------
# 3. Clone repository
# ------------------------------------------------------------------
echo "[3/8] Cloning repository..."
if [ -d "$APP_DIR/.git" ]; then
  echo "  Repo already exists, pulling latest..."
  cd "$APP_DIR"
  sudo -u "$APP_USER" git pull
else
  sudo -u "$APP_USER" git clone "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"

# ------------------------------------------------------------------
# 4. Backend setup
# ------------------------------------------------------------------
echo "[4/8] Setting up Python backend..."
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/venv"
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

# Create .env if it doesn't exist
if [ ! -f "$APP_DIR/.env" ]; then
  echo "  Creating .env from template..."
  cat > "$APP_DIR/.env" << 'ENVEOF'
# =============================================================================
# CryptoBot — Environment Configuration
# =============================================================================
# Fill in your Binance API keys below.

# Binance Testnet (for paper trading)
BINANCE_TESTNET_API_KEY=your_testnet_key_here
BINANCE_TESTNET_API_SECRET=your_testnet_secret_here

# Binance Live (for real trading — leave empty until ready)
BINANCE_API_KEY=
BINANCE_API_SECRET=

TRADING_MODE=paper
PAPER_INITIAL_CAPITAL=10000.0
DATABASE_URL=sqlite:////opt/cryptobot/trading_bot.db
SYMBOLS=BTCUSDT,ETHUSDT
MAX_POSITION_SIZE_PCT=2.0
DEFAULT_STOP_LOSS_PCT=3.0
DEFAULT_TAKE_PROFIT_PCT=5.0
LOG_LEVEL=INFO
BACKEND_HOST=127.0.0.1
BACKEND_PORT=8000
FRONTEND_URL=*
ENVEOF
  chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
  echo ""
  echo "  *** IMPORTANT: Edit /opt/cryptobot/.env with your Binance API keys ***"
  echo ""
fi

# ------------------------------------------------------------------
# 5. Frontend build
# ------------------------------------------------------------------
echo "[5/8] Building frontend..."
cd "$APP_DIR/frontend"
sudo -u "$APP_USER" npm install
sudo -u "$APP_USER" npm run build
cd "$APP_DIR"

# ------------------------------------------------------------------
# 6. Systemd service
# ------------------------------------------------------------------
echo "[6/8] Creating systemd service..."
cat > /etc/systemd/system/cryptobot.service << EOF
[Unit]
Description=CryptoBot Trading Engine
After=network.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=10
Environment=PATH=$APP_DIR/venv/bin:/usr/bin
StandardOutput=append:/var/log/cryptobot.log
StandardError=append:/var/log/cryptobot.log

[Install]
WantedBy=multi-user.target
EOF

touch /var/log/cryptobot.log
chown "$APP_USER:$APP_USER" /var/log/cryptobot.log

systemctl daemon-reload
systemctl enable cryptobot
systemctl restart cryptobot

echo "  Backend service started."

# ------------------------------------------------------------------
# 7. Nginx configuration
# ------------------------------------------------------------------
echo "[7/8] Configuring Nginx..."
cat > /etc/nginx/sites-available/cryptobot << 'NGINXEOF'
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;

    # Frontend (built React app)
    root /opt/cryptobot/frontend/dist;
    index index.html;

    # SPA fallback
    location / {
        try_files $uri $uri/ /index.html;
    }

    # Backend API proxy
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
    }

    # WebSocket support (if needed in the future)
    location /ws/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
NGINXEOF

# Enable site, disable default
ln -sf /etc/nginx/sites-available/cryptobot /etc/nginx/sites-enabled/cryptobot
rm -f /etc/nginx/sites-enabled/default

nginx -t
systemctl restart nginx
systemctl enable nginx

echo "  Nginx configured."

# ------------------------------------------------------------------
# 8. Firewall (Oracle Cloud iptables)
# ------------------------------------------------------------------
echo "[8/8] Configuring firewall..."
# Oracle Cloud Ubuntu uses iptables rules that block port 80/443 by default
# Add rules to allow HTTP and HTTPS traffic
iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT 2>/dev/null || true
iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT 2>/dev/null || true
netfilter-persistent save 2>/dev/null || true

echo ""
echo "========================================="
echo "  SETUP COMPLETE"
echo "========================================="
echo ""
echo "  Next steps:"
echo ""
echo "  1. Edit your Binance API keys:"
echo "     sudo nano /opt/cryptobot/.env"
echo ""
echo "  2. Restart the bot after editing:"
echo "     sudo systemctl restart cryptobot"
echo ""
echo "  3. Open port 80 in Oracle Cloud:"
echo "     Console > Networking > VCN > Security Lists"
echo "     Add Ingress Rule: 0.0.0.0/0, TCP, port 80"
echo ""
echo "  4. Access CryptoBot at:"
echo "     http://<YOUR_SERVER_IP>"
echo ""
echo "  5. (Optional) Add HTTPS with Let's Encrypt:"
echo "     sudo certbot --nginx -d your-domain.com"
echo ""
echo "  Useful commands:"
echo "     sudo systemctl status cryptobot    # Check bot status"
echo "     sudo systemctl restart cryptobot   # Restart bot"
echo "     sudo journalctl -u cryptobot -f    # Live logs"
echo "     tail -f /var/log/cryptobot.log     # App logs"
echo ""
