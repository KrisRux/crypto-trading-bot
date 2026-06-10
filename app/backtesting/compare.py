"""
Comparative back-test runner — strategies x symbols x timeframes.

This is *orchestration only*: it re-uses :class:`app.backtesting.engine.Backtester`
and :func:`app.backtesting.engine.walk_forward` (the single source of back-test
logic) and prints a comparison table. It duplicates no simulation or cost maths.

Each (symbol, interval) dataset is fetched **once** and reused across strategies.
Live strategy parameters can be injected via ``--params-file`` (a JSON map
``{strategy_name: {param: value}}``) so the back-test reflects the *deployed*
configuration rather than code defaults.

Example::

    python -m app.backtesting.compare \\
        --symbols BTCUSDT,ETHUSDT,BNBUSDT,XRPUSDT,SOLUSDT,LTCUSDT \\
        --strategies regime_breakout --interval 4h --days 365 \\
        --position-size 20 --atr-tp 0 --params-file live_params.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys


def _build_parser() -> argparse.ArgumentParser:
    from app.config import settings
    p = argparse.ArgumentParser(
        prog="python -m app.backtesting.compare",
        description="Comparative back-tester (strategies x symbols).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--symbols", default="BTCUSDT,ETHUSDT,BNBUSDT,XRPUSDT,SOLUSDT,LTCUSDT",
                   help="Comma-separated symbols")
    p.add_argument("--strategies", default="regime_breakout",
                   help="Comma-separated strategy names")
    p.add_argument("--interval", default="15m", help="Candle interval")
    p.add_argument("--days", type=float, default=120.0, help="Look-back window (days)")
    p.add_argument("--position-size", type=float, default=20.0,
                   help="Percent of equity per entry")
    p.add_argument("--capital", type=float, default=10_000.0)
    p.add_argument("--fee", type=float, default=settings.taker_fee_pct)
    p.add_argument("--slippage", type=float, default=settings.paper_slippage_pct)
    p.add_argument("--allow-short", action="store_true")
    p.add_argument("--atr-stops", dest="atr_stops", action=argparse.BooleanOptionalAction,
                   default=settings.use_atr_stops)
    p.add_argument("--atr-sl", type=float, default=settings.atr_sl_mult,
                   help="ATR stop-loss multiplier")
    p.add_argument("--atr-tp", type=float, default=settings.atr_tp_mult,
                   help="ATR take-profit multiplier (<=0 disables the TP)")
    p.add_argument("--sl", type=float, default=settings.default_stop_loss_pct,
                   help="Fixed stop-loss %% (fallback when ATR stops are off)")
    p.add_argument("--tp", type=float, default=settings.default_take_profit_pct,
                   help="Fixed take-profit %% (<=0 disables the TP)")
    p.add_argument("--params-file", default=None,
                   help="JSON file {strategy: {param: value}} applied via set_params")
    p.add_argument("--walk-forward", action="store_true",
                   help="Use rolling out-of-sample aggregate metrics per cell")
    p.add_argument("--train", type=int, default=1500)
    p.add_argument("--test", type=int, default=500)
    p.add_argument("--testnet", action="store_true")
    p.add_argument("--json", action="store_true", help="Also dump rows as JSON")
    p.add_argument("--verbose", "-v", action="store_true")
    return p


def _make_strategy(name: str, params_map: dict):
    """Resolve a strategy and apply live params (if provided) via set_params."""
    from app.backtesting.engine import resolve_strategy
    strat = resolve_strategy(name)
    params = (params_map or {}).get(name) or (params_map or {}).get(getattr(strat, "name", ""))
    if params:
        # set_params only assigns attributes the strategy actually exposes
        strat.set_params({k: v for k, v in params.items() if k != "enabled"})
    return strat


def _fmt(v, nd=2):
    return "n/a" if v is None else f"{v:.{nd}f}"


def _print_table(rows: list[dict]):
    cols = [
        ("strategy", 16, "s"), ("symbol", 9, "s"), ("trades", 7, "d"),
        ("net%", 9, "f2"), ("gross%", 9, "f2"), ("PF", 6, "f2"),
        ("win%", 6, "f1"), ("maxDD%", 8, "f2"), ("fees", 9, "f1"),
        ("B&H%", 8, "f2"), ("alpha%", 8, "f2"),
    ]
    header = " ".join(f"{name:>{w}}" if name not in ("strategy", "symbol")
                      else f"{name:<{w}}" for name, w, _ in cols)
    print(header)
    print("-" * len(header))
    for r in rows:
        cells = []
        for name, w, kind in cols:
            v = r.get(name)
            if kind == "s":
                cells.append(f"{str(v):<{w}}")
            elif kind == "d":
                cells.append(f"{(v if v is not None else 0):>{w}d}")
            elif kind == "f1":
                cells.append(f"{_fmt(v,1):>{w}}")
            else:
                cells.append(f"{_fmt(v,2):>{w}}")
        print(" ".join(cells))


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.ERROR,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from app.backtesting.data import load_klines_rest
    from app.backtesting.engine import Backtester, BacktestConfig, walk_forward

    params_map = {}
    if args.params_file:
        with open(args.params_file, encoding="utf-8") as f:
            params_map = json.load(f)

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    strategies = [s.strip() for s in args.strategies.split(",") if s.strip()]

    def cfg_for(symbol):
        return BacktestConfig(
            initial_capital=args.capital, position_size_pct=args.position_size,
            allow_short=args.allow_short, use_atr_stops=args.atr_stops,
            atr_sl_mult=args.atr_sl, atr_tp_mult=args.atr_tp,
            sl_pct=args.sl, tp_pct=args.tp,
            fee_pct=args.fee, slippage_pct=args.slippage, symbol=symbol,
        )

    rows: list[dict] = []
    for symbol in symbols:
        try:
            df = load_klines_rest(symbol, interval=args.interval, days=args.days,
                                  testnet=args.testnet)
        except Exception as exc:
            print(f"  ! {symbol}: data load failed: {exc}", file=sys.stderr)
            continue
        if len(df) < max(60, (args.train + args.test) if args.walk_forward else 60):
            print(f"  ! {symbol}: only {len(df)} candles — skipped", file=sys.stderr)
            continue
        for name in strategies:
            try:
                strat = _make_strategy(name, params_map)
                if args.walk_forward:
                    rep = walk_forward(df, train_size=args.train, test_size=args.test,
                                       strategy=strat, config=cfg_for(symbol))
                    m = rep.aggregate
                else:
                    m = Backtester(strat, cfg_for(symbol)).run(df).metrics
                rows.append({
                    "strategy": name, "symbol": symbol, "trades": m.num_trades,
                    "net%": m.total_return_pct, "gross%": (m.gross_pnl / args.capital * 100),
                    "PF": m.profit_factor, "win%": m.win_rate,
                    "maxDD%": m.max_drawdown_pct, "fees": m.total_fees,
                    "B&H%": m.benchmark_net_return_pct, "alpha%": m.alpha_pct,
                })
            except Exception as exc:
                print(f"  ! {name}/{symbol}: {exc}", file=sys.stderr)

    mode = f"walk-forward {args.train}/{args.test}" if args.walk_forward else "single-pass"
    print(f"\n=== COMPARATIVE BACKTEST ({mode}) — {args.interval}, ~{args.days:.0f}d, "
          f"size {args.position_size:.0f}%, fee {args.fee}/{args.slippage} ===")
    _print_table(rows)
    # quick verdict
    pos = [r for r in rows if (r["net%"] or 0) > 0 and (r["alpha%"] or 0) > 0]
    print(f"\nCelle con net>0 E alpha>0 (battono buy&hold al netto): {len(pos)}/{len(rows)}")
    for r in pos:
        print(f"   -> {r['strategy']} {r['symbol']}: net={r['net%']:.2f}% alpha={r['alpha%']:.2f}% PF={r['PF']:.2f}")
    if args.json:
        print(json.dumps(rows, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
