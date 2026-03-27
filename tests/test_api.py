"""
Integration tests for API routes.
Uses dependency_overrides on the actual app object.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app.database import Base, get_db
from app.main import app
from app.api.routes import set_engine
from app.models.user import User, hash_password


# Shared test engine and session factory (module-level for performance)
_test_engine = create_engine("sqlite:///./test_nrt.db",
                             connect_args={"check_same_thread": False})


@pytest.fixture(autouse=True, scope="module")
def setup_module_db():
    """Create tables once for the module."""
    Base.metadata.drop_all(_test_engine)
    Base.metadata.create_all(_test_engine)

    SF = sessionmaker(bind=_test_engine)
    s = SF()
    s.add_all([
        User(username="admin", password_hash=hash_password("adminpass"),
             display_name="Admin", role="admin"),
        User(username="user1", password_hash=hash_password("userpass"),
             display_name="User One", role="user"),
        User(username="guest1", password_hash=hash_password("guestpass"),
             display_name="Guest", role="guest"),
    ])
    s.commit()
    s.close()

    def override_db():
        session = SF()
        try:
            yield session
        finally:
            session.close()

    # Mock engine
    class MockEngine:
        symbols = ["BTCUSDT", "ETHUSDT"]
        last_prices = {"BTCUSDT": 65000.0, "ETHUSDT": 2000.0}
        last_price = 65000.0
        strategies = []
        signals_log = []
        running = True
        risk_manager = type("RM", (), {
            "get_params": lambda self: {
                "max_position_pct": 2.0, "default_sl_pct": 3.0, "default_tp_pct": 5.0,
            },
            "set_params": lambda self, p: None,
        })()
        paper_portfolio = type("PP", (), {
            "get_or_create": lambda self, db, uid, cap=10000: type("P", (), {
                "cash_balance": 10000, "total_equity": 10000,
                "total_pnl": 0, "total_trades": 0,
                "winning_trades": 0, "losing_trades": 0,
            })(),
            "reset": lambda self, db, uid: None,
            "export_trades_csv": lambda self, db, uid: "id,symbol\n",
        })()
        market_client = type("MC", (), {})()
        def add_symbol(self, s): pass
        def remove_symbol(self, s): pass

    set_engine(MockEngine(), None)
    app.dependency_overrides[get_db] = override_db
    yield
    app.dependency_overrides.clear()
    import os
    try:
        os.remove("test_nrt.db")
    except OSError:
        pass


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


def _login(c, user, pwd):
    return c.post("/api/login", json={"username": user, "password": pwd})


def _auth(c, user, pwd):
    r = _login(c, user, pwd)
    assert r.status_code == 200, f"Login failed for {user}: {r.text}"
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ------------------------------------------------------------------ Login

def test_login_success(client):
    r = _login(client, "admin", "adminpass")
    assert r.status_code == 200
    d = r.json()
    assert "access_token" in d
    assert d["role"] == "admin"
    assert d["session_timeout_minutes"] > 0


def test_login_wrong_password(client):
    assert _login(client, "admin", "wrong").status_code == 401


def test_login_nonexistent_user(client):
    assert _login(client, "nobody", "x").status_code == 401


def test_login_all_roles(client):
    for user, pwd, role in [("admin", "adminpass", "admin"),
                             ("user1", "userpass", "user"),
                             ("guest1", "guestpass", "guest")]:
        assert _login(client, user, pwd).json()["role"] == role


# ------------------------------------------------------------------ Auth enforcement

def test_no_token_401(client):
    assert client.get("/api/balance").status_code == 401


def test_bad_token_401(client):
    assert client.get("/api/balance", headers={"Authorization": "Bearer x"}).status_code == 401


def test_valid_token(client):
    assert client.get("/api/balance", headers=_auth(client, "admin", "adminpass")).status_code == 200


# ------------------------------------------------------------------ Role-based access

def test_admin_list_users(client):
    r = client.get("/api/users", headers=_auth(client, "admin", "adminpass"))
    assert r.status_code == 200
    assert len(r.json()) >= 3


def test_user_cannot_list_users(client):
    assert client.get("/api/users", headers=_auth(client, "user1", "userpass")).status_code == 403


def test_guest_cannot_list_users(client):
    assert client.get("/api/users", headers=_auth(client, "guest1", "guestpass")).status_code == 403


def test_admin_create_user(client):
    r = client.post("/api/users", json={
        "username": "new1", "password": "p", "display_name": "N", "role": "guest",
    }, headers=_auth(client, "admin", "adminpass"))
    assert r.status_code == 200


def test_guest_cannot_write(client):
    r = client.put("/api/strategies", json={"name": "x", "enabled": False},
                   headers=_auth(client, "guest1", "guestpass"))
    assert r.status_code == 403


# ------------------------------------------------------------------ Read endpoints

def test_all_roles_read(client):
    for u, p in [("admin", "adminpass"), ("user1", "userpass"), ("guest1", "guestpass")]:
        h = _auth(client, u, p)
        for ep in ["/api/balance", "/api/trades", "/api/signals"]:
            assert client.get(ep, headers=h).status_code == 200, f"{u} {ep}"


# ------------------------------------------------------------------ Misc

def test_me(client):
    r = client.get("/api/me", headers=_auth(client, "user1", "userpass"))
    assert r.status_code == 200
    assert r.json()["username"] == "user1"


def test_engine_status(client):
    r = client.get("/api/engine/status", headers=_auth(client, "admin", "adminpass"))
    assert r.status_code == 200
    assert "symbols" in r.json()
