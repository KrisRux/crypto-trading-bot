"""
Tests for JWT authentication and user management.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.user import User, hash_password, verify_password
from app.api.auth import create_token, decode_token


# ------------------------------------------------------------------ Password hashing

def test_hash_password_produces_salt():
    h = hash_password("mypassword")
    assert "$" in h
    salt, digest = h.split("$")
    assert len(salt) == 32  # 16 bytes hex
    assert len(digest) == 64  # sha256 hex


def test_verify_password_correct():
    h = hash_password("secret123")
    assert verify_password("secret123", h) is True


def test_verify_password_wrong():
    h = hash_password("secret123")
    assert verify_password("wrongpass", h) is False


def test_verify_password_different_hashes():
    h1 = hash_password("same")
    h2 = hash_password("same")
    # Different salts produce different hashes
    assert h1 != h2
    # But both verify correctly
    assert verify_password("same", h1)
    assert verify_password("same", h2)


# ------------------------------------------------------------------ JWT tokens

def test_create_and_decode_token():
    token, expires_in = create_token("admin", "admin")
    assert isinstance(token, str)
    assert expires_in > 0

    data = decode_token(token)
    assert data is not None
    assert data["sub"] == "admin"
    assert data["role"] == "admin"


def test_decode_invalid_token():
    data = decode_token("invalid.token.here")
    assert data is None


def test_decode_empty_token():
    data = decode_token("")
    assert data is None


def test_token_contains_role():
    token, _ = create_token("testuser", "user")
    data = decode_token(token)
    assert data["role"] == "user"


# ------------------------------------------------------------------ User model

@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_create_user(db_session):
    user = User(
        username="john", password_hash=hash_password("pass123"),
        display_name="John", role="user",
    )
    db_session.add(user)
    db_session.commit()

    found = db_session.query(User).filter(User.username == "john").first()
    assert found is not None
    assert found.display_name == "John"
    assert found.role == "user"
    assert found.is_active is True
    assert verify_password("pass123", found.password_hash)


def test_unique_username(db_session):
    u1 = User(username="dupe", password_hash=hash_password("a"), role="user")
    db_session.add(u1)
    db_session.commit()

    u2 = User(username="dupe", password_hash=hash_password("b"), role="user")
    db_session.add(u2)
    with pytest.raises(Exception):  # IntegrityError
        db_session.commit()


def test_api_keys_obfuscation(db_session):
    user = User(username="trader", password_hash=hash_password("x"), role="user")
    user.set_api_keys(
        api_key="myLiveKey123",
        api_secret="myLiveSecret456",
        testnet_key="myTestKey",
        testnet_secret="myTestSecret",
    )
    db_session.add(user)
    db_session.commit()

    found = db_session.query(User).filter(User.username == "trader").first()
    # Keys are stored obfuscated (base64), not plaintext
    assert found.binance_api_key != "myLiveKey123"
    # But can be retrieved
    assert found.get_api_key(live=True) == "myLiveKey123"
    assert found.get_api_secret(live=True) == "myLiveSecret456"
    assert found.get_api_key(live=False) == "myTestKey"
    assert found.get_api_secret(live=False) == "myTestSecret"


def test_has_api_keys(db_session):
    user = User(username="nokeys", password_hash=hash_password("x"), role="user")
    db_session.add(user)
    db_session.commit()

    assert user.has_api_keys(live=True) is False
    assert user.has_api_keys(live=False) is False

    user.set_api_keys(testnet_key="tk", testnet_secret="ts")
    assert user.has_api_keys(live=False) is True
    assert user.has_api_keys(live=True) is False


def test_user_trading_mode_default(db_session):
    user = User(username="default", password_hash=hash_password("x"))
    db_session.add(user)
    db_session.commit()
    assert user.trading_mode == "paper"
    assert user.paper_initial_capital == 10000.0
