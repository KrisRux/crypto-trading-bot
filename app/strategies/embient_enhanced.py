"""
Embient-Enhanced Strategy — regime-aware, score-based.

Market regimes (derived from ADX):
  TREND   : ADX >= adx_trend_threshold (default 25) — trend-following logic
  RANGE   : ADX <  adx_range_threshold  (default 20) — mean-reversion logic
  NEUTRAL : ADX between the two thresholds            — no new entries

Each regime has its own buy/sell scoring function (0-100).
Entry fires only when score >= buy_threshold (default 65).
Exit fires when score >= sell_threshold (default 65) OR SL/TP is hit
by the trading engine.

Code structure
--------------
  _detect_regime()        → TREND / RANGE / NEUTRAL
  _compute_indicators()   → pre-compute all series once per cycle
  _score_buy_trend()      → 0-100  (trend-following long)
  _score_buy_range()      → 0-100  (mean-reversion long)
  _score_sell_trend()     → 0-100  (trend-following exit)
  _score_sell_range()     → 0-100  (mean-reversion exit)
  generate_signals()      → final BUY / SELL / nothing

Presets (apply via UI by editing buy_threshold / sell_threshold):
  Conservative : threshold 72
  Balanced     : threshold 65  ← default
  Aggressive   : threshold 55

Skills wired in (parameter loading only, not scoring logic):
  moving-average-crossover → SMA periods
  rsi-divergence           → RSI thresholds
  macd-trading             → MACD periods
  bollinger-bands          → BB period / std
"""

import logging
import pandas as pd

from app.strategies.base import Strategy, Signal, SignalType
from app.strategies.indicators import Indicators
from app.embient_skills.loader import SkillsLibrary

logger = logging.getLogger(__name__)

REGIME_TREND   = "trend"
REGIME_RANGE   = "range"
REGIME_NEUTRAL = "neutral"


