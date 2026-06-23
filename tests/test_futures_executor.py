"""
Tests for the futures-testnet long/short executor. No network, no real DB
beyond in-memory SQLite. A FakeFuturesClient records orders and returns canned
fills, so we assert the executor's open/close/stop-and-reverse logic and that
PnL is booked NET for SHORTS.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.trade import Trade, TradeStatus, OrderSide
from app.trading_engine.futures_executor import FuturesTestnetExecutor, MODE
from app.strategies.regime_breakout_ls import RegimeBreakoutLongShort

from tests.test_backtesting import _ohlcv_from_closes


class FakeFuturesClient:
    def __init__(self, mark=100.0, balance=10_000.0):
        self.mark = mark
        self.balance = balance
        self.orders = []
        self.leverage_set = []
    async def get_mark_price(self, symbol):
        return self.mark
    async def get_balance(self, asset="USDT"):
        return self.balance
    async def set_leverage(self, symbol, leverage=None):
        self.leverage_set.append(symbol)
        return {"leverage": leverage or 1}
    async def place_market_order(self, symbol, side, quantity, reduce_only=False):
        self.orders.append({"symbol": symbol, "side": side, "qty": quantity,
                            "reduce_only": reduce_only})
        return {"orderId": len(self.orders), "status": "FILLED",
                "avgPrice": str(self.mark)}


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _user(uid=1):
    return SimpleNamespace(id=uid)


def _strat():
    return RegimeBreakoutLongShort(regime_ema_period=50, slope_lookback=5,
                                   entry_channel=20, atr_period=14,
                                   min_atr_pct=0.01, max_atr_pct=50.0)


def _downtrend():
    return _ohlcv_from_closes([300.0 - i * 1.5 for i in range(120)],
                              high_mult=1.001, low_mult=0.999)


def _uptrend():
    return _ohlcv_from_closes([100.0 + i * 1.5 for i in range(120)],
                              high_mult=1.001, low_mult=0.999)


def _exec():
    return FuturesTestnetExecutor(strategy=_strat())


def test_opens_short_on_bear_breakdown(db):
    ex = _exec()
    client = FakeFuturesClient(mark=130.0)
    asyncio.run(ex.run(db, _user(), "BTCUSDT", _downtrend(), client))
    t = db.query(Trade).filter(Trade.mode == MODE).first()
    assert t is not None and t.side == OrderSide.SELL and t.status == TradeStatus.OPEN
    assert client.orders[-1]["side"] == "SELL" and client.orders[-1]["reduce_only"] is False
    assert client.leverage_set == ["BTCUSDT"]


def test_opens_long_on_bull_breakout(db):
    ex = _exec()
    client = FakeFuturesClient(mark=270.0)
    asyncio.run(ex.run(db, _user(), "BTCUSDT", _uptrend(), client))
    t = db.query(Trade).filter(Trade.mode == MODE).first()
    assert t is not None and t.side == OrderSide.BUY


def test_no_double_open_when_already_in_position(db):
    ex = _exec()
    client = FakeFuturesClient(mark=130.0)
    asyncio.run(ex.run(db, _user(), "BTCUSDT", _downtrend(), client))
    n_orders = len(client.orders)
    # Same bar/state again → must NOT open a second position.
    asyncio.run(ex.run(db, _user(), "BTCUSDT", _downtrend(), client))
    assert len(client.orders) == n_orders
    assert db.query(Trade).filter(Trade.mode == MODE,
                                  Trade.status == TradeStatus.OPEN).count() == 1


def test_hard_stop_closes_short_and_books_net(db):
    ex = _exec()
    # Open a short at mark 100 (stop will sit above entry).
    open_client = FakeFuturesClient(mark=100.0)
    asyncio.run(ex.run(db, _user(), "BTCUSDT", _downtrend(), open_client))
    t = db.query(Trade).filter(Trade.mode == MODE).first()
    assert t.side == OrderSide.SELL
    stop = t.stop_loss
    assert stop > t.entry_price                      # short stop above entry

    # Next cycle: mark gaps above the stop → hard stop must close it.
    stop_client = FakeFuturesClient(mark=stop + 5.0)
    asyncio.run(ex.run(db, _user(), "BTCUSDT", _downtrend(), stop_client))
    db.refresh(t)
    assert t.status == TradeStatus.CLOSED
    assert t.exit_reason == "stop_loss"
    assert t.pnl is not None                         # NET pnl booked
    assert stop_client.orders[-1]["reduce_only"] is True
    assert stop_client.orders[-1]["side"] == "BUY"   # closing a short


def test_short_pnl_is_negative_when_price_rose(db):
    """A short closed at a HIGHER price than entry must book a loss (sign check)."""
    ex = _exec()
    open_client = FakeFuturesClient(mark=100.0)
    asyncio.run(ex.run(db, _user(), "BTCUSDT", _downtrend(), open_client))
    t = db.query(Trade).filter(Trade.mode == MODE).first()
    stop_client = FakeFuturesClient(mark=t.stop_loss + 1.0)  # price up → short loses
    asyncio.run(ex.run(db, _user(), "BTCUSDT", _downtrend(), stop_client))
    db.refresh(t)
    assert t.pnl < 0
