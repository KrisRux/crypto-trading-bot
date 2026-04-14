"""
Adaptive Guardrails — centralized pre-trade validation layer.

Provides a single entry point ``can_open_new_trade()`` that checks:
  1. KillSwitch    — global pause on extreme drawdown / loss streaks
  2. SymbolCooldown — per-symbol pause after repeated losses / clustered SLs
  3. TradeGate     — regime-aware indicator thresholds before entry
  4. DynamicScore  — adaptive min-score for strategies that produce scores
  5. EntryThrottle — max entries per candle / per hour by regime
  6. RiskScaler    — position size multiplier based on conditions
  7. StrategyCircuitBreaker — per-strategy pause after consecutive losses

All thresholds are loaded from ``config/guardrails.json`` and can be changed
without a deploy.  Metrics are aggregated in ``GuardrailStats`` for observability.

Usage in the engine:

    verdict = guardrails.can_open_new_trade(
        symbol=symbol,
        global_regime=global_regime,
        symbol_regime=symbol_snap.regime,
        adx=symbol_snap.adx,
        volume_ratio=symbol_snap.volume_ratio,
        bb_width_pct=symbol_snap.bb_width_pct,
        signal_score=score,
        strategy_name=strategy_name,
        perf=perf_snapshot,
    )
    if not verdict.allowed:
        logger.info("TRADE_GATE: blocked | symbol=%s | reason=%s", symbol, verdict.reason)
        return
    multiplier = guardrails.get_risk_multiplier(perf_snapshot)
"""

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "config", "guardrails.json")


@dataclass
class TradeVerdict:
    """Result of a guardrail check."""
    allowed: bool
    reason: str = ""
    details: dict = field(default_factory=dict)


@dataclass
class GuardrailStats:
    """Aggregate counters for observability (reset daily)."""
    blocked_kill_switch: int = 0
    blocked_symbol_cooldown: int = 0
    blocked_trade_gate: int = 0
    blocked_dynamic_score: int = 0
    blocked_entry_throttle: int = 0
    blocked_strategy_breaker: int = 0
    total_blocked: int = 0
    total_passed: int = 0

    def to_dict(self) -> dict:
        return {
            "blocked_kill_switch": self.blocked_kill_switch,
            "blocked_symbol_cooldown": self.blocked_symbol_cooldown,
            "blocked_trade_gate": self.blocked_trade_gate,
            "blocked_dynamic_score": self.blocked_dynamic_score,
            "blocked_entry_throttle": self.blocked_entry_throttle,
            "blocked_strategy_breaker": self.blocked_strategy_breaker,
            "total_blocked": self.total_blocked,
            "total_passed": self.total_passed,
        }

    def reset(self):
        """Reset daily counters."""
        self.blocked_kill_switch = 0
        self.blocked_symbol_cooldown = 0
        self.blocked_trade_gate = 0
        self.blocked_dynamic_score = 0
        self.blocked_entry_throttle = 0
        self.blocked_strategy_breaker = 0
        self.total_blocked = 0
        self.total_passed = 0


def _load_config() -> dict:
    try:
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    except Exception:
        logger.warning("Failed to load guardrails config, using defaults")
        return {}


# ======================================================================
# 1. Kill Switch
# ======================================================================

