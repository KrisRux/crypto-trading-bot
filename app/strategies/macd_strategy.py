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

    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9,
                 adx_period: int = 14, adx_threshold: float = 25.0,
                 mode: str = "independent"):
        self.fast = fast
        self.slow = slow
        self.signal = signal
        self.adx_period = adx_period
        self.adx_threshold = adx_threshold
        # Modes:
        #   "independent" — MACD trades on its own, no dependency on other strategies
        #   "standalone"  — legacy alias for "independent" (kept for back-compat)
        #   "confirm_only" — suppressed unless embient agrees (conservative)
        self.mode = mode

    def get_params(self) -> dict:
        return {
            "fast": self.fast,
            "slow": self.slow,
            "signal": self.signal,
            "adx_period": self.adx_period,
            "adx_threshold": self.adx_threshold,
            "mode": self.mode,
            "enabled": self.enabled,
        }

    def generate_signals(self, df: pd.DataFrame, symbol: str,
                         precomputed_adx: float | None = None) -> list[Signal]:
        if len(df) < self.slow + self.signal + 1:
            return []

        # ADX filter: only trade MACD crossovers in trending markets (ADX > threshold)
        adx_val = precomputed_adx
        if adx_val is None and "high" in df.columns and "low" in df.columns and len(df) >= self.adx_period * 2 + 2:
            adx = Indicators.adx(df["high"], df["low"], df["close"], self.adx_period)
            adx_val = float(adx.iloc[-1]) if pd.notna(adx.iloc[-1]) else None
        if adx_val is not None and adx_val < self.adx_threshold:
            return []  # ranging market — MACD crossovers produce false signals

        macd_line, signal_line, _ = Indicators.macd(
            df["close"], self.fast, self.slow, self.signal
        )

        prev_macd, curr_macd = macd_line.iloc[-2], macd_line.iloc[-1]
        prev_sig, curr_sig = signal_line.iloc[-2], signal_line.iloc[-1]

        if pd.isna(prev_macd) or pd.isna(curr_macd):
            return []

        current_price = float(df["close"].iloc[-1])

        # Mode is passed to the engine via metadata so the regime gate can
        # apply different rules (independent trades on its own, confirm_only
        # waits for embient agreement).
        is_confirm = (self.mode == "confirm_only")
        mode_tag = self.mode
        tag_suffix = f" [{mode_tag}]"

        # MACD crosses above signal -> BUY
        if prev_macd <= prev_sig and curr_macd > curr_sig:
            return [Signal(
                signal_type=SignalType.BUY,
                symbol=symbol,
                price=current_price,
                strategy_name=self.name,
                reason="MACD bullish crossover" + tag_suffix,
                confidence=0.82,
                metadata={"confirm_only": is_confirm, "mode": mode_tag},
            )]

        # MACD crosses below signal -> SELL
        if prev_macd >= prev_sig and curr_macd < curr_sig:
            return [Signal(
                signal_type=SignalType.SELL,
                symbol=symbol,
                price=current_price,
                strategy_name=self.name,
                reason="MACD bearish crossover" + tag_suffix,
                confidence=0.82,
                metadata={"confirm_only": is_confirm, "mode": mode_tag},
            )]

        return []
