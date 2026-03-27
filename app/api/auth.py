"""
JWT authentication with database-backed users and role-based access.

Roles:
  - admin: full access
  - user:  view + configure strategies/risk
  - guest: read-only
"""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.user import User, verify_password

logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=False)
ALGORITHM = "HS256"


# ------------------------------------------------------------------ Schemas
class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    role: str
    display_name: str


class UserCreate(BaseModel):
    username: str
    password: str
    display_name: str = ""
    role: str = "guest"  # admin, user, guest


class UserUpdate(BaseModel):
    display_name: str | None = None
    role: str | None = None
    password: str | None = None
    is_active: bool | None = None


class UserInfo(BaseModel):
    id: int
    username: str
    display_name: str | None
    role: str
    is_active: bool
    created_at: datetime | None
    last_login: datetime | None


# ------------------------------------------------------------------ Token helpers
def create_token(username: str, role: str) -> tuple[str, int]:
    expires_in = settings.jwt_expiry_hours * 3600
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expiry_hours)
    payload = {
        "sub": username,
        "role": role,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)
    return token, expires_in


def decode_token(token: str) -> dict | None:
    """Decode JWT. Returns {"sub": username, "role": role} or None."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
        if payload.get("sub"):
            return {"sub": payload["sub"], "role": payload.get("role", "guest")}
    except JWTError:
        pass
    return None


# ------------------------------------------------------------------ Dependencies
async def require_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> dict:
    """Returns {"username": str, "role": str} or raises 401."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    data = decode_token(credentials.credentials)
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return {"username": data["sub"], "role": data["role"]}


async def require_admin(user: dict = Depends(require_auth)) -> dict:
    """Only admin can access."""
    if user["role"] != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin access required")
    return user


async def require_write(user: dict = Depends(require_auth)) -> dict:
    """Admin or user can write. Guests cannot."""
    if user["role"] == "guest":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Read-only access")
    return user
