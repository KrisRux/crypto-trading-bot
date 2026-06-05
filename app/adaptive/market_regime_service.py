"""
Market Regime Service — multi-indicator regime detection.

Computes regime per symbol and a global aggregate using:
  ADX  — trend strength
  ATR  — volatility (normalized as % of price)
  BB width — Bollinger Band squeeze / expansion
  Volume ratio — current vs rolling average

Regimes: trend | range | volatile | defensive
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd

from app.strategies.indicators import Indicators

logger = logging.getLogger(__name__)


@dataclass
class RegimeSnapshot:
    """Structured snapshot of a single symbol's market regime."""
    symbol: str
    regime: str          # trend | range | volatile | defensive
    adx: float
    atr_pct: float       # ATR as % of current price
    bb_width_pct: float  # BB width as % of middle band
    volume_ratio: float  # current volume / avg volume
    direction: str = "flat"  # up | down | flat — directional bias (orthogonal to regime)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "regime": self.regime,
            "adx": round(self.adx, 2),
            "atr_pct": round(self.atr_pct, 3),
            "bb_width_pct": round(self.bb_width_pct, 3),
            "volume_ratio": round(self.volume_ratio, 2),
            "direction": self.direction,
            "timestamp": self.timestamp,
        }


class MarketRegimeService:
    """Computes per-symbol regime snapshots and a global aggregate."""

    # Thresholds
    ADX_TREND = 25.0
    ADX_RANGE = 20.0
    ATR_VOLATILE_PCT = 3.0     # ATR > 3% of price → high volatility
    BB_SQUEEZE_PCT = 2.0       # BB width < 2% → tight range
    BB_EXPAND_PCT = 6.0        # BB width > 6% → expansion / volatile
    VOLUME_SPIKE = 2.0         # volume > 2x average → spike
    EMA_PERIOD = 50            # slow EMA period used for directional bias

    def __init__(self):
        self._snapshots: dict[str, RegimeSnapshot] = {}

    @property
    def snapshots(self) -> dict[str, RegimeSnapshot]:
        return dict(self._snapshots)

    def compute(self, df: pd.DataFrame, symbol: str) -> RegimeSnapshot:
        """Compute regime for a single symbol from its OHLCV DataFrame."""
        adx = self._calc_adx(df)
        atr_pct = self._calc_atr_pct(df)
        bb_width_pct = self._calc_bb_width_pct(df)
        volume_ratio = self._calc_volume_ratio(df)
        direction = self._calc_direction(df)

        regime = self._classify(adx, atr_pct, bb_width_pct, volume_ratio)

        snap = RegimeSnapshot(
            symbol=symbol,
            regime=regime,
            adx=adx,
            atr_pct=atr_pct,
            bb_width_pct=bb_width_pct,
            volume_ratio=volume_ratio,
            direction=direction,
        )
        self._snapshots[symbol] = snap
        logger.info(
            "REGIME_SERVICE: %s → %s [%s] (ADX=%.1f ATR%%=%.2f BB%%=%.2f Vol=%.1fx)",
            symbol, regime.upper(), direction, adx, atr_pct, bb_width_pct, volume_ratio,
        )
        return snap

    # Minimum number of symbols that must be defensive before overriding
    # the global regime to defensive (avoids a single illiquid symbol blocking all trading)
    DEFENSIVE_OVERRIDE_MIN = 2

    def global_regime(self) -> str:
        """Aggregate regime across all tracked symbols (majority vote)."""
        if not self._snapshots:
            return "unknown"
        regimes = [s.regime for s in self._snapshots.values()]
        counts: dict[str, int] = {}
        for r in regimes:
            counts[r] = counts.get(r, 0) + 1
        # defensive takes priority only if enough symbols are defensive
        defensive_count = counts.get("defensive", 0)
        if defensive_count >= self.DEFENSIVE_OVERRIDE_MIN:
            return "defensive"
        # If only 1 symbol is defensive and there's only 1 symbol total
        if defensive_count > 0 and len(regimes) <= 1:
            return "defensive"
        return max(counts, key=counts.get)

    def global_direction(self) -> str:
        """Aggregate directional bias across all tracked symbols.

        Returns "down" if >=50% of symbols are "down", "up" if >=50% are
        "up", otherwise "flat". (Bear bias is checked first so a 50/50
        down-vs-other split surfaces as defensive "down".)
        """
        if not self._snapshots:
            return "flat"
        directions = [s.direction for s in self._snapshots.values()]
        total = len(directions)
        down = sum(1 for d in directions if d == "down")
        up = sum(1 for d in directions if d == "up")
        if down / total >= 0.5:
            return "down"
        if up / total >= 0.5:
            return "up"
        return "flat"

    def is_bearish(self, symbol: str) -> bool:
        """True if the symbol's current snapshot direction is "down".

        Returns False for untracked symbols.
        """
        snap = self._snapshots.get(symbol)
        return bool(snap is not None and snap.direction == "down")

    def is_bullish(self, symbol: str) -> bool:
        """True if the symbol's current snapshot direction is "up".

        Returns False for untracked symbols.
        """
        snap = self._snapshots.get(symbol)
        return bool(snap is not None and snap.direction == "up")

    def global_snapshot(self) -> dict:
        """Return global regime + global direction + all per-symbol snapshots."""
        return {
            "global_regime": self.global_regime(),
            "global_direction": self.global_direction(),
            "symbols": {s: snap.to_dict() for s, snap in self._snapshots.items()},
        }

    # ------------------------------------------------------------------
    # Indicator calculations
    # ------------------------------------------------------------------

    def _calc_adx(self, df: pd.DataFrame) -> float:
        try:
            if len(df) < 28:
                return 0.0
            adx_series = Indicators.adx(df["high"], df["low"], df["close"], 14)
            val = adx_series.iloc[-1]
            return float(val) if pd.notna(val) else 0.0
        except Exception:
            return 0.0

    def _calc_atr_pct(self, df: pd.DataFrame) -> float:
        """ATR(14) as percentage of current price."""
        try:
            if len(df) < 15:
                return 0.0
            high, low, close = df["high"], df["low"], df["close"]
            tr = pd.concat([
                high - low,
                (high - close.shift(1)).abs(),
                (low - close.shift(1)).abs(),
            ], axis=1).max(axis=1)
            atr = tr.ewm(alpha=1/14, min_periods=14).mean().iloc[-1]
            price = close.iloc[-1]
            return (atr / price * 100) if price > 0 else 0.0
        except Exception:
            return 0.0

    def _calc_bb_width_pct(self, df: pd.DataFrame) -> float:
        """Bollinger Band width as % of middle band."""
        try:
            if len(df) < 20:
                return 0.0
            upper, middle, lower = Indicators.bollinger_bands(df["close"], 20, 2.0)
            mid = middle.iloc[-1]
            if mid <= 0 or pd.isna(mid):
                return 0.0
            width = (upper.iloc[-1] - lower.iloc[-1]) / mid * 100
            return float(width)
        except Exception:
            return 0.0

    def _calc_volume_ratio(self, df: pd.DataFrame) -> float:
        """Last completed candle volume / 20-period average.

        Uses iloc[-2] (last closed candle) instead of iloc[-1] (current
        in-progress candle) to avoid near-zero volume on incomplete bars.
        """
        try:
            if len(df) < 22 or "volume" not in df.columns:
                return 1.0
            current_vol = df["volume"].iloc[-2]   # last CLOSED candle
            avg_vol = df["volume"].iloc[-22:-2].mean()  # 20-bar average of closed candles
            if avg_vol <= 0:
                return 1.0
            return float(current_vol / avg_vol)
        except Exception:
            return 1.0

    def _calc_direction(self, df: pd.DataFrame) -> str:
        """Directional bias from price vs slow EMA and EMA slope.

        Uses ONLY closed candles: the last closed bar is iloc[-2] (the
        in-progress bar iloc[-1] is ignored, consistent with
        _calc_volume_ratio). Slope is EMA[-2] - EMA[-3] (closed bars only).

          up   — close > EMA and EMA rising  (slope > 0)
          down — close < EMA and EMA falling (slope < 0)
          flat — otherwise (no agreement between level and slope)

        Returns "flat" on insufficient data or any error.
        """
        try:
            close = df["close"]
            # Need EMA_PERIOD bars to seed the EMA + a closed bar at -2 and
            # the prior closed bar at -3 to measure slope.
            if len(close) < self.EMA_PERIOD + 2:
                return "flat"
            ema_fn = getattr(Indicators, "ema", None)
            if callable(ema_fn):
                ema = ema_fn(close, self.EMA_PERIOD)
            else:
                ema = close.ewm(span=self.EMA_PERIOD, adjust=False).mean()

            price = close.iloc[-2]          # last CLOSED candle
            ema_now = ema.iloc[-2]          # EMA at last closed candle
            ema_prev = ema.iloc[-3]         # EMA at prior closed candle
            if pd.isna(price) or pd.isna(ema_now) or pd.isna(ema_prev):
                return "flat"

            slope = ema_now - ema_prev
            if price > ema_now and slope > 0:
                return "up"
            if price < ema_now and slope < 0:
                return "down"
            return "flat"
        except Exception:
            return "flat"

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    def _classify(self, adx: float, atr_pct: float,
                  bb_width_pct: float, volume_ratio: float) -> str:
        """
        Classify market regime from indicator values.

        Priority order:
          1. defensive — extreme volatility or volume anomaly
          2. volatile  — high ATR or wide BB
          3. trend     — ADX strong
          4. range     — default
        """
        # Defensive: extreme conditions
        if atr_pct >= self.ATR_VOLATILE_PCT * 1.5 or volume_ratio >= self.VOLUME_SPIKE * 1.5:
            return "defensive"

        # Volatile: high ATR or BB expansion with volume spike
        if atr_pct >= self.ATR_VOLATILE_PCT or (
            bb_width_pct >= self.BB_EXPAND_PCT and volume_ratio >= self.VOLUME_SPIKE
        ):
            return "volatile"

        # Trend: strong ADX
        if adx >= self.ADX_TREND:
            return "trend"

        # Range: low ADX and/or tight BB
        return "range"
