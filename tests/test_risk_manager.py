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
    assert rm.should_close_position(50000, 53000, 48000, 52000) == "tp"


def test_should_close_sl():
    rm = RiskManager()
    assert rm.should_close_position(50000, 47000, 48000, 52000) == "sl"


def test_should_not_close():
    rm = RiskManager()
    assert rm.should_close_position(50000, 50500, 48000, 52000) is None


def test_custom_sl_tp():
    rm = RiskManager()
    sl = rm.calculate_stop_loss(50000, sl_pct=1.0)
    tp = rm.calculate_take_profit(50000, tp_pct=2.0)
    assert sl == pytest.approx(49500.0)
    assert tp == pytest.approx(51000.0)
