from types import SimpleNamespace

from app.strategies.base import Signal, SignalType
from app.trading_engine.engine import TradingEngine


class _RegimeService:
    def __init__(self, snap, bearish=False):
        self.snapshots = {snap.symbol: snap}
        self._bearish = bearish

    def is_bearish(self, symbol):
        return self._bearish


def _engine_with_snapshot(snap, bearish=False):
    engine = object.__new__(TradingEngine)
    engine.meta_controller = SimpleNamespace(
        regime_service=_RegimeService(snap, bearish=bearish)
    )
    return engine


def _buy_signal(score=90):
    return Signal(
        signal_type=SignalType.BUY,
        symbol="BTCUSDT",
        price=100.0,
        strategy_name="embient_enhanced",
        metadata={"buy_score": score},
    )


def test_macro_filter_allows_strong_local_uptrend_against_htf_lag(monkeypatch):
    monkeypatch.setattr("app.trading_engine.engine.settings.flat_in_bear", True)
    monkeypatch.setattr("app.trading_engine.engine.settings.mtf_filter_enabled", True)
    monkeypatch.setattr("app.trading_engine.engine.settings.mtf_countertrend_override_enabled", True)
    monkeypatch.setattr("app.trading_engine.engine.settings.mtf_countertrend_min_score", 90.0)
    monkeypatch.setattr("app.trading_engine.engine.settings.mtf_countertrend_min_adx", 32.0)
    monkeypatch.setattr("app.trading_engine.engine.settings.mtf_countertrend_min_volume_ratio", 1.3)
    monkeypatch.setattr("app.trading_engine.engine.settings.mtf_countertrend_risk_multiplier", 0.5)
    snap = SimpleNamespace(
        symbol="BTCUSDT",
        regime="trend",
        direction="up",
        adx=40.0,
        volume_ratio=2.0,
    )
    signal = _buy_signal(score=90)
    engine = _engine_with_snapshot(snap, bearish=False)

    filtered = engine._apply_macro_trend_filter([signal], "BTCUSDT", htf_up=False, sym_snap=snap)

    assert filtered == [signal]
    assert filtered[0].metadata["macro_override"] == "strong_local_long"
    assert filtered[0].metadata["macro_risk_multiplier"] == 0.5


def test_macro_filter_still_blocks_true_bearish_regime(monkeypatch):
    monkeypatch.setattr("app.trading_engine.engine.settings.flat_in_bear", True)
    monkeypatch.setattr("app.trading_engine.engine.settings.mtf_filter_enabled", True)
    monkeypatch.setattr("app.trading_engine.engine.settings.mtf_countertrend_override_enabled", True)
    snap = SimpleNamespace(
        symbol="BTCUSDT",
        regime="trend",
        direction="up",
        adx=45.0,
        volume_ratio=3.0,
    )
    signal = _buy_signal(score=95)
    engine = _engine_with_snapshot(snap, bearish=True)

    filtered = engine._apply_macro_trend_filter([signal], "BTCUSDT", htf_up=False, sym_snap=snap)

    assert filtered == []


def test_macro_filter_blocks_weak_local_setup_against_htf_downtrend(monkeypatch):
    monkeypatch.setattr("app.trading_engine.engine.settings.flat_in_bear", True)
    monkeypatch.setattr("app.trading_engine.engine.settings.mtf_filter_enabled", True)
    monkeypatch.setattr("app.trading_engine.engine.settings.mtf_countertrend_override_enabled", True)
    monkeypatch.setattr("app.trading_engine.engine.settings.mtf_countertrend_min_score", 90.0)
    snap = SimpleNamespace(
        symbol="BTCUSDT",
        regime="trend",
        direction="up",
        adx=40.0,
        volume_ratio=2.0,
    )
    signal = _buy_signal(score=80)
    engine = _engine_with_snapshot(snap, bearish=False)

    filtered = engine._apply_macro_trend_filter([signal], "BTCUSDT", htf_up=False, sym_snap=snap)

    assert filtered == []


def test_macro_filter_blocks_strong_setup_when_override_disabled(monkeypatch):
    monkeypatch.setattr("app.trading_engine.engine.settings.flat_in_bear", True)
    monkeypatch.setattr("app.trading_engine.engine.settings.mtf_filter_enabled", True)
    monkeypatch.setattr("app.trading_engine.engine.settings.mtf_countertrend_override_enabled", False)
    snap = SimpleNamespace(
        symbol="BTCUSDT",
        regime="trend",
        direction="up",
        adx=45.0,
        volume_ratio=2.0,
    )
    signal = _buy_signal(score=95)
    engine = _engine_with_snapshot(snap, bearish=False)

    filtered = engine._apply_macro_trend_filter([signal], "BTCUSDT", htf_up=False, sym_snap=snap)

    assert filtered == []


def test_macro_filter_threshold_values_pass(monkeypatch):
    monkeypatch.setattr("app.trading_engine.engine.settings.flat_in_bear", True)
    monkeypatch.setattr("app.trading_engine.engine.settings.mtf_filter_enabled", True)
    monkeypatch.setattr("app.trading_engine.engine.settings.mtf_countertrend_override_enabled", True)
    monkeypatch.setattr("app.trading_engine.engine.settings.mtf_countertrend_min_score", 90.0)
    monkeypatch.setattr("app.trading_engine.engine.settings.mtf_countertrend_min_adx", 32.0)
    monkeypatch.setattr("app.trading_engine.engine.settings.mtf_countertrend_min_volume_ratio", 1.3)
    snap = SimpleNamespace(
        symbol="BTCUSDT",
        regime="trend",
        direction="up",
        adx=32.0,
        volume_ratio=1.3,
    )
    signal = _buy_signal(score=90)
    engine = _engine_with_snapshot(snap, bearish=False)

    filtered = engine._apply_macro_trend_filter([signal], "BTCUSDT", htf_up=False, sym_snap=snap)

    assert filtered == [signal]


def test_macro_filter_blocks_range_regime_override(monkeypatch):
    monkeypatch.setattr("app.trading_engine.engine.settings.flat_in_bear", True)
    monkeypatch.setattr("app.trading_engine.engine.settings.mtf_filter_enabled", True)
    monkeypatch.setattr("app.trading_engine.engine.settings.mtf_countertrend_override_enabled", True)
    snap = SimpleNamespace(
        symbol="BTCUSDT",
        regime="range",
        direction="up",
        adx=45.0,
        volume_ratio=2.0,
    )
    signal = _buy_signal(score=95)
    engine = _engine_with_snapshot(snap, bearish=False)

    filtered = engine._apply_macro_trend_filter([signal], "BTCUSDT", htf_up=False, sym_snap=snap)

    assert filtered == []


def test_macro_filter_keeps_sell_signals_when_buy_is_blocked(monkeypatch):
    monkeypatch.setattr("app.trading_engine.engine.settings.flat_in_bear", True)
    monkeypatch.setattr("app.trading_engine.engine.settings.mtf_filter_enabled", True)
    snap = SimpleNamespace(
        symbol="BTCUSDT",
        regime="range",
        direction="down",
        adx=15.0,
        volume_ratio=0.5,
    )
    buy = _buy_signal(score=95)
    sell = Signal(
        signal_type=SignalType.SELL,
        symbol="BTCUSDT",
        price=100.0,
        strategy_name="embient_enhanced",
        metadata={"sell_score": 90},
    )
    engine = _engine_with_snapshot(snap, bearish=False)

    filtered = engine._apply_macro_trend_filter([buy, sell], "BTCUSDT", htf_up=False, sym_snap=snap)

    assert filtered == [sell]
