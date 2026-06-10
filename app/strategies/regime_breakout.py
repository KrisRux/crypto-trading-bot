"""
Regime Breakout — regime-gated Donchian channel trend following (long-only).

Rationale (from the 2026 profitability review)
----------------------------------------------
The live system lost money because it traded a high-frequency, low-edge
signal whose average captured move (~0.16%) was below the round-trip cost
(~0.24%), and because it bought bounces inside downtrends. This strategy is
the opposite trade-off, built around four transparent, individually testable
rules:

1. **Regime gate (direction-aware)** — entries only when the market is in a
   bull regime: close above a slow EMA *and* that EMA is rising. In a bear or
   flat regime the strategy stays out entirely (spot cannot short — capital
   preservation IS the bear-market position).
2. **Entry = Donchian breakout** — BUY when the close breaks above the highest
   high of the previous ``entry_channel`` bars. Breakouts to fresh highs are
   the classic time-series-momentum entry: rare, objective and unambiguous.
3. **Exit = channel break, regime break, or hard stop** — SELL when the close
   falls below the lowest low of the previous ``exit_channel`` bars or when
   the regime turns bear. No take-profit: winners are left to run (the
   engine/backtester ATR stop remains the hard floor under every position).
4. **Cost filter** — entries require ATR%% within ``[min_atr_pct, max_atr_pct]``:
   enough volatility that an average trend leg dwarfs the ~0.24%% round-trip
   cost, but not panic volatility.

Designed for SLOW timeframes (recommended: 4h candles). It needs
``regime_ema_period + slope_lookback`` closed bars of history; on shorter
windows it simply emits nothing (safe default in the 15m live loop until the
engine supports per-strategy timeframes).
"""

import pandas as pd

from app.strategies.base import Strategy, Signal, SignalType
from app.strategies.indicators import Indicators


class RegimeBreakoutStrategy(Strategy):
    name = "regime_breakout"
    enabled = True

    # The engine feeds this strategy a dedicated closed-candle 4h frame via
    # TimeframeFeed and invokes it at most once per closed 4h bar.
    interval: str | None = "4h"

    # Take-profit multiple requested from the engine's _entry_plan: wide on
    # purpose (12 x ATR ~ +25-30%) — winners exit on the channel/regime break,
    # not on a TP cap. The backtest validation ran with the TP disabled.
    tp_atr_mult: float = 12.0

    def __init__(self,
                 regime_ema_period: int = 200,
                 slope_lookback: int = 10,
                 entry_channel: int = 55,
                 exit_channel: int = 20,
                 atr_period: int = 14,
                 min_atr_pct: float = 0.5,
                 max_atr_pct: float = 6.0):
        self.regime_ema_period = regime_ema_period
        self.slope_lookback = slope_lookback
        self.entry_channel = entry_channel
        self.exit_channel = exit_channel
        self.atr_period = atr_period
        self.min_atr_pct = min_atr_pct
        self.max_atr_pct = max_atr_pct
        self.min_history_bars = self._min_bars() + 10

    def get_params(self) -> dict:
        return {
            "regime_ema_period": self.regime_ema_period,
            "slope_lookback": self.slope_lookback,
            "entry_channel": self.entry_channel,
            "exit_channel": self.exit_channel,
            "atr_period": self.atr_period,
            "min_atr_pct": self.min_atr_pct,
            "max_atr_pct": self.max_atr_pct,
            "enabled": self.enabled,
        }

    # ------------------------------------------------------------------

    def _min_bars(self) -> int:
        # +1 everywhere: exits are edge-triggered, so we also evaluate the
        # regime/channel state of the PREVIOUS bar.
        return max(self.regime_ema_period + self.slope_lookback + 1,
                   self.entry_channel + 3,
                   self.exit_channel + 3,
                   self.atr_period + 3)

    def _bull_regime(self, close_px: float, ema: pd.Series, at: int) -> bool:
        """Bull regime at offset ``at`` (-1 = last bar, -2 = previous)."""
        ema_now = float(ema.iloc[at])
        ema_then = float(ema.iloc[at - self.slope_lookback])
        return close_px > ema_now and ema_now >= ema_then

    def generate_signals(self, df: pd.DataFrame, symbol: str,
                         precomputed_adx: float | None = None) -> list[Signal]:
        if len(df) < self._min_bars():
            return []

        close = df["close"]
        high = df["high"]
        low = df["low"]
        price = float(close.iloc[-1])

        prev_price = float(close.iloc[-2])

        ema = Indicators.ema(close, self.regime_ema_period)
        bull_regime = self._bull_regime(price, ema, -1)
        prev_bull = self._bull_regime(prev_price, ema, -2)

        # Channels exclude the current bar (shift(1)): the breakout must clear
        # the PRIOR N-bar extreme, otherwise every new high trivially "breaks"
        # a channel that already contains it.
        prior_high = float(
            high.rolling(self.entry_channel).max().shift(1).iloc[-1]
        )
        exit_lows = low.rolling(self.exit_channel).min().shift(1)
        prior_low = float(exit_lows.iloc[-1])
        prior_low_prev = float(exit_lows.iloc[-2])

        atr = Indicators.atr(high, low, close, self.atr_period)
        atr_now = float(atr.iloc[-1]) if pd.notna(atr.iloc[-1]) else 0.0
        atr_pct = (atr_now / price * 100.0) if price > 0 else 0.0

        # ---- EXIT first (edge-triggered: only on the bar where the break or
        # the regime flip actually happens, not on every bear bar) ----------
        exit_cross = price < prior_low and prev_price >= prior_low_prev
        regime_flip = prev_bull and not bull_regime
        if exit_cross or regime_flip:
            reason = "exit channel break" if exit_cross else "regime turned bear/flat"
            return [Signal(
                signal_type=SignalType.SELL,
                symbol=symbol,
                price=price,
                strategy_name=self.name,
                reason=f"Regime breakout exit: {reason}",
                confidence=0.9,
                # sell_score kept at 0 on purpose: this SELL means "close the
                # long / stand aside", it is NOT a short-entry conviction call
                # (the paper-short gate requires a high sell_score, so this
                # can never accidentally open a synthetic short).
                metadata={"exit_only": True, "sell_score": 0.0,
                          "atr_pct": round(atr_pct, 4)},
            )]

        # Never enter while below the exit channel or outside the bull regime.
        if not bull_regime or price < prior_low:
            return []

        # ---- ENTRY: bull regime + fresh breakout + sane volatility --------
        if price > prior_high and self.min_atr_pct <= atr_pct <= self.max_atr_pct:
            # Compatibility score for the live guardrails' DynamicScoreFilter:
            # base 80 for a valid breakout, up to +15 with trend strength.
            adx_val = precomputed_adx
            if adx_val is None and len(df) >= self.atr_period * 2 + 2:
                adx_series = Indicators.adx(high, low, close, self.atr_period)
                adx_val = (float(adx_series.iloc[-1])
                           if pd.notna(adx_series.iloc[-1]) else None)
            score = 80.0 + min(15.0, max(0.0, (adx_val or 20.0) - 20.0))
            return [Signal(
                signal_type=SignalType.BUY,
                symbol=symbol,
                price=price,
                strategy_name=self.name,
                reason=(f"Donchian {self.entry_channel}-bar breakout in bull "
                        f"regime (ATR {atr_pct:.2f}%)"),
                confidence=0.9,
                metadata={"buy_score": round(score, 1),
                          "atr_pct": round(atr_pct, 4),
                          "tp_atr_mult": self.tp_atr_mult,
                          "adx": adx_val},
            )]

        return []
