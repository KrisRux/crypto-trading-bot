"""
Binance USD-M Futures REST client — TESTNET-first, for the long/short research
track (Option B, Phase 2).

Why a separate client from rest_client.py
-----------------------------------------
The spot client cannot short. Futures can, but the endpoints, base URL and a few
parameters differ (positionSide, reduceOnly, leverage). Keeping this isolated
means the spot long-only live system is completely untouched: this module is
only ever used by the futures-testnet execution path.

SAFETY:
* Defaults to the TESTNET host (``https://testnet.binancefuture.com``).
* Defaults to ``default_leverage=1`` (no leverage) — shorts are sized like spot.
* No withdrawal endpoints exist here. Trading only.

Signing (HMAC-SHA256), recvWindow, server-time sync, weight tracking and the
retry policy mirror :class:`app.binance_client.rest_client.BinanceRestClient`
(POST is never auto-retried, to avoid duplicate orders).

Reference: https://binance-docs.github.io/apidocs/futures/en/
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

FUTURES_LIVE_BASE = "https://fapi.binance.com"
FUTURES_TESTNET_BASE = "https://testnet.binancefuture.com"

_USED_WEIGHT_WARN = 1000


class BinanceFuturesClient:
    """Async wrapper around the Binance USD-M Futures REST API (testnet default)."""

    def __init__(self, api_key: str, api_secret: str, testnet: bool = True,
                 default_leverage: int = 1, transport: httpx.AsyncBaseTransport | None = None):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.default_leverage = max(1, int(default_leverage))
        self.base_url = FUTURES_TESTNET_BASE if testnet else FUTURES_LIVE_BASE
        self._time_offset_ms = 0
        self.last_used_weight = 0
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"X-MBX-APIKEY": self.api_key},
            timeout=10.0,
            transport=transport,  # injectable for tests (MockTransport)
        )

    # ------------------------------------------------------------------
    # Signing
    # ------------------------------------------------------------------

    def _timestamp(self) -> int:
        return int(time.time() * 1000) + self._time_offset_ms

    def _sign(self, params: dict) -> dict:
        params["recvWindow"] = settings.binance_recv_window
        params["timestamp"] = self._timestamp()
        query = urlencode(params)
        params["signature"] = hmac.new(
            self.api_secret.encode(), query.encode(), hashlib.sha256
        ).hexdigest()
        return params

    def _resign(self, params: dict) -> dict:
        clean = {k: v for k, v in params.items()
                 if k not in ("timestamp", "signature", "recvWindow")}
        return self._sign(clean)

    # ------------------------------------------------------------------
    # Request plumbing
    # ------------------------------------------------------------------

    def _track_used_weight(self, resp: httpx.Response) -> None:
        raw = resp.headers.get("X-MBX-USED-WEIGHT-1m")
        if raw is None:
            return
        try:
            self.last_used_weight = int(raw)
        except (TypeError, ValueError):
            return
        if self.last_used_weight > _USED_WEIGHT_WARN:
            logger.warning("Futures used weight (1m) high: %d", self.last_used_weight)

    @staticmethod
    def _describe_http_error(exc: httpx.HTTPStatusError) -> str:
        resp = exc.response
        method = resp.request.method if resp.request else "?"
        url = str(resp.request.url) if resp.request else "?"
        detail = ""
        try:
            body = resp.json()
            if isinstance(body, dict) and ("code" in body or "msg" in body):
                detail = f" (Binance code={body.get('code')} msg={body.get('msg')!r})"
        except Exception:
            text = (resp.text or "").strip()
            if text:
                detail = f" (body={text[:200]!r})"
        return f"Futures {method} {url} -> HTTP {resp.status_code}{detail}"

    async def _request(self, method: str, path: str, params: dict | None = None,
                       signed: bool = False, retries: int = 0) -> dict | list:
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
                self._track_used_weight(exc.response)
                status = exc.response.status_code
                if status in (429, 502, 503) and attempt < attempts - 1:
                    await asyncio.sleep(2 ** attempt)
                    last_exc = exc
                    if signed:
                        params = self._resign(params)
                    continue
                msg = self._describe_http_error(exc)
                logger.error("Futures API error: %s", msg)
                raise httpx.HTTPStatusError(msg, request=exc.request,
                                            response=exc.response) from exc
            except (httpx.TimeoutException, httpx.RequestError) as exc:
                if attempt < attempts - 1:
                    await asyncio.sleep(2 ** attempt)
                    last_exc = exc
                    if signed:
                        params = self._resign(params)
                    continue
                logger.error("Network error calling Futures: %s", exc)
                raise
        raise last_exc

    async def get(self, path, params=None, signed=False):
        return await self._request("GET", path, params, signed, retries=2)

    async def post(self, path, params=None, signed=False, retries=0):
        return await self._request("POST", path, params, signed, retries=retries)

    # ------------------------------------------------------------------
    # Public / read endpoints
    # ------------------------------------------------------------------

    async def sync_time(self) -> int:
        local_before = int(time.time() * 1000)
        data = await self.get("/fapi/v1/time")
        local_after = int(time.time() * 1000)
        local_mid = (local_before + local_after) // 2
        self._time_offset_ms = int(data["serverTime"]) - local_mid
        logger.info("Synced Futures server time: offset=%dms", self._time_offset_ms)
        return self._time_offset_ms

    async def get_mark_price(self, symbol: str) -> float:
        data = await self.get("/fapi/v1/premiumIndex", {"symbol": symbol})
        return float(data["markPrice"])

    async def get_klines(self, symbol: str, interval: str = "4h", limit: int = 100) -> list:
        return await self.get("/fapi/v1/klines",
                              {"symbol": symbol, "interval": interval, "limit": limit})

    async def get_balance(self, asset: str = "USDT") -> float:
        """Available balance for ``asset`` (signed)."""
        data = await self.get("/fapi/v2/balance", signed=True)
        for b in data:
            if b.get("asset") == asset:
                return float(b.get("availableBalance", b.get("balance", 0)))
        return 0.0

    async def get_position(self, symbol: str) -> dict | None:
        """Current position for ``symbol`` (signed). None if flat."""
        data = await self.get("/fapi/v2/positionRisk", {"symbol": symbol}, signed=True)
        for p in data:
            if p.get("symbol") == symbol and float(p.get("positionAmt", 0)) != 0.0:
                return p
        return None

    # ------------------------------------------------------------------
    # Trading endpoints (testnet by default)
    # ------------------------------------------------------------------

    async def set_leverage(self, symbol: str, leverage: int | None = None) -> dict:
        lev = int(leverage or self.default_leverage)
        return await self.post("/fapi/v1/leverage",
                               {"symbol": symbol, "leverage": lev}, signed=True)

    async def place_market_order(self, symbol: str, side: str, quantity: float,
                                 reduce_only: bool = False) -> dict:
        """Place a MARKET order. ``side`` SELL opens a short (one-way mode);
        pass ``reduce_only=True`` to close an existing position.

        POST is never auto-retried (no duplicate fills)."""
        params = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": f"{quantity:.8f}",
        }
        if reduce_only:
            params["reduceOnly"] = "true"
        logger.info("Futures MARKET %s %s qty=%s reduce_only=%s",
                    side, symbol, quantity, reduce_only)
        return await self.post("/fapi/v1/order", params, signed=True, retries=0)

    async def close(self):
        await self._client.aclose()