class KillSwitch:
    """Global pause on new entries when performance is critically bad."""

    def __init__(self, cfg: dict):
        c = cfg.get("kill_switch", {})
        self.consec_loss_thresh = c.get("consecutive_losses_threshold", 6)
        self.low_wr_thresh = c.get("low_win_rate_threshold", 15)
        self.min_trades_for_wr = c.get("min_trades_for_win_rate_check", 5)
        self.dd_thresh = c.get("intraday_drawdown_threshold", 2.0)
        self.pnl_24h_thresh = c.get("pnl_24h_threshold", -6.0)
        self.pause_min_losses = c.get("pause_minutes_losses", 90)
        self.pause_min_dd = c.get("pause_minutes_drawdown", 120)
        self._active = False
        self._reason = ""
        self._value: Any = None
        self._pause_until: datetime | None = None

    @property
    def active(self) -> bool:
        if self._pause_until and datetime.now(timezone.utc) >= self._pause_until:
            self._deactivate()
        return self._active

    def update(self, perf: dict):
        """Re-evaluate kill switch conditions from performance snapshot."""
        now = datetime.now(timezone.utc)

        # Already active — just check expiry
        if self._active:
            if self._pause_until and now >= self._pause_until:
                self._deactivate()
            return

        # Check conditions (in priority order)
        consec = perf.get("consecutive_losses", 0)
        wr = perf.get("win_rate_last_10", 100)
        dd = perf.get("drawdown_intraday", 0)
        pnl24 = perf.get("pnl_24h", 0)
        total_trades = perf.get("total_recent_trades", 0)

        if consec >= self.consec_loss_thresh:
            self._activate("consecutive_losses", consec, self.pause_min_losses)
        elif wr <= self.low_wr_thresh and total_trades >= self.min_trades_for_wr:
            # Only block on low win-rate when we have enough trades to trust the metric.
            # Avoids infinite loop when bot has 0 trades (wr=0% with no history).
            self._activate("low_win_rate", wr, self.pause_min_losses)
        elif dd >= self.dd_thresh:
            self._activate("intraday_drawdown", dd, self.pause_min_dd)
        elif pnl24 <= self.pnl_24h_thresh:
            self._activate("pnl_24h_severe", pnl24, self.pause_min_dd)

    def _activate(self, reason: str, value: Any, pause_minutes: int):
        self._active = True
        self._reason = reason
        self._value = value
        self._pause_until = datetime.now(timezone.utc) + timedelta(minutes=pause_minutes)
        logger.warning(
            "KILL_SWITCH: activated | reason=%s | value=%s | pause_until=%s",
            reason, value, self._pause_until.strftime("%H:%M:%S UTC"),
        )

    def _deactivate(self):
        logger.info("KILL_SWITCH: expired | trading re-enabled")
        self._active = False
        self._reason = ""
        self._value = None
        self._pause_until = None

    def check(self) -> TradeVerdict:
        if self.active:
            remaining = ""
            if self._pause_until:
                remaining = f" | resumes={self._pause_until.strftime('%H:%M:%S UTC')}"
            logger.info("KILL_SWITCH: active | new entries blocked%s", remaining)
            return TradeVerdict(
                allowed=False,
                reason=f"kill_switch_{self._reason}",
                details={"value": self._value, "pause_until": str(self._pause_until)},
            )
        return TradeVerdict(allowed=True)

    def status(self) -> dict:
        return {
            "active": self._active,
            "reason": self._reason,
            "value": self._value,
            "pause_until": str(self._pause_until) if self._pause_until else None,
        }


# ======================================================================
# 2. Symbol Cooldown
# ======================================================================

