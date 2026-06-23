"""
Regime Breakout LONG/SHORT — research variant (NOT registered in the live engine).

This is the symmetric, stop-and-reverse version of ``regime_breakout`` used to
answer one question with the back-tester: **does adding a short side capture the
bear markets a spot long-only bot has to sit out?**

Logic (mirror of the validated long-only thesis):
* bull  = close > EMA200 AND EMA200 rising
* bear  = close < EMA200 AND EMA200 falling
* BUY   = bull AND close breaks above the prior ``entry_channel`` high
          → opens a long (or, in the back-tester, closes an open short)
* SELL  = bear AND close breaks below the prior ``entry_channel`` low
          → opens a short (or closes an open long)
* Exits between opposite breakouts are handled by the ATR hard stop; the
  take-profit is disabled so winners run.

Why a separate class (and why NOT in main.py):
* The live system is SPOT long-only — it cannot hold a real short. Shorting is
  only meaningful on a futures account, which is a deliberate, separate
  decision. This class exists purely to MEASURE the short edge before anyone
  commits to that. It is wired only into the back-tester registry.
* Signals here are pure long/short *intent* (no exit_only / sell_score=0
  guards): they must never reach the live spot engine.
"""

import pandas as pd

from app.strategies.base import Strategy, Signal, SignalType
from app.strategies.indicators import Indicators


class RegimeBreakoutLongShort(Strategy):
    name = "regime_breakout_ls"
    enabled = True
    interval: str | None = "4h"
    tp_atr_mult: float = 12.0

    def __init__(self,
                 regime_ema_period: int = 200,
                 slope_lookback: int = 10,
                 entry_channel: int = 55,
                 atr_period: int = 14,
                 min_atr_pct: float = 0.5,
                 max_atr_pct: float = 6.0):
        self.regime_ema_period = regime_ema_period
        self.slope_lookback = slope_lookback
        self.entry_channel = entry_channel
        self.atr_period = atr_period
        self.min_atr_pct = min_atr_pct
        self.max_atr_pct = max_atr_pct
        self.min_history_bars = self._min_bars() + 10

    def get_params(self) -> dict:
        return {
            "regime_ema_period": self.regime_ema_period,
            "slope_lookback": self.slope_lookback,
            "entry_channel": self.entry_channel,
            "atr_period": self.atr_period,
            "min_atr_pct": self.min_atr_pct,
            "max_atr_pct": self.max_atr_pct,
            "enabled": self.enabled,
        }

    def _min_bars(self) -> int:
        return max(self.regime_ema_period + self.slope_lookback + 1,
                   self.entry_channel + 3,
                   self.atr_period + 3)

    def generate_signals(self, df: pd.DataFrame, symbol: str,
                         precomputed_adx: float | None = None) -> list[Signal]:
        if len(df) < self._min_bars():
            return []

        close, high, low = df["close"], df["high"], df["low"]
        price = float(close.iloc[-1])

        ema = Indicators.ema(close, self.regime_ema_period)
        ema_now = float(ema.iloc[-1])
        ema_then = float(ema.iloc[-1 - self.slope_lookback])
        bull = price > ema_now and ema_now >= ema_then
        bear = price < ema_now and ema_now <= ema_then

        prior_high = float(high.rolling(self.entry_channel).max().shift(1).iloc[-1])
        prior_low = float(low.rolling(self.entry_channel).min().shift(1).iloc[-1])

        atr = Indicators.atr(high, low, close, self.atr_period)
        atr_now = float(atr.iloc[-1]) if pd.notna(atr.iloc[-1]) else 0.0
        atr_pct = (atr_now / price * 100.0) if price > 0 else 0.0
        cost_ok = self.min_atr_pct <= atr_pct <= self.max_atr_pct

        meta = {"atr_pct": round(atr_pct, 4), "tp_atr_mult": self.tp_atr_mult}

        # LONG breakout (bullish intent: open long / close short)
        if bull and price > prior_high and cost_ok:
            return [Signal(SignalType.BUY, symbol, price, self.name,
                           reason=f"LS long breakout (ATR {atr_pct:.2f}%)",
                           confidence=0.9, metadata={**meta, "buy_score": 85.0})]

        # SHORT breakdown (bearish intent: open short / close long)
        if bear and price < prior_low and cost_ok:
            return [Signal(SignalType.SELL, symbol, price, self.name,
                           reason=f"LS short breakdown (ATR {atr_pct:.2f}%)",
                           confidence=0.9, metadata={**meta, "sell_score": 85.0})]

        return []
