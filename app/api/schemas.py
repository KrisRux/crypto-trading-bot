"""Pydantic schemas for API request/response models."""

from pydantic import BaseModel
from datetime import datetime


# -- Mode --
class ModeResponse(BaseModel):
    mode: str  # "live" or "paper"


class ModeSwitch(BaseModel):
    mode: str


# -- Dashboard --
class BalanceResponse(BaseModel):
    mode: str
    cash_balance: float
    total_equity: float
    total_pnl: float
    total_trades: int
    winning_trades: int
    losing_trades: int


class PositionResponse(BaseModel):
    id: int
    symbol: str
    side: str
    quantity: float
    entry_price: float
    current_price: float | None
    unrealized_pnl: float
    stop_loss: float | None
    take_profit: float | None
    opened_at: datetime | None


class OrderResponse(BaseModel):
    id: int
    symbol: str
    side: str
    order_type: str
    quantity: float
    price: float | None
    filled_price: float | None
    status: str
    mode: str
    error_message: str | None
    created_at: datetime | None


class TradeResponse(BaseModel):
    id: int
    symbol: str
    side: str
    entry_price: float
    exit_price: float | None
    quantity: float
    stop_loss: float | None
    take_profit: float | None
    pnl: float | None
    pnl_pct: float | None
    status: str
    mode: str
    strategy: str | None
    opened_at: datetime | None
    closed_at: datetime | None


# -- Strategies --
class StrategyInfo(BaseModel):
    name: str
    enabled: bool
    params: dict


class StrategyUpdate(BaseModel):
    name: str
    enabled: bool | None = None
    params: dict | None = None


# -- Risk --
class RiskParams(BaseModel):
    max_position_pct: float
    default_sl_pct: float
    default_tp_pct: float


# -- Signals --
class SignalResponse(BaseModel):
    time: str
    type: str
    symbol: str
    price: float
    strategy: str
    reason: str


# -- Price --
class PriceResponse(BaseModel):
    symbol: str
    price: float
