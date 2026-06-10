"""
Tests for technical indicators and strategy signal generation.
"""

import pandas as pd
import numpy as np
import pytest

from app.strategies.indicators import Indicators
from app.strategies.regime_breakout import RegimeBreakoutStrategy
from app.strategies.base import SignalType


# --------------- Indicator tests ---------------

def _make_series(values: list[float]) -> pd.Series:
    return pd.Series(values, dtype=float)


def test_sma_basic():
    s = _make_series([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    result = Indicators.sma(s, period=3)
    assert pd.isna(result.iloc[0])
    assert pd.isna(result.iloc[1])
    assert result.iloc[2] == pytest.approx(2.0)  # (1+2+3)/3
    assert result.iloc[9] == pytest.approx(9.0)  # (8+9+10)/3


def test_ema_basic():
    s = _make_series([1, 2, 3, 4, 5])
    result = Indicators.ema(s, period=3)
    assert len(result) == 5
    # EMA should be responsive to recent values
    assert result.iloc[-1] > result.iloc[0]


def test_rsi_range():
    """RSI should always be between 0 and 100."""
    np.random.seed(42)
    prices = np.cumsum(np.random.randn(100)) + 100
    s = pd.Series(prices)
    rsi = Indicators.rsi(s, period=14)
    valid = rsi.dropna()
    assert (valid >= 0).all()
    assert (valid <= 100).all()


def test_rsi_trending_up():
    """A strongly rising series should produce RSI values in expected range."""
    # Alternating gains (big) and losses (tiny) to avoid all-gain NaN
    values = [100.0]
    for i in range(60):
        if i % 5 == 0:
            values.append(values[-1] - 0.5)  # small dip
        else:
            values.append(values[-1] + 2.0)  # big gain
    s = _make_series(values)
    rsi = Indicators.rsi(s, period=14)
    valid = rsi.dropna()
    assert len(valid) > 10
    # Should be well above 50 for a strong uptrend
    assert valid.iloc[-1] > 70


def test_rsi_extreme_down():
    """A monotonically decreasing series should have RSI close to 0."""
    s = _make_series(list(range(50, 1, -1)))
    rsi = Indicators.rsi(s, period=14)
    assert rsi.iloc[-1] < 5


def test_macd_returns_three():
    s = _make_series(list(range(1, 50)))
    macd_line, signal_line, histogram = Indicators.macd(s)
    assert len(macd_line) == len(s)
    assert len(signal_line) == len(s)
    assert len(histogram) == len(s)


def test_bollinger_bands():
    s = _make_series(list(range(1, 30)))
    upper, middle, lower = Indicators.bollinger_bands(s, period=10)
    valid_idx = middle.dropna().index
    for i in valid_idx:
        assert upper[i] >= middle[i] >= lower[i]


# --------------- Strategy tests ---------------

def _make_ohlcv(closes: list[float]) -> pd.DataFrame:
    df = pd.DataFrame({
        "open": closes,
        "high": [c * 1.01 for c in closes],
        "low": [c * 0.99 for c in closes],
        "close": closes,
        "volume": [1000.0] * len(closes),
    })
    return df


def test_strategy_no_signal_insufficient_data():
    """With too few data points, no signal should be generated (never crash)."""
    closes = [100.0, 101.0, 102.0]
    df = _make_ohlcv(closes)
    strategy = RegimeBreakoutStrategy()
    assert strategy.generate_signals(df, "BTCUSDT") == []


def test_strategy_get_set_params():
    s = RegimeBreakoutStrategy(entry_channel=55, exit_channel=20)
    params = s.get_params()
    assert params["entry_channel"] == 55
    assert params["exit_channel"] == 20
    s.set_params({"entry_channel": 70})
    assert s.entry_channel == 70
