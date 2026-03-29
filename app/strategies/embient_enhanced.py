"""
Embient-Enhanced Strategy — skill-driven implementation.

At startup, parameters (RSI thresholds, MACD periods, BB settings, MA periods)
are read directly from the Embient skills library via regex extraction from the
skill markdown files.  Key rules from each skill are loaded and enforced as
active score modifiers during signal generation, so any update to the skill
files is reflected automatically on the next bot restart.

Skills wired in:
  technical-strategies/moving-average-crossover  → SMA params + ADX trend filter
  technical-strategies/rsi-divergence            → RSI thresholds + extreme-zone rule
  technical-strategies/macd-trading              → MACD params + zero-line context rule
  technical-strategies/bollinger-bands           → BB params + band-width expansion rule
  technical-strategies/volume-profile-trading    → volume confirmation weight
"""

import logging
import pandas as pd

from app.strategies.base import Strategy, Signal, SignalType
from app.strategies.indicators import Indicators
from app.embient_skills.loader import SkillsLibrary

logger = logging.getLogger(__name__)


class EmbientEnhancedStrategy(Strategy):
    name = "embient_enhanced"
    enabled = True

    _ADX_PERIOD = 14

    # Maps internal factor names to skill names in the library
    SKILL_MAP = {
        "sma":    "moving-average-crossover",
        "rsi":    "rsi-divergence",
        "macd":   "macd-trading",
        "bb":     "bollinger-bands",
        "volume": "volume-profile-trading",
    }

    def __init__(
        self,
        skills_library: SkillsLibrary | None = None,
        # Score thresholds (0-100)
        buy_threshold: float = 60.0,
        sell_threshold: float = 60.0,
        # SMA defaults (overridden by skill if available)
        sma_fast: int = 10,
        sma_slow: int = 30,
        # RSI defaults
        rsi_period: int = 14,
        rsi_oversold: float = 30.0,
        rsi_overbought: float = 70.0,
        # MACD defaults
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        # Bollinger defaults
        bb_period: int = 20,
        bb_std: float = 2.0,
        # Volume
        volume_ma_period: int = 20,
        # ADX threshold for trend detection (moving-average-crossover key rule)
        adx_trend_threshold: float = 25.0,
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
        self.adx_trend_threshold = adx_trend_threshold
        self._active_rules: list[str] = []

        if skills_library is not None:
            self._load_from_skills(skills_library)
            self._active_rules = self._collect_active_rules(skills_library)
            logger.info(
                "EmbientEnhancedStrategy: params loaded from skills, %d active rules enforced",
                len(self._active_rules),
            )
            for rule in self._active_rules:
                logger.debug("  Rule: %s", rule)

    # ------------------------------------------------------------------
    # Skill integration
    # ------------------------------------------------------------------

    def _load_from_skills(self, lib: SkillsLibrary) -> None:
        """Override default params with values extracted from Embient skills."""
        p = lib.extract_numeric_params("rsi-divergence")
        if "rsi_oversold" in p:
            self.rsi_oversold = float(p["rsi_oversold"])
        if "rsi_overbought" in p:
            self.rsi_overbought = float(p["rsi_overbought"])

        p = lib.extract_numeric_params("macd-trading")
        if "macd_fast" in p:
            self.macd_fast = int(p["macd_fast"])
        if "macd_slow" in p:
            self.macd_slow = int(p["macd_slow"])
        if "macd_signal" in p:
            self.macd_signal = int(p["macd_signal"])

        p = lib.extract_numeric_params("bollinger-bands")
        if "bb_period" in p:
            self.bb_period = int(p["bb_period"])
        if "bb_std" in p:
            self.bb_std = float(p["bb_std"])

        p = lib.extract_numeric_params("moving-average-crossover")
        if "sma_fast" in p:
            self.sma_fast = int(p["sma_fast"])
        if "sma_slow" in p:
            self.sma_slow = int(p["sma_slow"])

        logger.info(
            "Skill params → SMA(%d/%d) RSI(%g/%g) MACD(%d,%d,%d) BB(%d/%.1f)",
            self.sma_fast, self.sma_slow,
            self.rsi_oversold, self.rsi_overbought,
            self.macd_fast, self.macd_slow, self.macd_signal,
            self.bb_period, self.bb_std,
        )

    def _collect_active_rules(self, lib: SkillsLibrary) -> list[str]:
        """Collect all key rules from relevant skills for traceability."""
        rules: list[str] = []
        for factor, skill_name in self.SKILL_MAP.items():
            skill = lib.get(skill_name)
            if skill:
                for rule in skill.key_rules:
                    rules.append(f"[{skill_name}] {rule}")
        return rules

    def get_params(self) -> dict:
        return {
            "buy_threshold": self.buy_threshold,
            "sell_threshold": self.sell_threshold,
            "sma_fast": self.sma_fast,
            "sma_slow": self.sma_slow,
            "rsi_period": self.rsi_period,
            "rsi_oversold": self.rsi_oversold,
            "rsi_overbought": self.rsi_overbought,
            "macd_fast": self.macd_fast,
            "macd_slow": self.macd_slow,
            "macd_signal": self.macd_signal,
            "bb_period": self.bb_period,
            "bb_std": self.bb_std,
            "adx_trend_threshold": self.adx_trend_threshold,
            "active_skill_rules": len(self._active_rules),
            "enabled": self.enabled,
        }

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def _score_buy(self, df: pd.DataFrame, adx_value: float | None) -> tuple[float, list[str]]:
        """
        Calculate a BUY score (0-100) with skill-rule adjustments.
        Returns (score, list_of_reasons).
        """
        close = df["close"]
        score = 0.0
        reasons: list[str] = []

        # Skill rule (moving-average-crossover):
        #   "NEVER use MA crossovers in ranging markets; check ADX >25 first"
        is_trending = (adx_value is None) or (adx_value >= self.adx_trend_threshold)
        sma_multiplier = 1.0 if is_trending else 0.5
        if not is_trending:
            reasons.append(
                f"[skill] ADX {adx_value:.1f} < {self.adx_trend_threshold} → ranging, SMA ÷2"
            )

        # --- Factor 1: SMA alignment (0-25 pts) ---
        fast_sma = Indicators.sma(close, self.sma_fast)
        slow_sma = Indicators.sma(close, self.sma_slow)
        if pd.notna(fast_sma.iloc[-1]) and pd.notna(slow_sma.iloc[-1]):
            sma_score = 0.0
            if fast_sma.iloc[-1] > slow_sma.iloc[-1]:
                sma_score += 15
                reasons.append("SMA fast > slow (bullish)")
            if (pd.notna(fast_sma.iloc[-2]) and pd.notna(slow_sma.iloc[-2])
                    and fast_sma.iloc[-2] <= slow_sma.iloc[-2]
                    and fast_sma.iloc[-1] > slow_sma.iloc[-1]):
                sma_score += 10
                reasons.append("SMA golden cross")
            score += sma_score * sma_multiplier

        # --- Factor 2: RSI (0-25 pts) ---
        # Skill rule (rsi-divergence): "RSI must be in extreme zone (<30 or >70)"
        # → full points only in extreme zone; partial credit otherwise
        rsi = Indicators.rsi(close, self.rsi_period)
        if pd.notna(rsi.iloc[-1]):
            curr_rsi = float(rsi.iloc[-1])
            if curr_rsi < self.rsi_oversold:
                score += 20
                reasons.append(f"RSI oversold ({curr_rsi:.1f})")
            elif curr_rsi < 45:
                score += 5  # not extreme zone → partial credit
                reasons.append(f"RSI low zone ({curr_rsi:.1f})")
            # Bounce bonus: RSI crossing up from oversold
            if (pd.notna(rsi.iloc[-2])
                    and float(rsi.iloc[-2]) < self.rsi_oversold
                    and curr_rsi >= self.rsi_oversold):
                score += 5
                reasons.append("RSI bouncing from oversold")

        # --- Factor 3: MACD (0-25 pts) ---
        # Skill rule (macd-trading): "bullish cross below zero is weaker" → score ×0.7
        macd_line, signal_line, histogram = Indicators.macd(
            close, self.macd_fast, self.macd_slow, self.macd_signal
        )
        if pd.notna(macd_line.iloc[-1]) and pd.notna(signal_line.iloc[-1]):
            macd_score = 0.0
            if macd_line.iloc[-1] > signal_line.iloc[-1]:
                macd_score += 15
                reasons.append("MACD > signal (bullish)")
            if pd.notna(histogram.iloc[-2]) and histogram.iloc[-1] > histogram.iloc[-2]:
                macd_score += 10
                reasons.append("MACD histogram rising")
            if float(macd_line.iloc[-1]) < 0:
                macd_score *= 0.7
                reasons.append("[skill] MACD below zero → score ×0.7")
            score += macd_score

        # --- Factor 4: Bollinger Bands (0-15 pts) ---
        # Skill rule (bollinger-bands): "NEVER trade mean reversion when BB width expanding"
        upper, middle, lower = Indicators.bollinger_bands(close, self.bb_period, self.bb_std)
        if pd.notna(lower.iloc[-1]) and pd.notna(upper.iloc[-1]):
            current_price = float(close.iloc[-1])
            bb_range = float(upper.iloc[-1] - lower.iloc[-1])
            mid_val = float(middle.iloc[-1])
            bb_width_pct = (bb_range / mid_val * 100) if mid_val > 0 else 0.0
            prev_range = (
                float(upper.iloc[-2] - lower.iloc[-2])
                if pd.notna(upper.iloc[-2]) and pd.notna(lower.iloc[-2])
                else bb_range
            )
            bb_expanding = bb_range > prev_range * 1.1

            if bb_range > 0:
                bb_pos = (current_price - float(lower.iloc[-1])) / bb_range
                if bb_pos < 0.2:
                    if bb_expanding:
                        score += 5
                        reasons.append(f"Near lower BB ({bb_pos:.0%}) [skill: expanding → ÷3]")
                    else:
                        score += 15
                        reasons.append(f"Near lower BB ({bb_pos:.0%})")
                elif bb_pos < 0.4:
                    score += 5
                    reasons.append(f"Lower BB half ({bb_pos:.0%})")

            # Squeeze bonus (BB width < 2% = breakout imminent)
            if bb_width_pct < 2.0:
                score += 5
                reasons.append(f"BB squeeze ({bb_width_pct:.1f}%)")

        # --- Factor 5: Volume confirmation (0-10 pts) ---
        if "volume" in df.columns:
            vol = df["volume"]
            vol_ma = vol.rolling(window=self.volume_ma_period).mean()
            if pd.notna(vol_ma.iloc[-1]) and float(vol_ma.iloc[-1]) > 0:
                vol_ratio = float(vol.iloc[-1]) / float(vol_ma.iloc[-1])
                if vol_ratio > 1.5:
                    score += 10
                    reasons.append(f"High volume ({vol_ratio:.1f}x avg)")
                elif vol_ratio > 1.0:
                    score += 5
                    reasons.append(f"Above-avg volume ({vol_ratio:.1f}x)")

        return score, reasons

    def _score_sell(self, df: pd.DataFrame, adx_value: float | None) -> tuple[float, list[str]]:
        """Calculate a SELL score (0-100) with skill-rule adjustments."""
        close = df["close"]
        score = 0.0
        reasons: list[str] = []

        # Skill rule (moving-average-crossover): ADX check
        is_trending = (adx_value is None) or (adx_value >= self.adx_trend_threshold)
        sma_multiplier = 1.0 if is_trending else 0.5
        if not is_trending:
            reasons.append(
                f"[skill] ADX {adx_value:.1f} < {self.adx_trend_threshold} → ranging, SMA ÷2"
            )

        # --- Factor 1: SMA death cross ---
        fast_sma = Indicators.sma(close, self.sma_fast)
        slow_sma = Indicators.sma(close, self.sma_slow)
        if pd.notna(fast_sma.iloc[-1]) and pd.notna(slow_sma.iloc[-1]):
            sma_score = 0.0
            if fast_sma.iloc[-1] < slow_sma.iloc[-1]:
                sma_score += 15
                reasons.append("SMA fast < slow (bearish)")
            if (pd.notna(fast_sma.iloc[-2]) and pd.notna(slow_sma.iloc[-2])
                    and fast_sma.iloc[-2] >= slow_sma.iloc[-2]
                    and fast_sma.iloc[-1] < slow_sma.iloc[-1]):
                sma_score += 10
                reasons.append("SMA death cross")
            score += sma_score * sma_multiplier

        # --- Factor 2: RSI overbought ---
        rsi = Indicators.rsi(close, self.rsi_period)
        if pd.notna(rsi.iloc[-1]):
            curr_rsi = float(rsi.iloc[-1])
            if curr_rsi > self.rsi_overbought:
                score += 20
                reasons.append(f"RSI overbought ({curr_rsi:.1f})")
            elif curr_rsi > 55:
                score += 5
                reasons.append(f"RSI high zone ({curr_rsi:.1f})")
            if (pd.notna(rsi.iloc[-2])
                    and float(rsi.iloc[-2]) > self.rsi_overbought
                    and curr_rsi <= self.rsi_overbought):
                score += 5
                reasons.append("RSI dropping from overbought")

        # --- Factor 3: MACD bearish ---
        # Skill rule (macd-trading): "bearish cross above zero is weaker" → score ×0.7
        macd_line, signal_line, histogram = Indicators.macd(
            close, self.macd_fast, self.macd_slow, self.macd_signal
        )
        if pd.notna(macd_line.iloc[-1]) and pd.notna(signal_line.iloc[-1]):
            macd_score = 0.0
            if macd_line.iloc[-1] < signal_line.iloc[-1]:
                macd_score += 15
                reasons.append("MACD < signal (bearish)")
            if pd.notna(histogram.iloc[-2]) and histogram.iloc[-1] < histogram.iloc[-2]:
                macd_score += 10
                reasons.append("MACD histogram falling")
            if float(macd_line.iloc[-1]) > 0:
                macd_score *= 0.7
                reasons.append("[skill] MACD above zero → score ×0.7")
            score += macd_score

        # --- Factor 4: Bollinger near upper band ---
        upper, middle, lower = Indicators.bollinger_bands(close, self.bb_period, self.bb_std)
        if pd.notna(lower.iloc[-1]) and pd.notna(upper.iloc[-1]):
            bb_range = float(upper.iloc[-1] - lower.iloc[-1])
            prev_range = (
                float(upper.iloc[-2] - lower.iloc[-2])
                if pd.notna(upper.iloc[-2]) and pd.notna(lower.iloc[-2])
                else bb_range
            )
            bb_expanding = bb_range > prev_range * 1.1
            if bb_range > 0:
                bb_pos = (float(close.iloc[-1]) - float(lower.iloc[-1])) / bb_range
                if bb_pos > 0.8:
                    if bb_expanding:
                        score += 5
                        reasons.append(f"Near upper BB ({bb_pos:.0%}) [skill: expanding → ÷3]")
                    else:
                        score += 15
                        reasons.append(f"Near upper BB ({bb_pos:.0%})")
                elif bb_pos > 0.6:
                    score += 5
                    reasons.append(f"Upper BB half ({bb_pos:.0%})")

        # --- Factor 5: Volume on down move ---
        if "volume" in df.columns:
            vol = df["volume"]
            vol_ma = vol.rolling(window=self.volume_ma_period).mean()
            if pd.notna(vol_ma.iloc[-1]) and float(vol_ma.iloc[-1]) > 0:
                vol_ratio = float(vol.iloc[-1]) / float(vol_ma.iloc[-1])
                price_change = (
                    (float(close.iloc[-1]) - float(close.iloc[-2])) / float(close.iloc[-2])
                    if float(close.iloc[-2]) != 0 else 0.0
                )
                if vol_ratio > 1.5 and price_change < 0:
                    score += 10
                    reasons.append(f"High vol on down move ({vol_ratio:.1f}x)")
                elif vol_ratio > 1.0 and price_change <= 0:
                    score += 5
                    reasons.append(f"Above-avg vol on down ({vol_ratio:.1f}x)")

        return score, reasons

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def generate_signals(self, df: pd.DataFrame, symbol: str) -> list[Signal]:
        min_req = max(
            self.sma_slow,
            self.bb_period,
            self.macd_slow + self.macd_signal,
            self._ADX_PERIOD * 2,
        ) + 2
        if len(df) < min_req:
            return []

        current_price = float(df["close"].iloc[-1])

        # Pre-compute ADX once for both buy and sell scoring
        adx_value: float | None = None
        if "high" in df.columns and "low" in df.columns:
            adx = Indicators.adx(df["high"], df["low"], df["close"], self._ADX_PERIOD)
            if pd.notna(adx.iloc[-1]):
                adx_value = float(adx.iloc[-1])

        buy_score, buy_reasons = self._score_buy(df, adx_value)
        sell_score, sell_reasons = self._score_sell(df, adx_value)
        signals: list[Signal] = []

        if buy_score >= self.buy_threshold and buy_score > sell_score:
            confidence = min(buy_score / 100.0, 1.0)
            signals.append(Signal(
                signal_type=SignalType.BUY,
                symbol=symbol,
                price=current_price,
                strategy_name=self.name,
                reason=f"BUY {buy_score:.0f}/100: {'; '.join(buy_reasons)}",
                confidence=confidence,
                metadata={
                    "buy_score": buy_score,
                    "sell_score": sell_score,
                    "adx": adx_value,
                    "skill_rules_active": len(self._active_rules),
                },
            ))
            logger.info("[%s] Embient BUY score=%.0f %s", symbol, buy_score, buy_reasons)

        elif sell_score >= self.sell_threshold and sell_score > buy_score:
            confidence = min(sell_score / 100.0, 1.0)
            signals.append(Signal(
                signal_type=SignalType.SELL,
                symbol=symbol,
                price=current_price,
                strategy_name=self.name,
                reason=f"SELL {sell_score:.0f}/100: {'; '.join(sell_reasons)}",
                confidence=confidence,
                metadata={
                    "buy_score": buy_score,
                    "sell_score": sell_score,
                    "adx": adx_value,
                    "skill_rules_active": len(self._active_rules),
                },
            ))
            logger.info("[%s] Embient SELL score=%.0f %s", symbol, sell_score, sell_reasons)

        return signals
