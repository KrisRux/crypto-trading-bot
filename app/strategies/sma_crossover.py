"""
SMA Crossover Strategy.

Generates a BUY signal when the fast SMA crosses above the slow SMA,
and a SELL signal when it crosses below.
"""

import pandas as pd

from app.strategies.base import Strategy, Signal, SignalType
from app.strategies.indicators import Indicators


class SmaCrossoverStrategy(Strategy):
    name = "sma_crossover"
    enabled = True

    def __init__(self, fast_period: int = 10, slow_period: int = 30,
                 adx_period: int = 14, adx_threshold: float = 25.0):
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.adx_period = adx_period
        self.adx_threshold = adx_threshold

    def get_params(self) -> dict:
        return {
            "fast_period": self.fast_period,
            "slow_period": self.slow_period,
            "adx_period": self.adx_period,
            "adx_threshold": self.adx_threshold,
            "enabled": self.enabled,
        }

    def generate_signals(self, df: pd.DataFrame, symbol: str) -> list[Signal]:
        if len(df) < self.slow_period + 1:
            return []

        # ADX filter: only trade in trending markets (ADX > threshold)
        if "high" in df.columns and "low" in df.columns and len(df) >= self.adx_period * 2 + 2:
            adx = Indicators.adx(df["high"], df["low"], df["close"], self.adx_period)
            if pd.notna(adx.iloc[-1]) and float(adx.iloc[-1]) < self.adx_threshold:
                return []  # ranging market — SMA crossovers are noise

        close = df["close"]
        fast = Indicators.sma(close, self.fast_period)
        slow = Indicators.sma(close, self.slow_period)

        # Detect crossover on the two most recent completed bars
        prev_fast, curr_fast = fast.iloc[-2], fast.iloc[-1]
        prev_slow, curr_slow = slow.iloc[-2], slow.iloc[-1]

        if pd.isna(prev_fast) or pd.isna(curr_fast):
            return []

        current_price = float(close.iloc[-1])

        # Golden cross: fast crosses above slow -> BUY
        if prev_fast <= prev_slow and curr_fast > curr_slow:
            return [Signal(
                signal_type=SignalType.BUY,
                symbol=symbol,
                price=current_price,
                strategy_name=self.name,
                reason=f"SMA golden cross (fast={self.fast_period} > slow={self.slow_period})",
                confidence=0.65,
            )]

        # Death cross: fast crosses below slow -> SELL
        if prev_fast >= prev_slow and curr_fast < curr_slow:
            return [Signal(
                signal_type=SignalType.SELL,
                symbol=symbol,
                price=current_price,
                strategy_name=self.name,
                reason=f"SMA death cross (fast={self.fast_period} < slow={self.slow_period})",
                confidence=0.65,
            )]

        return []
