"""
Approval Service — human-in-the-loop approval for profile switches.

Manages approval requests stored in the DB. Requests have a lifecycle:
  pending → approved | rejected | expired

Approval can be given via the REST API or (future) Telegram callback.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.approval import ApprovalRequest

logger = logging.getLogger(__name__)

# Default expiration for pending requests
DEFAULT_EXPIRY_MINUTES = 120


class ApprovalService:
    """Create, query, and resolve approval requests."""

    def __init__(self, expiry_minutes: int = DEFAULT_EXPIRY_MINUTES):
        self._expiry_minutes = expiry_minutes

    def create_request(
        self, db: Session,
        from_profile: str, to_profile: str,
        reason: str, metrics_snapshot: dict,
    ) -> ApprovalRequest:
        """Create a new pending approval request."""
        # Check for existing pending request for the same transition
        existing = (
            db.query(ApprovalRequest)
            .filter(
                ApprovalRequest.status == "pending",
                ApprovalRequest.to_profile == to_profile,
            )
            .first()
        )
        if existing:
            logger.info("Approval already pending for → %s (id=%d)", to_profile, existing.id)
            return existing

        now = datetime.utcnow()
        req = ApprovalRequest(
            request_type="profile_switch",
            from_profile=from_profile,
            to_profile=to_profile,
            reason=reason,
            metrics_snapshot=str(metrics_snapshot),
            status="pending",
            created_at=now,
            expires_at=now + timedelta(minutes=self._expiry_minutes),
        )
        db.add(req)
        db.commit()
        db.refresh(req)
        logger.info(
            "Approval request created: #%d %s → %s (reason: %s, expires: %s)",
            req.id, from_profile, to_profile, reason, req.expires_at,
        )
        return req

    def approve(self, db: Session, request_id: int, resolved_by: str = "admin") -> ApprovalRequest | None:
        """Approve a pending request. Returns the request or None if not found/expired."""
        req = db.query(ApprovalRequest).filter(ApprovalRequest.id == request_id).first()
        if not req:
            return None
        if req.status != "pending":
            logger.warning("Approval #%d is already %s", request_id, req.status)
            return req

        self._expire_if_needed(req)
        if req.status == "expired":
            db.commit()
            return req

        req.status = "approved"
        req.resolved_at = datetime.utcnow()
        req.resolved_by = resolved_by
        db.commit()
        logger.info("Approval #%d APPROVED by %s", request_id, resolved_by)
        return req

    def reject(self, db: Session, request_id: int, resolved_by: str = "admin") -> ApprovalRequest | None:
        """Reject a pending request."""
        req = db.query(ApprovalRequest).filter(ApprovalRequest.id == request_id).first()
        if not req:
            return None
        if req.status != "pending":
            return req

        req.status = "rejected"
        req.resolved_at = datetime.utcnow()
        req.resolved_by = resolved_by
        db.commit()
        logger.info("Approval #%d REJECTED by %s", request_id, resolved_by)
        return req

    def get_pending(self, db: Session) -> list[ApprovalRequest]:
        """Return all pending (non-expired) requests."""
        requests = (
            db.query(ApprovalRequest)
            .filter(ApprovalRequest.status == "pending")
            .order_by(ApprovalRequest.created_at.desc())
            .all()
        )
        # Expire stale ones
        for req in requests:
            self._expire_if_needed(req)
        db.commit()
        return [r for r in requests if r.status == "pending"]

    def get_approved_and_consume(self, db: Session, to_profile: str) -> ApprovalRequest | None:
        """Check if there is an approved request for a given target profile and consume it."""
        req = (
            db.query(ApprovalRequest)
            .filter(
                ApprovalRequest.status == "approved",
                ApprovalRequest.to_profile == to_profile,
            )
            .order_by(ApprovalRequest.resolved_at.desc())
            .first()
        )
        if req:
            req.status = "consumed"
            db.commit()
            logger.info("Approval #%d consumed for profile → %s", req.id, to_profile)
        return req

    def get_all(self, db: Session, limit: int = 50) -> list[ApprovalRequest]:
        """Return recent approval requests (all statuses)."""
        return (
            db.query(ApprovalRequest)
            .order_by(ApprovalRequest.created_at.desc())
            .limit(limit)
            .all()
        )

    def _expire_if_needed(self, req: ApprovalRequest):
        now = datetime.utcnow()
        if req.expires_at and now >= req.expires_at:
            req.status = "expired"
            req.resolved_at = now
            logger.info("Approval #%d expired", req.id)
