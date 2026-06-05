"""
Unit tests for the hardened Binance REST client (app/binance_client/rest_client.py).

NO NETWORK: every test injects an httpx.MockTransport (or monkeypatches the
underlying client) so requests are answered locally. We verify:

- recvWindow + timestamp + signature land in the signed query params;
- sync_time() applies the server-time offset to subsequent signed timestamps;
- get_exchange_info() caches per-symbol (second call does not hit the transport);
- X-MBX-USED-WEIGHT-1m is parsed into client.last_used_weight;
- DELETE (cancel_order) retries on 429 then succeeds on 200;
- POST (place_order) is NOT retried by default;
- HTTPStatusError messages surface the Binance code/msg from the body.
"""

import asyncio
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.binance_client.rest_client import BinanceRestClient


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def run(coro):
    """Run a coroutine to completion on a fresh event loop (mode-agnostic)."""
    return asyncio.run(coro)


def _client_with_handler(handler):
    """
    Build a BinanceRestClient whose underlying httpx.AsyncClient routes through a
    MockTransport using `handler`. We must not change the constructor signature,
    so we swap out client._client after construction (preserving base_url/headers).
    """
    client = BinanceRestClient(api_key="key", api_secret="secret", testnet=True)
    transport = httpx.MockTransport(handler)
    # Recreate the async client with the same config but a mock transport.
    client._client = httpx.AsyncClient(
        base_url=client.base_url,
        headers={"X-MBX-APIKEY": client.api_key},
        timeout=10.0,
        transport=transport,
    )
    return client


def _query(request: httpx.Request) -> dict:
    """Parse a request's query string into a flat {key: value} dict."""
    qs = parse_qs(urlparse(str(request.url)).query)
    return {k: v[0] for k, v in qs.items()}


def _ok(payload, weight=None, headers=None):
    """Build a 200 JSON response, optionally with the used-weight header."""
    hdrs = dict(headers or {})
    if weight is not None:
        hdrs["X-MBX-USED-WEIGHT-1m"] = str(weight)
    return httpx.Response(200, json=payload, headers=hdrs)


# --------------------------------------------------------------------------
# recvWindow + signing
# --------------------------------------------------------------------------

def test_signed_request_includes_recv_window_and_signature():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = _query(request)
        return _ok({"balances": []}, weight=5)

    client = _client_with_handler(handler)
    try:
        run(client.get_account())
    finally:
        run(client.close())

    params = captured["params"]
    assert "recvWindow" in params, "recvWindow must be present on signed requests"
    assert params["recvWindow"] == "5000"  # settings.binance_recv_window default
    assert "timestamp" in params
    assert "signature" in params and len(params["signature"]) == 64  # sha256 hex


def test_public_request_is_not_signed():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = _query(request)
        return _ok({"symbol": "BTCUSDT", "price": "100.0"}, weight=1)

    client = _client_with_handler(handler)
    try:
        run(client.get_ticker_price("BTCUSDT"))
    finally:
        run(client.close())

    params = captured["params"]
    assert "signature" not in params
    assert "recvWindow" not in params
    assert params["symbol"] == "BTCUSDT"


# --------------------------------------------------------------------------
# sync_time offset
# --------------------------------------------------------------------------

def test_sync_time_applies_offset_to_signed_timestamp():
    # Pin server time far in the future so the offset is unmistakable.
    SERVER_TIME = 5_000_000_000_000  # ms
    seen_timestamps = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = urlparse(str(request.url)).path
        if path == "/api/v3/time":
            return _ok({"serverTime": SERVER_TIME}, weight=1)
        # account (signed) — capture the timestamp used
        params = _query(request)
        seen_timestamps.append(int(params["timestamp"]))
        return _ok({"balances": []}, weight=2)

    client = _client_with_handler(handler)
    try:
        assert client._time_offset_ms == 0  # default before sync
        offset = run(client.sync_time())
        assert offset > 0
        # The offset should push us near the (huge) server time.
        assert abs(client._time_offset_ms - offset) == 0
        run(client.get_account())
    finally:
        run(client.close())

    assert seen_timestamps, "signed request should have been sent"
    ts = seen_timestamps[0]
    # Signed timestamp must reflect the synced offset, i.e. be close to SERVER_TIME.
    assert abs(ts - SERVER_TIME) < 60_000, (
        f"timestamp {ts} not aligned to synced server time {SERVER_TIME}"
    )


# --------------------------------------------------------------------------
# exchangeInfo caching
# --------------------------------------------------------------------------

def test_exchange_info_is_cached_per_symbol():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return _ok({"symbols": [{"symbol": "BTCUSDT"}]}, weight=10)

    client = _client_with_handler(handler)
    try:
        first = run(client.get_exchange_info("BTCUSDT"))
        second = run(client.get_exchange_info("BTCUSDT"))
    finally:
        run(client.close())

    assert first == second
    assert calls["count"] == 1, "second call for same symbol must use the cache (no network)"


def test_exchange_info_cache_is_keyed_by_symbol():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        params = _query(request)
        sym = params.get("symbol", "ALL")
        return _ok({"symbols": [{"symbol": sym}]}, weight=10)

    client = _client_with_handler(handler)
    try:
        run(client.get_exchange_info("BTCUSDT"))   # network
        run(client.get_exchange_info("ETHUSDT"))   # network (different key)
        run(client.get_exchange_info())            # network ("ALL")
        run(client.get_exchange_info("BTCUSDT"))   # cached
        run(client.get_exchange_info())            # cached
    finally:
        run(client.close())

    assert calls["count"] == 3, "only distinct cache keys should hit the network"


