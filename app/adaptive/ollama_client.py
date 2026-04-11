"""
Ollama local LLM client for the AI Tuning Advisor.

Calls the Ollama REST API (localhost:11434) with a structured prompt
containing bot metrics, guardrails config, and market regime data.
The model returns a JSON response with tuning suggestions.

Falls back gracefully if Ollama is not running or times out.

Setup on server:
  curl -fsSL https://ollama.com/install.sh | sh
  ollama pull mistral
  # or: ollama pull phi3
"""

import json
import logging

import httpx

logger = logging.getLogger(__name__)

DEFAULT_URL = "http://localhost:11434"
DEFAULT_MODEL = "mistral"
TIMEOUT_SECONDS = 90

# Safety caps enforced after LLM response (never trust raw LLM output)
SAFETY_LIMITS = {
    "min_adx": (10, 50),
    "min_volume_ratio": (0.3, 3.0),
    "min_bb_width_pct": (0.0, 5.0),
    "base_min_score": (65, 95),
    "max_score_cap": (80, 100),
}

# Max delta per change (LLM cannot suggest huge jumps)
MAX_DELTA = {
    "min_adx": 5,
    "min_volume_ratio": 0.5,
    "min_bb_width_pct": 0.5,
    "base_min_score": 5,
}

SYSTEM_PROMPT = """You are an expert crypto trading bot tuning advisor.
You analyze bot metrics and suggest SAFE, SMALL adjustments to guardrails parameters.

RULES YOU MUST FOLLOW:
- NEVER suggest changes when consecutive_losses >= 4 or drawdown >= 1.5%
- NEVER suggest large changes. Maximum adjustment: ADX +-4, volume +-0.4, score +-5
- ONLY suggest loosening when block_rate is high AND risk is low
- ONLY suggest tightening when losses are increasing
- If conditions are normal, respond with zero changes
- Each suggestion MUST have a clear reason
- You MUST respond with valid JSON only, no markdown, no explanation outside JSON"""

USER_PROMPT_TEMPLATE = """Analyze this trading bot state and suggest guardrails parameter adjustments.

## Current Metrics
- Global regime: {global_regime}
- Active profile: {active_profile}
- Win rate (last 10): {win_rate:.1f}%
- Consecutive losses: {consecutive_losses}
- Drawdown intraday: {drawdown:.2f}%
- PnL 24h: {pnl_24h:.2f} USDT
- Trades per hour: {trades_per_hour:.2f}
- Total blocked: {total_blocked}
- Total passed: {total_passed}
- Block rate: {block_rate:.0f}%
- Top block source: {top_block_source}

## Current Guardrails Config (trade_gate for {gate_regime} regime)
- min_adx: {current_adx}
- min_volume_ratio: {current_volume}
- min_bb_width_pct: {current_bb}

## Dynamic Score Config
- base_min_score: {current_base_score}
- max_score_cap: {current_max_cap}

## Per-Symbol Regimes
{symbol_regimes}

Respond with this exact JSON structure:
{{
  "changes": [
    {{"path": "trade_gate.{gate_regime}.min_adx", "from": {current_adx}, "to": <new_value>, "reason": "<why>"}},
  ],
  "reasoning": "<1-2 sentence overall explanation>",
  "confidence": <0.0 to 1.0>,
  "risk_level": "<low|medium|high>"
}}

If no changes needed, respond: {{"changes": [], "reasoning": "<why no changes>", "confidence": 0.0, "risk_level": "low"}}"""


async def check_ollama(url: str = DEFAULT_URL) -> bool:
    """Check if Ollama is running and reachable."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{url}/api/tags")
            return resp.status_code == 200
    except Exception:
        return False


async def get_available_models(url: str = DEFAULT_URL) -> list[str]:
    """List models available in Ollama."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{url}/api/tags")
            if resp.status_code == 200:
                data = resp.json()
                return [m["name"] for m in data.get("models", [])]
    except Exception:
        pass
    return []


