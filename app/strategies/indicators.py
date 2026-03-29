"""
Technical analysis indicators.

All functions accept a pandas Series (typically the 'close' column)
and return a Series of the same length.
"""

import numpy as np
import pandas as pd


class Indicators:
    """Collection of static methods for common technical indicators."""

    @staticmethod
    def sma(series: pd.Series, period: int) -> pd.Series:
        """Simple Moving Average."""
        return series.rolling(window=period, min_periods=period).mean()

    @staticmethod
    def ema(series: pd.Series, period: int) -> pd.Series:
        """Exponential Moving Average."""
        return series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def rsi(series: pd.Series, period: int = 14) -> pd.Series:
        """
        Relative Strength Index (0–100).
        Values below 30 are considered oversold; above 70 overbought.
        """
        delta = series.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
        avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        return rsi

    @staticmethod
    def macd(series: pd.Series, fast: int = 12, slow: int = 26,
             signal: int = 9) -> tuple[pd.Series, pd.Series, pd.Series]:
        """
        MACD indicator.
        Returns: (macd_line, signal_line, histogram)
        """
        ema_fast = series.ewm(span=fast, adjust=False).mean()
        ema_slow = series.ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram

    @staticmethod
    def bollinger_bands(series: pd.Series, period: int = 20,
                        std_dev: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
        """
        Bollinger Bands.
        Returns: (upper_band, middle_band, lower_band)
        """
        middle = series.rolling(window=period).mean()
        std = series.rolling(window=period).std()
        upper = middle + std_dev * std
        lower = middle - std_dev * std
        return upper, middle, lower

    @staticmethod
    def adx(high: pd.Series, low: pd.Series, close: pd.Series,
            period: int = 14) -> pd.Series:
        """
        Average Directional Index (0-100).
        Values above 25 indicate a trending market; below 25 = ranging.
        Uses Wilder's EWM smoothing (alpha = 1/period).
        """
        delta_high = high.diff()
        delta_low = -low.diff()

        plus_dm = delta_high.where((delta_high > delta_low) & (delta_high > 0), 0.0)
        minus_dm = delta_low.where((delta_low > delta_high) & (delta_low > 0), 0.0)

        hl = high - low
        hc = (high - close.shift()).abs()
        lc = (low - close.shift()).abs()
        tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)

        alpha = 1.0 / period
        eps = 1e-10
        atr_s = tr.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
        pdm_s = plus_dm.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
        mdm_s = minus_dm.ewm(alpha=alpha, min_periods=period, adjust=False).mean()

        plus_di = 100.0 * pdm_s / (atr_s + eps)
        minus_di = 100.0 * mdm_s / (atr_s + eps)
        di_sum = plus_di + minus_di
        dx = 100.0 * (plus_di - minus_di).abs() / (di_sum + eps)
        adx_series = dx.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
        return adx_series
