"""
Profile Manager — loads, applies, and switches trading profiles.

Profiles are defined in config/profiles.json. Each profile contains:
  - risk parameters (max_position_pct, SL, TP)
  - strategy thresholds per strategy
  - regime thresholds
  - auto_apply / requires_approval flags

Switching rules enforce:
  - cooldown between switches
  - hysteresis to prevent flip-flopping
  - max profile changes per day
  - min trades before upgrading
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

PROFILES_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "config", "profiles.json")


class ProfileManager:
    """Manages profile loading, evaluation, and application."""

    def __init__(self, profiles_path: str = PROFILES_FILE):
        self._profiles_path = os.path.abspath(profiles_path)
        self._data: dict = {}
        self._profiles: dict = {}
        self._switching_rules: dict = {}
        self._active_profile: str = "normal"
        self._switch_history: list[dict] = []  # [{from, to, at, reason}]
        self._last_switch_time: datetime | None = None
        self.load()

    @property
    def active_profile(self) -> str:
        return self._active_profile

    @property
    def profiles(self) -> dict:
        return dict(self._profiles)

    @property
    def switching_rules(self) -> dict:
        return dict(self._switching_rules)

    @property
    def switch_history(self) -> list[dict]:
        return list(self._switch_history)

    def load(self):
        """Load profiles from JSON file."""
        try:
            with open(self._profiles_path, "r") as f:
                self._data = json.load(f)
            self._profiles = self._data.get("profiles", {})
            self._switching_rules = self._data.get("switching_rules", {})
            self._active_profile = self._data.get("active_profile", "normal")
            logger.info(
                "Profiles loaded: %s (active: %s)",
                list(self._profiles.keys()), self._active_profile,
            )
        except Exception:
            logger.exception("Failed to load profiles from %s", self._profiles_path)
            self._profiles = {}
            self._switching_rules = {}

    def get_profile(self, name: str) -> dict | None:
        return self._profiles.get(name)

    def get_active(self) -> dict:
        return self._profiles.get(self._active_profile, {})

    # ------------------------------------------------------------------
    # Switching evaluation
    # ------------------------------------------------------------------

    def evaluate_switch(self, perf: dict, regime: str) -> dict | None:
        """
        Evaluate whether a profile switch is needed based on performance metrics
        and market regime.

        Returns:
          {"from": str, "to": str, "reason": str, "auto_apply": bool,
           "requires_approval": bool}
        or None if no switch needed.
        """
        current = self._active_profile

        # Check switching rules: cooldown
        if not self._can_switch():
            return None

        target = self._determine_target(current, perf, regime)
        if target is None or target == current:
            return None

        target_profile = self._profiles.get(target)
        if not target_profile:
            return None

        reason = self._build_reason(current, target, perf, regime)
        return {
            "from": current,
            "to": target,
            "reason": reason,
            "auto_apply": target_profile.get("auto_apply", False),
            "requires_approval": target_profile.get("requires_approval", False),
        }

    def _determine_target(self, current: str, perf: dict, regime: str) -> str | None:
        """Deterministic switching rules."""
        pnl_6h = perf.get("pnl_6h", 0)
        consec_losses = perf.get("consecutive_losses", 0)
        drawdown = perf.get("drawdown_intraday", 0)
        win_rate = perf.get("win_rate_last_10", 50)

        # Rule 1: normal → defensive
        if current == "normal":
            if pnl_6h <= -2 or consec_losses >= 3 or drawdown >= 1.5:
                return "defensive"

        # Rule 2: defensive → normal
        if current == "defensive":
            # Require positive conditions: low drawdown, decent win rate, few errors
            if win_rate >= 55 and drawdown < 1 and perf.get("api_error_count", 0) <= 2:
                return "normal"

        # Rule 3: normal → aggressive_trend (requires approval)
        if current == "normal" and regime == "trend":
            if win_rate >= 60 and perf.get("pnl_6h", 0) > 0:
                return "aggressive_trend"

        # Rule 4: aggressive → defensive if things go wrong
        if current == "aggressive_trend":
            if pnl_6h <= -2 or consec_losses >= 2 or drawdown >= 1.5:
                return "defensive"

        # Rule 5: volatile / defensive regime → defensive profile
        if regime in ("volatile", "defensive") and current != "defensive":
            return "defensive"

        return None

    def _build_reason(self, from_p: str, to_p: str, perf: dict, regime: str) -> str:
        parts = []
        if to_p == "defensive":
            if perf.get("pnl_6h", 0) <= -2:
                parts.append(f"PnL 6h = {perf['pnl_6h']:.2f}")
            if perf.get("consecutive_losses", 0) >= 3:
                parts.append(f"{perf['consecutive_losses']} consecutive losses")
            if perf.get("drawdown_intraday", 0) >= 1.5:
                parts.append(f"drawdown {perf['drawdown_intraday']:.2f}%")
            if regime in ("volatile", "defensive"):
                parts.append(f"regime={regime}")
        elif to_p == "normal":
            parts.append(f"WR={perf.get('win_rate_last_10', 0):.0f}% recovered")
        elif to_p == "aggressive_trend":
            parts.append(f"trend regime, WR={perf.get('win_rate_last_10', 0):.0f}%")
        return "; ".join(parts) if parts else f"{from_p} → {to_p}"

    def _can_switch(self) -> bool:
        """Check cooldown and daily limit."""
        now = datetime.now(timezone.utc)
        cooldown = self._switching_rules.get("cooldown_minutes", 60)
        hysteresis = self._switching_rules.get("hysteresis_minutes", 30)
        max_changes = self._switching_rules.get("max_profile_changes_per_day", 4)

        # Cooldown
        if self._last_switch_time:
            elapsed = (now - self._last_switch_time).total_seconds() / 60
            if elapsed < cooldown + hysteresis:
                return False

        # Daily limit
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_switches = [
            s for s in self._switch_history
            if datetime.fromisoformat(s["at"]) >= midnight
        ]
        if len(today_switches) >= max_changes:
            logger.info("Profile switch blocked: daily limit (%d/%d)", len(today_switches), max_changes)
            return False

        return True

    # ------------------------------------------------------------------
    # Apply profile
    # ------------------------------------------------------------------

    def apply_profile(self, profile_name: str, engine, reason: str = "") -> bool:
        """
        Apply a profile's parameters to the engine's risk manager and strategies.

        Args:
            profile_name: name of the profile to apply
            engine: TradingEngine instance
            reason: why the switch happened (for logging)

        Returns True if applied successfully.
        """
        profile = self._profiles.get(profile_name)
        if not profile:
            logger.error("Profile '%s' not found", profile_name)
            return False

        old = self._active_profile

        # Apply risk params
        risk = profile.get("risk", {})
        if risk:
            engine.risk_manager.set_params(risk)
            logger.info("PROFILE: risk params applied → %s", risk)

        # Apply strategy params
        strategies_cfg = profile.get("strategies", {})
        for strat in engine.strategies:
            if strat.name in strategies_cfg:
                strat.set_params(strategies_cfg[strat.name])
                logger.info("PROFILE: strategy '%s' params applied → %s",
                            strat.name, strategies_cfg[strat.name])

        # Update active profile
        self._active_profile = profile_name
        self._last_switch_time = datetime.now(timezone.utc)
        self._switch_history.append({
            "from": old,
            "to": profile_name,
            "at": self._last_switch_time.isoformat(),
            "reason": reason,
        })

        # Persist active profile to JSON
        self._save_active_profile()

        logger.info(
            "PROFILE_SWITCH: %s → %s (reason: %s)",
            old, profile_name, reason,
        )
        return True

    def _save_active_profile(self):
        """Persist active_profile to the JSON file."""
        try:
            self._data["active_profile"] = self._active_profile
            with open(self._profiles_path, "w") as f:
                json.dump(self._data, f, indent=2)
        except Exception:
            logger.exception("Failed to save active profile")
