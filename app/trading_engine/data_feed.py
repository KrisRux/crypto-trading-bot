"""
TimeframeFeed — closed-candle frames for per-strategy timeframes.

The engine's main loop runs on 15m candles. Strategies that declare a custom
``interval`` (e.g. regime_breakout on 4h) need their own data without
re-fetching ~200 candles from Binance every 15 minutes. This module provides:

* **Closed candles only** — any bar whose open time belongs to the candle
  still in formation is dropped (same no-lookahead policy as the main loop).
* **Caching keyed on the last closed bar** — a (symbol, interval) frame is
  re-fetched only when a NEW bar of that interval has closed; in between, the
  cached frame is returned (a 4h frame is fetched at most 6 times/day instead
  of 96).
* **Per-consumer bar dedup** — ``is_new_closed_bar`` lets the engine invoke a
  strategy at most once per closed bar of its interval, so a 4h breakout does
  not re-fire on all 16 fifteen-minute cycles inside the same 4h candle.

The class is transport-agnostic: it receives the engine's async
``fetch_klines(symbol, interval, limit)`` and never talks to Binance itself.
"""

from __future__ import annotations

import logging
import time
from typing import Awaitable, Callable

import pandas as pd

logger = logging.getLogger(__name__)

_INTERVAL_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
    "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000,
    "6h": 21_600_000, "8h": 28_800_000, "12h": 43_200_000,
    "1d": 86_400_000,
}

FetchFn = Callable[..., Awaitable[pd.DataFrame]]


def interval_to_ms(interval: str) -> int:
    try:
        return _INTERVAL_MS[interval]
    except KeyError:
        raise ValueError(
            f"Unsupported interval '{interval}'. Known: {sorted(_INTERVAL_MS)}"
        ) from None


class TimeframeFeed:
    def __init__(self, fetch_klines: FetchFn, *,
                 clock: Callable[[], float] = time.time):
        self._fetch = fetch_klines
        self._clock = clock  # injectable for tests
        self._cache: dict[tuple[str, str], pd.DataFrame] = {}
        self._last_bar_seen: dict[tuple[str, str, str], pd.Timestamp] = {}

    # ------------------------------------------------------------------

    def _current_open_ms(self, interval: str) -> int:
        """Open time (ms) of the candle currently in formation."""
        iv = interval_to_ms(interval)
        now_ms = int(self._clock() * 1000)
        return (now_ms // iv) * iv

    async def get_closed(self, symbol: str, interval: str,
                         min_bars: int) -> pd.DataFrame | None:
        """Return at least ``min_bars`` CLOSED candles for (symbol, interval),
        or ``None`` when not enough data exists. Cached until a new bar closes."""
        key = (symbol, interval)
        forming_open_ms = self._current_open_ms(interval)
        last_closed_open_ms = forming_open_ms - interval_to_ms(interval)

        cached = self._cache.get(key)
        if cached is not None and len(cached) >= min_bars:
            cached_last_ms = int(cached.index[-1].value // 1_000_000)
            if cached_last_ms == last_closed_open_ms:
                return cached  # newest closed bar already in cache

        df_full = await self._fetch(symbol, interval=interval, limit=min_bars + 1)
        if df_full is None or df_full.empty:
            return None
        # Drop the forming candle by timestamp, not blindly by position: if
        # Binance happens to return only closed bars the last row must stay.
        forming_ts = pd.Timestamp(forming_open_ms, unit="ms")
        closed = df_full[df_full.index < forming_ts]
        if len(closed) < min_bars:
            logger.debug("TimeframeFeed: %s %s has %d/%d closed bars",
                         symbol, interval, len(closed), min_bars)
            return None
        self._cache[key] = closed
        return closed

    # ------------------------------------------------------------------

    def is_new_closed_bar(self, consumer: str, symbol: str, interval: str,
                          bar_ts: pd.Timestamp) -> bool:
        """True the first time ``consumer`` sees ``bar_ts`` as the latest
        closed bar of (symbol, interval); False on every later call until a
        new bar closes. Marks the bar as seen as a side effect."""
        key = (consumer, symbol, interval)
        if self._last_bar_seen.get(key) == bar_ts:
            return False
        self._last_bar_seen[key] = bar_ts
        return True
