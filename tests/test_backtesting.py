"""
Tests for the back-testing harness (app/backtesting/*).

NO NETWORK: every test builds deterministic synthetic OHLCV data in-memory.
The Binance REST loader (`load_klines_rest`) is exercised only through a fake
HTTP getter via dependency injection — no socket is ever opened.

Run with:
    venv/Scripts/python.exe -m pytest tests/test_backtesting.py -q
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from app.backtesting.data import (
    interval_to_ms,
    load_dataframe,
    load_klines_rest,
    normalize_ohlcv,
)
from app.backtesting.engine import (
    Backtester,
    BacktestConfig,
    BacktestResult,
    EXIT_STOP_LOSS,
    EXIT_TAKE_PROFIT,
    resolve_strategy,
    walk_forward,
)
from app.backtesting.metrics import BacktestMetrics, compute_metrics
from app.strategies.base import Signal, SignalType, Strategy


# ===========================================================================
# Synthetic data builders (deterministic — no randomness unless seeded)
# ===========================================================================

def _ohlcv_from_closes(closes, *, start="2021-01-01", freq="15min",
                       high_mult=1.005, low_mult=0.995, volume=1000.0):
    """
    Build an OHLCV frame from a close-price path.

    open[i]  = close[i-1] (open[0] = close[0])
    high/low straddle the open/close so SL/TP have room to trigger.
    """
    closes = list(map(float, closes))
    n = len(closes)
    opens = [closes[0]] + closes[:-1]
    highs, lows = [], []
    for o, c in zip(opens, closes):
        hi = max(o, c) * high_mult
        lo = min(o, c) * low_mult
        highs.append(hi)
        lows.append(lo)
    idx = pd.date_range(start, periods=n, freq=freq)
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes,
         "volume": [volume] * n},
        index=idx,
    )


def make_uptrend(n=400, start_price=100.0, step=0.4):
    """Smooth, monotonic uptrend (buy & hold strongly positive)."""
    closes = [start_price + i * step for i in range(n)]
    return _ohlcv_from_closes(closes)


def make_downtrend(n=400, start_price=300.0, step=0.4):
    """Smooth, monotonic downtrend (buy & hold strongly negative)."""
    closes = [max(1.0, start_price - i * step) for i in range(n)]
    return _ohlcv_from_closes(closes)


def make_choppy(n=400, base=100.0, amp=5.0, period=20):
    """Deterministic sine-wave chop around a flat mean (B&H ~ 0)."""
    closes = [base + amp * math.sin(2 * math.pi * i / period) for i in range(n)]
    return _ohlcv_from_closes(closes)


# ===========================================================================
# A deterministic fake strategy — full control over signal timing
# ===========================================================================

class ScriptedStrategy(Strategy):
    """
    Emits a BUY/SELL on exactly the bars whose CLOSED-length matches a script.

    Because the back-tester feeds ``df.iloc[:i+1]`` at decision bar ``i``, the
    window length equals ``i+1``. The script maps that length -> signal type,
    letting tests pin entries/exits to specific bars deterministically.
    """

    name = "scripted"

    def __init__(self, script: dict[int, SignalType]):
        # script: window_len -> SignalType
        self.script = script
        # record the windows we were shown (to assert no-lookahead)
        self.seen_lengths: list[int] = []
        self.last_close_seen: list[float] = []

    def generate_signals(self, df, symbol, precomputed_adx=None):
        self.seen_lengths.append(len(df))
        self.last_close_seen.append(float(df["close"].iloc[-1]))
        st = self.script.get(len(df))
        if st is None:
            return []
        return [Signal(signal_type=st, symbol=symbol,
                       price=float(df["close"].iloc[-1]),
                       strategy_name=self.name, confidence=0.9)]


# ===========================================================================
# data.py
# ===========================================================================

def test_normalize_from_clean_ohlcv():
    df = make_uptrend(50)
    out = normalize_ohlcv(df)
    assert list(out.columns) == ["open", "high", "low", "close", "volume"]
    assert isinstance(out.index, pd.DatetimeIndex)
    assert out.index.is_monotonic_increasing
    assert out["close"].dtype == float


def test_normalize_case_insensitive_and_missing_volume():
    df = pd.DataFrame({
        "Open": [1.0, 2.0, 3.0],
        "High": [1.5, 2.5, 3.5],
        "Low": [0.5, 1.5, 2.5],
        "Close": [1.2, 2.2, 3.2],
    })
    out = normalize_ohlcv(df)
    assert "volume" in out.columns
    assert (out["volume"] == 0.0).all()
    assert len(out) == 3


def test_normalize_raw_binance_payload():
    # Simulate a raw 12-field kline list as returned by /api/v3/klines.
    base = 1_600_000_000_000
    rows = []
    for i in range(5):
        rows.append([
            base + i * 60_000, "100.0", "101.0", "99.0", "100.5", "10.0",
            base + i * 60_000 + 59_999, "1005.0", 50, "5.0", "502.0", "0",
        ])
    df = pd.DataFrame(rows)  # positional integer columns
    out = normalize_ohlcv(df)
    assert len(out) == 5
    assert out["close"].iloc[0] == pytest.approx(100.5)
    assert isinstance(out.index, pd.DatetimeIndex)


def test_load_dataframe_does_not_mutate_input():
    df = make_uptrend(30)
    snapshot = df.copy()
    _ = load_dataframe(df)
    pd.testing.assert_frame_equal(df, snapshot)


def test_interval_to_ms():
    assert interval_to_ms("15m") == 15 * 60_000
    assert interval_to_ms("1h") == 60 * 60_000
    assert interval_to_ms("1d") == 24 * 60 * 60_000
    with pytest.raises(ValueError):
        interval_to_ms("7s")


def test_load_klines_rest_paginates_with_fake_http(monkeypatch):
    """
    Exercise pagination logic with an injected fake getter — NO network.

    The fake returns 1000-row pages until the window is covered, proving the
    cursor advances and the loader stops on a short final page.
    """
    interval = "1m"
    step = interval_to_ms(interval)
    start = 1_600_000_000_000
    total_candles = 2500  # -> 3 pages (1000, 1000, 500)

    all_rows = []
    for i in range(total_candles):
        ot = start + i * step
        price = 100.0 + i * 0.01
        all_rows.append([
            ot, f"{price:.4f}", f"{price + 1:.4f}", f"{price - 1:.4f}",
            f"{price + 0.5:.4f}", "1.0",
            ot + step - 1, "0", 1, "0", "0", "0",
        ])

    calls = {"n": 0}

    class FakeSession:
        def get(self, url, params=None, timeout=None):
            calls["n"] += 1
            st = params["startTime"]
            # rows with open_time >= st, capped at limit
            page = [r for r in all_rows if r[0] >= st][: params["limit"]]

            class _Resp:
                def raise_for_status(self_inner):
                    return None

                def json(self_inner):
                    return page

            return _Resp()

    end = start + total_candles * step
    df = load_klines_rest(
        "BTCUSDT", interval=interval,
        start_time_ms=start, end_time_ms=end,
        session=FakeSession(), pause_s=0,
    )
    assert calls["n"] == 3                       # 1000 + 1000 + 500
    assert len(df) == total_candles
    assert df.index.is_monotonic_increasing
    assert not df.index.has_duplicates


# ===========================================================================
# resolve_strategy
# ===========================================================================

def test_resolve_strategy_by_name_and_instance():
    s = resolve_strategy("regime_breakout")
    assert s.name == "regime_breakout"
    # passing an instance returns it unchanged
    assert resolve_strategy(s) is s
    with pytest.raises(ValueError):
        resolve_strategy("does_not_exist")


# ===========================================================================
# engine — smoke / metrics
# ===========================================================================

def test_backtester_runs_and_returns_metrics():
    df = make_uptrend(300)
    bt = Backtester("regime_breakout", BacktestConfig(allow_short=False))
    result = bt.run(df)
    assert isinstance(result, BacktestResult)
    assert isinstance(result.metrics, BacktestMetrics)
    assert len(result.equity_curve) == len(normalize_ohlcv(df))
    # benchmark must be computed and strongly positive on a clean uptrend
    assert result.metrics.benchmark_return_pct > 50.0


def test_scripted_long_trade_executes_at_next_open():
    """A BUY at decision bar i fills at the open of bar i+1 (no lookahead)."""
    df = make_uptrend(40, start_price=100.0, step=1.0)
    # Decision when window length == 10 (i.e. i==9) -> fill at bar index 10 open.
    strat = ScriptedStrategy({10: SignalType.BUY, 20: SignalType.SELL})
    cfg = BacktestConfig(
        initial_capital=10_000, position_size_pct=100.0,
        use_atr_stops=False, sl_pct=50.0, tp_pct=50.0,  # wide so signal exit wins
        fee_pct=0.1, slippage_pct=0.02, allow_short=False,
    )
    bt = Backtester(strat, cfg)
    result = bt.run(df, warmup=2)

    assert result.num_trades == 1
    t = result.trades[0]
    ndf = normalize_ohlcv(df)
    # entry filled at bar 10's OPEN
    assert t.entry_index == 10
    assert t.entry_price == pytest.approx(ndf["open"].iloc[10])
    # exit (signal) filled at bar 20's OPEN
    assert t.exit_index == 20
    assert t.exit_price == pytest.approx(ndf["open"].iloc[20])


def test_net_less_than_gross_when_fee_positive():
    df = make_uptrend(40, start_price=100.0, step=1.0)
    strat = ScriptedStrategy({10: SignalType.BUY, 20: SignalType.SELL})
    cfg = BacktestConfig(initial_capital=10_000, position_size_pct=100.0,
                         use_atr_stops=False, sl_pct=50.0, tp_pct=50.0,
                         fee_pct=0.1, slippage_pct=0.02)
    result = Backtester(strat, cfg).run(df, warmup=2)
    m = result.metrics
    assert m.num_trades == 1
    assert m.gross_pnl > 0                       # winning long in an uptrend
    assert m.net_pnl < m.gross_pnl               # costs were deducted
    assert m.total_fees > 0
    assert m.total_slippage > 0
    assert math.isclose(m.net_pnl, m.gross_pnl - m.total_fees - m.total_slippage,
                        rel_tol=1e-9, abs_tol=1e-6)


def test_zero_fee_means_net_equals_gross():
    df = make_uptrend(40, start_price=100.0, step=1.0)
    strat = ScriptedStrategy({10: SignalType.BUY, 20: SignalType.SELL})
    cfg = BacktestConfig(use_atr_stops=False, sl_pct=50.0, tp_pct=50.0,
                         fee_pct=0.0, slippage_pct=0.0)
    result = Backtester(strat, cfg).run(df, warmup=2)
    m = result.metrics
    assert m.total_fees == 0.0
    assert m.total_slippage == 0.0
    assert math.isclose(m.net_pnl, m.gross_pnl, rel_tol=1e-9, abs_tol=1e-9)


# ===========================================================================
# engine — SL/TP intrabar, SL-wins-tie
# ===========================================================================

def test_stop_loss_fills_at_level_not_close():
    """
    Open a long, then feed a candle that pierces the SL. Exit must be booked at
    the SL price, not the candle close.
    """
    # Bars: warmup..entry, then a deep red candle on the bar after entry.
    closes = [100.0] * 12
    df = _ohlcv_from_closes(closes)
    # Make bar index 11 a crash candle that breaches a 3% stop from entry≈100.
    df.iloc[11, df.columns.get_loc("low")] = 90.0   # low well below SL≈97
    df.iloc[11, df.columns.get_loc("close")] = 95.0  # close above SL on purpose

    strat = ScriptedStrategy({10: SignalType.BUY})  # fill at bar 10 open (=100)
    cfg = BacktestConfig(initial_capital=10_000, position_size_pct=100.0,
                         use_atr_stops=False, sl_pct=3.0, tp_pct=100.0,
                         fee_pct=0.0, slippage_pct=0.0)
    result = Backtester(strat, cfg).run(df, warmup=2)

    assert result.num_trades == 1
    t = result.trades[0]
    assert t.exit_reason == EXIT_STOP_LOSS
    # entry price = open of bar 10 = 100.0; SL = 97.0
    assert t.entry_price == pytest.approx(100.0)
    assert t.exit_price == pytest.approx(97.0)    # booked at SL, NOT the 95 close


def test_sl_wins_when_both_sl_and_tp_in_same_candle():
    """When a single candle straddles BOTH SL and TP, the SL must win (loss)."""
    closes = [100.0] * 12
    df = _ohlcv_from_closes(closes)
    # Bar 11 spans from below SL (97) to above TP (103): a huge-range candle.
    df.iloc[11, df.columns.get_loc("high")] = 110.0   # above TP=103
    df.iloc[11, df.columns.get_loc("low")] = 90.0     # below SL=97
    df.iloc[11, df.columns.get_loc("close")] = 105.0

    strat = ScriptedStrategy({10: SignalType.BUY})    # entry at bar10 open=100
    cfg = BacktestConfig(initial_capital=10_000, position_size_pct=100.0,
                         use_atr_stops=False, sl_pct=3.0, tp_pct=3.0,
                         fee_pct=0.0, slippage_pct=0.0)
    result = Backtester(strat, cfg).run(df, warmup=2)

    assert result.num_trades == 1
    t = result.trades[0]
    assert t.exit_reason == EXIT_STOP_LOSS         # SL wins the tie
    assert t.exit_price == pytest.approx(97.0)     # filled at the stop level
    assert t.pnl.net_pnl < 0                        # it is a loss


def test_take_profit_fills_when_only_tp_touched():
    closes = [100.0] * 12
    df = _ohlcv_from_closes(closes)
    df.iloc[11, df.columns.get_loc("high")] = 110.0   # above TP=105
    df.iloc[11, df.columns.get_loc("low")] = 99.0     # stays above SL
    df.iloc[11, df.columns.get_loc("close")] = 108.0

    strat = ScriptedStrategy({10: SignalType.BUY})
    cfg = BacktestConfig(initial_capital=10_000, position_size_pct=100.0,
                         use_atr_stops=False, sl_pct=5.0, tp_pct=5.0,
                         fee_pct=0.0, slippage_pct=0.0)
    result = Backtester(strat, cfg).run(df, warmup=2)

    assert result.num_trades == 1
    t = result.trades[0]
    assert t.exit_reason == EXIT_TAKE_PROFIT
    assert t.exit_price == pytest.approx(105.0)    # booked at TP level


# ===========================================================================
# engine — shorts gating
# ===========================================================================

def test_short_not_opened_when_allow_short_false():
    df = make_downtrend(40, start_price=200.0, step=1.0)
    strat = ScriptedStrategy({10: SignalType.SELL})   # SELL while flat
    cfg = BacktestConfig(allow_short=False, use_atr_stops=False,
                         sl_pct=50, tp_pct=50, fee_pct=0.0, slippage_pct=0.0)
    result = Backtester(strat, cfg).run(df, warmup=2)
    # SELL while flat + no shorts allowed -> nothing happens.
    assert result.num_trades == 0


def test_short_opens_and_profits_when_allowed():
    df = make_downtrend(40, start_price=200.0, step=2.0)
    strat = ScriptedStrategy({10: SignalType.SELL, 20: SignalType.BUY})
    cfg = BacktestConfig(allow_short=True, use_atr_stops=False,
                         sl_pct=50, tp_pct=50, fee_pct=0.0, slippage_pct=0.0,
                         position_size_pct=100.0)
    result = Backtester(strat, cfg).run(df, warmup=2)
    assert result.num_trades == 1
    t = result.trades[0]
    assert t.side == "SELL"
    assert t.pnl.gross_pnl > 0                      # short profits as price falls


# ===========================================================================
# engine — determinism & no-lookahead
# ===========================================================================

def test_determinism_same_inputs_same_outputs():
    df = make_choppy(300)
    cfg = BacktestConfig(allow_short=False)
    r1 = Backtester("regime_breakout", cfg).run(df)
    r2 = Backtester("regime_breakout", cfg).run(df)
    assert r1.num_trades == r2.num_trades
    assert math.isclose(r1.metrics.net_pnl, r2.metrics.net_pnl, abs_tol=1e-9)
    assert math.isclose(r1.metrics.total_return_pct, r2.metrics.total_return_pct,
                        abs_tol=1e-9)
    pd.testing.assert_series_equal(r1.equity_curve, r2.equity_curve)


def test_no_lookahead_strategy_only_sees_closed_bars():
    """
    The strategy must only ever see candles up to the decision bar, and the
    close it sees at decision bar i must equal df.close[i] — never i+1.
    """
    df = make_uptrend(30, start_price=100.0, step=1.0)
    ndf = normalize_ohlcv(df)
    strat = ScriptedStrategy({})  # no signals; we only inspect what it saw
    Backtester(strat, BacktestConfig()).run(df, warmup=2)

    # window lengths are strictly increasing 1..(n-1): decisions never run on
    # the final bar (no next bar to fill into) and never skip ahead.
    assert strat.seen_lengths == list(range(2, len(ndf)))  # warmup=2 -> first len is 2
    # the close shown at each decision equals close[len-1] (the bar that closed),
    # i.e. it is NEVER the next (future) bar's close.
    for length, seen_close in zip(strat.seen_lengths, strat.last_close_seen):
        assert seen_close == pytest.approx(ndf["close"].iloc[length - 1])


def test_future_data_change_does_not_alter_past_decisions():
    """
    Mutating bars AFTER the decision point must not change the decision made at
    that point — a direct no-lookahead guarantee.
    """
    df = make_uptrend(30, start_price=100.0, step=1.0)
    strat_a = ScriptedStrategy({})
    Backtester(strat_a, BacktestConfig()).run(df, warmup=2)

    df2 = df.copy()
    # Corrupt the LAST few bars drastically.
    df2.iloc[-3:, df2.columns.get_loc("close")] = 1.0
    df2.iloc[-3:, df2.columns.get_loc("high")] = 1.0
    df2.iloc[-3:, df2.columns.get_loc("low")] = 1.0
    strat_b = ScriptedStrategy({})
    Backtester(strat_b, BacktestConfig()).run(df2, warmup=2)

    # Decisions taken before the corrupted tail must be identical.
    horizon = len(df) - 3
    a = [c for ln, c in zip(strat_a.seen_lengths, strat_a.last_close_seen) if ln <= horizon]
    b = [c for ln, c in zip(strat_b.seen_lengths, strat_b.last_close_seen) if ln <= horizon]
    assert a == b


# ===========================================================================
# metrics — benchmark, drawdown, profit factor
# ===========================================================================

def test_benchmark_buy_and_hold_positive_on_uptrend():
    df = make_uptrend(200, start_price=100.0, step=1.0)
    m = compute_metrics(trades=[], equity_curve=pd.Series([10_000.0] * len(df),
                                                          index=normalize_ohlcv(df).index),
                        price_df=normalize_ohlcv(df), initial_capital=10_000.0,
                        fee_pct=0.0, slippage_pct=0.0)
    ndf = normalize_ohlcv(df)
    expected = (ndf["close"].iloc[-1] - ndf["open"].iloc[0]) / ndf["open"].iloc[0] * 100.0
    assert m.benchmark_return_pct == pytest.approx(expected, rel=1e-6)
    assert m.benchmark_return_pct > 100.0


def test_benchmark_net_below_gross_with_costs():
    df = make_uptrend(100, start_price=100.0, step=1.0)
    ndf = normalize_ohlcv(df)
    m = compute_metrics(trades=[], equity_curve=pd.Series([10_000.0] * len(ndf),
                                                          index=ndf.index),
                        price_df=ndf, initial_capital=10_000.0,
                        fee_pct=0.1, slippage_pct=0.05)
    assert m.benchmark_net_return_pct < m.benchmark_return_pct


def test_max_drawdown_computation():
    # Equity goes 100 -> 120 -> 60 -> 90: max DD = (120-60)/120 = 50%.
    idx = pd.date_range("2021-01-01", periods=4, freq="D")
    eq = pd.Series([100.0, 120.0, 60.0, 90.0], index=idx)
    df = _ohlcv_from_closes([1, 1, 1, 1], start="2021-01-01", freq="D")
    m = compute_metrics(trades=[], equity_curve=eq, price_df=df,
                        initial_capital=100.0, fee_pct=0.0, slippage_pct=0.0)
    assert m.max_drawdown_pct == pytest.approx(50.0)


def test_profit_factor_and_win_rate():
    """Two synthetic trades: one win, one loss -> known PF and win rate."""
    df = make_uptrend(60, start_price=100.0, step=1.0)
    # win then loss via scripted entries/exits with tight stops on a flat patch
    # Easier: drive PF directly through compute_metrics with hand-built trades.
    from app.pnl import compute_pnl
    from app.backtesting.engine import BacktestTrade

    ts = pd.Timestamp("2021-01-01")
    win = BacktestTrade("BUY", "X", ts, ts, 100, 110, 1.0,
                        compute_pnl("BUY", 100, 110, 1.0, 0.0, 0.0),
                        EXIT_TAKE_PROFIT, 1, 0, 1)
    loss = BacktestTrade("BUY", "X", ts, ts, 100, 95, 1.0,
                         compute_pnl("BUY", 100, 95, 1.0, 0.0, 0.0),
                         EXIT_STOP_LOSS, 1, 2, 3)
    ndf = normalize_ohlcv(df)
    m = compute_metrics([win, loss], pd.Series([10_000.0] * len(ndf), index=ndf.index),
                        ndf, 10_000.0, fee_pct=0.0, slippage_pct=0.0)
    assert m.num_trades == 2
    assert m.num_wins == 1 and m.num_losses == 1
    assert m.win_rate == pytest.approx(0.5)
    # gross win = 10, gross loss = 5 -> PF = 2.0
    assert m.profit_factor == pytest.approx(2.0)
    assert m.gross_pnl == pytest.approx(5.0)


def test_profit_factor_infinite_with_no_losses():
    from app.pnl import compute_pnl
    from app.backtesting.engine import BacktestTrade
    ts = pd.Timestamp("2021-01-01")
    win = BacktestTrade("BUY", "X", ts, ts, 100, 110, 1.0,
                        compute_pnl("BUY", 100, 110, 1.0, 0.0, 0.0),
                        EXIT_TAKE_PROFIT, 1, 0, 1)
    df = _ohlcv_from_closes([1, 1, 1, 1], freq="D")
    m = compute_metrics([win], pd.Series([1.0, 1.0, 1.0, 1.0], index=df.index),
                        df, 10_000.0, fee_pct=0.0, slippage_pct=0.0)
    assert math.isinf(m.profit_factor)


# ===========================================================================
# walk-forward
# ===========================================================================

def test_walk_forward_runs_and_aggregates():
    df = make_choppy(600, amp=8.0, period=24)
    cfg = BacktestConfig(allow_short=False)
    report = walk_forward(df, train_size=150, test_size=100,
                          strategy="regime_breakout", config=cfg)
    assert report.num_windows >= 2
    assert isinstance(report.aggregate, BacktestMetrics)
    # combined trades = sum of per-window OOS trades
    assert report.aggregate.num_trades == len(report.combined_trades)


def test_walk_forward_validates_sizes():
    df = make_uptrend(100)
    with pytest.raises(ValueError):
        walk_forward(df, train_size=80, test_size=80)  # exceeds length
    with pytest.raises(ValueError):
        walk_forward(df, train_size=0, test_size=10)


def test_walk_forward_oos_entries_only_in_test_region():
    """Entries must never occur before each window's test segment (no leakage)."""
    df = make_uptrend(500, start_price=100.0, step=0.5)
    cfg = BacktestConfig(allow_short=False)
    report = walk_forward(df, train_size=200, test_size=100,
                          strategy="regime_breakout", config=cfg)
    for w in report.windows:
        local_warmup = 200  # train_size (rolling) == local test-start offset
        for t in w.result.trades:
            # every entry index is at/after the warm-up boundary
            assert t.entry_index >= local_warmup
