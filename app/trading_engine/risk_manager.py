"""
Risk management module.

Responsibilities:
  * position sizing — fixed-notional (legacy) and risk-based (size from SL distance)
  * stop-loss / take-profit levels — percentage or ATR-based (volatility-aware)
  * exit detection — SL-first (conservative tie-break) returning the trigger LEVEL

Exit detection deliberately checks the stop-loss BEFORE the take-profit: if a
single candle's range spans both levels we book the loss, never the optimistic
win. It also returns the trigger price so realised PnL is booked at the stop /
target rather than at a later (possibly recovered) price.
"""

import logging

from app.config import settings

logger = logging.getLogger(__name__)


def _is_short(side) -> bool:
    return str(getattr(side, "value", side)).upper() == "SELL"


class RiskManager:
    def __init__(self, max_position_pct: float = 2.0,
                 default_sl_pct: float = 3.0,
                 default_tp_pct: float = 5.0):
        # Maximum % of total capital per single position (hard notional ceiling)
        self.max_position_pct = max_position_pct
        # Default stop-loss and take-profit percentages
        self.default_sl_pct = default_sl_pct
        self.default_tp_pct = default_tp_pct

    # ------------------------------------------------------------------
    # Position sizing
    # ------------------------------------------------------------------

    def calculate_position_size(self, capital: float, price: float) -> float:
        """Legacy fixed-notional sizing: max_position_pct% of capital as notional."""
        if price <= 0:
            return 0.0
        max_usd = capital * (self.max_position_pct / 100)
        quantity = max_usd / price
        logger.info("Position sizing (notional): capital=%.2f max_usd=%.2f qty=%.6f @ %.2f",
                     capital, max_usd, quantity, price)
        return quantity

    def calculate_position_size_risk(self, equity: float, entry_price: float,
                                     stop_price: float,
                                     risk_pct: float | None = None) -> float:
        """Risk-based sizing: choose qty so the loss at the stop equals
        ``risk_pct``% of equity. Capped by ``max_position_pct`` as a hard
        notional ceiling. Falls back to notional sizing if the stop distance
        is unusable.
        """
        if entry_price <= 0:
            return 0.0
        risk_pct = settings.risk_pct_per_trade if risk_pct is None else risk_pct
        stop_dist = abs(entry_price - stop_price)
        if stop_dist <= 0:
            return self.calculate_position_size(equity, entry_price)
        risk_amount = equity * (risk_pct / 100.0)
        qty = risk_amount / stop_dist
        max_notional = equity * (self.max_position_pct / 100.0)
        if qty * entry_price > max_notional:
            qty = max_notional / entry_price
            logger.info("Risk sizing capped by notional ceiling (%.1f%% equity)",
                         self.max_position_pct)
        logger.info("Position sizing (risk): equity=%.2f risk=%.2f%% stop_dist=%.6f qty=%.6f",
                     equity, risk_pct, stop_dist, qty)
        return qty

    # ------------------------------------------------------------------
    # Stop-loss / take-profit levels
    # ------------------------------------------------------------------

    def calculate_stop_loss(self, entry_price: float, sl_pct: float | None = None,
                            side="BUY") -> float:
        """Stop loss X% adverse to entry (below for long, above for short)."""
        pct = sl_pct if sl_pct is not None else self.default_sl_pct
        if _is_short(side):
            return round(entry_price * (1 + pct / 100), 2)
        return round(entry_price * (1 - pct / 100), 2)

    def calculate_take_profit(self, entry_price: float, tp_pct: float | None = None,
                              side="BUY") -> float:
        """Take profit X% favourable to entry (above for long, below for short)."""
        pct = tp_pct if tp_pct is not None else self.default_tp_pct
        if _is_short(side):
            return round(entry_price * (1 - pct / 100), 2)
        return round(entry_price * (1 + pct / 100), 2)

    def calculate_atr_stops(self, entry_price: float, atr: float, side="BUY",
                            sl_mult: float | None = None,
                            tp_mult: float | None = None) -> tuple[float, float]:
        """Volatility-aware SL/TP from ATR. Returns (stop_loss, take_profit).

        Falls back to the percentage stops when ATR is missing/non-positive so a
        bad indicator never produces a degenerate (or inverted) stop.
        """
        sl_mult = settings.atr_sl_mult if sl_mult is None else sl_mult
        tp_mult = settings.atr_tp_mult if tp_mult is None else tp_mult
        if not atr or atr <= 0 or entry_price <= 0:
            return (self.calculate_stop_loss(entry_price, side=side),
                    self.calculate_take_profit(entry_price, side=side))
        if _is_short(side):
            sl = entry_price + sl_mult * atr
            tp = entry_price - tp_mult * atr
        else:
            sl = entry_price - sl_mult * atr
            tp = entry_price + tp_mult * atr
        return (round(sl, 8), round(max(tp, 0.0), 8))

    # ------------------------------------------------------------------
    # Exit detection
    # ------------------------------------------------------------------

    def should_close_position(self, entry_price: float, current_price: float,
                              stop_loss: float, take_profit: float,
                              candle_high: float | None = None,
                              candle_low: float | None = None,
                              side="BUY") -> tuple[str, float] | None:
        """Decide whether an open position must close.

        Returns ``(reason, exit_level)`` where reason is ``"sl"`` or ``"tp"`` and
        exit_level is the trigger price (book PnL there, not at a recovered
        price), or ``None`` if neither level was reached.

        Stop-loss is evaluated FIRST so a candle that spans both levels books the
        loss (conservative). Uses candle high/low for intrabar detection and the
        live price as a supplement for the still-forming candle.
        """
        high = candle_high if candle_high is not None else current_price
        low = candle_low if candle_low is not None else current_price

        if _is_short(side):
            if stop_loss and (high >= stop_loss or current_price >= stop_loss):
                return ("sl", stop_loss)
            if take_profit and (low <= take_profit or current_price <= take_profit):
                return ("tp", take_profit)
            return None

        # long
        if stop_loss and (low <= stop_loss or current_price <= stop_loss):
            return ("sl", stop_loss)
        if take_profit and (high >= take_profit or current_price >= take_profit):
            return ("tp", take_profit)
        return None

    # ------------------------------------------------------------------

    def get_params(self) -> dict:
        return {
            "max_position_pct": self.max_position_pct,
            "default_sl_pct": self.default_sl_pct,
            "default_tp_pct": self.default_tp_pct,
        }

    def set_params(self, params: dict):
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, float(v))
