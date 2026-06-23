"""
Event-driven, bar-by-bar back-tester.

Design — NO LOOKAHEAD
---------------------
The simulation walks the OHLCV frame one candle at a time. At decision bar
``i`` the strategy is shown **only closed candles** ``df.iloc[: i + 1]`` (i.e.
candle ``i`` is the most recent CLOSED bar — exactly what the live engine feeds
``generate_signals``). The resulting order is **executed at the NEXT bar's open**
(``i + 1``). This is the standard signal-on-close / fill-on-next-open model and
makes it structurally impossible for a fill to use information from the bar that
produced the signal.

Consequences:
* The candle currently "in formation" is never read by a strategy.
* The last bar of the frame can only ever close an open position (it has no
  ``i + 1`` to fill a new entry into); no new entry is opened on it.

Intrabar SL/TP (conservative)
-----------------------------
While a position is open, every subsequent candle is checked for SL/TP touches
using its high/low:
* If **both** SL and TP fall inside ``[low, high]`` of the same candle we cannot
  know the path, so we assume the **worst case: the stop-loss fills** (the
  conservative choice). The exit is booked **at the SL/TP level**, never the
  close.
* A gap (open already beyond a level) fills at that level too.
* SL/TP take priority over a strategy exit signal arriving on the same bar.

Costs
-----
Every close is priced through :func:`app.pnl.compute_pnl` with ``fee_pct`` /
``slippage_pct`` (defaults from settings), so back-test PnL reconciles exactly
with the live engine and ``/performance`` endpoints. Slippage is modelled by
``app.pnl`` as an explicit cost on both legs — fill prices themselves are the
quoted open / SL / TP levels.

Shorts
------
``allow_short`` defaults to ``False`` to match the spot-live reality (the live
system is long-only everywhere). With shorts disabled a SELL signal only
closes an existing long; it never opens a short.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from app.config import settings
from app.pnl import PnLResult, compute_pnl
from app.strategies.base import Signal, SignalType, Strategy
from app.strategies.indicators import Indicators
from app.backtesting.data import normalize_ohlcv
from app.backtesting.metrics import BacktestMetrics, compute_metrics

logger = logging.getLogger(__name__)

# Reasons a position was closed (for reporting / debugging).
EXIT_STOP_LOSS = "stop_loss"
EXIT_TAKE_PROFIT = "take_profit"
EXIT_SIGNAL = "signal"
EXIT_END_OF_DATA = "end_of_data"


# ---------------------------------------------------------------------------
# Strategy resolution — turn a name into an instance (read-only re-use)
# ---------------------------------------------------------------------------

def _strategy_registry() -> dict[str, Callable[[], Strategy]]:
    """Lazy factory map name -> zero-arg constructor (defaults from prod)."""
    from app.strategies.regime_breakout import RegimeBreakoutStrategy
    # Research-only long/short variant — back-tester registry ONLY, never live.
    from app.strategies.regime_breakout_ls import RegimeBreakoutLongShort

    return {
        "regime_breakout": RegimeBreakoutStrategy,
        "breakout": RegimeBreakoutStrategy,
        "regime_breakout_ls": RegimeBreakoutLongShort,
        "breakout_ls": RegimeBreakoutLongShort,
    }


def resolve_strategy(strategy: "str | Strategy") -> Strategy:
    """
    Accept either a ready strategy instance or a name and return an instance.

    Names map to the production strategy classes (constructed with their default
    parameters). Unknown names raise ``ValueError`` listing valid options.
    """
    if isinstance(strategy, Strategy):
        return strategy
    reg = _strategy_registry()
    key = str(strategy).strip().lower()
    if key not in reg:
        raise ValueError(
            f"Unknown strategy '{strategy}'. Available: {sorted(set(reg))}"
        )
    return reg[key]()


# ---------------------------------------------------------------------------
# Config / result containers
# ---------------------------------------------------------------------------

@dataclass
class BacktestConfig:
    """
    Knobs for a single back-test run.

    Stops: ``sl_pct``/``tp_pct`` are percent distances from entry (e.g. 3.0 =
    3%). When ``use_atr_stops`` is True the stop distances are ATR-based
    (``atr_sl_mult``/``atr_tp_mult`` x ATR at entry) and the percentages are
    used only as a fallback when ATR is unavailable.
    """

    initial_capital: float = 10_000.0
    position_size_pct: float = 100.0   # % of equity deployed per entry
    allow_short: bool = False

    # Fixed percentage stops (% distance from entry price).
    sl_pct: float = field(default_factory=lambda: settings.default_stop_loss_pct)
    tp_pct: float = field(default_factory=lambda: settings.default_take_profit_pct)

    # ATR stops (override the % stops when enabled and computable).
    use_atr_stops: bool = field(default_factory=lambda: settings.use_atr_stops)
    atr_period: int = 14
    atr_sl_mult: float = field(default_factory=lambda: settings.atr_sl_mult)
    atr_tp_mult: float = field(default_factory=lambda: settings.atr_tp_mult)

    # Costs (defaults mirror live settings; CLI can override).
    fee_pct: float = field(default_factory=lambda: settings.taker_fee_pct)
    slippage_pct: float = field(default_factory=lambda: settings.paper_slippage_pct)

    symbol: str = "BTCUSDT"
    # If True, a SELL on the next bar's open closes a long even without SL/TP.
    allow_signal_exit: bool = True


@dataclass
class BacktestTrade:
    """A single completed round-trip (entry -> exit)."""

    side: str                 # "BUY" (long) or "SELL" (short)
    symbol: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    quantity: float
    pnl: PnLResult
    exit_reason: str
    bars_held: int
    entry_index: int
    exit_index: int

    def as_dict(self) -> dict:
        return {
            "side": self.side,
            "symbol": self.symbol,
            "entry_time": str(self.entry_time),
            "exit_time": str(self.exit_time),
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "quantity": self.quantity,
            "exit_reason": self.exit_reason,
            "bars_held": self.bars_held,
            **self.pnl.as_dict(),
        }


@dataclass
class BacktestResult:
    """Output of :meth:`Backtester.run`."""

    trades: list[BacktestTrade]
    equity_curve: pd.Series
    metrics: BacktestMetrics
    config: BacktestConfig
    df: pd.DataFrame = field(repr=False, default=None)

    @property
    def num_trades(self) -> int:
        return len(self.trades)


# ---------------------------------------------------------------------------
# The back-tester
# ---------------------------------------------------------------------------

class Backtester:
    """
    Bar-by-bar back-tester for a single symbol and a single strategy.

    Usage::

        bt = Backtester(strategy="regime_breakout",
                        config=BacktestConfig(allow_short=False))
        result = bt.run(df)
        print(result.metrics.as_dict())
    """

    def __init__(self, strategy: "str | Strategy",
                 config: BacktestConfig | None = None):
        self.strategy = resolve_strategy(strategy)
        self.config = config or BacktestConfig()

    # -- internal state container, reset per run ---------------------------

    def _new_position(self) -> dict:
        return {
            "open": False,
            "side": None,            # "BUY" or "SELL"
            "entry_price": 0.0,
            "quantity": 0.0,
            "sl": None,
            "tp": None,
            "entry_time": None,
            "entry_index": -1,
        }

    # ---------------------------------------------------------------------
    # Stop levels
    # ---------------------------------------------------------------------

    def _stop_levels(self, side: str, entry_price: float,
                     atr_value: float | None) -> tuple[float, float | None]:
        """
        Return (sl_price, tp_price) for an entry.

        Long:  SL below entry, TP above. Short: mirrored.
        ATR stops win when enabled and ATR is finite & > 0; otherwise fall back
        to the percentage stops.

        A non-positive TP multiplier/percent disables the take-profit entirely
        (``tp_price = None``) so trend-following strategies can let winners run
        and exit on their own signal or the stop. The stop-loss can never be
        disabled — every position keeps a hard floor.
        """
        cfg = self.config
        use_atr = (
            cfg.use_atr_stops
            and atr_value is not None
            and np.isfinite(atr_value)
            and atr_value > 0
        )
        if use_atr:
            sl_dist = cfg.atr_sl_mult * atr_value
            tp_dist = cfg.atr_tp_mult * atr_value if cfg.atr_tp_mult > 0 else None
        else:
            sl_dist = entry_price * (cfg.sl_pct / 100.0)
            tp_dist = entry_price * (cfg.tp_pct / 100.0) if cfg.tp_pct > 0 else None

        if side == "BUY":
            return (entry_price - sl_dist,
                    entry_price + tp_dist if tp_dist is not None else None)
        # short
        return (entry_price + sl_dist,
                entry_price - tp_dist if tp_dist is not None else None)

    # ---------------------------------------------------------------------
    # Intrabar SL/TP resolution (conservative — SL wins a tie)
    # ---------------------------------------------------------------------

    @staticmethod
    def _resolve_intrabar_exit(side: str, sl: float | None, tp: float | None,
                               bar_high: float, bar_low: float
                               ) -> tuple[float, str] | None:
        """
        Decide whether SL or TP is hit within a single candle.

        Returns (fill_price, reason) booked AT the level, or ``None`` if neither
        is touched. When both are inside the bar's range the **stop-loss wins**
        (we cannot resolve intrabar path, so assume the adverse outcome).
        """
        if sl is None and tp is None:
            return None

        if side == "BUY":
            sl_hit = sl is not None and bar_low <= sl
            tp_hit = tp is not None and bar_high >= tp
        else:  # short
            sl_hit = sl is not None and bar_high >= sl
            tp_hit = tp is not None and bar_low <= tp

        if sl_hit:                       # SL priority on tie (and on gaps)
            return sl, EXIT_STOP_LOSS
        if tp_hit:
            return tp, EXIT_TAKE_PROFIT
        return None

    # ---------------------------------------------------------------------
    # Sizing
    # ---------------------------------------------------------------------

    def _position_quantity(self, equity: float, price: float) -> float:
        if price <= 0:
            return 0.0
        notional = equity * (self.config.position_size_pct / 100.0)
        return notional / price

    # ---------------------------------------------------------------------
    # Main loop
    # ---------------------------------------------------------------------

    def run(self, df: pd.DataFrame, *, warmup: int | None = None) -> BacktestResult:
        """
        Run the back-test over ``df`` and return a :class:`BacktestResult`.

        Args:
            df:     OHLCV data (any accepted shape; normalised internally).
            warmup: first bar index at which the strategy is allowed to act.
                    Defaults to a heuristic based on the data length so early
                    NaN-heavy indicator windows are skipped.
        """
        cfg = self.config
        df = normalize_ohlcv(df)
        n = len(df)

        equity = cfg.initial_capital
        realized_equity = cfg.initial_capital  # equity excluding open MTM
        pos = self._new_position()
        trades: list[BacktestTrade] = []

        # Pre-compute ATR over the WHOLE frame once. We only ever read ATR at a
        # decision bar i via atr.iloc[i] (a closed bar), so this introduces no
        # lookahead — value at i depends only on candles <= i.
        atr_series = None
        if cfg.use_atr_stops:
            atr_series = self._atr(df, cfg.atr_period)

        highs = df["high"].to_numpy(dtype=float)
        lows = df["low"].to_numpy(dtype=float)
        opens = df["open"].to_numpy(dtype=float)
        closes = df["close"].to_numpy(dtype=float)
        index = df.index

        # Warm-up: skip the leading region where indicators are NaN.
        if warmup is None:
            warmup = min(n, max(2, int(min(50, n // 5))))

        equity_points: list[float] = []
        bars_in_position = 0

        # Iterate decision bars. We need bar i+1 to fill, so the loop body that
        # OPENS a position runs while i < n-1; SL/TP/exit checks run for the
        # open position on the current bar i.
        for i in range(n):
            bar_high = highs[i]
            bar_low = lows[i]
            bar_open = opens[i]
            bar_close = closes[i]

            # --- 1) manage an OPEN position on THIS bar (i) ---------------
            if pos["open"]:
                bars_in_position += 1
                exit_info = self._resolve_intrabar_exit(
                    pos["side"], pos["sl"], pos["tp"], bar_high, bar_low
                )
                if exit_info is not None:
                    fill_price, reason = exit_info
                    trade = self._close(pos, fill_price, index[i], i, reason)
                    trades.append(trade)
                    realized_equity += trade.pnl.net_pnl
                    equity = realized_equity
                    pos = self._new_position()

            # --- 2) strategy decision using ONLY closed bars [0..i] -------
            # Execution of any resulting order happens at bar i+1 open, so we
            # only generate a decision when a next bar exists.
            decision: Signal | None = None
            if i + 1 < n and i >= warmup - 1:
                window = df.iloc[: i + 1]
                decision = self._decide(window, cfg.symbol)

            # --- 3) act on the decision at NEXT bar's open (i+1) ----------
            if decision is not None and i + 1 < n:
                next_open = opens[i + 1]
                self._apply_decision(
                    decision, pos, next_open, index[i + 1], i + 1,
                    atr_series, equity, trades,
                )
                # _apply_decision may have closed a position (signal exit) and
                # updated realized equity via the returned trade; recompute.
                if trades and trades[-1].exit_index == i + 1 and not pos["open"]:
                    realized_equity = cfg.initial_capital + sum(t.pnl.net_pnl for t in trades)
                    equity = realized_equity

            # --- 4) mark-to-market equity for the curve (uses close i) ----
            if pos["open"]:
                mtm = self._unrealized(pos, bar_close)
                equity = realized_equity + mtm
            else:
                equity = realized_equity
            equity_points.append(equity)

        # --- force-close any position still open at the end of data -------
        if pos["open"]:
            last_close = closes[-1]
            trade = self._close(pos, last_close, index[-1], n - 1, EXIT_END_OF_DATA)
            trades.append(trade)
            realized_equity += trade.pnl.net_pnl
            if equity_points:
                equity_points[-1] = realized_equity
            pos = self._new_position()

        equity_curve = pd.Series(equity_points, index=index[: len(equity_points)],
                                 name="equity")

        metrics = compute_metrics(
            trades=trades,
            equity_curve=equity_curve,
            price_df=df,
            initial_capital=cfg.initial_capital,
            fee_pct=cfg.fee_pct,
            slippage_pct=cfg.slippage_pct,
            bars_in_position=bars_in_position,
            total_bars=n,
        )
        return BacktestResult(trades=trades, equity_curve=equity_curve,
                              metrics=metrics, config=cfg, df=df)

    # ---------------------------------------------------------------------
    # Decision / order application helpers
    # ---------------------------------------------------------------------

    def _decide(self, window: pd.DataFrame, symbol: str) -> Signal | None:
        """Run the strategy on closed bars and pick the strongest signal."""
        try:
            signals = self.strategy.generate_signals(window, symbol)
        except Exception:
            logger.exception("strategy %s failed at bar window len=%d",
                             getattr(self.strategy, "name", "?"), len(window))
            return None
        if not signals:
            return None
        # Prefer the highest-confidence non-HOLD signal.
        actionable = [s for s in signals if s.signal_type != SignalType.HOLD]
        if not actionable:
            return None
        return max(actionable, key=lambda s: s.confidence)

    def _apply_decision(self, signal: Signal, pos: dict, fill_price: float,
                        fill_time: pd.Timestamp, fill_index: int,
                        atr_series, equity: float,
                        trades: list[BacktestTrade]) -> None:
        """
        Apply a decision at the next bar's open.

        Reversal is deliberately treated as an EXIT-ONLY event (no auto-flip on
        the same bar) — this matches the live engine, where SELL primarily
        manages an exit, and avoids surprising "always-in-market" behaviour:

        BUY:
          * flat        -> open long
          * short open  -> close short (signal exit), stay flat this bar
        SELL:
          * long open   -> close long (signal exit), stay flat this bar
          * flat        -> open short only when allow_short
        """
        cfg = self.config
        atr_val = None
        # ATR at the DECISION bar (fill_index - 1) — a closed bar; using
        # fill_index would peek at the bar being filled into. Guard bounds.
        if atr_series is not None and 0 <= fill_index - 1 < len(atr_series):
            v = atr_series.iloc[fill_index - 1]
            atr_val = float(v) if pd.notna(v) else None

        if signal.signal_type == SignalType.BUY:
            if pos["open"]:
                if pos["side"] == "SELL":
                    # close the short on this signal; do NOT auto-open a long
                    trades.append(self._close(pos, fill_price, fill_time, fill_index, EXIT_SIGNAL))
                    pos.update(self._new_position())
                # if already long, hold (let SL/TP manage the exit)
            else:
                self._open(pos, "BUY", fill_price, fill_time, fill_index, atr_val, equity)

        elif signal.signal_type == SignalType.SELL:
            if pos["open"]:
                if pos["side"] == "BUY" and cfg.allow_signal_exit:
                    # close the long on this signal; do NOT auto-open a short
                    trades.append(self._close(pos, fill_price, fill_time, fill_index, EXIT_SIGNAL))
                    pos.update(self._new_position())
            elif cfg.allow_short:
                self._open(pos, "SELL", fill_price, fill_time, fill_index, atr_val, equity)

    def _open(self, pos: dict, side: str, price: float, time: pd.Timestamp,
              index: int, atr_val: float | None, equity: float) -> None:
        qty = self._position_quantity(equity, price)
        if qty <= 0:
            return
        sl, tp = self._stop_levels(side, price, atr_val)
        pos.update({
            "open": True,
            "side": side,
            "entry_price": price,
            "quantity": qty,
            "sl": sl,
            "tp": tp,
            "entry_time": time,
            "entry_index": index,
        })

    def _close(self, pos: dict, price: float, time: pd.Timestamp, index: int,
               reason: str) -> BacktestTrade:
        cfg = self.config
        pnl = compute_pnl(
            pos["side"], pos["entry_price"], price, pos["quantity"],
            cfg.fee_pct, cfg.slippage_pct,
        )
        return BacktestTrade(
            side=pos["side"],
            symbol=cfg.symbol,
            entry_time=pos["entry_time"],
            exit_time=time,
            entry_price=pos["entry_price"],
            exit_price=price,
            quantity=pos["quantity"],
            pnl=pnl,
            exit_reason=reason,
            bars_held=max(0, index - pos["entry_index"]),
            entry_index=pos["entry_index"],
            exit_index=index,
        )

    def _unrealized(self, pos: dict, price: float) -> float:
        """Net mark-to-market PnL of the open position (for the equity curve)."""
        cfg = self.config
        pnl = compute_pnl(
            pos["side"], pos["entry_price"], price, pos["quantity"],
            cfg.fee_pct, cfg.slippage_pct,
        )
        return pnl.net_pnl

    @staticmethod
    def _atr(df: pd.DataFrame, period: int) -> pd.Series:
        """
        Average True Range via Wilder smoothing — derived from the same TR maths
        the production ADX uses (kept local to avoid touching indicators.py).
        """
        high, low, close = df["high"], df["low"], df["close"]
        hl = high - low
        hc = (high - close.shift()).abs()
        lc = (low - close.shift()).abs()
        tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        return tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()


# ---------------------------------------------------------------------------
# Walk-forward (rolling out-of-sample)
# ---------------------------------------------------------------------------

@dataclass
class WalkForwardWindow:
    """One out-of-sample test window's result."""

    index: int
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    result: BacktestResult