class SymbolCooldown:
    """Per-symbol cooldown after consecutive losses or clustered stop-losses."""

    def __init__(self, cfg: dict):
        c = cfg.get("symbol_cooldown", {})
        self.consec_loss_thresh = c.get("consecutive_losses_threshold", 3)
        self.cooldown_min_losses = c.get("cooldown_minutes_losses", 60)
        self.sl_cluster_count = c.get("stoploss_cluster_count", 2)
        self.sl_cluster_window = c.get("stoploss_cluster_window_minutes", 90)
        self.cooldown_min_cluster = c.get("cooldown_minutes_cluster", 90)
        # symbol -> cooldown_until
        self._cooldowns: dict[str, datetime] = {}
        # symbol -> list of recent loss/SL timestamps
        self._symbol_losses: dict[str, list[datetime]] = {}
        self._symbol_sl_times: dict[str, list[datetime]] = {}

    def record_loss(self, symbol: str, was_stoploss: bool = False):
        """Record a trade loss for a symbol."""
        now = datetime.now(timezone.utc)
        self._symbol_losses.setdefault(symbol, []).append(now)
        if was_stoploss:
            self._symbol_sl_times.setdefault(symbol, []).append(now)

        # Check consecutive losses
        losses = self._symbol_losses[symbol]
        if len(losses) >= self.consec_loss_thresh:
            # Count only recent consecutive (last N)
            self._activate(symbol, f"{self.consec_loss_thresh}_consecutive_losses",
                           self.cooldown_min_losses)

        # Check SL cluster
        if was_stoploss:
            cutoff = now - timedelta(minutes=self.sl_cluster_window)
            recent_sl = [t for t in self._symbol_sl_times.get(symbol, []) if t >= cutoff]
            self._symbol_sl_times[symbol] = recent_sl
            if len(recent_sl) >= self.sl_cluster_count:
                self._activate(symbol, f"{self.sl_cluster_count}_stoploss_cluster",
                               self.cooldown_min_cluster)

    def record_win(self, symbol: str):
        """A win resets the consecutive loss counter for a symbol."""
        self._symbol_losses.pop(symbol, None)

    def _activate(self, symbol: str, reason: str, minutes: int):
        until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        self._cooldowns[symbol] = until
        logger.warning(
            "SYMBOL_COOLDOWN: activated | symbol=%s | reason=%s | until=%s",
            symbol, reason, until.strftime("%H:%M:%S UTC"),
        )

    def check(self, symbol: str) -> TradeVerdict:
        """Check if symbol is on cooldown."""
        until = self._cooldowns.get(symbol)
        if until and datetime.now(timezone.utc) < until:
            logger.info("SYMBOL_COOLDOWN: blocked | symbol=%s | until=%s",
                         symbol, until.strftime("%H:%M:%S UTC"))
            return TradeVerdict(
                allowed=False,
                reason="symbol_cooldown",
                details={"symbol": symbol, "until": str(until)},
            )
        elif until:
            # Expired — clean up
            del self._cooldowns[symbol]
            logger.info("SYMBOL_COOLDOWN: expired | symbol=%s", symbol)
        return TradeVerdict(allowed=True)

    def status(self) -> dict:
        now = datetime.now(timezone.utc)
        return {
            sym: {"until": str(until), "remaining_min": max(0, (until - now).total_seconds() / 60)}
            for sym, until in self._cooldowns.items()
            if until > now
        }


# ======================================================================
# 3. Trade Gate (regime-aware indicator thresholds)
# ======================================================================

class TradeGate:
    """Pre-trade filter based on global/symbol regime + indicators."""

    def __init__(self, cfg: dict):
        c = cfg.get("trade_gate", {})
        self._thresholds = {
            "defensive": c.get("defensive", {"min_adx": 30, "min_volume_ratio": 1.6, "min_bb_width_pct": 1.2}),
            "range": c.get("range", {"min_adx": 32, "min_volume_ratio": 1.8, "min_bb_width_pct": 1.4}),
            "trend": c.get("trend", {"min_adx": 25, "min_volume_ratio": 1.0, "min_bb_width_pct": 0.0}),
            "volatile": c.get("volatile", {"min_adx": 28, "min_volume_ratio": 1.4, "min_bb_width_pct": 1.0}),
        }
        self._block_on_symbol_regime = set(c.get("block_entry_on_symbol_regime", ["range", "defensive"]))

    def check(self, *, global_regime: str, symbol_regime: str, symbol: str,
              adx: float, volume_ratio: float, bb_width_pct: float) -> TradeVerdict:
        """Check if a trade can pass the regime gate."""

        # D. If symbol_regime is in blocked list -> no entry
        if symbol_regime in self._block_on_symbol_regime:
            reason = f"symbol_regime_{symbol_regime}"
            logger.info("TRADE_GATE: blocked | symbol=%s | reason=%s", symbol, reason)
            return TradeVerdict(allowed=False, reason=reason,
                                details={"symbol_regime": symbol_regime})

        # Get thresholds for global regime
        thresholds = self._thresholds.get(global_regime, self._thresholds.get("range", {}))

        # Symbol must be in trend for defensive/range global regimes
        require_trend = thresholds.get("require_symbol_trend", True)
        if require_trend and symbol_regime != "trend":
            reason = f"global_{global_regime}_requires_symbol_trend"
            logger.info("TRADE_GATE: blocked | symbol=%s | reason=%s | symbol_regime=%s",
                         symbol, reason, symbol_regime)
            return TradeVerdict(allowed=False, reason=reason,
                                details={"global_regime": global_regime, "symbol_regime": symbol_regime})

        min_adx = thresholds.get("min_adx", 25)
        min_vol = thresholds.get("min_volume_ratio", 1.0)
        min_bb = thresholds.get("min_bb_width_pct", 0.0)

        if adx < min_adx:
            reason = f"global_{global_regime}_adx_too_low"
            logger.info("TRADE_GATE: blocked | symbol=%s | reason=%s | adx=%.1f required=%.0f",
                         symbol, reason, adx, min_adx)
            return TradeVerdict(allowed=False, reason=reason,
                                details={"adx": adx, "required": min_adx})

        if volume_ratio < min_vol:
            reason = f"global_{global_regime}_volume_too_low"
            logger.info("TRADE_GATE: blocked | symbol=%s | reason=%s | volume=%.2f required=%.1f",
                         symbol, reason, volume_ratio, min_vol)
            return TradeVerdict(allowed=False, reason=reason,
                                details={"volume_ratio": volume_ratio, "required": min_vol})

        if bb_width_pct < min_bb:
            reason = f"global_{global_regime}_bb_width_too_low"
            logger.info("TRADE_GATE: blocked | symbol=%s | reason=%s | bbw=%.2f required=%.1f",
                         symbol, reason, bb_width_pct, min_bb)
            return TradeVerdict(allowed=False, reason=reason,
                                details={"bb_width_pct": bb_width_pct, "required": min_bb})

        logger.info(
            "TRADE_GATE: passed | symbol=%s | regime=%s | adx=%.1f | volume=%.2f | bbw=%.2f",
            symbol, global_regime, adx, volume_ratio, bb_width_pct,
        )
        return TradeVerdict(allowed=True)


