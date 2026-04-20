"""
DeepSeek API client for the AI Tuning Advisor.

Calls DeepSeek Chat API (OpenAI-compatible) with structured prompt
containing bot metrics and guardrails config. Returns JSON suggestions.

DeepSeek is excellent at numerical/financial reasoning and costs
~$0.002 per request (~$0.14/M input tokens, $0.28/M output tokens).

Setup:
  1. Get API key from https://platform.deepseek.com/
  2. Add to .env: DEEPSEEK_API_KEY=sk-...
"""

import json
import logging
import re
import time

import httpx

logger = logging.getLogger(__name__)

# Cache for check_deepseek() — avoids burning API credits on every page load
_deepseek_check_cache: tuple[bool, float] = (False, 0.0)
_DEEPSEEK_CHECK_TTL = 300  # 5 minutes

API_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-chat"
TIMEOUT_SECONDS = 30

# Same safety caps as ollama_client — never trust raw LLM output
SAFETY_LIMITS = {
    "min_adx": (10, 50),
    "min_volume_ratio": (0.3, 3.0),
    "min_bb_width_pct": (0.0, 5.0),
    "base_min_score": (65, 95),
    "max_score_cap": (80, 100),
}

MAX_DELTA = {
    "min_adx": 5,
    "min_volume_ratio": 0.5,
    "min_bb_width_pct": 0.5,
    "base_min_score": 5,
}

SYSTEM_PROMPT = """You are a parameter optimization advisor for a crypto trading bot.
You analyze bot performance metrics and suggest concrete, data-driven parameter adjustments.

RULES:
- ALWAYS suggest at least 1 change when win_rate < 35% OR block_rate > 60%, even if other metrics look stable
- NEVER suggest large changes — max adjustment is 15% of the current value per parameter
- When risk is elevated (consecutive_losses >= 4 or drawdown >= 1.5%), ONLY suggest tightening
- When block_rate is high (> 60%), prioritize loosening the dominant block reason threshold
- Consider news sentiment: bearish sentiment = no loosening of entry thresholds
- Each change MUST cite the specific metric that justifies it
- Respond with valid JSON only"""

USER_PROMPT_TEMPLATE = """You are a parameter optimization advisor for a crypto trading bot.
Your job is to suggest concrete, data-driven parameter adjustments — even when the bot is stable but underperforming.

## Performance Metrics
- Global regime: {global_regime} | Active profile: {active_profile}
- Win rate (last 10 trades): {win_rate:.1f}% — TARGET: >35%
- Consecutive losses: {consecutive_losses} | Intraday drawdown: {drawdown:.2f}%
- PnL 24h: {pnl_24h:.2f} USDT | Trades per hour: {trades_per_hour:.2f}
- Signals blocked: {total_blocked} | Passed: {total_passed} | Block rate: {block_rate:.0f}%
- Dominant block reason: {top_block_source}

## Current Trade Gate ({gate_regime} regime)
- min_adx: {current_adx} | min_volume_ratio: {current_volume} | min_bb_width_pct: {current_bb}
- base_min_score: {current_base_score} | max_score_cap: {current_max_cap}
- effective dynamic_score_min (after penalties): {effective_min_score}
- Blocked by dynamic_score: {blocked_score_count} | Blocked by trade_gate: {blocked_gate_count}

## Strategy Parameters (current values)
{strategy_params_section}

## Kill Switch & Guardrails State
{guardrails_section}

## Per-Symbol Market Data
{symbol_regimes}

## News Sentiment
{news_section}

## Instructions
- If win_rate < 35%: suggest loosening entry filters OR tightening exit thresholds to improve selectivity
- If block_rate > 60%: suggest relaxing the dominant block reason threshold by 10-15%
- If trades_per_hour < 0.5 in a TREND regime: the bot is too conservative — suggest specific relaxations
- If consecutive_losses >= 3: suggest tightening score thresholds or reducing position exposure
- Always suggest at least 1 change unless all metrics are at target AND block_rate < 30%
- Prefer small incremental changes (10-15% of current value), never suggest changes >30% of current value
- You may suggest changes to: trade_gate thresholds, dynamic_score thresholds, strategy score thresholds

Respond with exactly this JSON (changes array may contain 1-3 items):
{{
  "changes": [
    {{
      "path": "<full.parameter.path>",
      "from": <current_value>,
      "to": <new_value>,
      "reason": "<specific metric that justifies this change>"
    }}
  ],
  "reasoning": "<2-3 sentences: what is the main bottleneck and what do these changes address>",
  "confidence": <float 0.0 to 1.0>,
  "risk_level": "<low|medium|high>"
}}

Valid path examples:
- trade_gate.{gate_regime}.min_adx
- trade_gate.{gate_regime}.min_volume_ratio
- trade_gate.{gate_regime}.min_bb_width_pct
- dynamic_score.base_min_score
- dynamic_score.extra_score_in_bad_regime
- embient_enhanced.trend_buy_threshold
- embient_enhanced.range_buy_threshold
- embient_enhanced.range_sell_threshold"""


