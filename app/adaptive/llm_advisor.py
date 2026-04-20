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
# These are the EFFECTIVE limits applied in _rule_based_suggestions()
# They match the first argument of min() in each rule — kept explicit for clarity.
_MAX_ADX_DELTA = 3      # max ADX change per suggestion (was 4 but effective limit was 3)
_MAX_VOLUME_DELTA = 0.3  # max volume ratio change per suggestion (was 0.4 but effective was 0.3)
_MAX_SCORE_DELTA = 3    # max base_min_score change per suggestion (was 5 but effective was 3)

# Classify parameter direction — True if raising the value tightens (makes entries harder)
# For fields not listed, direction is inferred as ambiguous and changes are kept.
_TIGHTEN_ON_INCREASE = {
    "min_adx", "min_volume_ratio", "min_bb_width_pct",
    "base_min_score", "min_score_after_3_losses", "min_score_after_5_losses",
    "extra_score_in_bad_regime", "max_score_cap",
    "consecutive_losses_threshold",  # lower = earlier circuit break = tighter? Actually higher = looser. Higher threshold → tolerate more losses → looser
}
_LOOSEN_ON_INCREASE = {
    "max_entries_per_hour", "max_entries_per_symbol_per_candle",
    "max_position_pct", "pnl_24h_threshold",  # less negative = looser
}


