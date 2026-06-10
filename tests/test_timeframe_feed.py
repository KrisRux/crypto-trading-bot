"""
Tests for per-strategy timeframes (Cycle 2 of the profitability overhaul):

  - TimeframeFeed: closed-candles-only, per-bar caching, per-consumer dedup
  - TradingEngine._run_strategies: a strategy declaring interval="4h" receives
    its dedicated 4h frame (not the 15m one) and fires at most once per closed
    4h bar
  - TradingEngine._entry_plan: signal-provided atr_pct/tp_atr_mult override
    the 15m snapshot (a 4h entry must not get a 15m-sized stop)

No network: fetches and clocks are injected.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pandas as pd
import pytest

from app.strategies.base import Signal, SignalType, Strategy
from app.trading_engine.data_feed import TimeframeFeed, interval_to_ms
from app.trading_engine.engine import TradingEngine
from app.trading_engine.risk_manager import RiskManager


H4 = interval_to_ms("4h")


def _df_4h(n: int, end_open_ms: int) -> pd.DataFrame:
    """n candles whose LAST row opens at end_open_ms (i.e. possibly forming)."""
    opens = [end_open_ms - (n - 1 - i) * H4 for i in range(n)]
    idx = pd.to_datetime(opens, unit="ms")
    base = [100.0 + i for i in range(n)]
    return pd.DataFrame(
        {"open": base, "high": [b + 1 for b in base], "low": [b - 1 for b in base],
         "close": [b + 0.5 for b in base], "volume": [1000.0] * n},
        index=idx,
    )


class FakeClockFetch:
    """Injectable clock + fetch that always returns candles up to 'now'."""

    def __init__(self, now_ms: int):
        self.now_ms = now_ms
        self.fetch_calls = 0

    def clock(self) -> float:
        return self.now_ms / 1000.0

    async def fetch(self, symbol, interval="1m", limit=100):
        self.fetch_calls += 1
        forming_open = (self.now_ms // H4) * H4
        return _df_4h(limit, forming_open)


# ---------------------------------------------------------------------------
# TimeframeFeed
# ---------------------------------------------------------------------------

def test_feed_drops_forming_candle_and_caches():
    now_ms = 1_750_000_000_000 - (1_750_000_000_000 % H4) + H4 // 2  # mid-bar
    fx = FakeClockFetch(now_ms)
    feed = TimeframeFeed(fx.fetch, clock=fx.clock)

    df = asyncio.run(feed.get_closed("BTCUSDT", "4h", min_bars=50))
    assert df is not None and len(df) >= 50
    forming_open = (now_ms // H4) * H4
    # Newest bar in the frame is the last CLOSED one, never the forming one.
    assert int(df.index[-1].value // 1_000_000) == forming_open - H4
    assert fx.fetch_calls == 1

    # Same bar still forming -> cache hit, no second fetch.
    df2 = asyncio.run(feed.get_closed("BTCUSDT", "4h", min_bars=50))
    assert fx.fetch_calls == 1
    assert df2.index[-1] == df.index[-1]

    # Clock crosses into the next 4h bar -> refetch with one more closed bar.
    fx.now_ms += H4
    df3 = asyncio.run(feed.get_closed("BTCUSDT", "4h", min_bars=50))
    assert fx.fetch_calls == 2
    assert df3.index[-1] > df.index[-1]


def test_feed_returns_none_when_not_enough_history():
    now_ms = 1_750_000_000_000
    fx = FakeClockFetch(now_ms)

    async def short_fetch(symbol, interval="1m", limit=100):
        forming_open = (now_ms // H4) * H4
        return _df_4h(10, forming_open)  # only 10 candles available

    feed = TimeframeFeed(short_fetch, clock=fx.clock)
    assert asyncio.run(feed.get_closed("BTCUSDT", "4h", min_bars=50)) is None


def test_feed_bar_dedup_per_consumer():
    fx = FakeClockFetch(1_750_000_000_000)
    feed = TimeframeFeed(fx.fetch, clock=fx.clock)
    ts1 = pd.Timestamp("2026-06-01 00:00")
    ts2 = pd.Timestamp("2026-06-01 04:00")
    assert feed.is_new_closed_bar("stratA", "BTCUSDT", "4h", ts1) is True
    assert feed.is_new_closed_bar("stratA", "BTCUSDT", "4h", ts1) is False
    # Different consumer or new bar -> True again.
    assert feed.is_new_closed_bar("stratB", "BTCUSDT", "4h", ts1) is True
    assert feed.is_new_closed_bar("stratA", "BTCUSDT", "4h", ts2) is True


# ---------------------------------------------------------------------------
# Engine dispatch (unbound methods on a fake self — no DB, no network)
# ---------------------------------------------------------------------------

class CountingStrategy(Strategy):
    name = "counting"

    def __init__(self, interval=None, min_history_bars=5, name="counting"):
        self.name = name
        self.interval = interval
        self.min_history_bars = min_history_bars
        self.calls: list[pd.DataFrame] = []

    def generate_signals(self, df, symbol, precomputed_adx=None):
        self.calls.append(df)
        return [Signal(signal_type=SignalType.BUY, symbol=symbol,
                       price=float(df["close"].iloc[-1]),
                       strategy_name=self.name, confidence=0.9)]


def _fake_engine(strategies):
    fx = FakeClockFetch(1_750_000_000_000)
    return SimpleNamespace(strategies=strategies,
                           feed=TimeframeFeed(fx.fetch, clock=fx.clock))


def test_run_strategies_routes_custom_interval_frame():
    s4h = CountingStrategy(interval="4h", name="s4h")
    s15 = CountingStrategy(interval=None, name="s15")
    fake = _fake_engine([s4h, s15])

    df15 = _df_4h(30, 1_750_000_000_000)   # shape irrelevant, identity matters
    df4h = _df_4h(20, 1_749_000_000_000)
    signals = TradingEngine._run_strategies(
        fake, df15, "BTCUSDT", None, alt_frames={"4h": df4h})

    assert len(signals) == 2
    assert s4h.calls[0] is df4h, "4h strategy must receive the 4h frame"
    assert s15.calls[0] is df15, "default strategy must receive the cycle frame"

    # Same closed 4h bar on the next cycle: 4h strategy must NOT re-fire.
    signals2 = TradingEngine._run_strategies(
        fake, df15, "BTCUSDT", None, alt_frames={"4h": df4h})
    assert len(s4h.calls) == 1
    assert len(s15.calls) == 2
    assert all(s.strategy_name != "s4h" for s in signals2)


def test_run_strategies_skips_custom_interval_without_frame():
    s4h = CountingStrategy(interval="4h", name="s4h")
    fake = _fake_engine([s4h])
    df15 = _df_4h(30, 1_750_000_000_000)
    signals = TradingEngine._run_strategies(fake, df15, "BTCUSDT", None,
                                            alt_frames=None)
    assert signals == [] and s4h.calls == []


def test_custom_intervals_collects_enabled_strategies():
    s4h = CountingStrategy(interval="4h", name="a")
    s4h.min_history_bars = 100
    s4h_b = CountingStrategy(interval="4h", name="b")
    s4h_b.min_history_bars = 220
    s_off = CountingStrategy(interval="1d", name="off")
    s_off.enabled = False
    fake = SimpleNamespace(strategies=[s4h, s4h_b, s_off])
    out = TradingEngine._custom_intervals(fake)
    assert out == {"4h": 220}


# ---------------------------------------------------------------------------
# _entry_plan ATR / TP overrides
# ---------------------------------------------------------------------------

def _entry_plan_fake_self():
    return SimpleNamespace(
        meta_controller=None,
        risk_manager=RiskManager(max_position_pct=2.0, default_sl_pct=3.0,
                                 default_tp_pct=5.0),
        _round_price=lambda symbol, p: p,
    )


def test_entry_plan_uses_signal_atr_override(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "use_atr_stops", True)
    fake = _entry_plan_fake_self()
    # atr_pct=2% of 100 -> atr_price=2.0; default mults: SL 2x, TP 3x.
    sl, tp, qty = TradingEngine._entry_plan(
        fake, "BTCUSDT", "BUY", 100.0, 10_000.0, atr_pct_override=2.0)
    assert sl == pytest.approx(100.0 - 2 * 2.0)
    assert tp == pytest.approx(100.0 + 3 * 2.0)
    assert qty > 0


def test_entry_plan_honors_tp_atr_mult_override(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "use_atr_stops", True)
    fake = _entry_plan_fake_self()
    sl, tp, _ = TradingEngine._entry_plan(
        fake, "BTCUSDT", "BUY", 100.0, 10_000.0,
        atr_pct_override=2.0, tp_atr_mult_override=12.0)
    assert sl == pytest.approx(96.0)      # stop unchanged
    assert tp == pytest.approx(124.0)     # 12 x ATR — winners not capped


def test_entry_plan_falls_back_without_override(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "use_atr_stops", True)
    fake = _entry_plan_fake_self()  # meta_controller=None -> no snapshot ATR
    sl, tp, _ = TradingEngine._entry_plan(fake, "BTCUSDT", "BUY", 100.0, 10_000.0)
    # No ATR available anywhere -> fixed-percentage stops.
    assert sl == pytest.approx(97.0)
    assert tp == pytest.approx(105.0)
