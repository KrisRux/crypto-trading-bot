"""
SQLAlchemy models for trades and orders.
"""

from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Float, DateTime, Enum as SAEnum
from app.database import Base
import enum


class OrderSide(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, enum.Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderStatus(str, enum.Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class TradeStatus(str, enum.Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=True, index=True)
    symbol = Column(String, nullable=False, index=True)
    side = Column(SAEnum(OrderSide), nullable=False)
    order_type = Column(SAEnum(OrderType), nullable=False)
    quantity = Column(Float, nullable=False)
    price = Column(Float, nullable=True)  # None for market orders
    filled_price = Column(Float, nullable=True)
    status = Column(SAEnum(OrderStatus), default=OrderStatus.PENDING)
    exchange_order_id = Column(String, nullable=True)  # Binance order ID
    mode = Column(String, default="paper")  # "live" or "paper"
    error_message = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))


class Trade(Base):
    """
    A trade represents a full cycle: entry order -> exit order.
    Tracks PnL and status.
    """
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=True, index=True)
    symbol = Column(String, nullable=False, index=True)
    side = Column(SAEnum(OrderSide), nullable=False)  # Direction of entry
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=True)
    quantity = Column(Float, nullable=False)
    stop_loss = Column(Float, nullable=True)
    take_profit = Column(Float, nullable=True)
    pnl = Column(Float, nullable=True)  # Profit/loss in USDT
    pnl_pct = Column(Float, nullable=True)  # Profit/loss percentage
    status = Column(SAEnum(TradeStatus), default=TradeStatus.OPEN)
    mode = Column(String, default="paper")
    strategy = Column(String, nullable=True)  # Which strategy opened this
    entry_order_id = Column(Integer, nullable=True)
    exit_order_id = Column(Integer, nullable=True)
    opened_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    closed_at = Column(DateTime, nullable=True)