def _is_tightening(change: dict) -> bool:
    """Return True if a change tightens constraints (makes entries harder / safer)."""
    path = change.get("path", "")
    field = path.split(".")[-1] if "." in path else path
    try:
        new_val = float(change.get("to", 0))
        old_val = float(change.get("from", 0)) if change.get("from") is not None else new_val
    except (TypeError, ValueError):
        return True  # keep if unclear — better safe
    delta = new_val - old_val
    if delta == 0:
        return True
    if field in _TIGHTEN_ON_INCREASE:
        return delta > 0
    if field in _LOOSEN_ON_INCREASE:
        return delta < 0
    # Unknown field → keep (don't filter) during tighten-only mode
    return True


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

    async def generate_tuning_suggestions(
        self,
        perf: dict,
        guardrails_status: dict,
        guardrails_config: dict,
        regime_snapshot: dict,
        news_sentiment: dict | None = None,
        strategy_params: dict | None = None,
    ) -> dict:
        """
        Generate guardrails tuning suggestions.

        Tries Ollama LLM first. Falls back to rule-based engine if Ollama
        is unavailable or returns an error. News sentiment is used as an
        additional safety factor when available.

        Returns:
            {
                "changes": [...],
                "reasoning": str,
                "confidence": float,
                "risk_level": "low"|"medium"|"high",
                "source": "ollama"|"rules"|"safety_gate",
                "news_sentiment": dict | None,
            }
        """
        from app.adaptive.deepseek_client import generate_suggestions as deepseek_generate
        from app.adaptive.ollama_client import generate_suggestions as ollama_generate, check_ollama
        from app.config import settings

        # Safety gate (applied regardless of source).
        # Two tiers:
        #   EXTREME: block ALL suggestions (wait for conditions to normalize)
        #   ELEVATED: allow LLM to run but keep only tightening suggestions
        consec = perf.get("consecutive_losses", 0)
        dd = perf.get("drawdown_intraday", 0)
        wr = perf.get("win_rate_last_10", 0)
        total_trades = perf.get("total_recent_trades", 0)
        sentiment_score = (news_sentiment or {}).get("score", 0)
        sentiment_available = bool(news_sentiment and news_sentiment.get("available"))

        # EXTREME: everything off
        if (consec >= 7
            or dd >= 3.0
            or (sentiment_available and sentiment_score < -0.5)
            or (total_trades >= 10 and wr <= 10)):
            return {
                "changes": [],
                "reasoning": (
                    f"No tuning: extreme risk (CL={consec}, DD={dd:.2f}%, WR={wr:.0f}%, "
                    f"sentiment={sentiment_score:.2f})."
                ),
                "confidence": 0.0,
                "risk_level": "high",
                "source": "safety_gate",
                "news_sentiment": news_sentiment,
            }

        # ELEVATED: allow LLM but restrict to tightening
        tighten_only = (
            consec >= 4
            or dd >= 1.5
            or (sentiment_available and sentiment_score < -0.3)
            or (total_trades >= 10 and wr <= 20)
        )

        llm_args = dict(perf=perf, guardrails_status=guardrails_status,
                        guardrails_config=guardrails_config, regime_snapshot=regime_snapshot,
                        news_sentiment=news_sentiment, strategy_params=strategy_params)

        def _apply_tighten_filter(result: dict) -> dict:
            """If risk is elevated, keep only tightening suggestions."""
            if not tighten_only or not result.get("changes"):
                return result
            kept = [c for c in result["changes"] if _is_tightening(c)]
            dropped = len(result["changes"]) - len(kept)
            result["changes"] = kept
            if dropped:
                result["reasoning"] = (
                    f"{result.get('reasoning','')} [tighten-only mode: "
                    f"{dropped} loosening suggestion(s) dropped]"
                ).strip()
                result["risk_level"] = "high"
            return result

        # 1. Try DeepSeek API first (fast, cheap, best quality)
        if settings.deepseek_api_key:
            try:
                result = await deepseek_generate(
                    **llm_args, api_key=settings.deepseek_api_key, model=settings.deepseek_model,
                )
                if result is not None:
                    result = _apply_tighten_filter(result)
                    result["source"] = "deepseek"
                    result["news_sentiment"] = news_sentiment
                    logger.info(
                        "TUNING_ADVISOR [deepseek]: %d suggestions | confidence=%.0f%% | risk=%s | sentiment=%.2f%s",
                        len(result["changes"]), result["confidence"] * 100, result["risk_level"],
                        sentiment_score, " [tighten-only]" if tighten_only else "",
                    )
                    return result
                logger.warning("TUNING_ADVISOR: DeepSeek returned None, trying Ollama")
            except Exception:
                logger.exception("TUNING_ADVISOR: DeepSeek failed, trying Ollama")

        # 2. Try Ollama local (slower, free, offline)
        ollama_available = await check_ollama(settings.ollama_url)
        if ollama_available:
            try:
                result = await ollama_generate(
                    **llm_args, ollama_url=settings.ollama_url, model=settings.ollama_model,
                )
                if result is not None:
                    result = _apply_tighten_filter(result)
                    result["source"] = "ollama"
                    result["news_sentiment"] = news_sentiment
                    logger.info(
                        "TUNING_ADVISOR [ollama]: %d suggestions | confidence=%.0f%% | risk=%s | sentiment=%.2f%s",
                        len(result["changes"]), result["confidence"] * 100, result["risk_level"],
                        sentiment_score, " [tighten-only]" if tighten_only else "",
                    )
                    return result
                logger.warning("TUNING_ADVISOR: Ollama returned None, falling back to rules")
            except Exception:
                logger.exception("TUNING_ADVISOR: Ollama failed, falling back to rules")

        # 3. Fallback: rule-based engine
        result = self._rule_based_suggestions(perf, guardrails_status, guardrails_config, regime_snapshot)
        result = _apply_tighten_filter(result)
        result["news_sentiment"] = news_sentiment

        if news_sentiment and news_sentiment.get("available") and sentiment_score < -0.1 and result["changes"]:
            result["confidence"] = max(result["confidence"] - 0.2, 0.1)
            result["reasoning"] += f" (confidence reduced: news sentiment {sentiment_score:.2f})"

        return result

    def _rule_based_suggestions(
        self, perf: dict, guardrails_status: dict,
        guardrails_config: dict, regime_snapshot: dict,
    ) -> dict:
        """Deterministic rule-based fallback when Ollama is not available."""
        changes: list[dict] = []
        reasons: list[str] = []

        consec = perf.get("consecutive_losses", 0)
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
        total = total_blocked + total_passed or 1
        block_pct = total_blocked / total * 100

        # Rule 1: High block rate + low trades → loosen ADX
        if block_pct > 70 and tph < 0.2 and blocked_gate > blocked_score and dd < 1.0:
            regime_key = global_regime if global_regime in tg else "range"
            current_adx = tg.get(regime_key, {}).get("min_adx", 25)
            new_adx = max(current_adx - _MAX_ADX_DELTA, 15)
            if new_adx < current_adx:
                changes.append({"path": f"trade_gate.{regime_key}.min_adx", "from": current_adx, "to": new_adx,
                                "reason": f"Block rate {block_pct:.0f}% dominated by trade_gate, trades/h={tph:.2f}"})
                reasons.append(f"ADX too strict for {regime_key} ({block_pct:.0f}% blocked)")
            current_vol = tg.get(regime_key, {}).get("min_volume_ratio", 1.0)
            if current_vol > 1.2:
                new_vol = round(max(current_vol - _MAX_VOLUME_DELTA, 0.5), 1)
                if new_vol < current_vol:
                    changes.append({"path": f"trade_gate.{regime_key}.min_volume_ratio", "from": current_vol, "to": new_vol,
                                    "reason": f"Volume threshold {current_vol} filtering valid signals"})

        # Rule 2: Score filter dominant → lower base_min_score
        if blocked_score > blocked_gate and blocked_score > 5 and dd < 1.0:
            current_score = ds.get("base_min_score", 80)
            new_score = max(current_score - _MAX_SCORE_DELTA, 70)
            if new_score < current_score:
                changes.append({"path": "dynamic_score.base_min_score", "from": current_score, "to": new_score,
                                "reason": f"Score blocked {blocked_score} signals, min={current_score}"})
                reasons.append(f"Score filter too aggressive ({blocked_score} blocked)")

        # Rule 3: Zero trades + signals available → loosen dominant blocker
        if tph == 0 and total_blocked > 10 and dd == 0 and consec <= 1:
            if blocked_gate > 0 and not any(c["path"].startswith("trade_gate") for c in changes):
                regime_key = global_regime if global_regime in tg else "range"
                current_adx = tg.get(regime_key, {}).get("min_adx", 25)
                new_adx = max(current_adx - min(2, _MAX_ADX_DELTA), 15)
                if new_adx < current_adx:
                    changes.append({"path": f"trade_gate.{regime_key}.min_adx", "from": current_adx, "to": new_adx,
                                    "reason": "Zero trades despite signals — bot too restrictive"})
                    reasons.append("Bot completely blocked")

        # Rule 4: Dynamic score min very high (counter-independent)
        # When consecutive losses push dynamic_score_min above base + extra,
        # but drawdown is manageable, the effective min may be too restrictive
        effective_min = guardrails_status.get("dynamic_score_min", 80)
        base_score = ds.get("base_min_score", 80)
        if effective_min >= 90 and dd < 1.0 and consec <= 3:
            # Losses are recovering but score threshold is still elevated from before
            # Suggest lowering base so the effective min drops proportionally
            new_base = max(base_score - 3, 70)
            if new_base < base_score and not any("base_min_score" in c["path"] for c in changes):
                changes.append({"path": "dynamic_score.base_min_score", "from": base_score, "to": new_base,
                                "reason": f"Effective min score {effective_min} very high, base={base_score}, lowering base to ease recovery"})
                reasons.append(f"Score threshold {effective_min} blocking most signals (base={base_score})")

        # Rule 5: Low trade frequency (counter-independent)
        # If trades_per_hour is very low and no specific blocker stands out,
        # check if trade_gate thresholds are high relative to current regime
        if tph < 0.1 and dd < 0.5 and not changes:
            regime_key = global_regime if global_regime in tg else "range"
            current_adx = tg.get(regime_key, {}).get("min_adx", 25)
            # Get average ADX from regime symbols
            symbols = regime_snapshot.get("symbols", {})
            adx_values = [s.get("adx", 0) for s in symbols.values() if s.get("regime") == "trend"]
            avg_adx = sum(adx_values) / len(adx_values) if adx_values else 0
            # If avg ADX of trending symbols is close to threshold, suggest loosening
            if avg_adx > 0 and avg_adx < current_adx + 3:
                new_adx = max(current_adx - 2, 15)
                if new_adx < current_adx:
                    changes.append({"path": f"trade_gate.{regime_key}.min_adx", "from": current_adx, "to": new_adx,
                                    "reason": f"Low trade freq ({tph:.2f}/h), avg ADX of trending symbols ({avg_adx:.1f}) near threshold ({current_adx})"})
                    reasons.append(f"ADX threshold ({current_adx}) too close to market avg ({avg_adx:.1f})")

        if not changes:
            return {"changes": [], "reasoning": "Current config appropriate for conditions.",
                    "confidence": 0.0, "risk_level": "low", "source": "rules"}

        confidence = 0.7 if dd < 0.5 and consec <= 1 else 0.5
        risk_level = "low" if dd < 0.5 and consec <= 1 else "medium"

        logger.info("TUNING_ADVISOR [rules]: %d suggestions | confidence=%.0f%% | risk=%s",
                     len(changes), confidence * 100, risk_level)

        return {"changes": changes, "reasoning": " ".join(reasons) or "Rule-based adjustments.",
                "confidence": confidence, "risk_level": risk_level, "source": "rules"}

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