# ======================================================================
# 4. Dynamic Score Filter
# ======================================================================

class DynamicScoreFilter:
    """Adaptive minimum score based on performance and regime."""

    def __init__(self, cfg: dict):
        c = cfg.get("dynamic_score", {})
        self.base_min = c.get("base_min_score", 80)
        self.after_3 = c.get("min_score_after_3_losses", 88)
        self.after_5 = c.get("min_score_after_5_losses", 92)
        self.extra_bad_regime = c.get("extra_score_in_bad_regime", 5)
        self.bad_regimes = set(c.get("bad_regimes", ["range", "defensive"]))
        self.max_cap = c.get("max_score_cap", 95)

    def get_min_score(self, consecutive_losses: int, global_regime: str,
                      symbol_regime: str = "") -> float:
        """Compute the minimum score threshold given current conditions."""
        if consecutive_losses >= 5:
            score = self.after_5
        elif consecutive_losses >= 3:
            score = self.after_3
        else:
            score = self.base_min

        # Apply bad-regime penalty ONLY if the symbol itself is also in a bad regime.
        # A trending symbol (symbol_regime="trend") should not be penalized just because
        # the global market is range/defensive.
        if global_regime in self.bad_regimes:
            if not symbol_regime or symbol_regime in self.bad_regimes:
                score += self.extra_bad_regime
            # else: symbol is trending locally → skip penalty

        return min(score, self.max_cap)

    def check(self, signal_score: float | None, consecutive_losses: int,
              global_regime: str, symbol: str, strategy_name: str,
              symbol_regime: str = "") -> TradeVerdict:
        """Check if the signal score meets the dynamic threshold."""
        if signal_score is None:
            # Strategy doesn't produce scores — pass through
            return TradeVerdict(allowed=True)

        min_score = self.get_min_score(consecutive_losses, global_regime, symbol_regime)

        if signal_score < min_score:
            reason = "dynamic_score_too_low"
            logger.info(
                "DYNAMIC_SCORE: blocked | symbol=%s | strategy=%s | score=%.0f < min=%.0f "
                "(consec_losses=%d, global=%s, symbol=%s)",
                symbol, strategy_name, signal_score, min_score, consecutive_losses, global_regime, symbol_regime,
            )
            return TradeVerdict(allowed=False, reason=reason,
                                details={"score": signal_score, "min_score": min_score,
                                          "consecutive_losses": consecutive_losses})

        logger.info(
            "DYNAMIC_SCORE: passed | symbol=%s | score=%.0f >= min=%.0f",
            symbol, signal_score, min_score,
        )
        return TradeVerdict(allowed=True)


