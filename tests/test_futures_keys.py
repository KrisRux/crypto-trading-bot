"""
Per-user futures-testnet key storage (User model). The keys are Fernet-encrypted
and independent of the spot keys — setting one must never clobber the other.
"""

from __future__ import annotations

from app.models.user import User


def test_futures_keys_roundtrip_and_isolation():
    u = User(username="x")
    u.set_api_keys(api_key="LIVE", api_secret="LIVES",
                   testnet_key="TNET", testnet_secret="TNETS")
    assert u.has_futures_keys() is False

    u.set_futures_keys(api_key="FUT", api_secret="FUTS")
    assert u.has_futures_keys() is True
    assert u.get_futures_key() == "FUT"
    assert u.get_futures_secret() == "FUTS"

    # Spot keys untouched by the futures setter.
    assert u.get_api_key(live=True) == "LIVE"
    assert u.get_api_key(live=False) == "TNET"

    # And the spot setter does not wipe futures keys.
    u.set_api_keys(api_key="LIVE2", api_secret="LIVES2",
                   testnet_key="TNET2", testnet_secret="TNETS2")
    assert u.get_futures_key() == "FUT"
    assert u.has_futures_keys() is True


def test_has_futures_keys_false_when_partial():
    u = User(username="y")
    u.set_futures_keys(api_key="ONLYKEY", api_secret="")
    assert u.has_futures_keys() is False


def test_routes_futures_client_helper(monkeypatch):
    """_futures_client_for builds from the user's keys, falls back to env, and
    returns None when neither is present."""
    from app.api import routes
    from app.config import settings

    monkeypatch.setattr(settings, "futures_testnet_api_key", "")
    monkeypatch.setattr(settings, "futures_testnet_api_secret", "")

    no_keys = User(username="nokeys")
    assert routes._futures_client_for(no_keys) is None

    with_keys = User(username="withkeys")
    with_keys.set_futures_keys(api_key="FK", api_secret="FS")
    client = routes._futures_client_for(with_keys)
    assert client is not None and client.testnet is True
    import asyncio
    asyncio.run(client.close())

    # Env fallback when the user has none.
    monkeypatch.setattr(settings, "futures_testnet_api_key", "ENVK")
    monkeypatch.setattr(settings, "futures_testnet_api_secret", "ENVS")
    env_client = routes._futures_client_for(no_keys)
    assert env_client is not None
    asyncio.run(env_client.close())
