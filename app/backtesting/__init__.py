"""
Event-driven back-testing harness for the crypto trading bot.

This package is self-contained and re-uses (read-only) the production
strategies, indicators and the centralised PnL accounting in ``app.pnl`` so
that back-test numbers reconcile exactly with the live engine. It adds **no**
new pip dependencies (pandas + numpy + the standard library only).

Public API
----------
- :func:`app.backtesting.data.load_klines_rest` — paginated Binance REST loader
  (real use). Network only — never called from the test-suite.
- :func:`app.backtesting.data.load_dataframe` — normalise a DataFrame / CSV into
  the canonical OHLCV shape (no network — used by tests).
- :class:`app.backtesting.engine.Backtester` — the bar-by-bar, no-lookahead
  back-tester. :class:`app.backtesting.engine.BacktestConfig` configures it and
  :class:`app.backtesting.engine.BacktestResult` holds the output.
- :func:`app.backtesting.engine.walk_forward` — rolling out-of-sample evaluation.
- :func:`app.backtesting.metrics.compute_metrics` — performance + benchmark.
- :class:`app.backtesting.metrics.BacktestMetrics` — the metrics dataclass.
"""

from app.backtesting.engine import (
    Backtester,
    BacktestConfig,
    BacktestResult,
    BacktestTrade,
    walk_forward,
)
from app.backtesting.metrics import BacktestMetrics, compute_metrics

__all__ = [
    "Backtester",
    "BacktestConfig",
    "BacktestResult",
    "BacktestTrade",
    "walk_forward",
    "BacktestMetrics",
    "compute_metrics",
]
