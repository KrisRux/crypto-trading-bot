"""Offline tests for the comparative back-test orchestrator (app/backtesting/compare.py).

No network: only the glue (param injection + table formatting) is exercised; the
back-test engine itself is covered by tests/test_backtesting.py.
"""

from app.backtesting.compare import _make_strategy, _print_table, _build_parser


def test_make_strategy_applies_live_params():
    params_map = {"embient_enhanced": {"trend_buy_threshold": 91.0,
                                       "range_buy_threshold": 77.0,
                                       "enabled": True}}
    strat = _make_strategy("embient_enhanced", params_map)
    assert strat.trend_buy_threshold == 91.0
    assert strat.range_buy_threshold == 77.0


def test_make_strategy_no_params_uses_defaults():
    strat = _make_strategy("embient_enhanced", {})
    # constructs cleanly with code defaults, exposes the tunable attribute
    assert hasattr(strat, "trend_buy_threshold")


def test_make_strategy_ignores_unknown_params():
    # set_params only assigns attributes the strategy exposes — no crash on junk
    strat = _make_strategy("sma_crossover", {"sma_crossover": {"nonexistent_param": 123}})
    assert not hasattr(strat, "nonexistent_param")


def test_print_table_smoke(capsys):
    rows = [{
        "strategy": "embient_enhanced", "symbol": "BTCUSDT", "trades": 12,
        "net%": -3.21, "gross%": 1.0, "PF": 0.8, "win%": 33.3,
        "maxDD%": 5.5, "fees": 40.0, "B&H%": -2.0, "alpha%": -1.21,
    }]
    _print_table(rows)
    out = capsys.readouterr().out
    assert "embient_enhanced" in out and "BTCUSDT" in out


def test_parser_defaults():
    args = _build_parser().parse_args([])
    assert "BTCUSDT" in args.symbols
    assert args.strategies == "embient_enhanced"
    assert args.interval == "15m"
