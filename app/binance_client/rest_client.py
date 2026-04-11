"""
Binance Spot REST API client.

Handles authentication (HMAC-SHA256 signature), request signing, and rate-limit
awareness. Supports both the live API and the testnet.

Reference: https://binance-docs.github.io/apidocs/spot/en/
"""

import asyncio
import hashlib
import hmac
import logging
import time
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

LIVE_BASE = "https://api.binance.com"
TESTNET_BASE = "https://testnet.binance.vision"

LIVE_WS_BASE = "wss://stream.binance.com:9443/ws"
TESTNET_WS_BASE = "wss://testnet.binance.vision/ws"


class BinanceRestClient:
    """
    Lightweight async wrapper around the Binance Spot REST API.
    """

    def __init__(self, api_key: str, api_secret: str, testnet: bool = True):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.base_url = TESTNET_BASE if testnet else LIVE_BASE
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"X-MBX-APIKEY": self.api_key},
            timeout=10.0,
        )

    # ------------------------------------------------------------------
    # Signature helpers
    # ------------------------------------------------------------------

    def _sign(self, params: dict) -> dict:
        """
        Append timestamp and HMAC-SHA256 signature to request params.
        Binance requires every signed endpoint to include a `timestamp`
        and `signature` query parameter.
        """
        params["timestamp"] = int(time.time() * 1000)
        query_string = urlencode(params)
        signature = hmac.new(
            self.api_secret.encode(), query_string.encode(), hashlib.sha256
        ).hexdigest()
        params["signature"] = signature
        return params

    # ------------------------------------------------------------------
    # Generic request helpers
    # ------------------------------------------------------------------

    async def _request(self, method: str, path: str, params: dict | None = None,
                       signed: bool = False, retries: int = 0) -> dict:
        params = params or {}
        if signed:
            params = self._sign(params)
        last_exc = None
        attempts = 1 + retries
        for attempt in range(attempts):
            try:
                resp = await self._client.request(method, path, params=params)
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                # Retry on 429 (rate limit) or 5xx (server error), not on 4xx client errors
                if status in (429, 502, 503) and attempt < attempts - 1:
                    delay = (2 ** attempt)  # 1s, 2s, 4s
                    logger.warning("Binance %s %s: HTTP %d, retrying in %ds (%d/%d)",
                                   method, path, status, delay, attempt + 1, attempts)
                    last_exc = exc
                    await asyncio.sleep(delay)
                    if signed:
                        params = self._sign({k: v for k, v in params.items() if k not in ("timestamp", "signature")})
                    continue
                logger.error("Binance API error %s %s: %s", method, path, exc.response.text)
                raise
            except (httpx.RequestError, httpx.TimeoutException) as exc:
                if attempt < attempts - 1:
                    delay = (2 ** attempt)
                    logger.warning("Binance %s %s: %s, retrying in %ds (%d/%d)",
                                   method, path, type(exc).__name__, delay, attempt + 1, attempts)
                    last_exc = exc
                    await asyncio.sleep(delay)
                    continue
                logger.error("Network error calling Binance: %s", exc)
                raise
        raise last_exc  # should not reach here

    async def get(self, path: str, params: dict | None = None, signed: bool = False):
        return await self._request("GET", path, params, signed, retries=2)

    async def post(self, path: str, params: dict | None = None, signed: bool = False):
        return await self._request("POST", path, params, signed)

    async def delete(self, path: str, params: dict | None = None, signed: bool = False):
        return await self._request("DELETE", path, params, signed)

    # ------------------------------------------------------------------
    # Public endpoints (no signature required)
    # ------------------------------------------------------------------

    async def get_server_time(self) -> dict:
        return await self.get("/api/v3/time")

    async def get_ticker_price(self, symbol: str) -> dict:
        """Current price for a symbol."""
        return await self.get("/api/v3/ticker/price", {"symbol": symbol})

    async def get_klines(self, symbol: str, interval: str = "1m",
                         limit: int = 100) -> list:
        """
        Candlestick/kline data.
        interval: 1m, 5m, 15m, 1h, 4h, 1d, etc.
        """
        return await self.get("/api/v3/klines", {
            "symbol": symbol, "interval": interval, "limit": limit
        })

    async def get_exchange_info(self, symbol: str | None = None) -> dict:
        params = {}
        if symbol:
            params["symbol"] = symbol
        return await self.get("/api/v3/exchangeInfo", params)

    async def get_order_book(self, symbol: str, limit: int = 20) -> dict:
        return await self.get("/api/v3/depth", {"symbol": symbol, "limit": limit})

    # ------------------------------------------------------------------
    # Signed endpoints (trading)
    # ------------------------------------------------------------------

    async def get_account(self) -> dict:
        """Fetch account balances."""
        return await self.get("/api/v3/account", signed=True)

    async def place_order(self, symbol: str, side: str, order_type: str,
                          quantity: float, price: float | None = None,
                          time_in_force: str | None = None) -> dict:
        """
        Place a new order on Binance.
        side: BUY or SELL
        order_type: MARKET or LIMIT
        For LIMIT orders, price and time_in_force (GTC) are required.
        """
        params = {
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "quantity": f"{quantity:.8f}",
        }
        if order_type == "LIMIT":
            if price is None:
                raise ValueError("price is required for LIMIT orders")
            params["price"] = f"{price:.8f}"
            params["timeInForce"] = time_in_force or "GTC"

        logger.info("Placing %s %s order: %s qty=%s price=%s",
                     side, order_type, symbol, quantity, price)
        return await self.post("/api/v3/order", params, signed=True)

    async def cancel_order(self, symbol: str, order_id: int) -> dict:
        return await self.delete("/api/v3/order", {
            "symbol": symbol, "orderId": order_id
        }, signed=True)

    async def get_open_orders(self, symbol: str | None = None) -> list:
        params = {}
        if symbol:
            params["symbol"] = symbol
        return await self.get("/api/v3/openOrders", params, signed=True)

    async def get_all_orders(self, symbol: str, limit: int = 50) -> list:
        return await self.get("/api/v3/allOrders", {
            "symbol": symbol, "limit": limit
        }, signed=True)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def close(self):
        await self._client.aclose()
