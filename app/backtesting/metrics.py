"""
Performance metrics for the back-tester.

All money figures are in quote currency (USDT) and derive from the per-trade
:class:`app.pnl.PnLResult` objects produced by the engine — there is no
duplicate fee/PnL maths here, keeping the back-tester reconciled with live
reporting.

The headline object is :class:`BacktestMetrics`; build it with
:func:`compute_metrics`.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:  # avoid an import cycle at runtime
    from app.backtesting.engine import BacktestTrade

# Trading days per year — used to annualise the daily-PnL Sharpe ratio.
_TRADING_DAYS_PER_YEAR = 365  # crypto trades 24/7


@dataclass
class BacktestMetrics:
    """Aggregated performance of a back-test run (plus a buy&hold benchmark)."""

    # --- headline ---
    initial_capital: float = 0.0
    final_equity: float = 0.0
    total_return_pct: float = 0.0
    gross_pnl: float = 0.0
    net_pnl: float = 0.0

    # --- trade stats ---
    num_trades: int = 0
    num_wins: int = 0
    num_losses: int = 0
    win_rate: float = 0.0            # 0..1
    profit_factor: float = 0.0       # gross wins / gross losses (net)
    avg_trade: float = 0.0           # mean net PnL per trade (USDT)
    avg_win: float = 0.0
    avg_loss: float = 0.0
    best_trade: float = 0.0
    worst_trade: float = 0.0

    # --- risk ---
    max_drawdown_pct: float = 0.0    # peak-to-trough on the equity curve (%)
    sharpe_per_trade: float = 0.0    # mean/std of per-trade net returns
    sharpe_annualized: float = 0.0   # from daily PnL, scaled by sqrt(365)

    # --- costs ---
    total_fees: float = 0.0
    total_slippage: float = 0.0

    # --- activity ---
    exposure: float = 0.0            # fraction of bars holding a position (0..1)
    avg_holding_bars: float = 0.0

    # --- benchmark (buy & hold over the same period) ---
    benchmark_return_pct: float = 0.0
    benchmark_net_return_pct: float = 0.0  # one round-trip cost applied
    alpha_pct: float = 0.0           # strategy total_return - benchmark gross

    # period
    bars: int = 0
    start: str | None = None
    end: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b not in (0, 0.0) else default


def _max_drawdown_pct(equity: pd.Series) -> float:
    """Largest peak-to-trough decline of an equity curve, as a positive %."""
    if equity is None or len(equity) == 0:
        return 0.0
    running_max = equity.cummax()
    # Guard against a zero/negative peak (shouldn't happen with positive capital).
    drawdown = (equity - running_max) / running_max.replace(0, np.nan)
    dd_min = drawdown.min()
    if pd.isna(dd_min):
        return 0.0
    return float(abs(dd_min) * 100.0)


def _daily_pnl(trades: list, index=None) -> pd.Series:
    """Sum net PnL by calendar day of each trade's EXIT time."""
    if not trades:
        return pd.Series(dtype=float)
    rows = {}
    for t in trades:
        day = pd.Timestamp(t.exit_time).normalize()
        rows[day] = rows.get(day, 0.0) + t.pnl.net_pnl
    s = pd.Series(rows).sort_index()
    return s


def _sharpe_annualized_from_daily(daily_pnl: pd.Series, initial_capital: float) -> float:
    """
    Annualised Sharpe from a daily-PnL series.

    Converts each day's PnL into a return on the (constant) initial capital,
    fills non-trading days with 0 across the spanned calendar, then scales the
    daily Sharpe by sqrt(365). Returns 0 when undefined (no variance / <2 days).
    """
    if daily_pnl is None or len(daily_pnl) < 2 or initial_capital <= 0:
        return 0.0
    full = daily_pnl.asfreq("D", fill_value=0.0) if daily_pnl.index.freq is None else daily_pnl
    # asfreq needs a sorted DatetimeIndex; reindex across the full span.
    full_index = pd.date_range(daily_pnl.index.min(), daily_pnl.index.max(), freq="D")
    daily_ret = daily_pnl.reindex(full_index, fill_value=0.0) / initial_capital
    std = daily_ret.std(ddof=1)
    if std == 0 or pd.isna(std):
        return 0.0
    return float(daily_ret.mean() / std * math.sqrt(_TRADING_DAYS_PER_YEAR))


