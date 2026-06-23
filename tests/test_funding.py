"""
Tests for perpetual funding cost modelling (app/backtesting/funding.py) and its
integration into the back-tester. No network: a fake session injects rows.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.backtesting.funding import load_funding_rates, funding_cost
from app.backtesting.engine import Backtester, BacktestConfig
from app.strategies.base import SignalType

from tests.test_backtesting import ScriptedStrategy, _ohlcv_from_closes


class _FakeResp:
    def __init__(self, payload):
        self._p = payload
    def raise_for_status(self):
        pass
    def json(self):
        return self._p


class _FakeSession:
    """Returns one page of funding rows then empty (stops pagination)."""
    def __init__(self, rows):
        self._rows = rows
        self._served = False
    def get(self, url, params=None, timeout=15):
        if self._served:
            return _FakeResp([])
        self._served = True
        return _FakeResp(self._rows)


def _rows(start_ms, n, rate):
    step = 8 * 60 * 60 * 1000
    return [{"symbol": "BTCUSDT", "fundingTime": start_ms + i * step,
             "fundingRate": str(rate)} for i in range(n)]


def test_load_funding_rates_parses_series():
    start = 1_700_000_000_000
    sess = _FakeSession(_rows(start, 5, 0.0001))
    s = load_funding_rates("BTCUSDT", start, start + 5 * 8 * 3600 * 1000,
                           session=sess, pause_s=0)
    assert len(s) == 5
    assert s.iloc[0] == pytest.approx(0.0001)
    assert isinstance(s.index, pd.DatetimeIndex)


def test_funding_cost_sign_long_vs_short():
    idx = pd.to_datetime([1_700_000_000_000 + i * 8 * 3600 * 1000
                          for i in range(3)], unit="ms")
    rates = pd.Series([0.0001, 0.0001, 0.0001], index=idx)  # positive funding
    entry, exit_ = idx[0] - pd.Timedelta(hours=1), idx[-1] + pd.Timedelta(hours=1)
    notional = 10_000.0
    long_cost = funding_cost("BUY", notional, entry, exit_, rates)
    short_cost = funding_cost("SELL", notional, entry, exit_, rates)
    # Positive funding: long PAYS (cost>0), short RECEIVES (cost<0), mirrored.
    assert long_cost == pytest.approx(0.0003 * notional)
    assert short_cost == pytest.approx(-0.0003 * notional)


def test_funding_cost_empty_or_no_window():
    assert funding_cost("SELL", 1000, "2020-01-01", "2020-01-02",
                        pd.Series(dtype=float)) == 0.0
    idx = pd.to_datetime([1_900_000_000_000], unit="ms")  # far future
    rates = pd.Series([0.01], index=idx)
    assert funding_cost("SELL", 1000, "2020-01-01", "2020-01-02", rates) == 0.0


def test_backtester_applies_funding_to_short():
    """A short held through positive funding should have its net IMPROVED by the
    received funding versus the same run without funding."""
    closes = [300.0 - i * 0.5 for i in range(120)]
    df = _ohlcv_from_closes(closes, high_mult=1.002, low_mult=0.998)
    # Open a short early (scripted SELL while flat + allow_short), hold to end.
    script = {60: SignalType.SELL}
    base_cfg = dict(use_atr_stops=True, atr_sl_mult=99.0, atr_tp_mult=0.0,
                    fee_pct=0.1, slippage_pct=0.02, allow_short=True,
                    position_size_pct=50.0)

    no_fund = Backtester(ScriptedStrategy(dict(script)),
                         BacktestConfig(**base_cfg)).run(df)

    # Positive funding across the whole frame → short receives a credit.
    idx = pd.to_datetime([int(df.index[0].timestamp() * 1000) + i * 8 * 3600 * 1000
                          for i in range(400)], unit="ms")
    funding = pd.Series([0.0005] * len(idx), index=idx)
    with_fund = Backtester(ScriptedStrategy(dict(script)),
                           BacktestConfig(funding_rates=funding, **base_cfg)).run(df)

    assert no_fund.num_trades >= 1 and with_fund.num_trades >= 1
    assert with_fund.trades[0].side == "SELL"
    assert with_fund.trades[0].funding_cost < 0           # a credit
    assert with_fund.metrics.net_pnl > no_fund.metrics.net_pnl
