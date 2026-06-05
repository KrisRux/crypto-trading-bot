"""
Tests for DIRECTIONAL awareness (bull/bear/flat) of the market regime service.

The regime labels (trend/range/volatile/defensive) intentionally carry NO
direction — an orderly decline is "trend" just like an orderly rise. These
tests cover the orthogonal `direction` field added to RegimeSnapshot and the
aggregate `global_direction`.

No network access: all DataFrames are synthetic.

Run:
    venv/Scripts/python.exe -m pytest tests/test_regime_direction.py -q
"""

import pandas as pd

from app.adaptive.market_regime_service import MarketRegimeService, RegimeSnapshot


# EMA_PERIOD is 50; direction needs len >= EMA_PERIOD + 2 closed bars.
N = 80


def _make_ohlcv(closes: list[float], volume: float = 1000.0) -> pd.DataFrame:
    """Build a synthetic OHLCV frame from a close series."""
    return pd.DataFrame({
        "open": closes,
        "high": [c * 1.01 for c in closes],
        "low": [c * 0.99 for c in closes],
        "close": closes,
        "volume": [volume] * len(closes),
    })


def _uptrend() -> pd.DataFrame:
    # Steadily rising: close stays above a rising EMA.
    closes = [100.0 + i * 1.0 for i in range(N)]
    return _make_ohlcv(closes)


def _downtrend() -> pd.DataFrame:
    # Steadily falling: close stays below a falling EMA.
    closes = [300.0 - i * 1.0 for i in range(N)]
    return _make_ohlcv(closes)


def _choppy() -> pd.DataFrame:
    # Flat sideways market: price sits on a flat EMA with zero slope, so
    # neither the up (close>EMA and slope>0) nor down (close<EMA and
    # slope<0) condition holds -> flat.
    closes = [100.0] * N
    return _make_ohlcv(closes)


# --------------------------------------------------------------------------
# Direction computation
# --------------------------------------------------------------------------

def test_strong_uptrend_is_up():
    svc = MarketRegimeService()
    snap = svc.compute(_uptrend(), "BTCUSDT")
    assert snap.direction == "up"


def test_downtrend_is_down():
    svc = MarketRegimeService()
    snap = svc.compute(_downtrend(), "BTCUSDT")
    assert snap.direction == "down"


def test_choppy_is_flat():
    svc = MarketRegimeService()
    snap = svc.compute(_choppy(), "BTCUSDT")
    assert snap.direction == "flat"


def test_insufficient_data_is_flat():
    svc = MarketRegimeService()
    # Far fewer than EMA_PERIOD + 2 rows.
    snap = svc.compute(_make_ohlcv([100.0 + i for i in range(10)]), "BTCUSDT")
    assert snap.direction == "flat"


def test_default_direction_is_flat():
    """Direction defaults to 'flat' when constructed without it."""
    snap = RegimeSnapshot(
        symbol="BTCUSDT", regime="range", adx=10.0,
        atr_pct=1.0, bb_width_pct=2.0, volume_ratio=1.0,
    )
    assert snap.direction == "flat"


# --------------------------------------------------------------------------
# to_dict / existing fields preserved (no regressions)
# --------------------------------------------------------------------------

def test_to_dict_contains_direction():
    svc = MarketRegimeService()
    snap = svc.compute(_uptrend(), "BTCUSDT")
    d = snap.to_dict()
    assert "direction" in d
    assert d["direction"] == "up"


def test_existing_snapshot_fields_preserved():
    """The fields engine/routes depend on must still be present."""
    svc = MarketRegimeService()
    snap = svc.compute(_uptrend(), "BTCUSDT")
    d = snap.to_dict()
    for key in ("symbol", "regime", "adx", "atr_pct",
                "bb_width_pct", "volume_ratio", "timestamp"):
        assert key in d, f"missing existing field: {key}"
    # Attribute access still works too.
    assert snap.symbol == "BTCUSDT"
    assert snap.regime in ("trend", "range", "volatile", "defensive")
    assert isinstance(snap.adx, float)


def test_regime_label_unchanged_by_direction():
    """Direction is orthogonal: a downtrend is still a valid regime label,
    never the literal string 'down'."""
    svc = MarketRegimeService()
    snap = svc.compute(_downtrend(), "BTCUSDT")
    assert snap.regime in ("trend", "range", "volatile", "defensive")
    assert snap.direction == "down"


# --------------------------------------------------------------------------
# is_bearish / is_bullish helpers
# --------------------------------------------------------------------------

def test_is_bullish_true_on_uptrend():
    svc = MarketRegimeService()
    svc.compute(_uptrend(), "BTCUSDT")
    assert svc.is_bullish("BTCUSDT") is True
    assert svc.is_bearish("BTCUSDT") is False


def test_is_bearish_true_on_downtrend():
    svc = MarketRegimeService()
    svc.compute(_downtrend(), "ETHUSDT")
    assert svc.is_bearish("ETHUSDT") is True
    assert svc.is_bullish("ETHUSDT") is False


def test_untracked_symbol_returns_false():
    svc = MarketRegimeService()
    assert svc.is_bearish("NOPEUSDT") is False
    assert svc.is_bullish("NOPEUSDT") is False


# --------------------------------------------------------------------------
# global_direction aggregation
# --------------------------------------------------------------------------

def test_global_snapshot_contains_global_direction():
    svc = MarketRegimeService()
    svc.compute(_uptrend(), "BTCUSDT")
    gs = svc.global_snapshot()
    assert "global_direction" in gs
    # Existing keys still present.
    assert "global_regime" in gs
    assert "symbols" in gs
    assert "BTCUSDT" in gs["symbols"]


def test_global_direction_empty_is_flat():
    svc = MarketRegimeService()
    assert svc.global_direction() == "flat"
    assert svc.global_snapshot()["global_direction"] == "flat"


def test_global_direction_majority_up():
    svc = MarketRegimeService()
    svc.compute(_uptrend(), "BTCUSDT")
    svc.compute(_uptrend(), "ETHUSDT")
    svc.compute(_downtrend(), "LTCUSDT")
    # 2/3 up >= 50% -> up
    assert svc.global_direction() == "up"


def test_global_direction_majority_down():
    svc = MarketRegimeService()
    svc.compute(_downtrend(), "BTCUSDT")
    svc.compute(_downtrend(), "ETHUSDT")
    svc.compute(_uptrend(), "LTCUSDT")
    # 2/3 down >= 50% -> down
    assert svc.global_direction() == "down"


def test_global_direction_down_priority_on_tie():
    """A 50/50 down-vs-up split surfaces as 'down' (bear checked first)."""
    svc = MarketRegimeService()
    svc.compute(_downtrend(), "BTCUSDT")
    svc.compute(_uptrend(), "ETHUSDT")
    # down 1/2 = 50% -> down wins the tie
    assert svc.global_direction() == "down"


def test_global_direction_no_majority_is_flat():
    """No direction reaches 50% -> flat."""
    svc = MarketRegimeService()
    svc.compute(_uptrend(), "BTCUSDT")
    svc.compute(_downtrend(), "ETHUSDT")
    svc.compute(_choppy(), "LTCUSDT")
    # up 1/3, down 1/3, flat 1/3 -> none >= 50% -> flat
    assert svc.global_direction() == "flat"