# ======================================================================
# 5. Entry Throttle
# ======================================================================

class EntryThrottle:
    """Limits how many new entries can be opened per candle and per hour."""

    def __init__(self, cfg: dict):
        c = cfg.get("entry_throttle", {})
        self.max_per_symbol_per_candle = c.get("max_entries_per_symbol_per_candle", 1)
        self.max_per_hour = c.get("max_entries_per_hour", {
            "defensive": 2, "range": 3, "trend": 5, "volatile": 3,
        })
        self.default_max_per_hour = c.get("default_max_entries_per_hour", 3)
        # Tracking — per-user to avoid cross-user throttling
        # f"{user_id}_{symbol}_{candle_key}" -> count
        self._candle_entries: dict[str, int] = {}
        # user_id -> list[datetime]
        self._hourly_entries: dict[int, list[datetime]] = {}
        self._current_candle_key: str = ""

    def new_candle(self, candle_key: str):
        """Reset per-candle counters when a new candle starts."""
        if candle_key != self._current_candle_key:
            self._candle_entries.clear()
            self._current_candle_key = candle_key

    def record_entry(self, symbol: str, user_id: int = 0):
        """Record that a new entry was made."""
        key = f"{user_id}_{symbol}_{self._current_candle_key}"
        self._candle_entries[key] = self._candle_entries.get(key, 0) + 1
        self._hourly_entries.setdefault(user_id, []).append(datetime.now(timezone.utc))

    def check(self, symbol: str, global_regime: str, user_id: int = 0) -> TradeVerdict:
        """Check if entry is allowed given throttle limits."""
        now = datetime.now(timezone.utc)

        # Per-symbol per-candle per-user
        key = f"{user_id}_{symbol}_{self._current_candle_key}"
        candle_count = self._candle_entries.get(key, 0)
        if candle_count >= self.max_per_symbol_per_candle:
            reason = "one_trade_per_candle"
            logger.info("ENTRY_THROTTLE: blocked | symbol=%s user=%d | reason=%s | count=%d",
                         symbol, user_id, reason, candle_count)
            return TradeVerdict(allowed=False, reason=reason,
                                details={"candle_count": candle_count})

        # Hourly per-user limit
        cutoff = now - timedelta(hours=1)
        user_entries = self._hourly_entries.get(user_id, [])
        user_entries = [t for t in user_entries if t >= cutoff]
        self._hourly_entries[user_id] = user_entries
        max_hour = self.max_per_hour.get(global_regime, self.default_max_per_hour)
        if len(user_entries) >= max_hour:
            reason = f"hourly_limit_{global_regime}"
            logger.info("ENTRY_THROTTLE: blocked | symbol=%s user=%d | reason=%s | entries_this_hour=%d max=%d",
                         symbol, user_id, reason, len(user_entries), max_hour)
            return TradeVerdict(allowed=False, reason=reason,
                                details={"hourly_count": len(user_entries), "max": max_hour})

        return TradeVerdict(allowed=True)


# ======================================================================
# 6. Risk Scaler
# ======================================================================

