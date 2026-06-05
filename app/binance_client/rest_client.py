"""
Binance Spot REST API client.

Handles authentication (HMAC-SHA256 signature), request signing, and rate-limit
awareness. Supports both the live API and the testnet.

Hardening features:
- recvWindow on every signed request (from settings.binance_recv_window).
- Server-time drift sync (sync_time) so signed timestamps stay within recvWindow.
- Per-symbol exchangeInfo cache with TTL to avoid repeated network calls.
- Idempotent retry with exponential backoff on GET and DELETE (cancel_order);
  POST (place_order) is NOT retried by default to avoid duplicate orders.
- Rate-limit header tracking (X-MBX-USED-WEIGHT-1m -> last_used_weight).
- Clearer exceptions that surface Binance error code/msg from the response body.

Reference: https://binance-docs.github.io/apidocs/spot/en/
"""

import asyncio
import hashlib
import hmac
import logging
import time
from urllib.parse import urlencode

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

LIVE_BASE = "https://api.binance.com"
TESTNET_BASE = "https://testnet.binance.vision"

LIVE_WS_BASE = "wss://stream.binance.com:9443/ws"
TESTNET_WS_BASE = "wss://testnet.binance.vision/ws"

# exchangeInfo cache TTL (seconds). Symbol filters change rarely, so ~1h is safe.
_EXCHANGE_INFO_TTL_S = 3600
# Warn if the rolling 1-minute used weight crosses this (Binance spot cap is ~1200).
_USED_WEIGHT_WARN = 1000


