"""
Unit tests for the RegimeBreakoutStrategy (app/strategies/regime_breakout.py).

All data is deterministic and synthetic. The key behaviours pinned here:
  - no signals with insufficient history (safe in the 15m live loop)
  - no BUY in a bear/flat regime, ever (direction-aware gate)
  - BUY on a fresh Donchian breakout in a bull regime
  - the ATR cost filter vetoes near-zero-volatility breakouts
  - edge-triggered SELL on exit-channel break and on regime flip
  - exit SELLs carry sell_score=0 so they can never open a paper short
  - backtest integration: profitable on a clean trend, flat in a downtrend
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from app.strategies.base import SignalType
from app.strategies.regime_breakout import RegimeBreakoutStrategy
from app.backtesting.engine import Backtester, BacktestConfig, resolve_strategy

from tests.test_backtesting import _ohlcv_from_closes


# Small windows so synthetic frames stay light.
def make_strategy(**over):
    params = dict(regime_ema_period=50, slope_lookback=5,
                  entry_channel=20, exit_channel=10,
                  atr_period=14, min_atr_pct=0.01, max_atr_pct=50.0)
    params.update(over)
    return RegimeBreakoutStrategy(**params)


def _uptrend_with_breakout(n=120, base=100.0, step=0.3):
    """Rising series whose last close is a fresh high (breakout bar).

    Wicks are kept tight (0.1%) so the per-bar step (~0.3%) genuinely clears
    the prior bars' HIGHS — a close-confirmed Donchian breakout."""
    closes = [base + i * step for i in range(n)]
    return _ohlcv_from_closes(closes, high_mult=1.001, low_mult=0.999)


def _downtrend(n=120, base=300.0, step=0.5):
    closes = [max(1.0, base - i * step) for i in range(n)]
    return _ohlcv_from_closes(closes)


def test_no_signal_with_short_history():
    strat = make_strategy()
    df = _uptrend_with_breakout(n=30)  # below min bars
    assert strat.generate_signals(df, "TESTUSDT") == []


def test_buy_on_breakout_in_bull_regime():
    strat = make_strategy()
    df = _uptrend_with_breakout()
    signals = strat.generate_signals(df, "TESTUSDT")
    assert len(signals) == 1
    sig = signals[0]
    assert sig.signal_type == SignalType.BUY
    assert sig.metadata["buy_score"] >= 80.0


def test_no_buy_in_downtrend_regime():
    strat = make_strategy()
    df = _downtrend()
    signals = strat.generate_signals(df, "TESTUSDT")
    # Either nothing or an exit SELL — never a BUY in a bear regime.
    assert all(s.signal_type != SignalType.BUY for s in signals)


def test_no_buy_on_flat_market_even_at_marginal_high():
    """Flat series + last bar barely above: EMA slope ~0 passes, but the ATR
    filter must veto the entry (move too small to beat costs)."""
    closes = [100.0] * 119 + [100.05]
    df = _ohlcv_from_closes(closes, high_mult=1.0001, low_mult=0.9999)
    strat = make_strategy(min_atr_pct=0.5)  # realistic cost filter
    signals = strat.generate_signals(df, "TESTUSDT")
    assert all(s.signal_type != SignalType.BUY for s in signals)


def test_exit_sell_on_channel_break_is_edge_triggered():
    # Uptrend, then one bar drops below the 10-bar low channel but stays
    # above the regime EMA (so ONLY the channel-break edge fires, not the
    # regime flip).
    closes = [100.0 + i * 0.5 for i in range(100)]
    closes += [closes[-1] - 9.5]  # cross bar: below channel, above EMA50
    df = _ohlcv_from_closes(closes)
    strat = make_strategy()
    signals = strat.generate_signals(df, "TESTUSDT")
    assert len(signals) == 1
    assert signals[0].signal_type == SignalType.SELL
    assert signals[0].metadata["exit_only"] is True
    assert signals[0].metadata["sell_score"] == 0.0

    # One bar later (mild recovery, no new cross, regime still bull):
    # no further SELL spam — the exit is edge-triggered.
    closes2 = closes + [closes[-1] + 0.5]
    df2 = _ohlcv_from_closes(closes2)
    signals2 = strat.generate_signals(df2, "TESTUSDT")
    assert signals2 == []


def test_registry_resolves_strategy():
    strat = resolve_strategy("regime_breakout")
    assert strat.name == "regime_breakout"


def test_backtest_uptrend_profitable_and_downtrend_flat():
    """Integration: on a clean long trend the strategy must be net-positive
    with few trades; on a clean downtrend it must do (almost) nothing."""
    strat_cfg = dict(regime_ema_period=50, slope_lookback=5,
                     entry_channel=20, exit_channel=10,
                     atr_period=14, min_atr_pct=0.01, max_atr_pct=50.0)
    cfg = BacktestConfig(use_atr_stops=True, atr_sl_mult=2.0, atr_tp_mult=0.0,
                         fee_pct=0.1, slippage_pct=0.02,
                         position_size_pct=50.0)

    # Steep trend (1.5/bar on base 100) so closes clear the default 0.5%
    # high-wick markup of the synthetic builder.
    up = _ohlcv_from_closes([100.0 + i * 1.5 for i in range(400)])
    res_up = Backtester(RegimeBreakoutStrategy(**strat_cfg), cfg).run(up)
    assert res_up.num_trades >= 1
    assert res_up.metrics.net_pnl > 0

    down = _ohlcv_from_closes([max(1.0, 300.0 - i * 0.4) for i in range(400)])
    res_down = Backtester(RegimeBreakoutStrategy(**strat_cfg), cfg).run(down)
    assert res_down.num_trades == 0, "no entries should ever fire in a downtrend"