class RiskScaler:
    """Computes a position-size multiplier based on performance conditions."""

    def __init__(self, cfg: dict):
        c = cfg.get("risk_scaling", {})
        self.mult_3_losses = c.get("consecutive_losses_3_multiplier", 0.75)
        self.mult_5_losses = c.get("consecutive_losses_5_multiplier", 0.50)
        self.dd_thresh = c.get("drawdown_threshold", 1.5)
        self.dd_min_mult = c.get("drawdown_min_multiplier", 0.50)
        self._last_logged_mult: float | None = None

    def get_multiplier(self, perf: dict) -> float:
        """Return a 0-1 multiplier for position sizing."""
        consec = perf.get("consecutive_losses", 0)
        dd = perf.get("drawdown_intraday", 0)

        mult = 1.0
        if consec >= 5:
            mult = min(mult, self.mult_5_losses)
        elif consec >= 3:
            mult = min(mult, self.mult_3_losses)

        if dd >= self.dd_thresh:
            mult = min(mult, self.dd_min_mult)

        # Log only when multiplier changes (avoid spam every tick)
        if mult != self._last_logged_mult:
            if mult < 1.0:
                logger.info(
                    "RISK_SCALING: consecutive_losses=%d | drawdown=%.2f%% | multiplier=%.2f",
                    consec, dd, mult,
                )
            elif self._last_logged_mult is not None and self._last_logged_mult < 1.0:
                logger.info("RISK_SCALING: restored to 1.0 (normal)")
            self._last_logged_mult = mult
        return mult


# ======================================================================
# 7. Strategy Circuit Breaker
# ======================================================================

class StrategyCircuitBreaker:
    """Pause a strategy after N consecutive losses."""

    def __init__(self, cfg: dict):
        c = cfg.get("strategy_circuit_breaker", {})
        self.consec_thresh = c.get("consecutive_losses_threshold", 4)
        self.pause_min = c.get("pause_minutes", 120)
        # strategy_name -> consecutive loss count
        self._losses: dict[str, int] = {}
        # strategy_name -> paused_until
        self._paused: dict[str, datetime] = {}
        # (symbol, strategy) -> consecutive loss count
        self._symbol_strat_losses: dict[tuple[str, str], int] = {}
        self._symbol_strat_paused: dict[tuple[str, str], datetime] = {}

    def record_result(self, strategy_name: str, symbol: str, is_win: bool):
        """Record a trade result for circuit breaker tracking."""
        # Per-strategy
        if is_win:
            self._losses[strategy_name] = 0
            self._symbol_strat_losses[(symbol, strategy_name)] = 0
        else:
            self._losses[strategy_name] = self._losses.get(strategy_name, 0) + 1
            key = (symbol, strategy_name)
            self._symbol_strat_losses[key] = self._symbol_strat_losses.get(key, 0) + 1

            if self._losses[strategy_name] >= self.consec_thresh:
                until = datetime.now(timezone.utc) + timedelta(minutes=self.pause_min)
                self._paused[strategy_name] = until
                logger.warning(
                    "STRATEGY_BREAKER: paused | strategy=%s | losses=%d | until=%s",
                    strategy_name, self._losses[strategy_name], until.strftime("%H:%M:%S UTC"),
                )

            if self._symbol_strat_losses[key] >= self.consec_thresh:
                until = datetime.now(timezone.utc) + timedelta(minutes=self.pause_min)
                self._symbol_strat_paused[key] = until
                logger.warning(
                    "STRATEGY_BREAKER: paused | strategy=%s symbol=%s | losses=%d | until=%s",
                    strategy_name, symbol, self._symbol_strat_losses[key],
                    until.strftime("%H:%M:%S UTC"),
                )

    def check(self, strategy_name: str, symbol: str) -> TradeVerdict:
        """Check if strategy (or symbol+strategy) is paused."""
        now = datetime.now(timezone.utc)

        # Per-strategy
        until = self._paused.get(strategy_name)
        if until:
            if now < until:
                logger.info("STRATEGY_BREAKER: blocked | strategy=%s | until=%s",
                             strategy_name, until.strftime("%H:%M:%S UTC"))
                return TradeVerdict(allowed=False, reason=f"strategy_breaker_{strategy_name}",
                                    details={"strategy": strategy_name, "until": str(until)})
            else:
                del self._paused[strategy_name]
                self._losses[strategy_name] = 0
                logger.info("STRATEGY_BREAKER: expired | strategy=%s", strategy_name)

        # Per symbol+strategy
        key = (symbol, strategy_name)
        until = self._symbol_strat_paused.get(key)
        if until:
            if now < until:
                logger.info("STRATEGY_BREAKER: blocked | strategy=%s symbol=%s",
                             strategy_name, symbol)
                return TradeVerdict(
                    allowed=False,
                    reason=f"strategy_breaker_{strategy_name}_{symbol}",
                    details={"strategy": strategy_name, "symbol": symbol, "until": str(until)},
                )
            else:
                del self._symbol_strat_paused[key]
                self._symbol_strat_losses[key] = 0

        return TradeVerdict(allowed=True)


