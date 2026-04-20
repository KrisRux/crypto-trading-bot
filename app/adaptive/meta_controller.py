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

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import pandas as pd
from sqlalchemy.orm import Session

from app.adaptive.market_regime_service import MarketRegimeService
from app.adaptive.performance_monitor import PerformanceMonitor
from app.adaptive.profile_manager import ProfileManager
from app.adaptive.notification_service import NotificationService
from app.adaptive.approval_service import ApprovalService
from app.adaptive.llm_advisor import LLMAdvisor
from app.adaptive.news_sentiment import NewsSentimentService
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
        self.news_sentiment = NewsSentimentService()

        self._last_global_regime: str | None = None
        self._daily_summary_sent: str | None = None
        # Avoid spamming: track alert state + silence period
        self._drawdown_alerted: bool = False
        self._consec_losses_alerted: bool = False
        self._api_errors_alerted: bool = False
        self._alert_silenced_until: dict[str, datetime] = {}  # alert_key → silence_until

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

            # 1. Regime snapshots — reuse if already computed by engine in this cycle
            existing_snaps = self.regime_service.snapshots
            if dataframes and not existing_snaps:
                # First cycle or snapshots not yet populated — compute here
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

            # 2. Performance metrics — pass kill switch reset cutoff so that
            # consecutive_losses restarts fresh after a pause expires.
            guardrails = getattr(self._engine, "guardrails", None)
            consec_cutoff = (
                guardrails.kill_switch.last_deactivated_at if guardrails else None
            )
            perf_snap = self.perf_monitor.compute(db, consec_reset_cutoff=consec_cutoff)
            perf_dict = perf_snap.to_dict()

            # HIGH notifications — once per episode + 30min silence to avoid oscillation spam
            now = datetime.now(timezone.utc)

            if perf_snap.drawdown_intraday >= 1.5:
                if not self._drawdown_alerted and now >= self._alert_silenced_until.get("dd", now):
                    await self.notifier.notify_drawdown_breach(
                        perf_snap.drawdown_intraday, 1.5, chat_ids=chat_ids,
                    )
                    self._drawdown_alerted = True
                    self._alert_silenced_until["dd"] = now + timedelta(minutes=30)
            else:
                self._drawdown_alerted = False

            if perf_snap.consecutive_losses >= 3:
                if not self._consec_losses_alerted and now >= self._alert_silenced_until.get("cl", now):
                    await self.notifier.notify_consecutive_losses(
                        perf_snap.consecutive_losses, chat_ids=chat_ids,
                    )
                    self._consec_losses_alerted = True
                    self._alert_silenced_until["cl"] = now + timedelta(minutes=30)
            else:
                self._consec_losses_alerted = False

            if perf_snap.api_error_count >= 5:
                if not self._api_errors_alerted and now >= self._alert_silenced_until.get("api", now):
                    await self.notifier.notify_api_errors(
                        perf_snap.api_error_count, chat_ids=chat_ids,
                    )
                    self._api_errors_alerted = True
                    self._alert_silenced_until["api"] = now + timedelta(minutes=30)
            else:
                self._api_errors_alerted = False

            # 3. Emergency escalation (bypasses cooldown/hysteresis)
            # Triggers when the bot is clearly losing money and not already in
            # the most conservative profile. Prevents staying in a bad profile
            # for the full 90-min cooldown while perdite si accumulano.
            active = self.profile_manager.active_profile
            if (active != "defensive"
                and perf_snap.total_recent_trades >= 5
                and perf_snap.win_rate_last_10 < 25
                and perf_snap.consecutive_losses >= 3):
                reason = (
                    f"emergency: WR={perf_snap.win_rate_last_10:.0f}% "
                    f"CL={perf_snap.consecutive_losses} "
                    f"DD={perf_snap.drawdown_intraday:.1f}%"
                )
                logger.warning("META_CTRL: EMERGENCY escalation → defensive (%s)", reason)
                applied = self.profile_manager.apply_profile(
                    "defensive", self._engine, reason,
                )
                if applied:
                    await self.notifier.notify_profile_switch(
                        active, "defensive", reason, perf_dict, chat_ids=chat_ids,
                    )

            # 4. Evaluate standard profile switch
            switch = self.profile_manager.evaluate_switch(perf_dict, global_regime)
            if switch:
                await self._handle_switch(db, switch, perf_dict, chat_ids)

            # Check for previously approved requests that can now be applied
            await self._apply_approved_requests(db, chat_ids)

            # 5. News sentiment (refresh every 30 min)
            if self.news_sentiment.needs_refresh(interval_minutes=30):
                try:
                    await self.news_sentiment.fetch_and_score()
                except Exception:
                    logger.debug("News sentiment fetch failed (non-critical)")

            # 6. LLM Advisor (read-only diagnostic + optional auto-tuning)
            self.advisor.analyze(
                regime_snapshot, perf_dict,
                self.profile_manager.active_profile,
                self.profile_manager.switch_history,
            )
            await self._apply_llm_tuning(perf_dict, regime_snapshot, chat_ids)

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

    async def _apply_llm_tuning(self, perf_dict: dict, regime_snapshot: dict,
                                chat_ids: list[str]):
        """
        Ask the LLM advisor for guardrail tuning suggestions and apply them
        if auto-tuning is enabled and confidence is high enough.
        """
        from app.config import settings
        if not settings.enable_llm_tuning:
            return
        guardrails = getattr(self._engine, "guardrails", None)
        if guardrails is None:
            return
        try:
            tuning = await self.advisor.generate_tuning_suggestions(
                perf=perf_dict,
                guardrails_status=guardrails.status(),
                guardrails_config=guardrails._cfg,
                regime_snapshot=regime_snapshot,
                news_sentiment=self.news_sentiment.snapshot.to_dict(),
            )
        except Exception:
            logger.exception("LLM tuning: generate failed")
            return

        if not tuning:
            return
        confidence = float(tuning.get("confidence", 0))
        risk_level = tuning.get("risk_level", "medium")
        changes = tuning.get("changes", [])
        if (not changes or confidence < settings.llm_tuning_min_confidence
                or risk_level == "high"):
            return

        applied = 0
        for change in changes:
            if guardrails.apply_tuning_change(change):
                applied += 1
        if applied:
            logger.info(
                "LLM_TUNING: applied %d/%d changes | confidence=%.0f%% | source=%s",
                applied, len(changes), confidence * 100, tuning.get("source", "?"),
            )
            try:
                await self.notifier.notify_tuning_applied(
                    applied, changes, confidence, chat_ids=chat_ids,
                )
            except AttributeError:
                # Notification method not implemented — silent fallback
                pass
            except Exception:
                logger.exception("LLM tuning: notification failed")

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

    # ------------------------------------------------------------------
    # Telegram callback polling (inline approval buttons)
    # ------------------------------------------------------------------

    async def start_callback_polling(self):
        """Background loop that polls Telegram for inline button callbacks."""
        # Flush old updates at startup so we don't process stale callbacks
        try:
            await self._flush_old_updates()
        except Exception:
            logger.exception("Failed to flush old Telegram updates")
        logger.info("Telegram callback polling started")
        while True:
            try:
                callbacks = await self.notifier.poll_callbacks()
                for cb in callbacks:
                    await self._process_callback(cb)
            except asyncio.CancelledError:
                logger.info("Telegram callback polling stopped")
                return
            except Exception:
                logger.exception("Callback polling error")
            await asyncio.sleep(10)

    async def _flush_old_updates(self):
        """Consume all pending Telegram updates so polling starts fresh."""
        import httpx
        if not self.notifier.enabled:
            return
        url = self.notifier.TELEGRAM_API.format(
            token=self.notifier._bot_token, method="getUpdates",
        )
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json={"offset": -1, "timeout": 0})
                if resp.status_code == 200:
                    data = resp.json()
                    results = data.get("result", [])
                    if results:
                        last_id = results[-1]["update_id"]
                        self.notifier._callback_offset = last_id + 1
                        logger.info(
                            "Flushed %d old Telegram updates, offset set to %d",
                            len(results), self.notifier._callback_offset,
                        )
                    else:
                        logger.info("No old Telegram updates to flush")
        except Exception:
            logger.exception("Failed to flush Telegram updates")

    async def _process_callback(self, cb: dict):
        """Process a single Telegram callback query (approve/reject)."""
        from app.database import SessionLocal

        action = cb["action"]
        request_id = cb["id"]
        from_user = cb["from_user"]
        callback_query_id = cb["callback_query_id"]
        chat_id = cb["chat_id"]

        db = SessionLocal()
        try:
            if action == "approve":
                req = self.approval_service.approve(db, request_id, resolved_by=f"telegram:{from_user}")
            else:
                req = self.approval_service.reject(db, request_id, resolved_by=f"telegram:{from_user}")

            if req is None:
                await self.notifier.answer_callback(callback_query_id, f"Request #{request_id} not found.")
                return

            if req.status in ("approved", "rejected"):
                status_text = f"Request #{request_id} {req.status.upper()} by {from_user}"
                await self.notifier.answer_callback(callback_query_id, status_text)
                # Send confirmation message to chat
                await self.notifier.send(
                    f"\u2705 <b>Request #{request_id} {req.status.upper()}</b>\n"
                    f"<b>By:</b> {from_user}\n"
                    f"<b>Profile:</b> <code>{req.from_profile}</code> \u2192 <code>{req.to_profile}</code>",
                    level="INFO", chat_id=chat_id, deduplicate=False,
                )
                logger.info("Telegram approval callback: #%d %s by %s", request_id, req.status, from_user)
            elif req.status == "expired":
                await self.notifier.answer_callback(callback_query_id, f"Request #{request_id} has expired.")
            else:
                await self.notifier.answer_callback(
                    callback_query_id,
                    f"Request #{request_id} is already {req.status}.",
                )
        finally:
            db.close()
