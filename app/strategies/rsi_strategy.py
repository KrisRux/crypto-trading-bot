"""
RSI Overbought/Oversold Strategy.

Generates BUY when RSI falls below oversold threshold and then crosses back up.
Generates SELL when RSI rises above overbought threshold and then crosses back down.
"""

import pandas as pd

from app.strategies.base import Strategy, Signal, SignalType
from app.strategies.indicators import Indicators


class RsiStrategy(Strategy):
    name = "rsi_reversal"
    enabled = True

    def __init__(self, period: int = 14, oversold: float = 30.0,
                 overbought: float = 70.0):
        self.period = period
        self.oversold = oversold
        self.overbought = overbought

    def get_params(self) -> dict:
        return {
            "period": self.period,
            "oversold": self.oversold,
            "overbought": self.overbought,
            "enabled": self.enabled,
        }

    def generate_signals(self, df: pd.DataFrame, symbol: str) -> list[Signal]:
        if len(df) < self.period + 2:
            return []

        rsi = Indicators.rsi(df["close"], self.period)
        prev_rsi = rsi.iloc[-2]
        curr_rsi = rsi.iloc[-1]

        if pd.isna(prev_rsi) or pd.isna(curr_rsi):
            return []

        current_price = float(df["close"].iloc[-1])

        # RSI crosses up from oversold -> BUY
        if prev_rsi < self.oversold and curr_rsi >= self.oversold:
            return [Signal(
                signal_type=SignalType.BUY,
                symbol=symbol,
                price=current_price,
                strategy_name=self.name,
                reason=f"RSI crossed up from oversold ({curr_rsi:.1f})",
                confidence=0.60,
                metadata={"rsi": float(curr_rsi)},
            )]

        # RSI crosses down from overbought -> SELL
        if prev_rsi > self.overbought and curr_rsi <= self.overbought:
            return [Signal(
                signal_type=SignalType.SELL,
                symbol=symbol,
                price=current_price,
                strategy_name=self.name,
                reason=f"RSI crossed down from overbought ({curr_rsi:.1f})",
                confidence=0.60,
                metadata={"rsi": float(curr_rsi)},
            )]

        return []