# ======================================================================
# Main Guardrails class — single entry point
# ======================================================================

class Guardrails:
    """
    Centralized guardrail system.

    Call ``can_open_new_trade()`` before every BUY entry.
    Call ``get_risk_multiplier()`` when calculating position size.
    Call ``record_trade_result()`` when a trade closes.
    Call ``update_performance()`` each cycle with fresh perf metrics.
    """

    def __init__(self):
        self._cfg = _load_config()
        self.kill_switch = KillSwitch(self._cfg)
        self.symbol_cooldown = SymbolCooldown(self._cfg)
        self.trade_gate = TradeGate(self._cfg)
        self.dynamic_score = DynamicScoreFilter(self._cfg)
        self.entry_throttle = EntryThrottle(self._cfg)
        self.risk_scaler = RiskScaler(self._cfg)
        self.strategy_breaker = StrategyCircuitBreaker(self._cfg)
        self.stats = GuardrailStats()
        self._perf: dict = {}
        logger.info("Guardrails initialized from %s", CONFIG_PATH)

    def reload_config(self):
        """Hot-reload config from disk. Only updates thresholds, preserves runtime state."""
        self._cfg = _load_config()
        # Stateless components: safe to recreate (they only hold thresholds)
        self.trade_gate = TradeGate(self._cfg)
        self.dynamic_score = DynamicScoreFilter(self._cfg)
        self.risk_scaler = RiskScaler(self._cfg)
        # Stateful components: update thresholds without destroying state
        ks_cfg = self._cfg.get("kill_switch", {})
        self.kill_switch.consec_loss_thresh = ks_cfg.get("consecutive_losses_threshold", 6)
        self.kill_switch.low_wr_thresh = ks_cfg.get("low_win_rate_threshold", 15)
        self.kill_switch.dd_thresh = ks_cfg.get("intraday_drawdown_threshold", 2.0)
        self.kill_switch.pnl_24h_thresh = ks_cfg.get("pnl_24h_threshold", -6.0)
        self.kill_switch.pause_min_losses = ks_cfg.get("pause_minutes_losses", 90)
        self.kill_switch.pause_min_dd = ks_cfg.get("pause_minutes_drawdown", 120)

        sc_cfg = self._cfg.get("symbol_cooldown", {})
        self.symbol_cooldown.consec_loss_thresh = sc_cfg.get("consecutive_losses_threshold", 3)
        self.symbol_cooldown.cooldown_min_losses = sc_cfg.get("cooldown_minutes_losses", 60)
        self.symbol_cooldown.sl_cluster_count = sc_cfg.get("stoploss_cluster_count", 2)
        self.symbol_cooldown.sl_cluster_window = sc_cfg.get("stoploss_cluster_window_minutes", 90)
        self.symbol_cooldown.cooldown_min_cluster = sc_cfg.get("cooldown_minutes_cluster", 90)

        et_cfg = self._cfg.get("entry_throttle", {})
        self.entry_throttle.max_per_symbol_per_candle = et_cfg.get("max_entries_per_symbol_per_candle", 1)
        self.entry_throttle.max_per_hour = et_cfg.get("max_entries_per_hour", {"defensive": 2, "range": 3, "trend": 5, "volatile": 3})
        self.entry_throttle.default_max_per_hour = et_cfg.get("default_max_entries_per_hour", 3)

        scb_cfg = self._cfg.get("strategy_circuit_breaker", {})
        self.strategy_breaker.consec_thresh = scb_cfg.get("consecutive_losses_threshold", 4)
        self.strategy_breaker.pause_min = scb_cfg.get("pause_minutes", 120)

        logger.info("Guardrails config reloaded (state preserved)")

    def update_performance(self, perf: dict):
        """Update cached performance snapshot and re-evaluate kill switch."""
        self._perf = perf
        self.kill_switch.update(perf)
        # Reset stats daily at midnight UTC
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if not hasattr(self, "_stats_date") or self._stats_date != today:
            if hasattr(self, "_stats_date"):
                logger.info("GUARDRAILS: daily stats reset | %s", self.stats.to_dict())
            self.stats.reset()
            self._stats_date = today

    def new_candle(self, candle_key: str):
        """Notify entry throttle of a new candle."""
        self.entry_throttle.new_candle(candle_key)

    def record_trade_result(self, symbol: str, strategy_name: str,
                            is_win: bool, was_stoploss: bool = False):
        """Record a trade result for symbol cooldown and strategy breaker."""
        if is_win:
            self.symbol_cooldown.record_win(symbol)
        else:
            self.symbol_cooldown.record_loss(symbol, was_stoploss=was_stoploss)
        self.strategy_breaker.record_result(strategy_name, symbol, is_win)

    def can_open_new_trade(
        self, *,
        symbol: str,
        global_regime: str,
        symbol_regime: str,
        adx: float,
        volume_ratio: float,
        bb_width_pct: float,
        signal_score: float | None = None,
        strategy_name: str = "",
        user_id: int = 0,
    ) -> TradeVerdict:
        """
        Single entry point: check all guardrails in order.
        Returns TradeVerdict(allowed=True) if all pass.
        """

        # 1. Kill Switch (global)
        v = self.kill_switch.check()
        if not v.allowed:
            self.stats.blocked_kill_switch += 1
            self.stats.total_blocked += 1
            return v

        # 2. Symbol Cooldown
        v = self.symbol_cooldown.check(symbol)
        if not v.allowed:
            self.stats.blocked_symbol_cooldown += 1
            self.stats.total_blocked += 1
            return v

        # 3. Strategy Circuit Breaker
        if strategy_name:
            v = self.strategy_breaker.check(strategy_name, symbol)
            if not v.allowed:
                self.stats.blocked_strategy_breaker += 1
                self.stats.total_blocked += 1
                return v

        # 4. Trade Gate (regime-aware indicators)
        v = self.trade_gate.check(
            global_regime=global_regime,
            symbol_regime=symbol_regime,
            symbol=symbol,
            adx=adx,
            volume_ratio=volume_ratio,
            bb_width_pct=bb_width_pct,
        )
        if not v.allowed:
            self.stats.blocked_trade_gate += 1
            self.stats.total_blocked += 1
            return v

        # 5. Dynamic Score
        consec_losses = self._perf.get("consecutive_losses", 0)
        v = self.dynamic_score.check(
            signal_score=signal_score,
            consecutive_losses=consec_losses,
            global_regime=global_regime,
            symbol=symbol,
            strategy_name=strategy_name,
            symbol_regime=symbol_regime,
        )
        if not v.allowed:
            self.stats.blocked_dynamic_score += 1
            self.stats.total_blocked += 1
            return v

        # 6. Entry Throttle
        v = self.entry_throttle.check(symbol, global_regime, user_id=user_id)
        if not v.allowed:
            self.stats.blocked_entry_throttle += 1
            self.stats.total_blocked += 1
            return v

        # All passed
        self.stats.total_passed += 1
        return TradeVerdict(allowed=True)

    def get_risk_multiplier(self) -> float:
        """Get position-size multiplier based on current conditions."""
        if self.kill_switch.active:
            return 0.0  # Should not reach here — kill switch blocks entry
        return self.risk_scaler.get_multiplier(self._perf)

    def status(self) -> dict:
        """Full guardrails status for API/observability."""
        return {
            "kill_switch": self.kill_switch.status(),
            "symbol_cooldowns": self.symbol_cooldown.status(),
            "stats": self.stats.to_dict(),
            "risk_multiplier": self.get_risk_multiplier(),
            "dynamic_score_min": self.dynamic_score.get_min_score(
                self._perf.get("consecutive_losses", 0),
                self._perf.get("global_regime", "unknown"),
            ) if self._perf else self.dynamic_score.base_min,
        }
