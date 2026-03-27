"""
JWT authentication for the trading bot API.

Login with username/password (from .env) → get a JWT token.
All /api/* routes require a valid token in the Authorization header.
"""

import hashlib
import logging
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=False)

ALGORITHM = "HS256"


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


def _hash_password(password: str) -> str:
    """Simple hash for comparison — not stored, just compared at runtime."""
    return hashlib.sha256(password.encode()).hexdigest()


def verify_credentials(username: str, password: str) -> bool:
    """Check username/password against .env config."""
    return (
        username == settings.auth_username
        and password == settings.auth_password
    )


def create_token(username: str) -> tuple[str, int]:
    """Create a JWT token. Returns (token, expires_in_seconds)."""
    expires_in = settings.jwt_expiry_hours * 3600
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expiry_hours)
    payload = {
        "sub": username,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)
    return token, expires_in


def decode_token(token: str) -> str | None:
    """Decode and validate a JWT token. Returns username or None."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None


async def require_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> str:
    """
    FastAPI dependency that enforces authentication.
    Returns the username if valid, raises 401 otherwise.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    username = decode_token(credentials.credentials)
    if username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return username
