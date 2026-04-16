"""
Risk management module.

Enforces position sizing limits and calculates stop-loss / take-profit levels
based on configurable percentages.
"""

import logging

logger = logging.getLogger(__name__)


class RiskManager:
    def __init__(self, max_position_pct: float = 2.0,
                 default_sl_pct: float = 3.0,
                 default_tp_pct: float = 5.0):
        # Maximum % of total capital per single position
        self.max_position_pct = max_position_pct
        # Default stop-loss and take-profit percentages
        self.default_sl_pct = default_sl_pct
        self.default_tp_pct = default_tp_pct

    def calculate_position_size(self, capital: float, price: float) -> float:
        """
        Calculate the maximum quantity to buy given the capital and risk limit.
        Returns quantity in base asset units.
        """
        max_usd = capital * (self.max_position_pct / 100)
        quantity = max_usd / price
        logger.info("Position sizing: capital=%.2f, max_usd=%.2f, qty=%.6f @ price=%.2f",
                     capital, max_usd, quantity, price)
        return quantity

    def calculate_stop_loss(self, entry_price: float, sl_pct: float | None = None) -> float:
        """Stop loss price X% below entry."""
        pct = sl_pct if sl_pct is not None else self.default_sl_pct
        sl = entry_price * (1 - pct / 100)
        return round(sl, 2)

    def calculate_take_profit(self, entry_price: float, tp_pct: float | None = None) -> float:
        """Take profit price X% above entry."""
        pct = tp_pct if tp_pct is not None else self.default_tp_pct
        tp = entry_price * (1 + pct / 100)
        return round(tp, 2)

    def should_close_position(self, entry_price: float, current_price: float,
                              stop_loss: float, take_profit: float,
                              candle_high: float | None = None,
                              candle_low: float | None = None) -> str | None:
        """
        Check if a position should be closed.

        Uses candle high/low to detect intracandle TP/SL hits (consistent with
        backtesting). current_price supplements for the live candle not yet closed.

        Returns 'tp' if take-profit hit, 'sl' if stop-loss hit, None otherwise.
        """
        high = candle_high if candle_high is not None else current_price
        low  = candle_low  if candle_low  is not None else current_price

        # TP: candle reached take-profit level, or live price already above it
        if high >= take_profit or current_price >= take_profit:
            return "tp"
        # SL: candle reached stop-loss level, or live price already below it
        if low <= stop_loss or current_price <= stop_loss:
            return "sl"
        return None

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