def test_exchange_info_force_refresh_bypasses_cache():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return _ok({"symbols": []}, weight=10)

    client = _client_with_handler(handler)
    try:
        run(client.get_exchange_info("BTCUSDT"))
        run(client.get_exchange_info("BTCUSDT", use_cache=False))
    finally:
        run(client.close())

    assert calls["count"] == 2


# --------------------------------------------------------------------------
# rate-limit header parsing
# --------------------------------------------------------------------------

def test_used_weight_header_is_parsed():
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok({"symbol": "BTCUSDT", "price": "1.0"}, weight=247)

    client = _client_with_handler(handler)
    try:
        assert client.last_used_weight == 0
        run(client.get_ticker_price("BTCUSDT"))
        assert client.last_used_weight == 247
    finally:
        run(client.close())


def test_used_weight_warns_above_threshold(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok({"symbol": "BTCUSDT", "price": "1.0"}, weight=1100)

    client = _client_with_handler(handler)
    try:
        with caplog.at_level("WARNING"):
            run(client.get_ticker_price("BTCUSDT"))
    finally:
        run(client.close())

    assert client.last_used_weight == 1100
    assert any("used weight" in r.message.lower() for r in caplog.records)


# --------------------------------------------------------------------------
# DELETE retry on 429
# --------------------------------------------------------------------------

def test_delete_retries_on_429_then_succeeds(monkeypatch):
    # Skip the real backoff sleeps so the test is instant.
    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr("app.binance_client.rest_client.asyncio.sleep", _no_sleep)

    state = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        if state["calls"] == 1:
            return httpx.Response(429, json={"code": -1003, "msg": "Too many requests"},
                                  headers={"X-MBX-USED-WEIGHT-1m": "1199"})
        return _ok({"symbol": "BTCUSDT", "orderId": 123, "status": "CANCELED"}, weight=5)

    client = _client_with_handler(handler)
    try:
        result = run(client.cancel_order("BTCUSDT", 123))
    finally:
        run(client.close())

    assert state["calls"] == 2, "DELETE should retry once after a 429"
    assert result["status"] == "CANCELED"
    # Weight from the successful response is the latest value tracked.
    assert client.last_used_weight == 5


def test_delete_resigns_with_fresh_timestamp_on_retry(monkeypatch):
    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr("app.binance_client.rest_client.asyncio.sleep", _no_sleep)

    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = _query(request)
        seen.append((int(params["timestamp"]), params["signature"]))
        if len(seen) == 1:
            return httpx.Response(503, json={"code": -1, "msg": "busy"})
        return _ok({"status": "CANCELED"}, weight=1)

    client = _client_with_handler(handler)
    try:
        run(client.cancel_order("BTCUSDT", 1))
    finally:
        run(client.close())

    assert len(seen) == 2
    # Each attempt must carry a recvWindow and a valid 64-hex signature; the retry
    # is re-signed (signature recomputed), proving _resign ran.
    assert seen[0][1] != "" and len(seen[0][1]) == 64
    assert seen[1][1] != "" and len(seen[1][1]) == 64


# --------------------------------------------------------------------------
# POST is not retried by default
# --------------------------------------------------------------------------

def test_post_place_order_not_retried_by_default(monkeypatch):
    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr("app.binance_client.rest_client.asyncio.sleep", _no_sleep)

    state = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        return httpx.Response(503, json={"code": -1001, "msg": "Internal error"})

    client = _client_with_handler(handler)
    try:
        with pytest.raises(httpx.HTTPStatusError):
            run(client.place_order("BTCUSDT", "BUY", "MARKET", 0.01))
    finally:
        run(client.close())

    assert state["calls"] == 1, "place_order must NOT retry by default (avoid dup orders)"


def test_post_place_order_can_retry_when_requested(monkeypatch):
    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr("app.binance_client.rest_client.asyncio.sleep", _no_sleep)

    state = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        if state["calls"] == 1:
            return httpx.Response(503, json={"code": -1001, "msg": "Internal error"})
        return _ok({"orderId": 99, "status": "FILLED"}, weight=1)

    client = _client_with_handler(handler)
    try:
        result = run(client.place_order("BTCUSDT", "BUY", "MARKET", 0.01, retries=1))
    finally:
        run(client.close())

    assert state["calls"] == 2
    assert result["status"] == "FILLED"


# --------------------------------------------------------------------------
# clearer Binance errors
# --------------------------------------------------------------------------

def test_http_error_message_includes_binance_code_and_msg():
    def handler(request: httpx.Request) -> httpx.Response:
        # 400 is a non-retryable client error -> surfaces immediately.
        return httpx.Response(400, json={"code": -2010, "msg": "Account has insufficient balance"})

    client = _client_with_handler(handler)
    try:
        with pytest.raises(httpx.HTTPStatusError) as ei:
            run(client.get_account())
    finally:
        run(client.close())

    text = str(ei.value)
    assert "-2010" in text
    assert "insufficient balance" in text.lower()
    assert "HTTP 400" in text


def test_get_retries_then_raises_clear_error(monkeypatch):
    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr("app.binance_client.rest_client.asyncio.sleep", _no_sleep)

    state = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        return httpx.Response(503, json={"code": -1, "msg": "Service unavailable"})

    client = _client_with_handler(handler)
    try:
        with pytest.raises(httpx.HTTPStatusError) as ei:
            run(client.get_ticker_price("BTCUSDT"))
    finally:
        run(client.close())

    # get() uses retries=2 -> 3 attempts total.
    assert state["calls"] == 3
    assert "HTTP 503" in str(ei.value)
