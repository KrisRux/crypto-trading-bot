"""
Tests for the research-only long/short breakout variant.

Pins: symmetric signal logic (long on bull up-breakout, short on bear
down-breakdown), the ATR cost filter, registry wiring, and that it is NOT
registered in the live engine (spot is long-only).
"""

from __future__ import annotations

from app.strategies.base import SignalType
from app.strategies.regime_breakout_ls import RegimeBreakoutLongShort
from app.backtesting.engine import resolve_strategy, Backtester, BacktestConfig

from tests.test_backtesting import _ohlcv_from_closes


def _strat(**over):
    p = dict(regime_ema_period=50, slope_lookback=5, entry_channel=20,
             atr_period=14, min_atr_pct=0.01, max_atr_pct=50.0)
    p.update(over)
    return RegimeBreakoutLongShort(**p)


def test_long_on_bull_breakout():
    df = _ohlcv_from_closes([100.0 + i * 1.5 for i in range(120)],
                            high_mult=1.001, low_mult=0.999)
    sig = _strat().generate_signals(df, "T")
    assert len(sig) == 1 and sig[0].signal_type == SignalType.BUY


def test_short_on_bear_breakdown():
    # Monotonic downtrend: price below a falling EMA, fresh channel low.
    df = _ohlcv_from_closes([300.0 - i * 1.5 for i in range(120)],
                            high_mult=1.001, low_mult=0.999)
    sig = _strat().generate_signals(df, "T")
    assert len(sig) == 1 and sig[0].signal_type == SignalType.SELL
    assert sig[0].metadata["sell_score"] == 85.0


def test_atr_filter_blocks_flat_market():
    df = _ohlcv_from_closes([100.0] * 120, high_mult=1.0001, low_mult=0.9999)
    assert _strat(min_atr_pct=0.5).generate_signals(df, "T") == []


def test_registry_has_ls_but_engine_does_not():
    assert resolve_strategy("regime_breakout_ls").name == "regime_breakout_ls"
    # The live engine must NOT register the short-capable variant.
    import app.main as main_mod
    import inspect
    src = inspect.getsource(main_mod)
    assert "RegimeBreakoutLongShort" not in src
    assert "regime_breakout_ls" not in src


def test_short_side_profits_in_downtrend_backtest():
    """The whole point: on a clean downtrend, allow_short must turn a loss-free
    flat into a positive short PnL."""
    down = _ohlcv_from_closes([300.0 - i * 0.5 for i in range(400)],
                              high_mult=1.002, low_mult=0.998)
    cfg = BacktestConfig(use_atr_stops=True, atr_sl_mult=2.0, atr_tp_mult=0.0,
                         fee_pct=0.1, slippage_pct=0.02, position_size_pct=50.0,
                         allow_short=True)
    res = Backtester(_strat(), cfg).run(down)
    assert res.num_trades >= 1
    assert any(t.side == "SELL" for t in res.trades)
    assert res.metrics.net_pnl > 0
