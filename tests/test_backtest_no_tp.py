"""
Tests for the disable-TP option of the back-tester (atr_tp_mult/tp_pct <= 0).

Trend-following strategies must be able to let winners run: with the TP
disabled a long held through a monotonic uptrend should only ever exit on
its own signal, the stop, or end-of-data — never on take_profit.
"""

from __future__ import annotations

import pytest

from app.backtesting.engine import (
    Backtester,
    BacktestConfig,
    EXIT_TAKE_PROFIT,
)
from app.strategies.base import SignalType

from tests.test_backtesting import ScriptedStrategy, make_uptrend


def test_stop_levels_tp_none_when_atr_mult_nonpositive():
    bt = Backtester(
        ScriptedStrategy({}),
        BacktestConfig(use_atr_stops=True, atr_sl_mult=2.0, atr_tp_mult=0.0),
    )
    sl, tp = bt._stop_levels("BUY", 100.0, atr_value=2.0)
    assert sl == pytest.approx(96.0)
    assert tp is None
    # Shorts mirrored: SL above entry, TP still disabled.
    sl_s, tp_s = bt._stop_levels("SELL", 100.0, atr_value=2.0)
    assert sl_s == pytest.approx(104.0)
    assert tp_s is None


def test_stop_levels_tp_none_when_pct_nonpositive():
    bt = Backtester(
        ScriptedStrategy({}),
        BacktestConfig(use_atr_stops=False, sl_pct=3.0, tp_pct=0.0),
    )
    sl, tp = bt._stop_levels("BUY", 100.0, atr_value=None)
    assert sl == pytest.approx(97.0)
    assert tp is None


def test_stop_loss_never_disabled():
    bt = Backtester(
        ScriptedStrategy({}),
        BacktestConfig(use_atr_stops=True, atr_sl_mult=2.0, atr_tp_mult=0.0),
    )
    sl, _ = bt._stop_levels("BUY", 100.0, atr_value=1.5)
    assert sl is not None and sl < 100.0


def test_uptrend_winner_runs_without_tp_cap():
    """Same uptrend, same entry: without a TP the long must capture MORE
    profit than the TP-capped version and never exit on take_profit."""
    df = make_uptrend(n=300)
    entry_script = {100: SignalType.BUY}

    capped = Backtester(
        ScriptedStrategy(dict(entry_script)),
        BacktestConfig(use_atr_stops=True, atr_sl_mult=2.0, atr_tp_mult=3.0,
                       fee_pct=0.1, slippage_pct=0.02),
    ).run(df)

    uncapped = Backtester(
        ScriptedStrategy(dict(entry_script)),
        BacktestConfig(use_atr_stops=True, atr_sl_mult=2.0, atr_tp_mult=0.0,
                       fee_pct=0.1, slippage_pct=0.02),
    ).run(df)

    assert capped.num_trades == 1
    assert uncapped.num_trades == 1
    assert capped.trades[0].exit_reason == EXIT_TAKE_PROFIT
    assert uncapped.trades[0].exit_reason != EXIT_TAKE_PROFIT
    assert uncapped.trades[0].pnl.net_pnl > capped.trades[0].pnl.net_pnl