async def check_deepseek(api_key: str) -> bool:
    """Check if DeepSeek API key is configured and valid (cached for 5 min)."""
    global _deepseek_check_cache
    if not api_key:
        return False
    cached_ok, cached_ts = _deepseek_check_cache
    if time.time() - cached_ts < _DEEPSEEK_CHECK_TTL:
        return cached_ok
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.deepseek.com/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            result = resp.status_code == 200
    except Exception:
        result = False
    _deepseek_check_cache = (result, time.time())
    return result


async def generate_suggestions(
    perf: dict,
    guardrails_status: dict,
    guardrails_config: dict,
    regime_snapshot: dict,
    api_key: str,
    model: str = DEFAULT_MODEL,
    news_sentiment: dict | None = None,
    strategy_params: dict | None = None,
) -> dict | None:
    """
    Call DeepSeek API to generate tuning suggestions.
    Returns parsed suggestion dict or None on failure.
    """
    if not api_key:
        return None

    # Build context
    stats = guardrails_status.get("stats", {})
    total_blocked = stats.get("total_blocked", 0)
    total_passed = stats.get("total_passed", 0)
    total = total_blocked + total_passed or 1
    block_rate = total_blocked / total * 100

    block_keys = [(k, v) for k, v in stats.items() if k.startswith("blocked_") and v > 0]
    top_block = max(block_keys, key=lambda x: x[1])[0].replace("blocked_", "") if block_keys else "none"

    global_regime = regime_snapshot.get("global_regime", "unknown")
    gate_regime = global_regime if global_regime in guardrails_config.get("trade_gate", {}) else "range"
    gate_cfg = guardrails_config.get("trade_gate", {}).get(gate_regime, {})
    ds_cfg = guardrails_config.get("dynamic_score", {})

    # Symbol data
    sym_lines = []
    for sym, snap in regime_snapshot.get("symbols", {}).items():
        sym_lines.append(f"  {sym}: regime={snap.get('regime', '?')} ADX={snap.get('adx', 0):.1f} Vol={snap.get('volume_ratio', 0):.1f}")
    symbol_regimes = "\n".join(sym_lines) if sym_lines else "  No data"

    # News
    if news_sentiment and news_sentiment.get("available"):
        ns_lines = [f"Sentiment score: {news_sentiment.get('score', 0):.2f} ({news_sentiment.get('label', 'unknown')})"]
        for h in news_sentiment.get("top_headlines", [])[:3]:
            ns_lines.append(f'  "{h.get("title", "")}" (sentiment: {h.get("sentiment", 0):.2f})')
        news_section = "\n".join(ns_lines)
    else:
        news_section = "Not available"

    # Strategy parameters (embient thresholds, macd mode, etc.)
    if strategy_params:
        sp_lines = []
        for name, params in strategy_params.items():
            # Show only tunable numeric/string params, skip noise
            tunable = {k: v for k, v in params.items()
                       if not isinstance(v, bool) and k != "enabled"}
            sp_lines.append(f"- {name}: {json.dumps(tunable)}")
        strategy_params_section = "\n".join(sp_lines) if sp_lines else "  No data"
    else:
        strategy_params_section = "  Not provided"

    # Kill switch and risk state from guardrails_status + config
    ks_status = guardrails_status.get("kill_switch", {})
    ks_cfg = guardrails_config.get("kill_switch", {})
    guardrails_section = (
        f"- kill_switch: active={ks_status.get('active', False)} "
        f"reason={ks_status.get('reason', '—')} "
        f"consec_threshold={ks_cfg.get('consecutive_losses_threshold', 6)} "
        f"wr_threshold={ks_cfg.get('low_win_rate_threshold', 15)}%\n"
        f"- risk_multiplier: {guardrails_status.get('risk_multiplier', 1.0):.2f}\n"
        f"- dynamic_score_min (current): {guardrails_status.get('dynamic_score_min', 80)}\n"
        f"- symbol_cooldowns_active: {len(guardrails_status.get('symbol_cooldowns', {}))}"
    )

    prompt = USER_PROMPT_TEMPLATE.format(
        global_regime=global_regime,
        active_profile=perf.get("active_profile", "unknown"),
        win_rate=perf.get("win_rate_last_10", 0),
        consecutive_losses=perf.get("consecutive_losses", 0),
        drawdown=perf.get("drawdown_intraday", 0),
        pnl_24h=perf.get("pnl_24h", 0),
        trades_per_hour=perf.get("trades_per_hour", 0),
        total_blocked=total_blocked,
        total_passed=total_passed,
        block_rate=block_rate,
        top_block_source=top_block,
        gate_regime=gate_regime,
        current_adx=gate_cfg.get("min_adx", 25),
        current_volume=gate_cfg.get("min_volume_ratio", 1.0),
        current_bb=gate_cfg.get("min_bb_width_pct", 0),
        current_base_score=ds_cfg.get("base_min_score", 80),
        current_max_cap=ds_cfg.get("max_score_cap", 95),
        effective_min_score=guardrails_status.get("dynamic_score_min", 80),
        blocked_score_count=stats.get("blocked_dynamic_score", 0),
        blocked_gate_count=stats.get("blocked_trade_gate", 0),
        strategy_params_section=strategy_params_section,
        guardrails_section=guardrails_section,
        symbol_regimes=symbol_regimes,
        news_section=news_section,
    )

    try:
        logger.info("DEEPSEEK: sending request (model=%s)", model)
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            resp = await client.post(
                API_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 512,
                    "response_format": {"type": "json_object"},
                },
            )
            if resp.status_code != 200:
                logger.warning("DEEPSEEK: HTTP %d — %s", resp.status_code, resp.text[:300])
                return None

            data = resp.json()
            raw_response = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            logger.info("DEEPSEEK: response received (%d chars, in=%d out=%d tokens)",
                        len(raw_response), usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))

    except httpx.TimeoutException:
        logger.warning("DEEPSEEK: request timed out (%ds)", TIMEOUT_SECONDS)
        return None
    except Exception:
        logger.exception("DEEPSEEK: request failed")
        return None

    # Parse JSON
    try:
        result = json.loads(raw_response)
    except json.JSONDecodeError:
        m = re.search(r'\{.*\}', raw_response, re.DOTALL)
        if m:
            try:
                result = json.loads(m.group(0))
            except json.JSONDecodeError:
                logger.warning("DEEPSEEK: could not parse response as JSON")
                return None
        else:
            logger.warning("DEEPSEEK: no JSON found in response")
            return None

    # Sanitize (same logic as ollama_client)
    changes = result.get("changes", [])
    sanitized = []
    for c in changes:
        if not isinstance(c, dict) or "path" not in c or "to" not in c:
            continue
        path = c["path"]
        new_val = c["to"]
        old_val = c.get("from")
        field = path.split(".")[-1] if "." in path else path

        if field in SAFETY_LIMITS:
            lo, hi = SAFETY_LIMITS[field]
            if not isinstance(new_val, (int, float)):
                continue
            new_val = max(lo, min(hi, new_val))

        if field in MAX_DELTA and old_val is not None and isinstance(old_val, (int, float)):
            max_d = MAX_DELTA[field]
            if abs(new_val - old_val) > max_d:
                new_val = old_val + (max_d if new_val > old_val else -max_d)

        if isinstance(new_val, float):
            new_val = round(new_val, 1)

        sanitized.append({
            "path": path,
            "from": old_val,
            "to": new_val,
            "reason": c.get("reason", "Suggested by DeepSeek"),
        })

    confidence = min(max(float(result.get("confidence", 0.5)), 0.0), 1.0)
    risk_level = result.get("risk_level", "medium")
    if risk_level not in ("low", "medium", "high"):
        risk_level = "medium"

    logger.info("DEEPSEEK: %d suggestions (sanitized from %d raw), confidence=%.0f%%, risk=%s",
                len(sanitized), len(changes), confidence * 100, risk_level)

    return {
        "changes": sanitized,
        "reasoning": result.get("reasoning", "AI-generated suggestion (DeepSeek)"),
        "confidence": confidence,
        "risk_level": risk_level,
    }
