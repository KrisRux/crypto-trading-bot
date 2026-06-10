"""
Command-line entry point for the back-tester.

Examples
--------
Real data from Binance (live host), regime_breakout, 365 days of 4h candles::

    venv/Scripts/python.exe -m app.backtesting.run \\
        --symbol BTCUSDT --interval 4h --days 365 --strategy regime_breakout

Override costs::

    python -m app.backtesting.run --symbol ETHUSDT --interval 4h --days 365 \\
        --strategy regime_breakout --fee 0.075 --slippage 0.02

Back-test a local CSV (no network) and run a walk-forward pass::

    python -m app.backtesting.run --csv data/btc_4h.csv --strategy regime_breakout \\
        --walk-forward --train 1500 --test 500

The report is printed to stdout; ``--json`` additionally dumps the metrics as
JSON for piping into other tools.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from app.config import settings


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m app.backtesting.run",
        description="Event-driven back-tester for the crypto trading bot.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    # Data source: either Binance REST (--symbol/--days) or a local --csv.
    p.add_argument("--symbol", default="BTCUSDT", help="Trading pair, e.g. BTCUSDT")
    p.add_argument("--interval", default="15m",
                   help="Candle interval (1m,5m,15m,1h,4h,1d,...)")
    p.add_argument("--days", type=float, default=90.0,
                   help="Look-back window in days (Binance REST source)")
    p.add_argument("--csv", default=None,
                   help="Load candles from a CSV file instead of the network")
    p.add_argument("--testnet", action="store_true",
                   help="Fetch klines from the Binance testnet host")

    # Strategy + execution.
    p.add_argument("--strategy", default="regime_breakout",
                   help="Strategy name (see app.backtesting.engine registry)")
    p.add_argument("--allow-short", action="store_true",
                   help="Permit short positions (default: long-only, spot-like)")
    p.add_argument("--capital", type=float, default=10_000.0,
                   help="Initial capital (USDT)")
    p.add_argument("--position-size", type=float, default=100.0,
                   help="Percent of equity deployed per entry")

    # Costs / stops (default to live settings).
    p.add_argument("--fee", type=float, default=settings.taker_fee_pct,
                   help="Per-leg fee percent")
    p.add_argument("--slippage", type=float, default=settings.paper_slippage_pct,
                   help="Per-leg slippage percent")
    p.add_argument("--sl", type=float, default=settings.default_stop_loss_pct,
                   help="Stop-loss percent (used when ATR stops are off/NA)")
    p.add_argument("--tp", type=float, default=settings.default_take_profit_pct,
                   help="Take-profit percent (used when ATR stops are off/NA)")
    p.add_argument("--atr-stops", dest="atr_stops",
                   action=argparse.BooleanOptionalAction,
                   default=settings.use_atr_stops,
                   help="Use ATR-based stops (--no-atr-stops to disable)")

    # Walk-forward.
    p.add_argument("--walk-forward", action="store_true",
                   help="Run a rolling out-of-sample walk-forward instead of "
                        "a single pass")
    p.add_argument("--train", type=int, default=1500,
                   help="Walk-forward training/warm-up window (candles)")
    p.add_argument("--test", type=int, default=500,
                   help="Walk-forward out-of-sample window (candles)")
    p.add_argument("--anchored", action="store_true",
                   help="Anchored (expanding) walk-forward instead of rolling")

    p.add_argument("--json", action="store_true",
                   help="Also print metrics as JSON")
    p.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    return p


def _load_data(args):
    """Load candles from CSV (offline) or Binance REST (network)."""
    from app.backtesting.data import load_csv, load_klines_rest

    if args.csv:
        df = load_csv(args.csv)
        source = f"CSV {args.csv}"
    else:
        df = load_klines_rest(
            args.symbol, interval=args.interval, days=args.days,
            testnet=args.testnet,
        )
        host = "testnet" if args.testnet else "live"
        source = f"Binance {host} {args.symbol} {args.interval} ~{args.days}d"
    return df, source


def _build_config(args):
    from app.backtesting.engine import BacktestConfig
    return BacktestConfig(
        initial_capital=args.capital,
        position_size_pct=args.position_size,
        allow_short=args.allow_short,
        sl_pct=args.sl,
        tp_pct=args.tp,
        use_atr_stops=args.atr_stops,
        fee_pct=args.fee,
        slippage_pct=args.slippage,
        symbol=args.symbol,
    )


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Imports kept inside main so `--help` works without pandas import cost and
    # so a network failure surfaces a clean message rather than a traceback.
    from app.backtesting.engine import Backtester, walk_forward
    from app.backtesting.metrics import format_report

    try:
        df, source = _load_data(args)
    except Exception as exc:  # network / file errors
        print(f"ERROR loading data: {exc}", file=sys.stderr)
        return 2

    if len(df) < 10:
        print(f"ERROR: only {len(df)} candles loaded — need more data.",
              file=sys.stderr)
        return 2

    cfg = _build_config(args)

    if args.walk_forward:
        report = walk_forward(
            df, train_size=args.train, test_size=args.test,
            strategy=args.strategy, config=cfg, anchored=args.anchored,
        )
        extra = {
            "Source": source,
            "Strategy": args.strategy,
            "Mode": "walk-forward (anchored)" if args.anchored else "walk-forward (rolling)",
            "Windows": report.num_windows,
            "Train/Test": f"{args.train}/{args.test}",
            "Allow short": args.allow_short,
            "Fee/Slip %": f"{args.fee}/{args.slippage}",
        }
        print(format_report(report.aggregate,
                            title="WALK-FORWARD (OUT-OF-SAMPLE) REPORT",
                            extra=extra))
        if args.json:
            print(json.dumps(report.aggregate.as_dict(), indent=2, default=str))
        return 0

    bt = Backtester(args.strategy, cfg)
    result = bt.run(df)
    extra = {
        "Source": source,
        "Strategy": args.strategy,
        "Allow short": args.allow_short,
        "ATR stops": args.atr_stops,
        "Fee/Slip %": f"{args.fee}/{args.slippage}",
        "SL/TP %": f"{args.sl}/{args.tp}",
    }
    print(format_report(result.metrics, title="BACKTEST REPORT", extra=extra))
    if args.json:
        print(json.dumps(result.metrics.as_dict(), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