def _benchmark_buy_hold(price_df: pd.DataFrame, fee_pct: float,
                        slippage_pct: float) -> tuple[float, float]:
    """
    Buy & hold over the whole period: buy at the first open, mark to the last
    close. Returns (gross_return_pct, net_return_pct) where net applies a single
    round-trip cost (entry + exit) of (fee+slippage) on each leg.
    """
    if price_df is None or len(price_df) < 2:
        return 0.0, 0.0
    entry = float(price_df["open"].iloc[0])
    exit_ = float(price_df["close"].iloc[-1])
    if entry <= 0:
        return 0.0, 0.0
    gross_pct = (exit_ - entry) / entry * 100.0
    # Round-trip cost as a % of entry notional: (entry+exit)/entry * cost_pct.
    cost_pct = (fee_pct + slippage_pct)
    cost_fraction = (entry + exit_) / entry * (cost_pct / 100.0) * 100.0
    net_pct = gross_pct - cost_fraction
    return float(gross_pct), float(net_pct)


def compute_metrics(
    trades: list,
    equity_curve: pd.Series,
    price_df: pd.DataFrame,
    initial_capital: float,
    *,
    fee_pct: float,
    slippage_pct: float,
    bars_in_position: int = 0,
    total_bars: int | None = None,
) -> BacktestMetrics:
    """
    Build a :class:`BacktestMetrics` from the engine's outputs.

    Args:
        trades:           list of ``BacktestTrade`` (each carries a ``PnLResult``).
        equity_curve:     per-bar equity Series (indexed by candle time).
        price_df:         the OHLCV frame the back-test ran on (for benchmark).
        initial_capital:  starting equity (USDT).
        fee_pct / slippage_pct: cost rates used (for the benchmark only — the
                          per-trade costs already live inside each PnLResult).
        bars_in_position: number of bars where a position was open (exposure).
        total_bars:       total bars in the run (defaults to len(price_df)).
    """
    m = BacktestMetrics()
    m.initial_capital = float(initial_capital)
    m.bars = int(total_bars if total_bars is not None else (len(price_df) if price_df is not None else 0))

    if price_df is not None and len(price_df) > 0:
        m.start = str(price_df.index[0])
        m.end = str(price_df.index[-1])

    # --- per-trade aggregation (net is authoritative, from app.pnl) ---
    nets = [t.pnl.net_pnl for t in trades]
    grosses = [t.pnl.gross_pnl for t in trades]
    fees = [t.pnl.fee for t in trades]
    slips = [t.pnl.slippage for t in trades]

    m.num_trades = len(trades)
    m.gross_pnl = float(sum(grosses))
    m.net_pnl = float(sum(nets))
    m.total_fees = float(sum(fees))
    m.total_slippage = float(sum(slips))

    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x < 0]
    m.num_wins = len(wins)
    m.num_losses = len(losses)
    m.win_rate = _safe_div(len(wins), len(nets))
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    # Profit factor: undefined with no losses → report inf-as-large when there
    # are wins, else 0.
    if gross_loss > 0:
        m.profit_factor = float(gross_win / gross_loss)
    else:
        m.profit_factor = float("inf") if gross_win > 0 else 0.0
    m.avg_trade = float(np.mean(nets)) if nets else 0.0
    m.avg_win = float(np.mean(wins)) if wins else 0.0
    m.avg_loss = float(np.mean(losses)) if losses else 0.0
    m.best_trade = float(max(nets)) if nets else 0.0
    m.worst_trade = float(min(nets)) if nets else 0.0

    if trades:
        m.avg_holding_bars = float(np.mean([t.bars_held for t in trades]))

    # --- equity-curve metrics ---
    if equity_curve is not None and len(equity_curve) > 0:
        m.final_equity = float(equity_curve.iloc[-1])
        m.max_drawdown_pct = _max_drawdown_pct(equity_curve)
    else:
        m.final_equity = m.initial_capital + m.net_pnl
    m.total_return_pct = _safe_div(m.final_equity - m.initial_capital, m.initial_capital) * 100.0

    # --- Sharpe (per-trade) on net returns relative to initial capital ---
    if len(nets) >= 2 and initial_capital > 0:
        rets = np.array(nets) / initial_capital
        std = rets.std(ddof=1)
        if std > 0:
            m.sharpe_per_trade = float(rets.mean() / std)

    # --- Sharpe (annualised) from daily PnL ---
    m.sharpe_annualized = _sharpe_annualized_from_daily(_daily_pnl(trades), initial_capital)

    # --- exposure ---
    if m.bars > 0:
        m.exposure = float(min(1.0, bars_in_position / m.bars))

    # --- benchmark buy & hold ---
    bh_gross, bh_net = _benchmark_buy_hold(price_df, fee_pct, slippage_pct)
    m.benchmark_return_pct = bh_gross
    m.benchmark_net_return_pct = bh_net
    m.alpha_pct = m.total_return_pct - bh_gross

    return m


