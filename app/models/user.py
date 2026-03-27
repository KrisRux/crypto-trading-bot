"""
User model for authentication and role-based access.
Roles:
  - admin: full access (manage users, switch mode, configure everything)
  - user:  view + configure strategies/risk, cannot manage users or switch mode
  - guest: read-only (dashboard, positions, trades, logs)
"""

import hashlib
import secrets
from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, Boolean, DateTime
from app.database import Base


def hash_password(password: str, salt: str = "") -> str:
    """Hash a password with optional salt using SHA-256."""
    if not salt:
        salt = secrets.token_hex(16)
    hashed = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
    return f"{salt}${hashed}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify a password against a stored hash."""
    if "$" not in stored_hash:
        return False
    salt, _ = stored_hash.split("$", 1)
    return hash_password(password, salt) == stored_hash


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    display_name = Column(String, nullable=True)
    role = Column(String, default="guest")  # admin, user, guest
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_login = Column(DateTime, nullable=True)
