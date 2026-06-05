"""
Centralised PnL accounting — the SINGLE source of truth for fees, slippage and
net profit across the whole bot (engine, paper portfolio, API reporting,
back-testing and the DB backfill).

Design rules
------------
* ``gross_pnl`` is price-only: ``(exit - entry) * qty`` for a long, the negative
  of that for a short. It uses the *quoted* price (slippage is NOT baked into
  the fill price anywhere — it is modelled here as an explicit cost).
* ``fee`` and ``slippage`` are modelled as costs on the traded notional of BOTH
  legs (entry + exit), so a round trip pays two sides.
* ``net_pnl = gross_pnl - fee - slippage``.

Keeping every consumer on these functions guarantees that ``/balance``,
``/performance/*``, the kill-switch and the back-tester all report the same
number — the bug this module exists to kill was four divergent fee formulas.

Pure module: depends only on the standard library so it can be imported from
``database.py`` without creating an import cycle.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PnLResult:
    gross_pnl: float      # price-only PnL (USDT)
    fee: float            # round-trip fees (USDT)
    slippage: float       # round-trip slippage cost (USDT)
    cost: float           # fee + slippage (USDT)
    net_pnl: float        # gross_pnl - cost (USDT)
    gross_pnl_pct: float  # gross relative to entry notional (%)
    net_pnl_pct: float    # net relative to entry notional (%)

    def as_dict(self) -> dict:
        return {
            "gross_pnl": round(self.gross_pnl, 8),
            "fee": round(self.fee, 8),
            "slippage": round(self.slippage, 8),
            "cost": round(self.cost, 8),
            "net_pnl": round(self.net_pnl, 8),
            "gross_pnl_pct": round(self.gross_pnl_pct, 6),
            "net_pnl_pct": round(self.net_pnl_pct, 6),
        }


def _norm_side(side) -> str:
    s = getattr(side, "value", side)
    return str(s).upper()


def round_trip_cost(entry_price: float, exit_price: float, quantity: float,
                    fee_pct: float, slippage_pct: float) -> tuple[float, float]:
    """Return (fee, slippage) charged on entry + exit legs of a round trip."""
    notional = (abs(entry_price) + abs(exit_price)) * abs(quantity)
    fee = notional * (fee_pct / 100.0)
    slippage = notional * (slippage_pct / 100.0)
    return fee, slippage


def gross_pnl(side, entry_price: float, exit_price: float, quantity: float) -> float:
    """Price-only PnL. Long profits when price rises; short when it falls."""
    direction = -1.0 if _norm_side(side) == "SELL" else 1.0
    return (exit_price - entry_price) * quantity * direction


def compute_pnl(side, entry_price: float, exit_price: float, quantity: float,
                fee_pct: float, slippage_pct: float) -> PnLResult:
    """Full realised-PnL breakdown for a closed position (entry -> exit)."""
    g = gross_pnl(side, entry_price, exit_price, quantity)
    fee, slip = round_trip_cost(entry_price, exit_price, quantity, fee_pct, slippage_pct)
    cost = fee + slip
    net = g - cost
    base = abs(entry_price) * abs(quantity)
    g_pct = (g / base * 100.0) if base else 0.0
    n_pct = (net / base * 100.0) if base else 0.0
    return PnLResult(g, fee, slip, cost, net, g_pct, n_pct)


def unrealised_pnl(side, entry_price: float, current_price: float, quantity: float,
                   fee_pct: float, slippage_pct: float) -> PnLResult:
    """Mark-to-market breakdown for an open position (entry -> current price).

    Equivalent to :func:`compute_pnl` with ``exit_price = current_price`` — the
    fee/slippage represent the *estimated* cost still to be paid on the round trip.
    """
    return compute_pnl(side, entry_price, current_price, quantity, fee_pct, slippage_pct)