@dataclass
class WalkForwardReport:
    """Aggregated walk-forward outcome across all out-of-sample windows."""

    windows: list[WalkForwardWindow]
    aggregate: BacktestMetrics
    combined_trades: list[BacktestTrade]
    combined_equity: pd.Series

    @property
    def num_windows(self) -> int:
        return len(self.windows)


def walk_forward(
    df: pd.DataFrame,
    train_size: int,
    test_size: int,
    *,
    strategy: "str | Strategy" = "regime_breakout",
    config: BacktestConfig | None = None,
    step: int | None = None,
    anchored: bool = False,
) -> WalkForwardReport:
    """
    Rolling walk-forward evaluation.

    The frame is split into consecutive ``train``/``test`` blocks. For each
    window the strategy runs on ``train + test`` candles but **only the test
    segment's trades count** toward the aggregate (the train segment is warm-up
    so indicators are primed and no entry is taken before the test region). This
    yields a stitched, purely out-of-sample equity curve.

    NOTE: the production strategies here have no fit step, so "train" is a
    warm-up window rather than a parameter-optimisation pass. The structure is
    in place for optimisable strategies — pass a pre-configured instance.

    Args:
        df:         OHLCV data.
        train_size: candles per training/warm-up block.
        test_size:  candles per out-of-sample block (also the default step).
        strategy:   name or instance (same instance reused across windows).
        config:     base :class:`BacktestConfig` (cloned per window).
        step:       advance between windows (default = ``test_size``, no overlap).
        anchored:   if True, the train window always starts at bar 0 (expanding
                    window); if False it slides (rolling window).

    Returns:
        :class:`WalkForwardReport` with per-window results and an aggregate
        metrics object computed over the concatenated out-of-sample trades.
    """
    df = normalize_ohlcv(df)
    n = len(df)
    if train_size < 1 or test_size < 1:
        raise ValueError("train_size and test_size must be >= 1")
    if train_size + test_size > n:
        raise ValueError(
            f"train_size + test_size ({train_size + test_size}) exceeds data length ({n})"
        )
    step = step or test_size
    base_cfg = config or BacktestConfig()

    windows: list[WalkForwardWindow] = []
    combined_trades: list[BacktestTrade] = []
    equity_segments: list[pd.Series] = []

    win_idx = 0
    test_start = train_size
    capital = base_cfg.initial_capital
    while test_start + test_size <= n:
        train_start = 0 if anchored else (test_start - train_size)
        window_df = df.iloc[train_start: test_start + test_size]
        # Position within window_df where the test region begins.
        local_warmup = test_start - train_start

        cfg = _clone_config(base_cfg, initial_capital=capital)
        bt = Backtester(resolve_strategy(strategy), cfg)
        # warmup = local_warmup ensures no entry is opened before the test block
        # (entries require i >= warmup-1 AND a next bar). Trades therefore live
        # only in the out-of-sample region.
        result = bt.run(window_df, warmup=local_warmup)

        # Keep only trades whose ENTRY is in the test region (defensive; warmup
        # already prevents earlier entries).
        test_index_start = train_start + local_warmup
        oos_trades = [t for t in result.trades if t.entry_index >= local_warmup]
        combined_trades.extend(oos_trades)

        # Compound capital across windows for a realistic stitched curve.
        capital = capital + sum(t.pnl.net_pnl for t in oos_trades)

        windows.append(WalkForwardWindow(
            index=win_idx,
            train_start=df.index[train_start],
            train_end=df.index[test_start - 1],
            test_start=df.index[test_start],
            test_end=df.index[min(test_start + test_size, n) - 1],
            result=result,
        ))
        # The equity curve segment for the test region only.
        seg = result.equity_curve.iloc[local_warmup:]
        equity_segments.append(seg)

        win_idx += 1
        test_start += step

    combined_equity = (
        pd.concat(equity_segments) if equity_segments else pd.Series(dtype=float)
    )
    combined_equity = combined_equity[~combined_equity.index.duplicated(keep="last")].sort_index()

    aggregate = compute_metrics(
        trades=combined_trades,
        equity_curve=combined_equity,
        price_df=df.iloc[train_size:],   # benchmark over the OOS span
        initial_capital=base_cfg.initial_capital,
        fee_pct=base_cfg.fee_pct,
        slippage_pct=base_cfg.slippage_pct,
        bars_in_position=int(sum(t.bars_held for t in combined_trades)),
        total_bars=max(1, n - train_size),
    )
    return WalkForwardReport(
        windows=windows,
        aggregate=aggregate,
        combined_trades=combined_trades,
        combined_equity=combined_equity,
    )


def _clone_config(cfg: BacktestConfig, **overrides) -> BacktestConfig:
    from dataclasses import replace
    return replace(cfg, **overrides)
