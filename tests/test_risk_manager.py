"""Tests for the risk manager."""

import pytest
from app.trading_engine.risk_manager import RiskManager


def test_position_size():
    rm = RiskManager(max_position_pct=2.0)
    qty = rm.calculate_position_size(capital=10000, price=50000)
    # 2% of 10000 = 200 USDT -> 200 / 50000 = 0.004 BTC
    assert qty == pytest.approx(0.004)


def test_stop_loss():
    rm = RiskManager(default_sl_pct=3.0)
    sl = rm.calculate_stop_loss(entry_price=50000)
    assert sl == pytest.approx(48500.0)


def test_take_profit():
    rm = RiskManager(default_tp_pct=5.0)
    tp = rm.calculate_take_profit(entry_price=50000)
    assert tp == pytest.approx(52500.0)


def test_should_close_tp():
    rm = RiskManager()
    # now returns (reason, exit_level): book PnL at the target, not at 53000
    assert rm.should_close_position(50000, 53000, 48000, 52000) == ("tp", 52000)


def test_should_close_sl():
    rm = RiskManager()
    assert rm.should_close_position(50000, 47000, 48000, 52000) == ("sl", 48000)


def test_should_not_close():
    rm = RiskManager()
    assert rm.should_close_position(50000, 50500, 48000, 52000) is None


def test_sl_wins_tie_when_candle_spans_both():
    # A candle whose range hits BOTH the SL (48000) and TP (52000): book the loss.
    rm = RiskManager()
    res = rm.should_close_position(
        50000, 50000, 48000, 52000, candle_high=53000, candle_low=47000,
    )
    assert res == ("sl", 48000)


def test_short_exits_inverted():
    rm = RiskManager()
    # short: SL above entry, TP below. Price drops to TP -> win at the level.
    assert rm.should_close_position(50000, 47000, 52000, 48000, side="SELL") == ("tp", 48000)
    # price rises to SL -> loss at the level
    assert rm.should_close_position(50000, 53000, 52000, 48000, side="SELL") == ("sl", 52000)


def test_atr_stops_long_and_short():
    rm = RiskManager()
    sl, tp = rm.calculate_atr_stops(100.0, atr=2.0, side="BUY", sl_mult=2.0, tp_mult=3.0)
    assert sl == pytest.approx(96.0) and tp == pytest.approx(106.0)
    sl_s, tp_s = rm.calculate_atr_stops(100.0, atr=2.0, side="SELL", sl_mult=2.0, tp_mult=3.0)
    assert sl_s == pytest.approx(104.0) and tp_s == pytest.approx(94.0)


def test_atr_stops_fallback_when_no_atr():
    rm = RiskManager(default_sl_pct=3.0, default_tp_pct=5.0)
    sl, tp = rm.calculate_atr_stops(100.0, atr=0.0)
    assert sl == pytest.approx(97.0) and tp == pytest.approx(105.0)


def test_risk_based_sizing_respects_risk_and_cap():
    rm = RiskManager(max_position_pct=60.0)
    # risk 1% of 10000 = 100 USDT; stop 2 away from entry 100 -> qty 50 (5000 USDT < 6000 cap)
    qty = rm.calculate_position_size_risk(10000, entry_price=100, stop_price=98, risk_pct=1.0)
    assert qty == pytest.approx(50.0)
    # a tiny stop distance would blow past the 60% notional cap (6000 USDT -> 60 units) -> capped
    qty_capped = rm.calculate_position_size_risk(10000, entry_price=100, stop_price=99.99, risk_pct=1.0)
    assert qty_capped == pytest.approx(60.0)


def test_custom_sl_tp():
    rm = RiskManager()
    sl = rm.calculate_stop_loss(50000, sl_pct=1.0)
    tp = rm.calculate_take_profit(50000, tp_pct=2.0)
    assert sl == pytest.approx(49500.0)
    assert tp == pytest.approx(51000.0)
