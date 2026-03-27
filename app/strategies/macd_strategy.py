"""
MACD Crossover Strategy.

Generates BUY when MACD line crosses above signal line.
Generates SELL when MACD line crosses below signal line.
"""

import pandas as pd

from app.strategies.base import Strategy, Signal, SignalType
from app.strategies.indicators import Indicators


class MacdStrategy(Strategy):
    name = "macd_crossover"
    enabled = True

    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9):
        self.fast = fast
        self.slow = slow
        self.signal = signal

    def get_params(self) -> dict:
        return {
            "fast": self.fast,
            "slow": self.slow,
            "signal": self.signal,
            "enabled": self.enabled,
        }

    def generate_signals(self, df: pd.DataFrame, symbol: str) -> list[Signal]:
        if len(df) < self.slow + self.signal + 1:
            return []

        macd_line, signal_line, _ = Indicators.macd(
            df["close"], self.fast, self.slow, self.signal
        )

        prev_macd, curr_macd = macd_line.iloc[-2], macd_line.iloc[-1]
        prev_sig, curr_sig = signal_line.iloc[-2], signal_line.iloc[-1]

        if pd.isna(prev_macd) or pd.isna(curr_macd):
            return []

        current_price = float(df["close"].iloc[-1])

        # MACD crosses above signal -> BUY
        if prev_macd <= prev_sig and curr_macd > curr_sig:
            return [Signal(
                signal_type=SignalType.BUY,
                symbol=symbol,
                price=current_price,
                strategy_name=self.name,
                reason="MACD bullish crossover",
                confidence=0.60,
            )]

        # MACD crosses below signal -> SELL
        if prev_macd >= prev_sig and curr_macd < curr_sig:
            return [Signal(
                signal_type=SignalType.SELL,
                symbol=symbol,
                price=current_price,
                strategy_name=self.name,
                reason="MACD bearish crossover",
                confidence=0.60,
            )]

        return []
