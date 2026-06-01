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

ALLOWED_DYNAMIC_SCORE_PATHS = {
    "dynamic_score.base_min_score",
    "dynamic_score.min_score_after_3_losses",
    "dynamic_score.min_score_after_5_losses",
    "dynamic_score.extra_score_in_bad_regime",
    "dynamic_score.max_score_cap",
    "entry_throttle.max_open_positions",
    "performance_gate.symbol_max_recent_net_loss",
    "performance_gate.symbol_max_all_time_net_loss",
    "performance_gate.strategy_max_recent_net_loss",
    "paper_short.min_sell_score",
    "paper_short.max_open_shorts",
    "paper_short.allow_with_open_long",
    "stale_position.profit_lock_trigger_pct",
    "stale_position.profit_lock_min_pct",
    "stale_position.profit_trail_start_pct",
    "stale_position.profit_trail_distance_pct",
    "stale_position.range_profit_exit_enabled",
    "stale_position.range_profit_exit_min_pct",
    "stale_position.range_profit_exit_min_hours",
}


def _is_allowed_tuning_path(path: str) -> bool:
    if path in ALLOWED_DYNAMIC_SCORE_PATHS:
        return True
    if re.fullmatch(r"trade_gate\.(defensive|range|trend|volatile)\.(min_adx|min_volume_ratio|min_bb_width_pct)", path):
        return True
    if re.fullmatch(r"strategy\.[a-zA-Z0-9_]+\.enabled", path):
        return True
    return False

