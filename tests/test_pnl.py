"""Tests for the centralised PnL accounting module (app/pnl.py)."""

import math
from app.pnl import compute_pnl, gross_pnl, round_trip_cost, unrealised_pnl


def test_long_gross_and_net():
    # Buy 1 unit @100, sell @110. fee 0.1%/side, slip 0.02%/side.
    r = compute_pnl("BUY", 100.0, 110.0, 1.0, 0.1, 0.02)
    assert math.isclose(r.gross_pnl, 10.0)
    # notional = (100+110)*1 = 210; fee=0.21, slip=0.042
    assert math.isclose(r.fee, 0.21)
    assert math.isclose(r.slippage, 0.042)
    assert math.isclose(r.cost, 0.252)
    assert math.isclose(r.net_pnl, 9.748)
    assert math.isclose(r.gross_pnl_pct, 10.0)        # 10 / (100*1) * 100
    assert r.net_pnl_pct < r.gross_pnl_pct


def test_short_profits_when_price_falls():
    r = compute_pnl("SELL", 100.0, 90.0, 1.0, 0.1, 0.02)
    assert math.isclose(r.gross_pnl, 10.0)            # short gains 10 on a 10 drop
    assert r.net_pnl < r.gross_pnl                    # costs still apply
    # losing short: price rises
    r2 = compute_pnl("SELL", 100.0, 110.0, 1.0, 0.1, 0.02)
    assert math.isclose(r2.gross_pnl, -10.0)


def test_fees_always_reduce_net():
    r = compute_pnl("BUY", 50.0, 50.0, 2.0, 0.1, 0.02)
    assert math.isclose(r.gross_pnl, 0.0)
    assert r.net_pnl < 0                              # flat trade still loses the round-trip cost


def test_round_trip_cost_both_legs():
    fee, slip = round_trip_cost(100.0, 100.0, 1.0, 0.1, 0.02)
    assert math.isclose(fee, 0.2)                     # (100+100)*1*0.001
    assert math.isclose(slip, 0.04)


def test_enum_like_side_accepted():
    class S:
        value = "BUY"
    assert math.isclose(gross_pnl(S(), 100.0, 101.0, 1.0), 1.0)


def test_unrealised_matches_compute():
    a = unrealised_pnl("BUY", 100.0, 105.0, 1.0, 0.1, 0.02)
    b = compute_pnl("BUY", 100.0, 105.0, 1.0, 0.1, 0.02)
    assert a == b


def test_zero_quantity_safe():
    r = compute_pnl("BUY", 100.0, 110.0, 0.0, 0.1, 0.02)
    assert r.gross_pnl == 0.0 and r.net_pnl == 0.0 and r.net_pnl_pct == 0.0