def format_report(metrics: BacktestMetrics, *, title: str = "BACKTEST REPORT",
                   extra: dict | None = None) -> str:
    """Render a human-readable, fixed-width text report of the metrics."""
    pf = "inf" if math.isinf(metrics.profit_factor) else f"{metrics.profit_factor:.2f}"
    lines = [
        "=" * 60,
        title.center(60),
        "=" * 60,
    ]
    if extra:
        for k, v in extra.items():
            lines.append(f"  {k:<22} {v}")
        lines.append("-" * 60)
    if metrics.start:
        lines.append(f"  Period                 {metrics.start}  ->  {metrics.end}")
    lines += [
        f"  Bars                   {metrics.bars}",
        "-" * 60,
        f"  Initial capital        {metrics.initial_capital:>14,.2f} USDT",
        f"  Final equity           {metrics.final_equity:>14,.2f} USDT",
        f"  Total return           {metrics.total_return_pct:>14.2f} %",
        f"  Gross PnL              {metrics.gross_pnl:>14,.2f} USDT",
        f"  Net PnL                {metrics.net_pnl:>14,.2f} USDT",
        "-" * 60,
        f"  Trades                 {metrics.num_trades:>14d}",
        f"  Wins / Losses          {metrics.num_wins:>6d} / {metrics.num_losses:<6d}",
        f"  Win rate               {metrics.win_rate * 100:>14.2f} %",
        f"  Profit factor          {pf:>14}",
        f"  Avg trade              {metrics.avg_trade:>14,.2f} USDT",
        f"  Avg win / loss         {metrics.avg_win:>7,.2f} / {metrics.avg_loss:<7,.2f}",
        f"  Best / worst           {metrics.best_trade:>7,.2f} / {metrics.worst_trade:<7,.2f}",
        f"  Avg holding (bars)     {metrics.avg_holding_bars:>14.1f}",
        "-" * 60,
        f"  Max drawdown           {metrics.max_drawdown_pct:>14.2f} %",
        f"  Sharpe (per-trade)     {metrics.sharpe_per_trade:>14.2f}",
        f"  Sharpe (annualised)    {metrics.sharpe_annualized:>14.2f}",
        f"  Exposure               {metrics.exposure * 100:>14.2f} %",
        "-" * 60,
        f"  Total fees             {metrics.total_fees:>14,.2f} USDT",
        f"  Total slippage         {metrics.total_slippage:>14,.2f} USDT",
        "-" * 60,
        f"  Buy & Hold (gross)     {metrics.benchmark_return_pct:>14.2f} %",
        f"  Buy & Hold (net)       {metrics.benchmark_net_return_pct:>14.2f} %",
        f"  Alpha vs B&H           {metrics.alpha_pct:>14.2f} %",
        "=" * 60,
    ]
    return "\n".join(lines)