SYSTEM_PROMPT = """You are a parameter optimization advisor for a crypto trading bot.
You analyze bot performance metrics and suggest concrete, data-driven parameter adjustments.

RULES:
- Prefer no change over weak changes when sample size is too small or recent data is inconclusive
- NEVER suggest large changes — max adjustment is 15% of the current value per parameter
- When risk is elevated (consecutive_losses >= 4 or drawdown >= 1.5%), ONLY suggest tightening
- When block_rate is high (> 60%), prioritize loosening the dominant block reason threshold
- When an enabled strategy has at least 10 trades and negative estimated net PnL, prefer disabling it over loosening entries
- Do not suggest no-op changes where from == to
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

## Realized Performance Breakdown (fee/slippage adjusted estimate)
{performance_breakdown_section}

## Mark-to-Market & Open Positions
{mark_to_market_section}

## Opportunity Scanner
{opportunities_section}

## Recent Execution Diagnostics
{diagnostics_section}

## Kill Switch & Guardrails State
{guardrails_section}

## Per-Symbol Market Data
{symbol_regimes}

## News Sentiment
{news_section}

## Instructions
- Output language: {output_language}. All user-facing JSON strings MUST be in this language, especially "reasoning" and every "changes[].reason". Keep enum values and parameter paths unchanged.
- Use recent windows first (24h/7d), then all-time data as supporting evidence.
- Do not say the bot is "not trading" when open_positions > 0; distinguish realized closed trades from mark-to-market open PnL.
- If 24h closed trades are 0 but open mark-to-market PnL exists, analyze the open position and opportunity blockers instead of waiting passively.
- If recent closed trades < 5: do not loosen just because trades_per_hour is low.
- If win_rate < 35% with at least 10 recent trades: tighten selectivity via trade_gate or dynamic_score.
- If block_rate > 60% with at least 10 blocked/passed decisions and low drawdown: relax the dominant block reason threshold by 10-15%.
- If trades_per_hour < 0.5 in a TREND regime: only relax filters when blocked/passed sample size is meaningful.
- If consecutive_losses >= 3: tighten score thresholds.
- If an enabled strategy has >=10 trades and negative estimated_net_pnl: suggest strategy.<name>.enabled=false.
- You may return an empty changes array when data is insufficient or current config is appropriate.
- Prefer small incremental changes (10-15% of current value), never suggest changes >30% of current value
- You may suggest ONLY these path families: trade_gate.* thresholds, dynamic_score.* thresholds, performance_gate loss thresholds, entry_throttle.max_open_positions, paper_short min/open/allow flags, stale_position profit-lock/range-profit thresholds, strategy.<name>.enabled flags.
- Do not suggest direct strategy numeric thresholds such as embient_enhanced.trend_buy_threshold; they are not applyable through this endpoint.
- Do not suggest no-op changes where from == to.

Respond with exactly this JSON (changes array may contain 1-3 items):
{{
  "changes": [
    {{
      "path": "<full.parameter.path>",
      "from": <current_value>,
      "to": <new_value>,
      "reason": "<specific metric in {output_language} that justifies this change>"
    }}
  ],
  "reasoning": "<2-3 sentences in {output_language}: what is the main bottleneck and what do these changes address>",
  "confidence": <float 0.0 to 1.0>,
  "risk_level": "<low|medium|high>"
}}

Valid path examples:
- trade_gate.{gate_regime}.min_adx
- trade_gate.{gate_regime}.min_volume_ratio
- trade_gate.{gate_regime}.min_bb_width_pct
- dynamic_score.base_min_score
- dynamic_score.extra_score_in_bad_regime
- entry_throttle.max_open_positions
- performance_gate.strategy_max_recent_net_loss
- performance_gate.symbol_max_recent_net_loss
- paper_short.min_sell_score
- paper_short.max_open_shorts
- paper_short.allow_with_open_long
- stale_position.profit_lock_trigger_pct
- stale_position.profit_trail_distance_pct
- stale_position.range_profit_exit_min_pct
- stale_position.range_profit_exit_min_hours
- strategy.embient_enhanced.enabled
- strategy.sma_crossover.enabled
- strategy.rsi_reversal.enabled
- strategy.macd_crossover.enabled"""


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
    performance_breakdown: dict | None = None,
    output_language: str = "Italian",
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
            # Show tunable values plus enabled state so the advisor avoids stale disables.
            tunable = {k: v for k, v in params.items()
                       if k == "enabled" or not isinstance(v, bool)}
            sp_lines.append(f"- {name}: {json.dumps(tunable)}")
        strategy_params_section = "\n".join(sp_lines) if sp_lines else "  No data"
    else:
        strategy_params_section = "  Not provided"

    if performance_breakdown:
        lines = []
        windows = performance_breakdown.get("windows") or {"all_time": performance_breakdown}
        for label, breakdown in windows.items():
            overall = breakdown.get("overall", {})
            lines.append(
                f"{label} overall: trades={overall.get('trades', 0)} "
                f"gross={overall.get('gross_pnl', 0)} "
                f"est_cost={overall.get('estimated_roundtrip_cost', overall.get('estimated_roundtrip_fees', 0))} "
                f"est_net={overall.get('estimated_net_pnl', 0)} "
                f"win_rate={overall.get('win_rate', 0)}%"
            )
            for name, row in breakdown.get("by_strategy", {}).items():
                lines.append(
                    f"{label} strategy {name}: trades={row.get('trades', 0)} "
                    f"est_net={row.get('estimated_net_pnl', 0)} "
                    f"win_rate={row.get('win_rate', 0)}%"
                )
            for symbol, row in breakdown.get("by_symbol", {}).items():
                lines.append(
                    f"{label} symbol {symbol}: trades={row.get('trades', 0)} "
                    f"est_net={row.get('estimated_net_pnl', 0)} "
                    f"win_rate={row.get('win_rate', 0)}%"
                )
        performance_breakdown_section = "\n".join(lines)

        mtm_lines = []
        for label, row in (performance_breakdown.get("mark_to_market") or {}).items():
            realized = row.get("realized", {})
            mtm_lines.append(
                f"{label}: closed_trades={realized.get('trades', 0)} "
                f"realized_net={realized.get('estimated_net_pnl', 0)} "
                f"open_positions={row.get('open_positions', 0)} "
                f"open_exposure={row.get('open_exposure', 0)} "
                f"unrealized_net={row.get('unrealized_estimated_net_pnl', 0)} "
                f"total_net={row.get('total_estimated_net_pnl', 0)}"
            )
            for pos in row.get("positions", [])[:4]:
                mtm_lines.append(
                    f"  open {pos.get('symbol')} {pos.get('side')} age={pos.get('age_hours')}h "
                    f"entry={pos.get('entry_price')} current={pos.get('current_price')} "
                    f"net={pos.get('estimated_net_pnl')} pnl_pct={pos.get('pnl_pct')}"
                )
        mark_to_market_section = "\n".join(mtm_lines) if mtm_lines else "  Not provided"

        opp = performance_breakdown.get("opportunities") or {}
        opp_lines = [
            f"global_regime={opp.get('global_regime', 'unknown')} "
            f"attacks={opp.get('attack_count', 0)} watch={opp.get('watch_count', 0)} "
            f"open_positions={opp.get('open_position_count', 0)}"
        ]
        for row in opp.get("top", [])[:6]:
            opp_lines.append(
                f"  {row.get('symbol')} {row.get('side')} action={row.get('action')} "
                f"score={row.get('score')} active={row.get('active')} open={row.get('position_open')} "
                f"regime={row.get('regime')} adx={row.get('adx')} vol={row.get('volume_ratio')} "
                f"recent_net={row.get('recent_net_pnl')} blockers={','.join(str(b) for b in row.get('blockers', [])[:3])}"
            )
        opportunities_section = "\n".join(opp_lines)

        diag = performance_breakdown.get("diagnostics") or {}
        diagnostics_section = (
            f"events={diag.get('sampled_events', 0)} blocks={diag.get('blocks', 0)} "
            f"passes={diag.get('passes', 0)} fills={diag.get('fills', 0)}\n"
            f"block_sources={json.dumps(diag.get('block_sources', {}))}\n"
            f"block_reasons={json.dumps(diag.get('block_reasons', {}))}\n"
            f"blocked_symbols={json.dumps(diag.get('blocked_symbols', {}))}"
        )
    else:
        performance_breakdown_section = "  Not provided"
        mark_to_market_section = "  Not provided"
        opportunities_section = "  Not provided"
        diagnostics_section = "  Not provided"

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
        performance_breakdown_section=performance_breakdown_section,
        mark_to_market_section=mark_to_market_section,
        opportunities_section=opportunities_section,
        diagnostics_section=diagnostics_section,
        guardrails_section=guardrails_section,
        symbol_regimes=symbol_regimes,
        news_section=news_section,
        output_language=output_language,
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
                    "max_tokens": 768,
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
        if not _is_allowed_tuning_path(path):
            logger.info("DEEPSEEK: dropped unsupported tuning path: %s", path)
            continue

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

        if old_val == new_val:
            continue

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