class EmbientEnhancedStrategy(Strategy):
    name = "embient_enhanced"
    enabled = True

    _ADX_PERIOD = 14

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
        # Score thresholds (0-100) — see presets in module docstring
        buy_threshold: float = 65.0,
        sell_threshold: float = 65.0,
        # SMA
        sma_fast: int = 9,
        sma_slow: int = 21,
        # RSI
        rsi_period: int = 14,
        rsi_oversold: float = 30.0,
        rsi_overbought: float = 70.0,
        # MACD
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        # Bollinger Bands
        bb_period: int = 20,
        bb_std: float = 2.0,
        # Volume
        volume_ma_period: int = 20,
        # ADX regime boundaries
        adx_trend_threshold: float = 25.0,   # >= this → TREND mode
        adx_range_threshold: float = 20.0,   # <  this → RANGE mode
    ):
        self.buy_threshold       = buy_threshold
        self.sell_threshold      = sell_threshold
        self.sma_fast            = sma_fast
        self.sma_slow            = sma_slow
        self.rsi_period          = rsi_period
        self.rsi_oversold        = rsi_oversold
        self.rsi_overbought      = rsi_overbought
        self.macd_fast           = macd_fast
        self.macd_slow           = macd_slow
        self.macd_signal         = macd_signal
        self.bb_period           = bb_period
        self.bb_std              = bb_std
        self.volume_ma_period    = volume_ma_period
        self.adx_trend_threshold = adx_trend_threshold
        self.adx_range_threshold = adx_range_threshold
        self._active_rules: list[str] = []

        if skills_library is not None:
            self._load_from_skills(skills_library)
            self._active_rules = self._collect_active_rules(skills_library)
            logger.info(
                "EmbientEnhancedStrategy: params loaded from skills, %d active rules",
                len(self._active_rules),
            )

    # ------------------------------------------------------------------
    # Skills integration — parameter loading only
    # ------------------------------------------------------------------

    def _load_from_skills(self, lib: SkillsLibrary) -> None:
        p = lib.extract_numeric_params("rsi-divergence")
        if "rsi_oversold"   in p: self.rsi_oversold   = float(p["rsi_oversold"])
        if "rsi_overbought" in p: self.rsi_overbought  = float(p["rsi_overbought"])

        p = lib.extract_numeric_params("macd-trading")
        if "macd_fast"   in p: self.macd_fast   = int(p["macd_fast"])
        if "macd_slow"   in p: self.macd_slow   = int(p["macd_slow"])
        if "macd_signal" in p: self.macd_signal = int(p["macd_signal"])

        p = lib.extract_numeric_params("bollinger-bands")
        if "bb_period" in p: self.bb_period = int(p["bb_period"])
        if "bb_std"    in p: self.bb_std    = float(p["bb_std"])

        p = lib.extract_numeric_params("moving-average-crossover")
        if "sma_fast" in p: self.sma_fast = int(p["sma_fast"])
        if "sma_slow" in p: self.sma_slow = int(p["sma_slow"])

        logger.info(
            "Skill params → SMA(%d/%d) RSI(%g/%g) MACD(%d,%d,%d) BB(%d/%.1f)",
            self.sma_fast, self.sma_slow,
            self.rsi_oversold, self.rsi_overbought,
            self.macd_fast, self.macd_slow, self.macd_signal,
            self.bb_period, self.bb_std,
        )

    def _collect_active_rules(self, lib: SkillsLibrary) -> list[str]:
        rules: list[str] = []
        for factor, skill_name in self.SKILL_MAP.items():
            skill = lib.get(skill_name)
            if skill:
                for rule in skill.key_rules:
                    rules.append(f"[{skill_name}] {rule}")
        return rules

    def get_params(self) -> dict:
        return {
            "buy_threshold":       self.buy_threshold,
            "sell_threshold":      self.sell_threshold,
            "sma_fast":            self.sma_fast,
            "sma_slow":            self.sma_slow,
            "rsi_period":          self.rsi_period,
            "rsi_oversold":        self.rsi_oversold,
            "rsi_overbought":      self.rsi_overbought,
            "macd_fast":           self.macd_fast,
            "macd_slow":           self.macd_slow,
            "macd_signal":         self.macd_signal,
            "bb_period":           self.bb_period,
            "bb_std":              self.bb_std,
            "adx_trend_threshold": self.adx_trend_threshold,
            "adx_range_threshold": self.adx_range_threshold,
            "enabled":             self.enabled,
        }

    # ------------------------------------------------------------------
    # Market regime detection
    # ------------------------------------------------------------------

    def _detect_regime(self, adx_value: float | None) -> str:
        """
        Classify market state from ADX value.

        TREND   : ADX >= adx_trend_threshold  → trend-following signals
        RANGE   : ADX <  adx_range_threshold  → mean-reversion signals
        NEUTRAL : between the two thresholds  → no new entries (ambiguous)
        """
        if adx_value is None:
            return REGIME_NEUTRAL
        if adx_value >= self.adx_trend_threshold:
            return REGIME_TREND
        if adx_value < self.adx_range_threshold:
            return REGIME_RANGE
        return REGIME_NEUTRAL

    # ------------------------------------------------------------------
    # Indicator pre-computation (one call per cycle)
    # ------------------------------------------------------------------

    def _compute_indicators(self, df: pd.DataFrame) -> dict:
        close = df["close"]
        ind: dict = {}

        ind["fast_sma"]   = Indicators.sma(close, self.sma_fast)
        ind["slow_sma"]   = Indicators.sma(close, self.sma_slow)
        ind["rsi"]        = Indicators.rsi(close, self.rsi_period)

        ind["macd_line"], ind["signal_line"], ind["histogram"] = Indicators.macd(
            close, self.macd_fast, self.macd_slow, self.macd_signal
        )

        ind["bb_upper"], ind["bb_middle"], ind["bb_lower"] = Indicators.bollinger_bands(
            close, self.bb_period, self.bb_std
        )

        if "volume" in df.columns:
            ind["vol"]    = df["volume"]
            ind["vol_ma"] = df["volume"].rolling(window=self.volume_ma_period).mean()
        else:
            ind["vol"]    = None
            ind["vol_ma"] = None

        ind["current_price"] = float(close.iloc[-1])
        ind["prev_price"]    = float(close.iloc[-2]) if len(close) >= 2 else ind["current_price"]
        return ind

    # ------------------------------------------------------------------
    # Volume helper (shared across all scoring functions)
    # ------------------------------------------------------------------

    def _volume_score(self, ind: dict) -> tuple[float, list[str]]:
        """Returns (score 0-10, reasons). Max 10 pts."""
        vol    = ind["vol"]
        vol_ma = ind["vol_ma"]
        if vol is None or vol_ma is None:
            return 0.0, []
        if not (pd.notna(vol_ma.iloc[-1]) and float(vol_ma.iloc[-1]) > 0):
            return 0.0, []
        ratio = float(vol.iloc[-1]) / float(vol_ma.iloc[-1])
        if ratio > 1.5:
            return 10.0, [f"High volume ({ratio:.1f}x avg)"]
        if ratio > 1.0:
            return 5.0,  [f"Above-avg volume ({ratio:.1f}x avg)"]
        return 0.0, []

    # ------------------------------------------------------------------
    # BUY score — TREND mode  (max ~100)
    # ------------------------------------------------------------------
    # Weights: SMA 25 | MACD 25 | RSI momentum 15 | BB position 15
    #          ADX confirmation 10 | Volume 10

    def _score_buy_trend(self, ind: dict, adx: float) -> tuple[float, list[str]]:
        score   = 0.0
        reasons: list[str] = []

        # SMA (max 25) — fast > slow is baseline; golden cross is bonus
        fast = ind["fast_sma"]; slow = ind["slow_sma"]
        if pd.notna(fast.iloc[-1]) and pd.notna(slow.iloc[-1]):
            if fast.iloc[-1] > slow.iloc[-1]:
                score += 15; reasons.append("SMA fast > slow (bullish)")
                if (pd.notna(fast.iloc[-2]) and pd.notna(slow.iloc[-2])
                        and fast.iloc[-2] <= slow.iloc[-2]):
                    score += 10; reasons.append("SMA golden cross")
            else:
                # Death cross is a negative signal in trend mode
                reasons.append("SMA fast < slow (bearish) — no SMA pts")

        # MACD (max 25) — line above signal + histogram rising
        macd = ind["macd_line"]; sig = ind["signal_line"]; hist = ind["histogram"]
        if pd.notna(macd.iloc[-1]) and pd.notna(sig.iloc[-1]):
            if macd.iloc[-1] > sig.iloc[-1]:
                score += 15; reasons.append("MACD > signal (bullish)")
            if pd.notna(hist.iloc[-2]) and hist.iloc[-1] > hist.iloc[-2]:
                score += 10; reasons.append("MACD histogram rising")

        # RSI as momentum filter (max 15) — not as reversal
        rsi = ind["rsi"]
        if pd.notna(rsi.iloc[-1]):
            r = float(rsi.iloc[-1])
            if r > 55:
                score += 15; reasons.append(f"RSI strong momentum ({r:.1f})")
            elif r > 50:
                score += 8;  reasons.append(f"RSI above 50 ({r:.1f})")
            elif r < 40:
                score -= 5;  reasons.append(f"RSI weak in trend ({r:.1f}) -5")

        # Bollinger position (max 15) — price should be above middle in trend
        bu = ind["bb_upper"]; bm = ind["bb_middle"]; bl = ind["bb_lower"]
        if pd.notna(bm.iloc[-1]) and pd.notna(bl.iloc[-1]) and pd.notna(bu.iloc[-1]):
            bb_range = float(bu.iloc[-1] - bl.iloc[-1])
            if bb_range > 0:
                bb_pos = (ind["current_price"] - float(bl.iloc[-1])) / bb_range
                if bb_pos > 0.5:
                    score += 15; reasons.append(f"Price above BB middle ({bb_pos:.0%})")
                elif bb_pos > 0.35:
                    score += 7;  reasons.append(f"Price near BB middle ({bb_pos:.0%})")

        # ADX regime confirmation (max 10)
        score += 10; reasons.append(f"ADX trending ({adx:.1f})")

        # Volume (max 10)
        v_score, v_reasons = self._volume_score(ind)
        score += v_score; reasons.extend(v_reasons)

        return score, reasons

    # ------------------------------------------------------------------
    # BUY score — RANGE mode  (max ~100)
    # ------------------------------------------------------------------
    # Weights: RSI recovery 25 | BB lower band 25 | MACD not bearish 20
    #          SMA not death cross 10 | ADX confirmation 10 | Volume 10

    def _score_buy_range(self, ind: dict, adx: float) -> tuple[float, list[str]]:
        score   = 0.0
        reasons: list[str] = []

        # RSI oversold / recovery (max 25)
        rsi = ind["rsi"]
        if pd.notna(rsi.iloc[-1]):
            r      = float(rsi.iloc[-1])
            r_prev = float(rsi.iloc[-2]) if pd.notna(rsi.iloc[-2]) else r
            if r_prev < self.rsi_oversold and r >= self.rsi_oversold:
                score += 25; reasons.append(f"RSI recovery from oversold ({r:.1f})")
            elif r < self.rsi_oversold:
                score += 20; reasons.append(f"RSI oversold ({r:.1f})")
            elif r < 40:
                score += 10; reasons.append(f"RSI low zone ({r:.1f})")

        # Bollinger — near lower band (max 25)
        bu = ind["bb_upper"]; bm = ind["bb_middle"]; bl = ind["bb_lower"]
        if pd.notna(bl.iloc[-1]) and pd.notna(bu.iloc[-1]) and pd.notna(bm.iloc[-1]):
            bb_range = float(bu.iloc[-1] - bl.iloc[-1])
            mid_val  = float(bm.iloc[-1])
            if bb_range > 0:
                bb_pos       = (ind["current_price"] - float(bl.iloc[-1])) / bb_range
                bb_width_pct = (bb_range / mid_val * 100) if mid_val > 0 else 0.0
                if bb_pos < 0.15:
                    score += 20; reasons.append(f"Price at lower BB ({bb_pos:.0%})")
                elif bb_pos < 0.25:
                    score += 15; reasons.append(f"Price near lower BB ({bb_pos:.0%})")
                elif bb_pos < 0.40:
                    score += 7;  reasons.append(f"Price lower BB zone ({bb_pos:.0%})")
                # BB squeeze bonus (breakout imminent — max total 25)
                if bb_width_pct < 2.0 and score < 25:
                    score += 5; reasons.append(f"BB squeeze ({bb_width_pct:.1f}%)")

        # MACD — not bearish (max 20)
        macd = ind["macd_line"]; sig = ind["signal_line"]; hist = ind["histogram"]
        if pd.notna(macd.iloc[-1]) and pd.notna(sig.iloc[-1]):
            if macd.iloc[-1] > sig.iloc[-1]:
                score += 20; reasons.append("MACD bullish (range)")
            elif pd.notna(hist.iloc[-2]) and hist.iloc[-1] > hist.iloc[-2]:
                score += 10; reasons.append("MACD histogram recovering")
            elif hist.iloc[-1] < hist.iloc[-2]:
                score -= 5;  reasons.append("MACD falling (range) -5")

        # SMA — no death cross (max 10)
        fast = ind["fast_sma"]; slow = ind["slow_sma"]
        if pd.notna(fast.iloc[-1]) and pd.notna(slow.iloc[-1]):
            if fast.iloc[-1] >= slow.iloc[-1]:
                score += 10; reasons.append("SMA not bearish")
            elif (pd.notna(fast.iloc[-2]) and pd.notna(slow.iloc[-2])
                  and fast.iloc[-2] >= slow.iloc[-2]):
                score -= 5; reasons.append("Fresh SMA death cross -5")

        # ADX regime confirmation (max 10)
        score += 10; reasons.append(f"ADX ranging ({adx:.1f})")

        # Volume (max 10)
        v_score, v_reasons = self._volume_score(ind)
        score += v_score; reasons.extend(v_reasons)

        return score, reasons

    # ------------------------------------------------------------------
    # SELL score — TREND mode  (max ~100)
    # ------------------------------------------------------------------
    # Weights: SMA 25 | MACD 25 | RSI weakening 15 | BB below middle 15
    #          ADX 10 | Volume 10

    def _score_sell_trend(self, ind: dict, adx: float) -> tuple[float, list[str]]:
        score   = 0.0
        reasons: list[str] = []

        # SMA death cross (max 25)
        fast = ind["fast_sma"]; slow = ind["slow_sma"]
        if pd.notna(fast.iloc[-1]) and pd.notna(slow.iloc[-1]):
            if fast.iloc[-1] < slow.iloc[-1]:
                score += 15; reasons.append("SMA fast < slow (bearish)")
                if (pd.notna(fast.iloc[-2]) and pd.notna(slow.iloc[-2])
                        and fast.iloc[-2] >= slow.iloc[-2]):
                    score += 10; reasons.append("SMA death cross")

        # MACD bearish (max 25)
        macd = ind["macd_line"]; sig = ind["signal_line"]; hist = ind["histogram"]
        if pd.notna(macd.iloc[-1]) and pd.notna(sig.iloc[-1]):
            if macd.iloc[-1] < sig.iloc[-1]:
                score += 15; reasons.append("MACD < signal (bearish)")
            if pd.notna(hist.iloc[-2]) and hist.iloc[-1] < hist.iloc[-2]:
                score += 10; reasons.append("MACD histogram falling")

        # RSI weakening (max 15)
        rsi = ind["rsi"]
        if pd.notna(rsi.iloc[-1]):
            r = float(rsi.iloc[-1])
            if r < 45:
                score += 15; reasons.append(f"RSI weak ({r:.1f})")
            elif r < 50:
                score += 8;  reasons.append(f"RSI below 50 ({r:.1f})")

        # Bollinger — price below middle (max 15)
        bu = ind["bb_upper"]; bm = ind["bb_middle"]; bl = ind["bb_lower"]
        if pd.notna(bm.iloc[-1]) and pd.notna(bl.iloc[-1]) and pd.notna(bu.iloc[-1]):
            bb_range = float(bu.iloc[-1] - bl.iloc[-1])
            if bb_range > 0:
                bb_pos = (ind["current_price"] - float(bl.iloc[-1])) / bb_range
                if bb_pos < 0.5:
                    score += 15; reasons.append(f"Price below BB middle ({bb_pos:.0%})")
                elif bb_pos < 0.65:
                    score += 7;  reasons.append(f"Price near BB middle ({bb_pos:.0%})")

        # ADX regime confirmation (max 10)
        score += 10; reasons.append(f"ADX trending ({adx:.1f})")

        # Volume (max 10)
        v_score, v_reasons = self._volume_score(ind)
        score += v_score; reasons.extend(v_reasons)

        return score, reasons

    # ------------------------------------------------------------------
    # SELL score — RANGE mode  (max ~100)
    # ------------------------------------------------------------------
    # Weights: RSI overbought 25 | BB upper band 25 | MACD bearish 20
    #          SMA not golden cross 10 | ADX 10 | Volume 10

    def _score_sell_range(self, ind: dict, adx: float) -> tuple[float, list[str]]:
        score   = 0.0
        reasons: list[str] = []

        # RSI overbought / falling from overbought (max 25)
        rsi = ind["rsi"]
        if pd.notna(rsi.iloc[-1]):
            r      = float(rsi.iloc[-1])
            r_prev = float(rsi.iloc[-2]) if pd.notna(rsi.iloc[-2]) else r
            if r_prev > self.rsi_overbought and r <= self.rsi_overbought:
                score += 25; reasons.append(f"RSI dropping from overbought ({r:.1f})")
            elif r > self.rsi_overbought:
                score += 20; reasons.append(f"RSI overbought ({r:.1f})")
            elif r > 60:
                score += 10; reasons.append(f"RSI high zone ({r:.1f})")

        # Bollinger — near upper band (max 25)
        bu = ind["bb_upper"]; bm = ind["bb_middle"]; bl = ind["bb_lower"]
        if pd.notna(bu.iloc[-1]) and pd.notna(bl.iloc[-1]):
            bb_range = float(bu.iloc[-1] - bl.iloc[-1])
            if bb_range > 0:
                bb_pos = (ind["current_price"] - float(bl.iloc[-1])) / bb_range
                if bb_pos > 0.85:
                    score += 25; reasons.append(f"Price at upper BB ({bb_pos:.0%})")
                elif bb_pos > 0.75:
                    score += 18; reasons.append(f"Price near upper BB ({bb_pos:.0%})")
                elif bb_pos > 0.60:
                    score += 8;  reasons.append(f"Price upper BB zone ({bb_pos:.0%})")

        # MACD bearish (max 20)
        macd = ind["macd_line"]; sig = ind["signal_line"]; hist = ind["histogram"]
        if pd.notna(macd.iloc[-1]) and pd.notna(sig.iloc[-1]):
            if macd.iloc[-1] < sig.iloc[-1]:
                score += 20; reasons.append("MACD bearish (range)")
            elif pd.notna(hist.iloc[-2]) and hist.iloc[-1] < hist.iloc[-2]:
                score += 10; reasons.append("MACD histogram falling")

        # SMA — not golden cross (max 10)
        fast = ind["fast_sma"]; slow = ind["slow_sma"]
        if pd.notna(fast.iloc[-1]) and pd.notna(slow.iloc[-1]):
            if fast.iloc[-1] <= slow.iloc[-1]:
                score += 10; reasons.append("SMA not bullish")

        # ADX regime confirmation (max 10)
        score += 10; reasons.append(f"ADX ranging ({adx:.1f})")

        # Volume (max 10)
        v_score, v_reasons = self._volume_score(ind)
        score += v_score; reasons.extend(v_reasons)

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

        # --- ADX → regime detection ---
        adx_value: float | None = None
        if "high" in df.columns and "low" in df.columns:
            adx_series = Indicators.adx(df["high"], df["low"], df["close"], self._ADX_PERIOD)
            if pd.notna(adx_series.iloc[-1]):
                adx_value = float(adx_series.iloc[-1])

        regime = self._detect_regime(adx_value)

        if regime == REGIME_NEUTRAL:
            logger.debug(
                "[%s] Embient: ADX neutral (%.1f) — no new entries",
                symbol, adx_value or 0,
            )
            return []

        # --- Compute all indicators once ---
        ind = self._compute_indicators(df)
        current_price = ind["current_price"]

        # --- Score both directions ---
        if regime == REGIME_TREND:
            buy_score,  buy_reasons  = self._score_buy_trend(ind,  adx_value)
            sell_score, sell_reasons = self._score_sell_trend(ind, adx_value)
        else:  # REGIME_RANGE
            buy_score,  buy_reasons  = self._score_buy_range(ind,  adx_value)
            sell_score, sell_reasons = self._score_sell_range(ind, adx_value)

        # Clamp to 0-100
        buy_score  = max(0.0, min(100.0, buy_score))
        sell_score = max(0.0, min(100.0, sell_score))

        signals: list[Signal] = []

        if buy_score >= self.buy_threshold and buy_score > sell_score:
            reason = (
                f"BUY {buy_score:.0f}/100 [{regime}]: "
                + "; ".join(buy_reasons)
            )
            signals.append(Signal(
                signal_type=SignalType.BUY,
                symbol=symbol,
                price=current_price,
                strategy_name=self.name,
                reason=reason,
                confidence=min(buy_score / 100.0, 1.0),
                metadata={
                    "buy_score": buy_score, "sell_score": sell_score,
                    "regime": regime, "adx": adx_value,
                },
            ))
            logger.info("[%s] Embient %s", symbol, reason)

        elif sell_score >= self.sell_threshold and sell_score > buy_score:
            reason = (
                f"SELL {sell_score:.0f}/100 [{regime}]: "
                + "; ".join(sell_reasons)
            )
            signals.append(Signal(
                signal_type=SignalType.SELL,
                symbol=symbol,
                price=current_price,
                strategy_name=self.name,
                reason=reason,
                confidence=min(sell_score / 100.0, 1.0),
                metadata={
                    "buy_score": buy_score, "sell_score": sell_score,
                    "regime": regime, "adx": adx_value,
                },
            ))
            logger.info("[%s] Embient %s", symbol, reason)

        else:
            # Detailed skip log — useful for tuning thresholds
            if buy_score > sell_score:
                logger.debug(
                    "[%s] Embient BUY skipped: score %.0f < threshold %.0f [%s] — %s",
                    symbol, buy_score, self.buy_threshold, regime,
                    "; ".join(buy_reasons) or "no conditions met",
                )
            elif sell_score > 0:
                logger.debug(
                    "[%s] Embient SELL skipped: score %.0f < threshold %.0f [%s] — %s",
                    symbol, sell_score, self.sell_threshold, regime,
                    "; ".join(sell_reasons) or "no conditions met",
                )

        return signals