async def generate_suggestions(
    perf: dict,
    guardrails_status: dict,
    guardrails_config: dict,
    regime_snapshot: dict,
    ollama_url: str = DEFAULT_URL,
    model: str = DEFAULT_MODEL,
) -> dict | None:
    """
    Call Ollama to generate tuning suggestions.
    Returns the parsed suggestion dict or None if Ollama is unavailable/fails.
    """
    # Build context
    stats = guardrails_status.get("stats", {})
    total_blocked = stats.get("total_blocked", 0)
    total_passed = stats.get("total_passed", 0)
    total = total_blocked + total_passed or 1
    block_rate = total_blocked / total * 100

    # Find top block source
    block_keys = [(k, v) for k, v in stats.items() if k.startswith("blocked_") and v > 0]
    top_block = max(block_keys, key=lambda x: x[1])[0].replace("blocked_", "") if block_keys else "none"

    global_regime = regime_snapshot.get("global_regime", "unknown")
    gate_regime = global_regime if global_regime in guardrails_config.get("trade_gate", {}) else "range"
    gate_cfg = guardrails_config.get("trade_gate", {}).get(gate_regime, {})
    ds_cfg = guardrails_config.get("dynamic_score", {})

    # Symbol regimes summary
    sym_lines = []
    for sym, snap in regime_snapshot.get("symbols", {}).items():
        sym_lines.append(f"  {sym}: {snap.get('regime', '?')} (ADX={snap.get('adx', 0):.1f}, Vol={snap.get('volume_ratio', 0):.1f})")
    symbol_regimes = "\n".join(sym_lines) if sym_lines else "  No symbol data available"

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
        symbol_regimes=symbol_regimes,
    )

    try:
        logger.info("OLLAMA: sending request to %s model=%s", ollama_url, model)
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            resp = await client.post(
                f"{ollama_url}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "system": SYSTEM_PROMPT,
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": 0.3, "num_predict": 512},
                },
            )
            if resp.status_code != 200:
                logger.warning("OLLAMA: HTTP %d — %s", resp.status_code, resp.text[:200])
                return None

            data = resp.json()
            raw_response = data.get("response", "")
            logger.info("OLLAMA: response received (%d chars, %.1fs)",
                        len(raw_response), data.get("total_duration", 0) / 1e9)

    except httpx.TimeoutException:
        logger.warning("OLLAMA: request timed out (%ds)", TIMEOUT_SECONDS)
        return None
    except Exception:
        logger.exception("OLLAMA: request failed")
        return None

    # Parse JSON response
    try:
        result = json.loads(raw_response)
    except json.JSONDecodeError:
        # Try to extract JSON from markdown fences
        import re
        m = re.search(r'\{.*\}', raw_response, re.DOTALL)
        if m:
            try:
                result = json.loads(m.group(0))
            except json.JSONDecodeError:
                logger.warning("OLLAMA: could not parse response as JSON")
                return None
        else:
            logger.warning("OLLAMA: no JSON found in response")
            return None

    # Validate and sanitize the response
    changes = result.get("changes", [])
    sanitized_changes = []
    for c in changes:
        if not isinstance(c, dict) or "path" not in c or "to" not in c:
            continue
        path = c["path"]
        new_val = c["to"]
        old_val = c.get("from")

        # Extract the field name for limit checking
        field = path.split(".")[-1] if "." in path else path

        # Enforce safety limits
        if field in SAFETY_LIMITS:
            lo, hi = SAFETY_LIMITS[field]
            if not isinstance(new_val, (int, float)):
                continue
            new_val = max(lo, min(hi, new_val))

        # Enforce max delta
        if field in MAX_DELTA and old_val is not None and isinstance(old_val, (int, float)):
            max_d = MAX_DELTA[field]
            if abs(new_val - old_val) > max_d:
                new_val = old_val + (max_d if new_val > old_val else -max_d)

        # Round to sensible precision
        if isinstance(new_val, float):
            new_val = round(new_val, 1)

        sanitized_changes.append({
            "path": path,
            "from": old_val,
            "to": new_val,
            "reason": c.get("reason", "Suggested by AI model"),
        })

    confidence = min(max(float(result.get("confidence", 0.5)), 0.0), 1.0)
    risk_level = result.get("risk_level", "medium")
    if risk_level not in ("low", "medium", "high"):
        risk_level = "medium"

    logger.info("OLLAMA: %d suggestions (sanitized from %d raw), confidence=%.0f%%, risk=%s",
                len(sanitized_changes), len(changes), confidence * 100, risk_level)

    return {
        "changes": sanitized_changes,
        "reasoning": result.get("reasoning", "AI-generated suggestion"),
        "confidence": confidence,
        "risk_level": risk_level,
    }
