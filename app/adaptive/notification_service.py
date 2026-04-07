"""
Notification Service — Telegram Bot API integration.

Sends structured HTML messages via Telegram HTTP API.
Supports severity levels (INFO, WARNING, CRITICAL), deduplication,
and rate limiting to prevent spam.

Bot token is server-wide (from env). Chat ID is per-user (from DB).
The service can broadcast to all users with Telegram enabled, or send
to a specific chat_id.

Config:
  TELEGRAM_BOT_TOKEN  — Bot token from @BotFather (server-wide, in .env)
  telegram_chat_id    — Per-user, stored in DB (User model)
"""

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger(__name__)


class NotificationService:
    """Async Telegram notification sender with dedup and rate limiting."""

    TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"

    # Rate limit: max messages per window
    MAX_MESSAGES_PER_MINUTE = 10
    DEDUP_WINDOW_SECONDS = 300  # 5 min dedup for similar messages

    def __init__(self, bot_token: str = ""):
        self._bot_token = bot_token
        self._enabled = bool(bot_token)
        # Rate limit state
        self._sent_timestamps: list[datetime] = []
        # Dedup: hash -> last sent time
        self._dedup_cache: dict[str, datetime] = {}
        # Telegram callback polling offset
        self._callback_offset: int = 0

        if self._enabled:
            logger.info("Telegram notifications enabled (bot token configured)")
        else:
            logger.info("Telegram notifications disabled (missing bot token)")

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def send(self, text: str, level: str = "INFO",
                   chat_id: str = "", deduplicate: bool = True) -> bool:
        """
        Send an HTML message to a specific Telegram chat.

        Args:
            text: HTML-formatted message body.
            level: INFO | WARNING | CRITICAL.
            chat_id: target chat ID. If empty, message is skipped.
            deduplicate: skip if a similar message was sent recently.

        Returns True if sent, False if skipped or failed.
        """
        if not self._enabled or not chat_id:
            return False

        # Dedup check (per chat_id)
        if deduplicate:
            dedup_key = f"{chat_id}:{hashlib.md5(text.encode()).hexdigest()[:12]}"
            now = datetime.now(timezone.utc)
            last_sent = self._dedup_cache.get(dedup_key)
            if last_sent and (now - last_sent).total_seconds() < self.DEDUP_WINDOW_SECONDS:
                return False
            self._dedup_cache[dedup_key] = now
            # Prune old entries
            cutoff = now - timedelta(seconds=self.DEDUP_WINDOW_SECONDS * 2)
            self._dedup_cache = {k: v for k, v in self._dedup_cache.items() if v > cutoff}

        # Rate limit check
        now = datetime.now(timezone.utc)
        self._sent_timestamps = [
            t for t in self._sent_timestamps if (now - t).total_seconds() < 60
        ]
        if len(self._sent_timestamps) >= self.MAX_MESSAGES_PER_MINUTE:
            logger.warning("Telegram rate limit reached, skipping")
            return False

        # Build message
        level_emoji = {
            "INFO": "\u2139\ufe0f",
            "WARNING": "\u26a0\ufe0f",
            "CRITICAL": "\U0001F6A8",
        }.get(level, "")
        full_text = f"{level_emoji} <b>[{level}]</b>\n\n{text}"

        url = self.TELEGRAM_API.format(token=self._bot_token, method="sendMessage")
        payload = {
            "chat_id": chat_id,
            "text": full_text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    self._sent_timestamps.append(now)
                    logger.info("Telegram sent [%s] to %s: %s", level, chat_id, text[:80])
                    return True
                else:
                    logger.warning("Telegram API error %d: %s", resp.status_code, resp.text[:200])
                    return False
        except Exception:
            logger.exception("Telegram send failed")
            return False

    async def broadcast(self, text: str, level: str = "INFO",
                        chat_ids: list[str] | None = None,
                        deduplicate: bool = True) -> int:
        """
        Send to multiple chat_ids. Returns count of successful sends.
        """
        if not chat_ids:
            return 0
        sent = 0
        for cid in chat_ids:
            if await self.send(text, level=level, chat_id=cid, deduplicate=deduplicate):
                sent += 1
        return sent

    # ------------------------------------------------------------------
    # Convenience methods (accept chat_ids list for broadcast)
    # ------------------------------------------------------------------

    async def notify_profile_switch(self, from_profile: str, to_profile: str,
                                    reason: str, metrics: dict, chat_ids: list[str] = None):
        await self.broadcast(
            f"<b>Profile Switch</b>\n"
            f"<code>{from_profile}</code> \u2192 <code>{to_profile}</code>\n\n"
            f"<b>Reason:</b> {reason}\n"
            f"<b>PnL 6h:</b> {metrics.get('pnl_6h', 'N/A')}\n"
            f"<b>Win Rate:</b> {metrics.get('win_rate_last_10', 'N/A')}%\n"
            f"<b>Drawdown:</b> {metrics.get('drawdown_intraday', 'N/A')}%",
            level="WARNING", chat_ids=chat_ids or [],
        )

    async def notify_approval_required(self, from_profile: str, to_profile: str,
                                       reason: str, request_id: int, chat_ids: list[str] = None):
        """Send approval request with inline Approve/Reject buttons."""
        text = (
            f"\U0001F510 <b>Approval Required</b>\n\n"
            f"Profile: <code>{from_profile}</code> \u2192 <code>{to_profile}</code>\n"
            f"<b>Reason:</b> {reason}\n"
            f"<b>Request ID:</b> #{request_id}\n\n"
            f"Use the buttons below or API:\n"
            f"<code>POST /api/approvals/{request_id}/approve</code>"
        )
        inline_keyboard = {
            "inline_keyboard": [[
                {"text": "\u2705 Approve", "callback_data": json.dumps({"action": "approve", "id": request_id})},
                {"text": "\u274c Reject", "callback_data": json.dumps({"action": "reject", "id": request_id})},
            ]]
        }
        for cid in (chat_ids or []):
            await self._send_with_keyboard(text, inline_keyboard, chat_id=cid)

    async def _send_with_keyboard(self, text: str, reply_markup: dict,
                                   chat_id: str = "") -> bool:
        """Send a message with inline keyboard to a specific chat."""
        if not self._enabled or not chat_id:
            return False

        level_emoji = "\U0001F6A8"
        full_text = f"{level_emoji} <b>[CRITICAL]</b>\n\n{text}"

        url = self.TELEGRAM_API.format(token=self._bot_token, method="sendMessage")
        payload = {
            "chat_id": chat_id,
            "text": full_text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": reply_markup,
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    self._sent_timestamps.append(datetime.now(timezone.utc))
                    logger.info("Telegram sent approval keyboard to %s", chat_id)
                    return True
                else:
                    logger.warning("Telegram keyboard error %d: %s", resp.status_code, resp.text[:200])
                    return False
        except Exception:
            logger.exception("Telegram keyboard send failed")
            return False

    async def notify_drawdown_breach(self, drawdown_pct: float, threshold: float,
                                     chat_ids: list[str] = None):
        await self.broadcast(
            f"\U0001F6A8 <b>Drawdown Threshold Breached</b>\n\n"
            f"<b>Current:</b> {drawdown_pct:.2f}%\n"
            f"<b>Threshold:</b> {threshold:.2f}%\n\n"
            f"Switching to defensive profile.",
            level="CRITICAL", chat_ids=chat_ids or [],
        )

    async def notify_consecutive_losses(self, count: int, chat_ids: list[str] = None):
        await self.broadcast(
            f"\u26a0\ufe0f <b>{count} Consecutive Losses</b>\n\n"
            f"Bot is evaluating a profile switch to defensive mode.",
            level="WARNING", chat_ids=chat_ids or [],
        )

    async def notify_regime_change(self, old_regime: str, new_regime: str,
                                   chat_ids: list[str] = None):
        await self.broadcast(
            f"<b>Global Regime Change</b>\n"
            f"<code>{old_regime}</code> \u2192 <code>{new_regime}</code>",
            level="INFO", chat_ids=chat_ids or [],
        )

    async def notify_bot_paused(self, reason: str, chat_ids: list[str] = None):
        await self.broadcast(
            f"\U0001F6D1 <b>Bot Paused</b>\n\n<b>Reason:</b> {reason}",
            level="CRITICAL", chat_ids=chat_ids or [], deduplicate=False,
        )

    async def notify_daily_summary(self, metrics: dict, chat_ids: list[str] = None):
        await self.broadcast(
            f"\U0001F4CA <b>Daily Summary</b>\n\n"
            f"<b>PnL 24h:</b> {metrics.get('pnl_24h', 0):.2f} USDT\n"
            f"<b>Win Rate (last 10):</b> {metrics.get('win_rate_last_10', 0):.0f}%\n"
            f"<b>Trades:</b> {metrics.get('trades_per_hour', 0) * 24:.0f} (est. daily)\n"
            f"<b>Max Drawdown:</b> {metrics.get('drawdown_intraday', 0):.2f}%\n"
            f"<b>Cooldown hits:</b> {metrics.get('cooldown_hits', 0)}\n"
            f"<b>API errors:</b> {metrics.get('api_error_count', 0)}",
            level="INFO", chat_ids=chat_ids or [],
        )

    async def notify_api_errors(self, count: int, chat_ids: list[str] = None):
        await self.broadcast(
            f"\u26a0\ufe0f <b>Persistent API Errors</b>\n\n"
            f"<b>Count:</b> {count} errors detected.\n"
            f"Check exchange connectivity.",
            level="CRITICAL", chat_ids=chat_ids or [],
        )

    # ------------------------------------------------------------------
    # Telegram callback query polling (for inline approvals)
    # ------------------------------------------------------------------

    async def poll_callbacks(self) -> list[dict]:
        """
        Poll Telegram getUpdates for callback_query events.
        Returns a list of parsed callback dicts:
          [{"callback_query_id": str, "action": str, "id": int, "chat_id": str, "from_user": str}]
        """
        if not self._enabled:
            return []

        url = self.TELEGRAM_API.format(token=self._bot_token, method="getUpdates")
        params = {
            "offset": self._callback_offset,
            "timeout": 5,
            "allowed_updates": json.dumps(["callback_query"]),
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url, params=params)
                if resp.status_code != 200:
                    logger.warning("Telegram getUpdates error %d", resp.status_code)
                    return []
                data = resp.json()
        except Exception:
            logger.exception("Telegram getUpdates failed")
            return []

        results = []
        for update in data.get("result", []):
            update_id = update["update_id"]
            self._callback_offset = update_id + 1

            cb = update.get("callback_query")
            if not cb or not cb.get("data"):
                continue

            try:
                payload = json.loads(cb["data"])
            except (json.JSONDecodeError, TypeError):
                continue

            action = payload.get("action")
            req_id = payload.get("id")
            if action not in ("approve", "reject") or req_id is None:
                continue

            from_user = cb.get("from", {}).get("first_name", "telegram_user")
            chat_id = str(cb.get("message", {}).get("chat", {}).get("id", ""))
            results.append({
                "callback_query_id": cb["id"],
                "action": action,
                "id": int(req_id),
                "chat_id": chat_id,
                "from_user": from_user,
            })

        return results

    async def answer_callback(self, callback_query_id: str, text: str) -> bool:
        """Answer a Telegram callback query (shows a toast notification to the user)."""
        if not self._enabled:
            return False
        url = self.TELEGRAM_API.format(token=self._bot_token, method="answerCallbackQuery")
        payload = {"callback_query_id": callback_query_id, "text": text, "show_alert": True}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json=payload)
                return resp.status_code == 200
        except Exception:
            logger.exception("answerCallbackQuery failed")
            return False
