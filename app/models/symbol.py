"""
Persistent trading symbols configuration.
Symbols added/removed via the API are stored here so they survive restarts.
"""

from sqlalchemy import Column, String
from app.database import Base


class TradingSymbol(Base):
    __tablename__ = "trading_symbols"

    symbol = Column(String, primary_key=True, nullable=False)
