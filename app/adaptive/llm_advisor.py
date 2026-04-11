"""
LLM Advisor — read-only advisory module.

IMPORTANT: This module NEVER modifies parameters, places orders, or triggers
profile switches. It only reads state and produces structured suggestions.

Output:
  - explanation of current bot behavior
  - suggested profile (with reasoning)
  - summary of regime + performance context
  - guardrails tuning suggestions (V1)

Can be wired to an external LLM API or used as a local rule-based advisor.
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Maximum parameter adjustment per suggestion (safety cap)
_MAX_ADX_DELTA = 4
_MAX_VOLUME_DELTA = 0.4
_MAX_SCORE_DELTA = 5


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

    def generate_tuning_suggestions(
        self,
        perf: dict,
        guardrails_status: dict,
        guardrails_config: dict,
        regime_snapshot: dict,
    ) -> dict:
        """
        Generate guardrails tuning suggestions based on current state.

        Returns:
            {
                "changes": [{"path": str, "from": X, "to": Y, "reason": str}, ...],
                "reasoning": str,
                "confidence": float,  # 0.0-1.0
                "risk_level": "low"|"medium"|"high",
            }

        Rules are conservative: never suggest changes when risk is elevated.
        """
        changes: list[dict] = []
        reasons: list[str] = []

        consec = perf.get("consecutive_losses", 0)
        wr = perf.get("win_rate_last_10", 0)
        dd = perf.get("drawdown_intraday", 0)
        tph = perf.get("trades_per_hour", 0)
        global_regime = regime_snapshot.get("global_regime", "unknown")

        stats = guardrails_status.get("stats", {})
        total_blocked = stats.get("total_blocked", 0)
        total_passed = stats.get("total_passed", 0)
        blocked_gate = stats.get("blocked_trade_gate", 0)
        blocked_score = stats.get("blocked_dynamic_score", 0)

        tg = guardrails_config.get("trade_gate", {})
        ds = guardrails_config.get("dynamic_score", {})

        # Safety: never suggest loosening when risk is elevated
        if consec >= 4 or dd >= 1.5 or wr <= 20:
            return {
                "changes": [],
                "reasoning": f"No tuning suggested: risk elevated (CL={consec}, DD={dd:.2f}%, WR={wr:.0f}%).",
                "confidence": 0.0,
                "risk_level": "high",
            }

        total = total_blocked + total_passed or 1
        block_pct = total_blocked / total * 100

        # Rule 1: Too many trade_gate blocks + low trade frequency → loosen ADX
        if block_pct > 70 and tph < 0.2 and blocked_gate > blocked_score and dd < 1.0:
            regime_key = global_regime if global_regime in tg else "range"
            current_adx = tg.get(regime_key, {}).get("min_adx", 25)
            new_adx = max(current_adx - min(3, _MAX_ADX_DELTA), 15)
            if new_adx < current_adx:
                changes.append({
                    "path": f"trade_gate.{regime_key}.min_adx",
                    "from": current_adx, "to": new_adx,
                    "reason": f"Block rate {block_pct:.0f}% dominated by trade_gate, trades/h={tph:.2f}",
                })
                reasons.append(f"ADX threshold too strict for {regime_key} regime ({block_pct:.0f}% blocked)")

            # Also check volume if regime thresholds are high
            current_vol = tg.get(regime_key, {}).get("min_volume_ratio", 1.0)
            if current_vol > 1.2:
                new_vol = round(max(current_vol - min(0.3, _MAX_VOLUME_DELTA), 0.5), 1)
                if new_vol < current_vol:
                    changes.append({
                        "path": f"trade_gate.{regime_key}.min_volume_ratio",
                        "from": current_vol, "to": new_vol,
                        "reason": f"Volume threshold {current_vol} may be filtering valid signals",
                    })

        # Rule 2: Too many dynamic_score blocks → lower base_min_score slightly
        if blocked_score > blocked_gate and blocked_score > 5 and dd < 1.0:
            current_score = ds.get("base_min_score", 80)
            new_score = max(current_score - min(3, _MAX_SCORE_DELTA), 70)
            if new_score < current_score:
                changes.append({
                    "path": "dynamic_score.base_min_score",
                    "from": current_score, "to": new_score,
                    "reason": f"Dynamic score blocked {blocked_score} signals, current min={current_score}",
                })
                reasons.append(f"Score filter too aggressive (blocked {blocked_score} signals)")

        # Rule 3: Zero trades but signals generated → check overall gating
        if tph == 0 and total_blocked > 10 and dd == 0 and consec <= 1:
            # Suggest loosening the dominant blocker
            if blocked_gate > 0 and not any(c["path"].startswith("trade_gate") for c in changes):
                regime_key = global_regime if global_regime in tg else "range"
                current_adx = tg.get(regime_key, {}).get("min_adx", 25)
                new_adx = max(current_adx - 2, 15)
                if new_adx < current_adx:
                    changes.append({
                        "path": f"trade_gate.{regime_key}.min_adx",
                        "from": current_adx, "to": new_adx,
                        "reason": "Zero trades despite available signals — bot is too restrictive",
                    })
                    reasons.append("Bot completely blocked: zero trades executed")

        # Compute confidence and risk
        if not changes:
            return {
                "changes": [],
                "reasoning": "Current guardrails configuration appears appropriate for market conditions.",
                "confidence": 0.0,
                "risk_level": "low",
            }

        confidence = 0.7 if dd < 0.5 and consec <= 1 else 0.5
        risk_level = "low" if dd < 0.5 and consec <= 1 else "medium"
        reasoning = " ".join(reasons) if reasons else "Suggested adjustments based on guardrail block analysis."

        logger.info(
            "TUNING_ADVISOR: %d suggestions | confidence=%.0f%% | risk=%s | %s",
            len(changes), confidence * 100, risk_level,
            "; ".join(f"{c['path']}: {c['from']}→{c['to']}" for c in changes),
        )

        return {
            "changes": changes,
            "reasoning": reasoning,
            "confidence": confidence,
            "risk_level": risk_level,
        }

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
