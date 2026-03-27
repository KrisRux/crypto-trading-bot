"""
User model for authentication, per-user Binance keys, and role-based access.

Each user has:
  - Their own Binance API keys (live + testnet)
  - Their own paper trading portfolio
  - Their own trade/order history

Roles:
  - admin: full access (manage users, switch mode, configure everything)
  - user:  view + configure strategies/risk, trade with own keys
  - guest: read-only (dashboard, positions, trades, logs)
"""

import base64
import hashlib
import secrets
from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, Boolean, DateTime, Float
from app.database import Base


def hash_password(password: str, salt: str = "") -> str:
    if not salt:
        salt = secrets.token_hex(16)
    hashed = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
    return f"{salt}${hashed}"


def verify_password(password: str, stored_hash: str) -> bool:
    if "$" not in stored_hash:
        return False
    salt, _ = stored_hash.split("$", 1)
    return hash_password(password, salt) == stored_hash


def _obfuscate(value: str) -> str:
    """Simple base64 obfuscation for API keys stored in DB."""
    if not value:
        return ""
    return base64.b64encode(value.encode()).decode()


def _deobfuscate(value: str) -> str:
    if not value:
        return ""
    try:
        return base64.b64decode(value.encode()).decode()
    except Exception:
        return value


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    display_name = Column(String, nullable=True)
    role = Column(String, default="guest")  # admin, user, guest
    is_active = Column(Boolean, default=True)
    trading_enabled = Column(Boolean, default=False)  # Must opt-in explicitly
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_login = Column(DateTime, nullable=True)

    # Per-user Binance API keys (base64 obfuscated)
    binance_api_key = Column(String, default="")
    binance_api_secret = Column(String, default="")
    binance_testnet_api_key = Column(String, default="")
    binance_testnet_api_secret = Column(String, default="")

    # Per-user trading preferences
    trading_mode = Column(String, default="paper")  # "paper" or "live"
    paper_initial_capital = Column(Float, default=10000.0)

    # Trading schedule (UTC hours, 0-23). None = always active when enabled.
    trading_start_hour = Column(Integer, nullable=True)  # e.g. 8
    trading_end_hour = Column(Integer, nullable=True)    # e.g. 22

    def set_api_keys(self, api_key: str = "", api_secret: str = "",
                     testnet_key: str = "", testnet_secret: str = ""):
        self.binance_api_key = _obfuscate(api_key)
        self.binance_api_secret = _obfuscate(api_secret)
        self.binance_testnet_api_key = _obfuscate(testnet_key)
        self.binance_testnet_api_secret = _obfuscate(testnet_secret)

    def get_api_key(self, live: bool = False) -> str:
        if live:
            return _deobfuscate(self.binance_api_key)
        return _deobfuscate(self.binance_testnet_api_key)

    def get_api_secret(self, live: bool = False) -> str:
        if live:
            return _deobfuscate(self.binance_api_secret)
        return _deobfuscate(self.binance_testnet_api_secret)

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
