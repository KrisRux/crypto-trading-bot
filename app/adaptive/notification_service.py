"""
Notification Service — Telegram Bot API integration.

Sends structured HTML messages via Telegram HTTP API.
Supports severity levels (INFO, WARNING, CRITICAL), deduplication,
and rate limiting to prevent spam.

Config via env vars:
  TELEGRAM_BOT_TOKEN  — Bot token from @BotFather
  TELEGRAM_CHAT_ID    — Target chat/group ID
"""

import asyncio
import hashlib
import logging
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger(__name__)


class NotificationService:
    """Async Telegram notification sender with dedup and rate limiting."""

    TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

    # Rate limit: max messages per window
    MAX_MESSAGES_PER_MINUTE = 10
    DEDUP_WINDOW_SECONDS = 300  # 5 min dedup for similar messages

    def __init__(self, bot_token: str = "", chat_id: str = ""):
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._enabled = bool(bot_token and chat_id)
        # Rate limit state
        self._sent_timestamps: list[datetime] = []
        # Dedup: hash → last sent time
        self._dedup_cache: dict[str, datetime] = {}

        if self._enabled:
            logger.info("Telegram notifications enabled (chat_id=%s)", chat_id)
        else:
            logger.info("Telegram notifications disabled (missing token or chat_id)")

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def send(self, text: str, level: str = "INFO", deduplicate: bool = True) -> bool:
        """
        Send an HTML message to Telegram.

        Args:
            text: HTML-formatted message body.
            level: INFO | WARNING | CRITICAL — prefixed to message.
            deduplicate: skip if a similar message was sent recently.

        Returns True if sent, False if skipped or failed.
        """
        if not self._enabled:
            logger.debug("Telegram disabled, message skipped: %s", text[:80])
            return False

        # Dedup check
        if deduplicate:
            msg_hash = hashlib.md5(text.encode()).hexdigest()[:12]
            now = datetime.now(timezone.utc)
            last_sent = self._dedup_cache.get(msg_hash)
            if last_sent and (now - last_sent).total_seconds() < self.DEDUP_WINDOW_SECONDS:
                logger.debug("Telegram dedup: skipping duplicate message")
                return False
            self._dedup_cache[msg_hash] = now
            # Prune old dedup entries
            cutoff = now - timedelta(seconds=self.DEDUP_WINDOW_SECONDS * 2)
            self._dedup_cache = {
                k: v for k, v in self._dedup_cache.items() if v > cutoff
            }

        # Rate limit check
        now = datetime.now(timezone.utc)
        self._sent_timestamps = [
            t for t in self._sent_timestamps
            if (now - t).total_seconds() < 60
        ]
        if len(self._sent_timestamps) >= self.MAX_MESSAGES_PER_MINUTE:
            logger.warning("Telegram rate limit reached, message queued for later")
            return False

        # Build message
        level_emoji = {"INFO": "\u2139\ufe0f", "WARNING": "\u26a0\ufe0f", "CRITICAL": "\U0001F6A8"}.get(level, "")
        full_text = f"{level_emoji} <b>[{level}]</b>\n\n{text}"

        # Send
        url = self.TELEGRAM_API.format(token=self._bot_token)
        payload = {
            "chat_id": self._chat_id,
            "text": full_text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    self._sent_timestamps.append(now)
                    logger.info("Telegram sent [%s]: %s", level, text[:80])
                    return True
                else:
                    logger.warning("Telegram API error %d: %s", resp.status_code, resp.text[:200])
                    return False
        except Exception:
            logger.exception("Telegram send failed")
            return False

    # ------------------------------------------------------------------
    # Convenience methods for common notifications
    # ------------------------------------------------------------------

    async def notify_profile_switch(self, from_profile: str, to_profile: str,
                                    reason: str, metrics: dict):
        await self.send(
            f"<b>Profile Switch</b>\n"
            f"<code>{from_profile}</code> \u2192 <code>{to_profile}</code>\n\n"
            f"<b>Reason:</b> {reason}\n"
            f"<b>PnL 6h:</b> {metrics.get('pnl_6h', 'N/A')}\n"
            f"<b>Win Rate:</b> {metrics.get('win_rate_last_10', 'N/A')}%\n"
            f"<b>Drawdown:</b> {metrics.get('drawdown_intraday', 'N/A')}%",
            level="WARNING",
        )

    async def notify_approval_required(self, from_profile: str, to_profile: str,
                                       reason: str, request_id: int):
        await self.send(
            f"\U0001F510 <b>Approval Required</b>\n\n"
            f"Profile: <code>{from_profile}</code> \u2192 <code>{to_profile}</code>\n"
            f"<b>Reason:</b> {reason}\n"
            f"<b>Request ID:</b> #{request_id}\n\n"
            f"Approve via API: <code>POST /api/approvals/{request_id}/approve</code>",
            level="CRITICAL",
            deduplicate=False,
        )

    async def notify_drawdown_breach(self, drawdown_pct: float, threshold: float):
        await self.send(
            f"\U0001F6A8 <b>Drawdown Threshold Breached</b>\n\n"
            f"<b>Current:</b> {drawdown_pct:.2f}%\n"
            f"<b>Threshold:</b> {threshold:.2f}%\n\n"
            f"Switching to defensive profile.",
            level="CRITICAL",
        )

    async def notify_consecutive_losses(self, count: int):
        await self.send(
            f"\u26a0\ufe0f <b>{count} Consecutive Losses</b>\n\n"
            f"Bot is evaluating a profile switch to defensive mode.",
            level="WARNING",
        )

    async def notify_regime_change(self, old_regime: str, new_regime: str):
        await self.send(
            f"<b>Global Regime Change</b>\n"
            f"<code>{old_regime}</code> \u2192 <code>{new_regime}</code>",
            level="INFO",
        )

    async def notify_bot_paused(self, reason: str):
        await self.send(
            f"\U0001F6D1 <b>Bot Paused</b>\n\n<b>Reason:</b> {reason}",
            level="CRITICAL",
            deduplicate=False,
        )

    async def notify_daily_summary(self, metrics: dict):
        await self.send(
            f"\U0001F4CA <b>Daily Summary</b>\n\n"
            f"<b>PnL 24h:</b> {metrics.get('pnl_24h', 0):.2f} USDT\n"
            f"<b>Win Rate (last 10):</b> {metrics.get('win_rate_last_10', 0):.0f}%\n"
            f"<b>Trades:</b> {metrics.get('trades_per_hour', 0) * 24:.0f} (est. daily)\n"
            f"<b>Max Drawdown:</b> {metrics.get('drawdown_intraday', 0):.2f}%\n"
            f"<b>Cooldown hits:</b> {metrics.get('cooldown_hits', 0)}\n"
            f"<b>API errors:</b> {metrics.get('api_error_count', 0)}",
            level="INFO",
        )

    async def notify_api_errors(self, count: int):
        await self.send(
            f"\u26a0\ufe0f <b>Persistent API Errors</b>\n\n"
            f"<b>Count:</b> {count} errors detected.\n"
            f"Check exchange connectivity.",
            level="CRITICAL",
        )
