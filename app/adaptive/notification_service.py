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
import os
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger(__name__)

# Severity ordering for per-user minimum-level filtering.
_LEVEL_RANK = {"INFO": 0, "WARNING": 1, "CRITICAL": 2}


def _level_passes(level: str, min_level: str) -> bool:
    """True if `level` meets or exceeds `min_level`. Unknown values default to INFO."""
    return _LEVEL_RANK.get(level, 0) >= _LEVEL_RANK.get(min_level, 0)


# A recipient can be a bare chat_id (legacy) or a (chat_id, min_level) tuple.
Recipient = str | tuple[str, str]


def _normalize_recipients(recipients) -> list[tuple[str, str]]:
    """Normalize list to [(chat_id, min_level)] — bare str becomes (str, '')."""
    out: list[tuple[str, str]] = []
    for r in recipients or []:
        if isinstance(r, tuple) and len(r) == 2:
            out.append((str(r[0]), str(r[1] or "")))
        elif isinstance(r, str):
            out.append((r, ""))
    return out

_DEDUP_PERSIST_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "logs", "telegram_dedup_cache.json"
)


class NotificationService:
    """Async Telegram notification sender with dedup and rate limiting."""

    TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"

    # Rate limit: max messages per window
    MAX_MESSAGES_PER_MINUTE = 10
    DEDUP_WINDOW_SECONDS = 300  # 5 min dedup for similar messages

    def __init__(self, bot_token: str = "", default_min_level: str = "WARNING"):
        self._bot_token = bot_token
        self._enabled = bool(bot_token)
        # Global fallback min level used when a recipient has no per-user override.
        self._default_min_level = default_min_level if default_min_level in _LEVEL_RANK else "WARNING"
        # Rate limit state
        self._sent_timestamps: list[datetime] = []
        # Dedup: hash -> last sent time (persisted to disk to survive restarts)
        self._dedup_cache: dict[str, datetime] = {}
        self._dedup_last_save: datetime = datetime.now(timezone.utc)
        self._load_dedup_cache()
        # Telegram callback polling offset
        self._callback_offset: int = 0

        if self._enabled:
            logger.info("Telegram notifications enabled (bot token configured)")
        else:
            logger.info("Telegram notifications disabled (missing bot token)")

    def _load_dedup_cache(self):
        """Load persisted dedup cache from disk (ignores missing/corrupt file)."""
        try:
            path = os.path.normpath(_DEDUP_PERSIST_PATH)
            if not os.path.exists(path):
                return
            with open(path, "r") as f:
                raw = json.load(f)
            now = datetime.now(timezone.utc)
            cutoff = now - timedelta(seconds=self.DEDUP_WINDOW_SECONDS)
            for k, v in raw.items():
                try:
                    dt = datetime.fromisoformat(v)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if dt > cutoff:
                        self._dedup_cache[k] = dt
                except Exception:
                    pass
            logger.info("Telegram dedup cache restored: %d active entries", len(self._dedup_cache))
        except Exception:
            pass  # non-critical: fresh cache is fine

    def _save_dedup_cache(self):
        """Persist dedup cache to disk (throttled to at most once per 30s)."""
        now = datetime.now(timezone.utc)
        if (now - self._dedup_last_save).total_seconds() < 30:
            return
        self._dedup_last_save = now
        try:
            path = os.path.normpath(_DEDUP_PERSIST_PATH)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                json.dump({k: v.isoformat() for k, v in self._dedup_cache.items()}, f)
        except Exception:
            pass  # non-critical

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def send(self, text: str, level: str = "INFO",
                   chat_id: str = "", deduplicate: bool = True,
                   user_min_level: str = "") -> bool:
        """
        Send an HTML message to a specific Telegram chat.

        Args:
            text: HTML-formatted message body.
            level: INFO | WARNING | CRITICAL.
            chat_id: target chat ID. If empty, message is skipped.
            deduplicate: skip if a similar message was sent recently.
            user_min_level: per-user minimum level override. Empty = use global.

        Returns True if sent, False if skipped or failed.
        """
        if not self._enabled or not chat_id:
            return False
        # Severity filter: skip if below the effective minimum for this chat.
        effective_min = user_min_level if user_min_level in _LEVEL_RANK else self._default_min_level
        if not _level_passes(level, effective_min):
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
            # Persist to disk so dedup survives restarts
            self._save_dedup_cache()

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
                        chat_ids: "list[Recipient] | None" = None,
                        deduplicate: bool = True) -> int:
        """
        Send to multiple recipients. Returns count of successful sends.
        Accepts either a list of chat_id strings (legacy) or a list of
        (chat_id, user_min_level) tuples for per-user severity filtering.
        """
        if not chat_ids:
            return 0
        sent = 0
        for cid, min_level in _normalize_recipients(chat_ids):
            if await self.send(text, level=level, chat_id=cid,
                               deduplicate=deduplicate,
                               user_min_level=min_level):
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
        # Approval requests are CRITICAL — no per-user filtering, but we still
        # need to handle the (chat_id, min_level) tuple form of chat_ids.
        for cid, _min in _normalize_recipients(chat_ids or []):
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
                                   chat_ids: list = None, level: str = "INFO"):
        await self.broadcast(
            f"<b>Global Regime Change</b>\n"
            f"<code>{old_regime}</code> \u2192 <code>{new_regime}</code>",
            level=level, chat_ids=chat_ids or [],
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
        # Use POST with JSON body — more reliable than GET query params for
        # allowed_updates (avoids URL-encoding issues with JSON arrays)
        payload = {
            "offset": self._callback_offset,
            "timeout": 5,
            "allowed_updates": ["callback_query"],
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code != 200:
                    logger.warning("Telegram getUpdates error %d: %s",
                                   resp.status_code, resp.text[:200])
                    return []
                data = resp.json()
                if not data.get("ok"):
                    logger.warning("Telegram getUpdates not ok: %s", data.get("description", ""))
                    return []
        except Exception:
            logger.exception("Telegram getUpdates failed")
            return []

        results = []
        for update in data.get("result", []):
            update_id = update["update_id"]
            # Always advance offset — even for non-callback updates — to prevent
            # old message updates from blocking the queue
            self._callback_offset = update_id + 1

            cb = update.get("callback_query")
            if not cb or not cb.get("data"):
                continue

            try:
                payload_data = json.loads(cb["data"])
            except (json.JSONDecodeError, TypeError):
                logger.warning("Telegram callback: invalid JSON in data: %s", cb.get("data"))
                continue

            action = payload_data.get("action")
            req_id = payload_data.get("id")
            if action not in ("approve", "reject") or req_id is None:
                logger.warning("Telegram callback: unknown action=%s id=%s", action, req_id)
                continue

            from_user = cb.get("from", {}).get("first_name", "telegram_user")
            chat_id = str(cb.get("message", {}).get("chat", {}).get("id", ""))
            logger.info("Telegram callback received: action=%s request_id=%d from=%s",
                        action, req_id, from_user)
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
