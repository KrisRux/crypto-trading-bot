"""
Binance WebSocket client for real-time price streams.

Subscribes to trade/kline streams and dispatches price updates to registered
callbacks (e.g. the trading engine).
"""

import asyncio
import json
import logging
from typing import Callable

import websockets

logger = logging.getLogger(__name__)

LIVE_WS = "wss://stream.binance.com:9443/ws"


class BinanceWebSocket:
    """
    Manages a persistent WebSocket connection to Binance for real-time data.
    Reconnects automatically on disconnection.

    Always uses the live Binance WebSocket for price data (public, no auth needed).
    The testnet does not provide reliable WebSocket streams.
    """

    def __init__(self, testnet: bool = True):
        # Always use live WS for price data — it's public and doesn't require API keys
        self.base_url = LIVE_WS
        self._callbacks: list[Callable] = []
        self._running = False
        self._ws = None
        self._task: asyncio.Task | None = None

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
                async with websockets.connect(url, ping_interval=20) as ws:
                    self._ws = ws
                    logger.info("WebSocket connected: %s", url)
                    async for raw in ws:
                        msg = json.loads(raw)
                        for cb in self._callbacks:
                            try:
                                await cb(msg) if asyncio.iscoroutinefunction(cb) else cb(msg)
                            except Exception:
                                logger.exception("Error in WebSocket callback")
            except websockets.ConnectionClosed:
                logger.warning("WebSocket disconnected, reconnecting in 5s...")
                await asyncio.sleep(5)
            except Exception:
                logger.exception("WebSocket error, reconnecting in 10s...")
                await asyncio.sleep(10)

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
        logger.info("WebSocket stopped")
