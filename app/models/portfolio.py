"""
SQLAlchemy models for paper trading portfolio.
"""

from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Float, DateTime
from app.database import Base


class PaperPortfolio(Base):
    """Virtual portfolio state for paper trading."""
    __tablename__ = "paper_portfolios"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, default="default", unique=True)
    initial_capital = Column(Float, nullable=False)
    cash_balance = Column(Float, nullable=False)  # Available USDT
    total_equity = Column(Float, nullable=False)  # Cash + value of positions
    total_pnl = Column(Float, default=0.0)
    total_trades = Column(Integer, default=0)
    winning_trades = Column(Integer, default=0)
    losing_trades = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))


class PaperPosition(Base):
    """Open position in the paper portfolio."""
    __tablename__ = "paper_positions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    portfolio_id = Column(Integer, nullable=False)
    symbol = Column(String, nullable=False, index=True)
    side = Column(String, nullable=False)  # BUY (long)
    quantity = Column(Float, nullable=False)
    entry_price = Column(Float, nullable=False)
    current_price = Column(Float, nullable=True)
    unrealized_pnl = Column(Float, default=0.0)
    stop_loss = Column(Float, nullable=True)
    take_profit = Column(Float, nullable=True)
    opened_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
