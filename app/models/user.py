"""
User model for authentication, per-user Binance keys, and role-based access.

Each user has:
  - Their own Binance API keys (live + testnet)
  - Their own paper trading portfolio
  - Their own trade/order history

Roles:
  - admin: full access (manage users, switch mode, configure everything)
  - user:  view + configure strategies/risk, trade with own keys

Security:
  - Passwords hashed with bcrypt (salted, slow by design)
  - API keys encrypted with Fernet (AES-128-CBC + HMAC-SHA256)
    The encryption key is read from ENCRYPTION_KEY in .env.
    If not set, a random key is generated at startup (keys become unreadable
    after restart — set a stable key in production).
"""

import logging
from datetime import datetime, timezone

import bcrypt
from cryptography.fernet import Fernet
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Float
from app.database import Base

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Fernet key — loaded lazily from app.config so we don't import at module
# level before settings are fully initialised.
# ---------------------------------------------------------------------------
_fernet: "Fernet | None" = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        from app.config import settings
        key = settings.encryption_key
        if not key:
            # Auto-generate an ephemeral key — warn operator that keys won't
            # survive a restart unless ENCRYPTION_KEY is set in .env.
            key = Fernet.generate_key().decode()
            logger.warning(
                "ENCRYPTION_KEY not set — using ephemeral key. "
                "Stored API keys will be unreadable after restart. "
                "Set ENCRYPTION_KEY in .env for production."
            )
        _fernet = Fernet(key.encode() if isinstance(key, str) else key)
    return _fernet


# ---------------------------------------------------------------------------
# Password helpers (bcrypt)
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """Return a bcrypt hash of the password (includes salt)."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify password against a bcrypt hash. Also supports legacy SHA256 hashes."""
    if not stored_hash:
        return False
    # Legacy SHA256 format: "salt$hexhash"
    if stored_hash.startswith("$2b$") or stored_hash.startswith("$2a$"):
        try:
            return bcrypt.checkpw(password.encode(), stored_hash.encode())
        except Exception:
            return False
    # Legacy SHA256 migration path
    if "$" in stored_hash:
        import hashlib
        salt, _ = stored_hash.split("$", 1)
        import hashlib
        hashed = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
        return f"{salt}${hashed}" == stored_hash
    return False


# ---------------------------------------------------------------------------
# API key encryption helpers (Fernet)
# ---------------------------------------------------------------------------

def _encrypt(value: str) -> str:
    """Encrypt a string with Fernet. Returns empty string for empty input."""
    if not value:
        return ""
    return _get_fernet().encrypt(value.encode()).decode()


def _decrypt(value: str) -> str:
    """Decrypt a Fernet-encrypted string. Falls back gracefully on errors."""
    if not value:
        return ""
    try:
        return _get_fernet().decrypt(value.encode()).decode()
    except Exception:
        logger.warning("Failed to decrypt API key — key may have changed or data is corrupted")
        return ""


# ---------------------------------------------------------------------------
# User model
# ---------------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    display_name = Column(String, nullable=True)
    role = Column(String, default="user")  # admin, user
    is_active = Column(Boolean, default=True)
    trading_enabled = Column(Boolean, default=False)  # Must opt-in explicitly
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_login = Column(DateTime, nullable=True)

    # Per-user Binance API keys (Fernet-encrypted)
    binance_api_key = Column(String, default="")
    binance_api_secret = Column(String, default="")
    binance_testnet_api_key = Column(String, default="")
    binance_testnet_api_secret = Column(String, default="")

    # Per-user trading preferences
    trading_mode = Column(String, default="paper")  # "dry_run", "paper", or "live"
    paper_initial_capital = Column(Float, default=10000.0)

    # Trading schedule (UTC hours, 0-23). None = always active when enabled.
    trading_start_hour = Column(Integer, nullable=True)  # e.g. 8
    trading_end_hour = Column(Integer, nullable=True)    # e.g. 22

    # Telegram notifications (per-user)
    telegram_chat_id = Column(String, default="")
    telegram_enabled = Column(Boolean, default=False)
    # "" = use global TELEGRAM_MIN_LEVEL; else INFO|WARNING|CRITICAL
    telegram_min_level = Column(String, default="")

    def set_api_keys(self, api_key: str = "", api_secret: str = "",
                     testnet_key: str = "", testnet_secret: str = ""):
        self.binance_api_key = _encrypt(api_key)
        self.binance_api_secret = _encrypt(api_secret)
        self.binance_testnet_api_key = _encrypt(testnet_key)
        self.binance_testnet_api_secret = _encrypt(testnet_secret)

    def get_api_key(self, live: bool = False) -> str:
        if live:
            return _decrypt(self.binance_api_key)
        return _decrypt(self.binance_testnet_api_key)

    def get_api_secret(self, live: bool = False) -> str:
        if live:
            return _decrypt(self.binance_api_secret)
        return _decrypt(self.binance_testnet_api_secret)

    def has_api_keys(self, live: bool = False) -> bool:
        return bool(self.get_api_key(live) and self.get_api_secret(live))

    def is_within_trading_hours(self) -> bool:
        """Check if current UTC time is within the user's trading schedule."""
        if self.trading_start_hour is None or self.trading_end_hour is None:
            return True  # No schedule = always active

        current_hour = datetime.now(timezone.utc).hour

        if self.trading_start_hour <= self.trading_end_hour:
            # Normal range, e.g. 8-22
            return self.trading_start_hour <= current_hour < self.trading_end_hour
        else:
            # Overnight range, e.g. 22-8 (trades at night)
            return current_hour >= self.trading_start_hour or current_hour < self.trading_end_hour
