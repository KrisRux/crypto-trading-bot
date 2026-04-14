"""
Abstract base class for trading strategies.

Every strategy must implement `generate_signals()` which receives a DataFrame
of OHLCV data and returns a list of Signal objects. This design allows new
strategies to be added without modifying the trading engine.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum

import pandas as pd


class SignalType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class Signal:
    signal_type: SignalType
    symbol: str
    price: float
    strategy_name: str
    reason: str = ""
    confidence: float = 0.0  # 0.0 – 1.0
    metadata: dict = field(default_factory=dict)


class Strategy(ABC):
    """
    Interface that every strategy must implement.
    """

    name: str = "base"
    enabled: bool = True

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame, symbol: str,
                         precomputed_adx: float | None = None) -> list[Signal]:
        """
        Analyse the OHLCV DataFrame and return trading signals.

        Expected DataFrame columns: open, high, low, close, volume
        (indexed by datetime).

        Args:
            precomputed_adx: if provided, use this ADX value instead of
                             recomputing internally (avoids redundant computation).
        """
        ...

    def get_params(self) -> dict:
        """Return current parameters for the UI."""
        return {}

    def set_params(self, params: dict):
        """Update parameters from the UI."""
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)
