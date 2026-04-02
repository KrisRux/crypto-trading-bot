"""
ApprovalRequest model — tracks human-in-the-loop approval for profile switches.
"""

from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, Text, DateTime

from app.database import Base


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    id = Column(Integer, primary_key=True, index=True)
    request_type = Column(String, nullable=False, default="profile_switch")
    from_profile = Column(String, nullable=True)
    to_profile = Column(String, nullable=True)
    reason = Column(String, nullable=True)
    metrics_snapshot = Column(Text, nullable=True)
    status = Column(String, nullable=False, default="pending")  # pending | approved | rejected | expired | consumed
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    resolved_at = Column(DateTime, nullable=True)
    resolved_by = Column(String, nullable=True)
    expires_at = Column(DateTime, nullable=True)
