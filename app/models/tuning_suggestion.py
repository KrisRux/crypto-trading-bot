"""
TuningSuggestion model — stores AI advisor guardrails tuning suggestions.

Lifecycle: new → applied | rejected | expired
"""

from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, Float, Text, DateTime, Boolean

from app.database import Base


class TuningSuggestion(Base):
    __tablename__ = "tuning_suggestions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, default=lambda: datetime.utcnow())
    status = Column(String, default="new")  # new, applied, rejected, expired
    # Snapshot at creation time
    global_regime = Column(String, nullable=True)
    active_profile = Column(String, nullable=True)
    consecutive_losses = Column(Integer, default=0)
    win_rate = Column(Float, default=0)
    drawdown = Column(Float, default=0)
    trades_per_hour = Column(Float, default=0)
    total_blocked = Column(Integer, default=0)
    total_passed = Column(Integer, default=0)
    # Suggestion
    changes_json = Column(Text, nullable=True)   # JSON: [{"path":"...", "from":X, "to":Y}, ...]
    reasoning = Column(Text, nullable=True)
    confidence = Column(Float, default=0)
    risk_level = Column(String, default="low")   # low, medium, high
    # Resolution
    resolved_at = Column(DateTime, nullable=True)
    resolved_by = Column(String, nullable=True)
