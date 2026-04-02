"""
Meta Controller — orchestrator for the adaptive layer.

Called after each trading cycle to:
  1. Compute regime snapshots for all symbols
  2. Compute performance metrics
  3. Evaluate profile switching rules
  4. Apply auto-switches or create approval requests
  5. Send Telegram notifications (per-user)
  6. Run LLM advisor (advisory only)

This is a layer on top of the trading engine — it never touches order execution
directly. All parameter changes go through ProfileManager.apply_profile().
"""

import logging
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy.orm import Session

from app.adaptive.market_regime_service import MarketRegimeService
from app.adaptive.performance_monitor import PerformanceMonitor
from app.adaptive.profile_manager import ProfileManager
from app.adaptive.notification_service import NotificationService
from app.adaptive.approval_service import ApprovalService
from app.adaptive.llm_advisor import LLMAdvisor
from app.models.user import User

logger = logging.getLogger(__name__)


class MetaController:
    """
    Orchestrates the adaptive layer after each engine cycle.

    Lifecycle:
      1. engine.run_cycle() completes
      2. engine calls meta_controller.evaluate(db, dataframes)
      3. meta_controller runs regime -> performance -> profile -> notify -> advise
    """

    def __init__(self, engine, bot_token: str = ""):
        self._engine = engine
        self.regime_service = MarketRegimeService()
        self.perf_monitor = PerformanceMonitor()
        self.profile_manager = ProfileManager()
        self.notifier = NotificationService(bot_token=bot_token)
        self.approval_service = ApprovalService()
        self.advisor = LLMAdvisor()

        self._last_global_regime: str | None = None
        self._daily_summary_sent: str | None = None

        logger.info(
            "MetaController initialized (profile=%s, telegram=%s)",
            self.profile_manager.active_profile,
            "enabled" if self.notifier.enabled else "disabled",
        )

    def _get_chat_ids(self, db: Session) -> list[str]:
        """Get Telegram chat_ids for all users with notifications enabled."""
        users = (
            db.query(User)
            .filter(User.telegram_enabled == True, User.telegram_chat_id != "")
            .all()
        )
        return [u.telegram_chat_id for u in users if u.telegram_chat_id]

    async def evaluate(self, db: Session, dataframes: dict[str, pd.DataFrame] | None = None):
        """
        Run the full adaptive evaluation cycle.

        Args:
            db: database session
            dataframes: {symbol: DataFrame} from the latest fetch_klines
        """
        try:
            # Collect per-user chat_ids for notifications
            chat_ids = self._get_chat_ids(db)

            # 1. Regime snapshots
            if dataframes:
                for symbol, df in dataframes.items():
                    if not df.empty:
                        self.regime_service.compute(df, symbol)

            regime_snapshot = self.regime_service.global_snapshot()
            global_regime = regime_snapshot.get("global_regime", "unknown")

            # Detect regime change
            if self._last_global_regime and global_regime != self._last_global_regime:
                logger.info(
                    "REGIME_CHANGE: %s -> %s", self._last_global_regime, global_regime,
                )
                await self.notifier.notify_regime_change(
                    self._last_global_regime, global_regime, chat_ids=chat_ids,
                )
            self._last_global_regime = global_regime

            # 2. Performance metrics
            perf_snap = self.perf_monitor.compute(db)
            perf_dict = perf_snap.to_dict()

            # HIGH notifications based on performance
            if perf_snap.consecutive_losses >= 3:
                await self.notifier.notify_consecutive_losses(
                    perf_snap.consecutive_losses, chat_ids=chat_ids,
                )

            if perf_snap.drawdown_intraday >= 1.5:
                await self.notifier.notify_drawdown_breach(
                    perf_snap.drawdown_intraday, 1.5, chat_ids=chat_ids,
                )

            if perf_snap.api_error_count >= 5:
                await self.notifier.notify_api_errors(
                    perf_snap.api_error_count, chat_ids=chat_ids,
                )

            # 3. Evaluate profile switch
            switch = self.profile_manager.evaluate_switch(perf_dict, global_regime)
            if switch:
                await self._handle_switch(db, switch, perf_dict, chat_ids)

            # Check for previously approved requests that can now be applied
            await self._apply_approved_requests(db, chat_ids)

            # 4. LLM Advisor (read-only)
            self.advisor.analyze(
                regime_snapshot, perf_dict,
                self.profile_manager.active_profile,
                self.profile_manager.switch_history,
            )

            # 5. Daily summary (once per day at ~00:00 UTC cycle)
            await self._maybe_send_daily_summary(perf_dict, chat_ids)

            logger.info(
                "META_CTRL: cycle complete | regime=%s | profile=%s | "
                "PnL6h=%.2f | WR=%.0f%% | DD=%.2f%%",
                global_regime, self.profile_manager.active_profile,
                perf_snap.pnl_6h, perf_snap.win_rate_last_10,
                perf_snap.drawdown_intraday,
            )

        except Exception:
            logger.exception("MetaController.evaluate failed")

    async def _handle_switch(self, db: Session, switch: dict,
                             perf_dict: dict, chat_ids: list[str]):
        """Handle a profile switch decision."""
        from_p = switch["from"]
        to_p = switch["to"]
        reason = switch["reason"]

        if switch["requires_approval"]:
            req = self.approval_service.create_request(
                db, from_p, to_p, reason, perf_dict,
            )
            await self.notifier.notify_approval_required(
                from_p, to_p, reason, req.id, chat_ids=chat_ids,
            )
            logger.info(
                "PROFILE: %s -> %s requires approval (request #%d)",
                from_p, to_p, req.id,
            )
        elif switch["auto_apply"]:
            applied = self.profile_manager.apply_profile(to_p, self._engine, reason)
            if applied:
                await self.notifier.notify_profile_switch(
                    from_p, to_p, reason, perf_dict, chat_ids=chat_ids,
                )
        else:
            logger.info("PROFILE: switch %s -> %s evaluated but not auto_apply", from_p, to_p)

    async def _apply_approved_requests(self, db: Session, chat_ids: list[str]):
        """Check for approved requests and apply them."""
        for profile_name in self.profile_manager.profiles:
            if profile_name == self.profile_manager.active_profile:
                continue
            approved = self.approval_service.get_approved_and_consume(db, profile_name)
            if approved:
                reason = f"approved by {approved.resolved_by} (request #{approved.id})"
                applied = self.profile_manager.apply_profile(
                    profile_name, self._engine, reason,
                )
                if applied:
                    perf_dict = self.perf_monitor.snapshot.to_dict() if self.perf_monitor.snapshot else {}
                    await self.notifier.notify_profile_switch(
                        approved.from_profile or "unknown",
                        profile_name, reason, perf_dict, chat_ids=chat_ids,
                    )

    async def _maybe_send_daily_summary(self, perf_dict: dict, chat_ids: list[str]):
        """Send daily summary once per day."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._daily_summary_sent == today:
            return
        now = datetime.now(timezone.utc)
        if now.hour == 0 and now.minute < 20:
            await self.notifier.notify_daily_summary(perf_dict, chat_ids=chat_ids)
            self._daily_summary_sent = today