class BinanceRestClient:
    """
    Lightweight async wrapper around the Binance Spot REST API.
    """

    def __init__(self, api_key: str, api_secret: str, testnet: bool = True):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.base_url = TESTNET_BASE if testnet else LIVE_BASE
        # Difference (server_time - local_time) in ms; applied to signed timestamps.
        # Stays 0 until sync_time() succeeds, so behaviour is unchanged by default.
        self._time_offset_ms = 0
        # Most recent X-MBX-USED-WEIGHT-1m header value (int) seen on any response.
        self.last_used_weight = 0
        # exchangeInfo cache: {key -> (expires_at_epoch, payload)}.
        # key is the symbol string, or "ALL" for the full snapshot.
        self._exchange_info_cache: dict[str, tuple[float, dict]] = {}
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"X-MBX-APIKEY": self.api_key},
            timeout=10.0,
        )

    # ------------------------------------------------------------------
    # Signature helpers
    # ------------------------------------------------------------------

    def _timestamp(self) -> int:
        """Current epoch ms adjusted by the synced server-time offset."""
        return int(time.time() * 1000) + self._time_offset_ms

    def _sign(self, params: dict) -> dict:
        """
        Append recvWindow, timestamp and HMAC-SHA256 signature to request params.
        Binance requires every signed endpoint to include a `timestamp` and
        `signature` query parameter; `recvWindow` bounds the accepted clock drift.
        """
        params["recvWindow"] = settings.binance_recv_window
        params["timestamp"] = self._timestamp()
        query_string = urlencode(params)
        signature = hmac.new(
            self.api_secret.encode(), query_string.encode(), hashlib.sha256
        ).hexdigest()
        params["signature"] = signature
        return params

    def _resign(self, params: dict) -> dict:
        """Re-sign params for a retry: strip prior signing fields and sign afresh
        so the timestamp (and synced offset) are current for the new attempt."""
        clean = {
            k: v
            for k, v in params.items()
            if k not in ("timestamp", "signature", "recvWindow")
        }
        return self._sign(clean)

    # ------------------------------------------------------------------
    # Rate-limit awareness
    # ------------------------------------------------------------------

    def _track_used_weight(self, response: httpx.Response) -> None:
        """Read X-MBX-USED-WEIGHT-1m, cache it, and warn if it gets dangerously high."""
        raw = response.headers.get("X-MBX-USED-WEIGHT-1m")
        if raw is None:
            return
        try:
            weight = int(raw)
        except (TypeError, ValueError):
            return
        self.last_used_weight = weight
        if weight > _USED_WEIGHT_WARN:
            logger.warning(
                "Binance used weight (1m) is high: %d (cap ~1200). Backing off may be needed.",
                weight,
            )

    # ------------------------------------------------------------------
    # Error helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _describe_http_error(exc: httpx.HTTPStatusError) -> str:
        """Build a clear message that includes the Binance error code/msg if present."""
        resp = exc.response
        method = resp.request.method if resp.request else "?"
        url = str(resp.request.url) if resp.request else "?"
        detail = ""
        try:
            body = resp.json()
            if isinstance(body, dict) and ("code" in body or "msg" in body):
                detail = f" (Binance code={body.get('code')} msg={body.get('msg')!r})"
        except Exception:
            # Body wasn't JSON; fall back to raw text snippet.
            text = (resp.text or "").strip()
            if text:
                detail = f" (body={text[:200]!r})"
        return f"Binance {method} {url} -> HTTP {resp.status_code}{detail}"

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
                self._track_used_weight(resp)
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as exc:
                # raise_for_status doesn't go through _track_used_weight's caller path
                # only on the success branch; capture weight from the error response too.
                self._track_used_weight(exc.response)
                status = exc.response.status_code
                # Retry on 429 (rate limit) or 5xx (server error), not on 4xx client errors
                if status in (429, 502, 503) and attempt < attempts - 1:
                    delay = (2 ** attempt)  # 1s, 2s, 4s
                    logger.warning("Binance %s %s: HTTP %d, retrying in %ds (%d/%d)",
                                   method, path, status, delay, attempt + 1, attempts)
                    last_exc = exc
                    await asyncio.sleep(delay)
                    if signed:
                        params = self._resign(params)
                    continue
                msg = self._describe_http_error(exc)
                logger.error("Binance API error: %s | body=%s", msg, exc.response.text)
                raise httpx.HTTPStatusError(
                    msg, request=exc.request, response=exc.response
                ) from exc
            except (httpx.TimeoutException, httpx.RequestError) as exc:
                if attempt < attempts - 1:
                    delay = (2 ** attempt)
                    logger.warning("Binance %s %s: %s, retrying in %ds (%d/%d)",
                                   method, path, type(exc).__name__, delay, attempt + 1, attempts)
                    last_exc = exc
                    await asyncio.sleep(delay)
                    if signed:
                        params = self._resign(params)
                    continue
                logger.error("Network error calling Binance: %s", exc)
                raise
        raise last_exc  # should not reach here

    async def get(self, path: str, params: dict | None = None, signed: bool = False):
        return await self._request("GET", path, params, signed, retries=2)

    async def post(self, path: str, params: dict | None = None, signed: bool = False,
                   retries: int = 0):
        # POST is not retried by default to avoid placing duplicate orders.
        return await self._request("POST", path, params, signed, retries=retries)

    async def delete(self, path: str, params: dict | None = None, signed: bool = False,
                     retries: int = 2):
        # DELETE (e.g. cancel_order) is idempotent, so retry like GET.
        return await self._request("DELETE", path, params, signed, retries=retries)

    # ------------------------------------------------------------------
    # Server-time sync
    # ------------------------------------------------------------------

    async def sync_time(self) -> int:
        """
        Fetch Binance server time and cache the local clock drift so that signed
        requests carry a timestamp within `recvWindow`. Returns the offset in ms
        (server_time - local_time). Call once at engine startup (and optionally
        periodically). Safe to ignore — offset defaults to 0 if never called.
        """
        local_before = int(time.time() * 1000)
        data = await self.get("/api/v3/time")
        local_after = int(time.time() * 1000)
        server_time = int(data["serverTime"])
        # Use the midpoint of the round-trip as the local reference to reduce
        # one-way latency bias when computing the offset.
        local_mid = (local_before + local_after) // 2
        self._time_offset_ms = server_time - local_mid
        logger.info(
            "Synced Binance server time: offset=%dms (rtt=%dms)",
            self._time_offset_ms, local_after - local_before,
        )
        return self._time_offset_ms

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

    async def get_exchange_info(self, symbol: str | None = None,
                                use_cache: bool = True) -> dict:
        """
        Fetch exchange trading rules / symbol filters.

        Results are cached per-symbol (and under "ALL" for the full snapshot) with
        a ~1h TTL, so repeated calls for the same symbol do NOT hit the network.
        Pass use_cache=False to force a refresh.
        """
        cache_key = symbol if symbol else "ALL"
        now = time.time()
        if use_cache:
            cached = self._exchange_info_cache.get(cache_key)
            if cached and cached[0] > now:
                return cached[1]

        params = {}
        if symbol:
            params["symbol"] = symbol
        data = await self.get("/api/v3/exchangeInfo", params)
        self._exchange_info_cache[cache_key] = (now + _EXCHANGE_INFO_TTL_S, data)
        return data

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
                          time_in_force: str | None = None,
                          retries: int = 0) -> dict:
        """
        Place a new order on Binance.
        side: BUY or SELL
        order_type: MARKET or LIMIT
        For LIMIT orders, price and time_in_force (GTC) are required.

        retries defaults to 0: order placement is NOT retried automatically to
        avoid duplicate fills. Only raise it if the caller guarantees idempotency
        (e.g. supplies a newClientOrderId), which this helper does not yet manage.
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
        return await self.post("/api/v3/order", params, signed=True, retries=retries)

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
