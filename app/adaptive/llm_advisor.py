"""
LLM Advisor — read-only advisory module.

IMPORTANT: This module NEVER modifies parameters, places orders, or triggers
profile switches. It only reads state and produces structured suggestions.

Output:
  - explanation of current bot behavior
  - suggested profile (with reasoning)
  - summary of regime + performance context

Can be wired to an external LLM API or used as a local rule-based advisor.
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class LLMAdvisor:
    """
    Read-only advisor that produces human-readable analysis.
    Does NOT execute any changes.
    """

    def __init__(self):
        self._last_advice: dict | None = None

    @property
    def last_advice(self) -> dict | None:
        return self._last_advice

    def analyze(
        self,
        regime_snapshot: dict,
        perf_snapshot: dict,
        active_profile: str,
        switch_history: list[dict],
    ) -> dict:
        """
        Analyze current state and produce advisory output.

        Returns:
            {
                "timestamp": str,
                "current_profile": str,
                "suggested_profile": str | None,
                "explanation": str,
                "reasoning": str,
                "confidence": float,  # 0.0 - 1.0
            }

        This output is purely informational. No side effects.
        """
        explanation = self._build_explanation(regime_snapshot, perf_snapshot, active_profile)
        suggestion = self._suggest_profile(regime_snapshot, perf_snapshot, active_profile)
        reasoning = self._build_reasoning(regime_snapshot, perf_snapshot, active_profile, suggestion)

        advice = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "current_profile": active_profile,
            "suggested_profile": suggestion["profile"],
            "explanation": explanation,
            "reasoning": reasoning,
            "confidence": suggestion["confidence"],
        }
        self._last_advice = advice

        logger.info(
            "LLM_ADVISOR: profile=%s suggested=%s (confidence=%.0f%%) — %s",
            active_profile, suggestion["profile"] or "no change",
            suggestion["confidence"] * 100, reasoning[:100],
        )
        return advice

    def _build_explanation(self, regime: dict, perf: dict, profile: str) -> str:
        """Human-readable explanation of current bot state."""
        global_regime = regime.get("global_regime", "unknown")
        pnl_6h = perf.get("pnl_6h", 0)
        win_rate = perf.get("win_rate_last_10", 0)
        drawdown = perf.get("drawdown_intraday", 0)
        consec = perf.get("consecutive_losses", 0)

        lines = [
            f"The bot is currently in <b>{profile}</b> profile.",
            f"Global market regime: <b>{global_regime}</b>.",
        ]

        if pnl_6h > 0:
            lines.append(f"Performance is positive (PnL 6h: +{pnl_6h:.2f} USDT).")
        elif pnl_6h < 0:
            lines.append(f"Performance is negative (PnL 6h: {pnl_6h:.2f} USDT).")
        else:
            lines.append("No recent PnL data.")

        if win_rate >= 60:
            lines.append(f"Win rate is strong at {win_rate:.0f}%.")
        elif win_rate < 40:
            lines.append(f"Win rate is low at {win_rate:.0f}%.")

        if consec >= 3:
            lines.append(f"Warning: {consec} consecutive losses.")

        if drawdown >= 1.5:
            lines.append(f"Intraday drawdown at {drawdown:.2f}% — elevated risk.")

        return " ".join(lines)

    def _suggest_profile(self, regime: dict, perf: dict, current: str) -> dict:
        """
        Rule-based profile suggestion. Returns {"profile": str|None, "confidence": float}.
        None means no change suggested.
        """
        pnl_6h = perf.get("pnl_6h", 0)
        consec = perf.get("consecutive_losses", 0)
        drawdown = perf.get("drawdown_intraday", 0)
        win_rate = perf.get("win_rate_last_10", 0)
        global_regime = regime.get("global_regime", "unknown")

        # Strong defensive signal
        if (pnl_6h <= -2 or consec >= 3 or drawdown >= 1.5) and current != "defensive":
            return {"profile": "defensive", "confidence": 0.9}

        # Recovery from defensive
        if current == "defensive" and win_rate >= 55 and drawdown < 1.0 and pnl_6h >= 0:
            return {"profile": "normal", "confidence": 0.7}

        # Aggressive opportunity
        if current == "normal" and global_regime == "trend" and win_rate >= 60 and pnl_6h > 0:
            return {"profile": "aggressive_trend", "confidence": 0.6}

        # Market turned volatile/defensive while aggressive
        if current == "aggressive_trend" and global_regime in ("volatile", "defensive"):
            return {"profile": "defensive", "confidence": 0.85}

        return {"profile": None, "confidence": 0.0}

    def _build_reasoning(self, regime: dict, perf: dict,
                         current: str, suggestion: dict) -> str:
        """Build reasoning text for the suggestion."""
        suggested = suggestion["profile"]
        if suggested is None:
            return f"Current profile ({current}) is appropriate for the observed conditions."

        global_regime = regime.get("global_regime", "unknown")
        parts = [f"Suggest switching from {current} to {suggested}."]

        if suggested == "defensive":
            triggers = []
            if perf.get("pnl_6h", 0) <= -2:
                triggers.append(f"PnL 6h={perf['pnl_6h']:.2f}")
            if perf.get("consecutive_losses", 0) >= 3:
                triggers.append(f"{perf['consecutive_losses']} consecutive losses")
            if perf.get("drawdown_intraday", 0) >= 1.5:
                triggers.append(f"drawdown={perf['drawdown_intraday']:.2f}%")
            if global_regime in ("volatile", "defensive"):
                triggers.append(f"regime={global_regime}")
            parts.append(f"Triggers: {', '.join(triggers)}.")

        elif suggested == "normal":
            parts.append(
                f"Recovery indicators positive: WR={perf.get('win_rate_last_10', 0):.0f}%, "
                f"drawdown={perf.get('drawdown_intraday', 0):.2f}%."
            )

        elif suggested == "aggressive_trend":
            parts.append(
                f"Market trending ({global_regime}), "
                f"WR={perf.get('win_rate_last_10', 0):.0f}%, "
                f"positive momentum."
            )

        return " ".join(parts)
