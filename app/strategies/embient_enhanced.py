"""
Embient-Enhanced Strategy.

Combines multiple technical indicators with rules and knowledge extracted
from the Embient agent-trading-skills library to produce higher-quality
signals. Instead of relying on a single indicator crossover, this strategy
applies a multi-factor scoring system informed by:

- Moving Average Crossover rules (SMA/EMA alignment)
- RSI extremes with divergence awareness
- MACD momentum confirmation
- Volume confirmation
- Bollinger Band squeeze/breakout
- Risk management rules from Embient skills (position sizing, stop-loss)

Each factor contributes a score. A BUY signal is generated only when the
combined score exceeds a configurable threshold, reducing false positives.
"""

import logging
import pandas as pd

from app.strategies.base import Strategy, Signal, SignalType
from app.strategies.indicators import Indicators

logger = logging.getLogger(__name__)


class EmbientEnhancedStrategy(Strategy):
    name = "embient_enhanced"
    enabled = True

    def __init__(
        self,
        # Score threshold to trigger a signal (0-100)
        buy_threshold: float = 60.0,
        sell_threshold: float = 60.0,
        # SMA params
        sma_fast: int = 10,
        sma_slow: int = 30,
        # RSI params
        rsi_period: int = 14,
        rsi_oversold: float = 30.0,
        rsi_overbought: float = 70.0,
        # MACD params
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        # Bollinger params
        bb_period: int = 20,
        bb_std: float = 2.0,
        # Volume confirmation
        volume_ma_period: int = 20,
    ):
        self.buy_threshold = buy_threshold
        self.sell_threshold = sell_threshold
        self.sma_fast = sma_fast
        self.sma_slow = sma_slow
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.macd_fast = macd_fast
        self.macd_slow = macd_slow
        self.macd_signal = macd_signal
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.volume_ma_period = volume_ma_period

    def get_params(self) -> dict:
        return {
            "buy_threshold": self.buy_threshold,
            "sell_threshold": self.sell_threshold,
            "sma_fast": self.sma_fast,
            "sma_slow": self.sma_slow,
            "rsi_period": self.rsi_period,
            "rsi_oversold": self.rsi_oversold,
            "rsi_overbought": self.rsi_overbought,
            "enabled": self.enabled,
        }

    def _score_buy(self, df: pd.DataFrame) -> tuple[float, list[str]]:
        """
        Calculate a buy score (0-100) based on multiple factors.
        Returns (score, list_of_reasons).
        """
        close = df["close"]
        score = 0.0
        reasons: list[str] = []

        # --- Factor 1: SMA alignment (0-25 points) ---
        # Embient skill "moving-average-crossover": golden cross = bullish
        fast_sma = Indicators.sma(close, self.sma_fast)
        slow_sma = Indicators.sma(close, self.sma_slow)
        if pd.notna(fast_sma.iloc[-1]) and pd.notna(slow_sma.iloc[-1]):
            if fast_sma.iloc[-1] > slow_sma.iloc[-1]:
                score += 15
                reasons.append("SMA fast > slow (bullish alignment)")
            # Bonus: recent crossover
            if (pd.notna(fast_sma.iloc[-2]) and pd.notna(slow_sma.iloc[-2])
                    and fast_sma.iloc[-2] <= slow_sma.iloc[-2]
                    and fast_sma.iloc[-1] > slow_sma.iloc[-1]):
                score += 10
                reasons.append("SMA golden cross (fresh crossover)")

        # --- Factor 2: RSI zone (0-25 points) ---
        # Embient skill "rsi-divergence": oversold bounce is strong buy
        rsi = Indicators.rsi(close, self.rsi_period)
        if pd.notna(rsi.iloc[-1]):
            curr_rsi = rsi.iloc[-1]
            if curr_rsi < self.rsi_oversold:
                score += 20
                reasons.append(f"RSI oversold ({curr_rsi:.1f})")
            elif curr_rsi < 45:
                score += 10
                reasons.append(f"RSI in low zone ({curr_rsi:.1f})")
            # Bonus: RSI bouncing up from oversold
            if (pd.notna(rsi.iloc[-2])
                    and rsi.iloc[-2] < self.rsi_oversold
                    and curr_rsi >= self.rsi_oversold):
                score += 5
                reasons.append("RSI bouncing from oversold")

        # --- Factor 3: MACD momentum (0-25 points) ---
        # Embient skill "macd-trading": histogram rising = momentum building
        macd_line, signal_line, histogram = Indicators.macd(
            close, self.macd_fast, self.macd_slow, self.macd_signal
        )
        if pd.notna(macd_line.iloc[-1]) and pd.notna(signal_line.iloc[-1]):
            if macd_line.iloc[-1] > signal_line.iloc[-1]:
                score += 15
                reasons.append("MACD above signal (bullish)")
            # Bonus: histogram increasing
            if (pd.notna(histogram.iloc[-2])
                    and histogram.iloc[-1] > histogram.iloc[-2]):
                score += 10
                reasons.append("MACD histogram rising")

        # --- Factor 4: Bollinger Band position (0-15 points) ---
        # Embient skill "bollinger-bands": price near lower band = potential buy
        upper, middle, lower = Indicators.bollinger_bands(
            close, self.bb_period, self.bb_std
        )
        if pd.notna(lower.iloc[-1]) and pd.notna(upper.iloc[-1]):
            current_price = close.iloc[-1]
            bb_range = upper.iloc[-1] - lower.iloc[-1]
            if bb_range > 0:
                bb_position = (current_price - lower.iloc[-1]) / bb_range
                if bb_position < 0.2:
                    score += 15
                    reasons.append(f"Price near lower BB ({bb_position:.0%})")
                elif bb_position < 0.4:
                    score += 8
                    reasons.append(f"Price in lower BB half ({bb_position:.0%})")

        # --- Factor 5: Volume confirmation (0-10 points) ---
        # Embient skill "volume-profile-trading": above-average volume confirms
        if "volume" in df.columns:
            vol = df["volume"]
            vol_ma = vol.rolling(window=self.volume_ma_period).mean()
            if pd.notna(vol_ma.iloc[-1]) and vol_ma.iloc[-1] > 0:
                vol_ratio = vol.iloc[-1] / vol_ma.iloc[-1]
                if vol_ratio > 1.5:
                    score += 10
                    reasons.append(f"High volume ({vol_ratio:.1f}x avg)")
                elif vol_ratio > 1.0:
                    score += 5
                    reasons.append(f"Above-avg volume ({vol_ratio:.1f}x)")

        return score, reasons

    def _score_sell(self, df: pd.DataFrame) -> tuple[float, list[str]]:
        """Calculate a sell score (0-100) based on multiple factors."""
        close = df["close"]
        score = 0.0
        reasons: list[str] = []

        # --- SMA: death cross ---
        fast_sma = Indicators.sma(close, self.sma_fast)
        slow_sma = Indicators.sma(close, self.sma_slow)
        if pd.notna(fast_sma.iloc[-1]) and pd.notna(slow_sma.iloc[-1]):
            if fast_sma.iloc[-1] < slow_sma.iloc[-1]:
                score += 15
                reasons.append("SMA fast < slow (bearish alignment)")
            if (pd.notna(fast_sma.iloc[-2]) and pd.notna(slow_sma.iloc[-2])
                    and fast_sma.iloc[-2] >= slow_sma.iloc[-2]
                    and fast_sma.iloc[-1] < slow_sma.iloc[-1]):
                score += 10
                reasons.append("SMA death cross (fresh crossover)")

        # --- RSI: overbought ---
        rsi = Indicators.rsi(close, self.rsi_period)
        if pd.notna(rsi.iloc[-1]):
            curr_rsi = rsi.iloc[-1]
            if curr_rsi > self.rsi_overbought:
                score += 20
                reasons.append(f"RSI overbought ({curr_rsi:.1f})")
            elif curr_rsi > 55:
                score += 10
                reasons.append(f"RSI in high zone ({curr_rsi:.1f})")
            if (pd.notna(rsi.iloc[-2])
                    and rsi.iloc[-2] > self.rsi_overbought
                    and curr_rsi <= self.rsi_overbought):
                score += 5
                reasons.append("RSI dropping from overbought")

        # --- MACD: bearish ---
        macd_line, signal_line, histogram = Indicators.macd(
            close, self.macd_fast, self.macd_slow, self.macd_signal
        )
        if pd.notna(macd_line.iloc[-1]) and pd.notna(signal_line.iloc[-1]):
            if macd_line.iloc[-1] < signal_line.iloc[-1]:
                score += 15
                reasons.append("MACD below signal (bearish)")
            if (pd.notna(histogram.iloc[-2])
                    and histogram.iloc[-1] < histogram.iloc[-2]):
                score += 10
                reasons.append("MACD histogram falling")

        # --- Bollinger: near upper band ---
        upper, middle, lower = Indicators.bollinger_bands(
            close, self.bb_period, self.bb_std
        )
        if pd.notna(lower.iloc[-1]) and pd.notna(upper.iloc[-1]):
            bb_range = upper.iloc[-1] - lower.iloc[-1]
            if bb_range > 0:
                bb_position = (close.iloc[-1] - lower.iloc[-1]) / bb_range
                if bb_position > 0.8:
                    score += 15
                    reasons.append(f"Price near upper BB ({bb_position:.0%})")
                elif bb_position > 0.6:
                    score += 8
                    reasons.append(f"Price in upper BB half ({bb_position:.0%})")

        # --- Volume spike on down move ---
        if "volume" in df.columns:
            vol = df["volume"]
            vol_ma = vol.rolling(window=self.volume_ma_period).mean()
            if pd.notna(vol_ma.iloc[-1]) and vol_ma.iloc[-1] > 0:
                vol_ratio = vol.iloc[-1] / vol_ma.iloc[-1]
                price_change = (close.iloc[-1] - close.iloc[-2]) / close.iloc[-2]
                if vol_ratio > 1.5 and price_change < 0:
                    score += 10
                    reasons.append(f"High volume on down move ({vol_ratio:.1f}x)")

        return score, reasons

    def generate_signals(self, df: pd.DataFrame, symbol: str) -> list[Signal]:
        min_required = max(self.sma_slow, self.bb_period, self.macd_slow + self.macd_signal) + 2
        if len(df) < min_required:
            return []

        current_price = float(df["close"].iloc[-1])

        # Calculate both scores
        buy_score, buy_reasons = self._score_buy(df)
        sell_score, sell_reasons = self._score_sell(df)

        signals: list[Signal] = []

        if buy_score >= self.buy_threshold and buy_score > sell_score:
            confidence = min(buy_score / 100.0, 1.0)
            signals.append(Signal(
                signal_type=SignalType.BUY,
                symbol=symbol,
                price=current_price,
                strategy_name=self.name,
                reason=f"Multi-factor BUY (score {buy_score:.0f}/100): {'; '.join(buy_reasons)}",
                confidence=confidence,
                metadata={"buy_score": buy_score, "sell_score": sell_score},
            ))
            logger.info("Embient BUY signal: score=%.0f reasons=%s", buy_score, buy_reasons)

        elif sell_score >= self.sell_threshold and sell_score > buy_score:
            confidence = min(sell_score / 100.0, 1.0)
            signals.append(Signal(
                signal_type=SignalType.SELL,
                symbol=symbol,
                price=current_price,
                strategy_name=self.name,
                reason=f"Multi-factor SELL (score {sell_score:.0f}/100): {'; '.join(sell_reasons)}",
                confidence=confidence,
                metadata={"buy_score": buy_score, "sell_score": sell_score},
            ))
            logger.info("Embient SELL signal: score=%.0f reasons=%s", sell_score, sell_reasons)

        return signals
