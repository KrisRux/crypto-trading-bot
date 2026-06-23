"""
Tests for the Binance USD-M Futures testnet client. No network: an httpx
MockTransport answers every request, and we assert on the request the client
built (path, signing, params) and on response parsing.
"""

from __future__ import annotations

import asyncio
import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.binance_client.futures_client import (
    BinanceFuturesClient, FUTURES_TESTNET_BASE, FUTURES_LIVE_BASE,
)


def _client(handler):
    transport = httpx.MockTransport(handler)
    return BinanceFuturesClient(api_key="k", api_secret="s", testnet=True,
                                transport=transport)


def _qs(request):
    return {k: v[0] for k, v in parse_qs(urlparse(str(request.url)).query).items()}


def test_defaults_to_testnet_and_1x_leverage():
    c = BinanceFuturesClient("k", "s")
    assert c.testnet is True
    assert c.base_url == FUTURES_TESTNET_BASE
    assert c.default_leverage == 1
    assert BinanceFuturesClient("k", "s", testnet=False).base_url == FUTURES_LIVE_BASE


def test_get_mark_price_parses_float():
    def handler(request):
        assert request.url.path == "/fapi/v1/premiumIndex"
        return httpx.Response(200, json={"symbol": "BTCUSDT", "markPrice": "61234.5"})
    c = _client(handler)
    price = asyncio.run(c.get_mark_price("BTCUSDT"))
    assert price == pytest.approx(61234.5)
    asyncio.run(c.close())


def test_get_balance_selects_asset():
    def handler(request):
        assert request.url.path == "/fapi/v2/balance"
        q = _qs(request)
        assert "signature" in q and "timestamp" in q  # signed
        return httpx.Response(200, json=[
            {"asset": "BNB", "availableBalance": "1.0"},
            {"asset": "USDT", "availableBalance": "5000.0", "balance": "5100.0"},
        ])
    c = _client(handler)
    assert asyncio.run(c.get_balance("USDT")) == pytest.approx(5000.0)
    asyncio.run(c.close())


def test_get_position_none_when_flat():
    def handler(request):
        return httpx.Response(200, json=[
            {"symbol": "BTCUSDT", "positionAmt": "0.000"},
        ])
    c = _client(handler)
    assert asyncio.run(c.get_position("BTCUSDT")) is None
    asyncio.run(c.close())


def test_get_position_returns_open_short():
    def handler(request):
        return httpx.Response(200, json=[
            {"symbol": "BTCUSDT", "positionAmt": "-0.010", "entryPrice": "60000"},
        ])
    c = _client(handler)
    pos = asyncio.run(c.get_position("BTCUSDT"))
    assert pos is not None and float(pos["positionAmt"]) == -0.01
    asyncio.run(c.close())


def test_place_market_short_builds_signed_post():
    captured = {}
    def handler(request):
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["q"] = _qs(request)
        return httpx.Response(200, json={"orderId": 1, "status": "FILLED",
                                         "side": "SELL", "avgPrice": "60000"})
    c = _client(handler)
    res = asyncio.run(c.place_market_order("BTCUSDT", "SELL", 0.01))
    assert captured["method"] == "POST"
    assert captured["path"] == "/fapi/v1/order"
    assert captured["q"]["side"] == "SELL"
    assert captured["q"]["type"] == "MARKET"
    assert "signature" in captured["q"]
    assert "reduceOnly" not in captured["q"]   # opening, not closing
    assert res["status"] == "FILLED"
    asyncio.run(c.close())


def test_close_short_sets_reduce_only():
    captured = {}
    def handler(request):
        captured["q"] = _qs(request)
        return httpx.Response(200, json={"orderId": 2, "status": "FILLED"})
    c = _client(handler)
    asyncio.run(c.place_market_order("BTCUSDT", "BUY", 0.01, reduce_only=True))
    assert captured["q"]["reduceOnly"] == "true"
    assert captured["q"]["side"] == "BUY"
    asyncio.run(c.close())


def test_set_leverage_uses_default():
    captured = {}
    def handler(request):
        captured["path"] = request.url.path
        captured["q"] = _qs(request)
        return httpx.Response(200, json={"leverage": 1, "symbol": "BTCUSDT"})
    c = _client(handler)
    asyncio.run(c.set_leverage("BTCUSDT"))
    assert captured["path"] == "/fapi/v1/leverage"
    assert captured["q"]["leverage"] == "1"
    asyncio.run(c.close())


def test_http_error_surfaces_binance_code():
    def handler(request):
        return httpx.Response(400, json={"code": -2019, "msg": "Margin is insufficient."})
    c = _client(handler)
    with pytest.raises(httpx.HTTPStatusError) as ei:
        asyncio.run(c.place_market_order("BTCUSDT", "SELL", 1.0))
    assert "-2019" in str(ei.value)
    asyncio.run(c.close())
