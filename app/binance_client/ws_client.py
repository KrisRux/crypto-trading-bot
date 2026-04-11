"""
Binance WebSocket client for real-time price streams.

Subscribes to trade/kline streams and dispatches price updates to registered
callbacks (e.g. the trading engine).
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Callable

import websockets

logger = logging.getLogger(__name__)

LIVE_WS = "wss://stream.binance.com:9443/ws"

# Reconnection backoff
_INITIAL_DELAY = 5
_MAX_DELAY = 60


class BinanceWebSocket:
    """
    Manages a persistent WebSocket connection to Binance for real-time data.
    Reconnects automatically on disconnection with exponential backoff.

    Always uses the live Binance WebSocket for price data (public, no auth needed).
    The testnet does not provide reliable WebSocket streams.
    """

    def __init__(self):
        self.base_url = LIVE_WS
        self._callbacks: list[Callable] = []
        self._running = False
        self._ws = None
        self._task: asyncio.Task | None = None
        # Reconnection state
        self._reconnect_delay = _INITIAL_DELAY
        self._reconnect_count = 0
        self._connected_since: datetime | None = None

    def on_message(self, callback: Callable):
        """Register a callback that receives parsed JSON messages."""
        self._callbacks.append(callback)

    async def start(self, streams: list[str]):
        """
        Connect to combined streams.
        streams example: ["btcusdt@trade", "btcusdt@kline_1m"]
        """
        self._running = True
        url = f"{self.base_url}/{'/'.join(streams)}"
        self._task = asyncio.create_task(self._listen(url))
        logger.info("WebSocket started for streams: %s", streams)

    async def _listen(self, url: str):
        while self._running:
            try:
                async with websockets.connect(
                    url,
                    ping_interval=30,
                    ping_timeout=10,
                    close_timeout=10,
                ) as ws:
                    self._ws = ws
                    self._reconnect_delay = _INITIAL_DELAY  # reset on successful connect
                    self._connected_since = datetime.now(timezone.utc)
                    logger.info("WebSocket connected: %s", url)
                    async for raw in ws:
                        msg = json.loads(raw)
                        for cb in self._callbacks:
                            try:
                                await cb(msg) if asyncio.iscoroutinefunction(cb) else cb(msg)
                            except Exception:
                                logger.exception("Error in WebSocket callback")
            except websockets.ConnectionClosed as e:
                self._reconnect_count += 1
                uptime = ""
                if self._connected_since:
                    secs = (datetime.now(timezone.utc) - self._connected_since).total_seconds()
                    uptime = f" (uptime {secs:.0f}s)"
                logger.warning(
                    "WebSocket disconnected (code=%s)%s, reconnecting in %ds... [reconnects=%d]",
                    e.code, uptime, self._reconnect_delay, self._reconnect_count,
                )
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, _MAX_DELAY)
            except asyncio.CancelledError:
                return
            except Exception:
                self._reconnect_count += 1
                logger.exception(
                    "WebSocket error, reconnecting in %ds... [reconnects=%d]",
                    self._reconnect_delay, self._reconnect_count,
                )
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, _MAX_DELAY)

    async def stop(self):
        self._running = False
        if self._ws:
            await self._ws.close()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("WebSocket stopped (total reconnects: %d)", self._reconnect_count)
