"""
FastAPI routes for the trading bot web application.
All routes (except /api/login) require JWT authentication.
"""

import logging
from collections import Counter, defaultdict
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.trade import Trade, Order, TradeStatus, OrderSide
from app.models.portfolio import PaperPortfolio, PaperPosition
from app.api.schemas import (
    BalanceResponse, PositionResponse,
    OrderResponse, TradeResponse, StrategyInfo, StrategyUpdate,
    RiskParams, SignalResponse, PriceResponse,
)
from app.api.auth import (
    LoginRequest, TokenResponse, UserCreate, UserUpdate, UserInfo,
    create_token, require_auth, require_admin, require_write, COOKIE_NAME,
)
from app.models.user import User, verify_password, hash_password
from app.config import settings
from app.strategy_store import save_strategy_params, save_risk_params
from app.adaptive.guardrails_validation import validate_guardrails_values, diff_configs
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

_engine = None
_skills_library = None
_meta_controller = None
DEFAULT_SYMBOL_CANDIDATES = {"BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "SOLUSDT", "LTCUSDT"}


def set_engine(engine, skills_library=None, meta_controller=None):
    global _engine, _skills_library, _meta_controller
    _engine = engine
    _skills_library = skills_library
    _meta_controller = meta_controller


def get_engine():
    if _engine is None:
        raise HTTPException(500, "Trading engine not initialized")
    return _engine


def get_meta_controller():
    if _meta_controller is None:
        raise HTTPException(500, "MetaController not initialized")
    return _meta_controller


def _guardrails_config_paths() -> tuple[str, str]:
    import os
    config_dir = os.path.join(os.path.dirname(__file__), "..", "..", "config")
    return (
        os.path.join(config_dir, "guardrails.local.json"),
        os.path.join(config_dir, "guardrails.json"),
    )


def _deep_merge_config(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_config(merged[key], value)
        else:
            merged[key] = value
    return merged


def _read_guardrails_config() -> dict:
    import json as _json
    import os
    local_path, default_path = _guardrails_config_paths()
    with open(default_path, "r") as f:
        default_cfg = _json.load(f)
    if not os.path.exists(local_path):
        return default_cfg
    with open(local_path, "r") as f:
        local_cfg = _json.load(f)
    return _deep_merge_config(default_cfg, local_cfg)


def _write_guardrails_config(config: dict):
    import json as _json
    import os
    import tempfile
    local_path, _default_path = _guardrails_config_paths()
    config_dir = os.path.dirname(local_path)
    fd, tmp_path = tempfile.mkstemp(dir=config_dir, suffix=".json")
    with os.fdopen(fd, "w") as f:
        _json.dump(config, f, indent=2)
    os.replace(tmp_path, local_path)


def _paper_cost_estimate(trade: Trade) -> dict:
    entry_notional = (trade.entry_price or 0) * (trade.quantity or 0)
    # Prefer the values stored at close (single source of truth — app.pnl). trade.pnl
    # is already NET; only fall back to a post-hoc estimate for legacy rows that
    # predate the gross_pnl/fee/slippage columns (avoids double-counting fees).
    if trade.fee is not None and trade.slippage is not None:
        fees = trade.fee or 0.0
        slippage = trade.slippage or 0.0
        net = trade.pnl
    else:
        exit_price = trade.exit_price if trade.exit_price is not None else trade.entry_price
        exit_notional = (exit_price or 0) * (trade.quantity or 0)
        fees = (entry_notional + exit_notional) * (settings.paper_fee_pct / 100)
        slippage = (entry_notional + exit_notional) * (settings.paper_slippage_pct / 100)
        net = trade.pnl - fees - slippage if trade.pnl is not None else None
    basis = entry_notional
    return {
        "estimated_roundtrip_fee": round(fees, 6),
        "estimated_roundtrip_slippage": round(slippage, 6),
        "estimated_roundtrip_cost": round(fees + slippage, 6),
        "estimated_net_pnl": round(net, 6) if net is not None else None,
        "estimated_net_pnl_pct": round(net / basis * 100, 4) if net is not None and basis else None,
    }


def _summarize_trades(group: list[Trade]) -> dict:
    # trade.pnl is NET (after fees+slippage); gross_pnl column holds the price-only
    # PnL. Win/loss is judged on NET — the honest measure. No fee re-subtraction.
    def _gross(t: Trade) -> float:
        return t.gross_pnl if t.gross_pnl is not None else (t.pnl or 0)
    gross = sum(_gross(t) for t in group)
    net = sum(t.pnl or 0 for t in group)
    wins = sum(1 for t in group if (t.pnl or 0) > 0)
    losses = sum(1 for t in group if (t.pnl or 0) < 0)
    costs = [_paper_cost_estimate(t) for t in group]
    estimated_fees = sum(c["estimated_roundtrip_fee"] for c in costs)
    estimated_slippage = sum(c["estimated_roundtrip_slippage"] for c in costs)
    estimated_cost = estimated_fees + estimated_slippage
    avg_pct = sum(t.pnl_pct or 0 for t in group) / len(group) if group else 0
    return {
        "trades": len(group),
        "gross_pnl": round(gross, 6),
        "net_pnl": round(net, 6),
        "estimated_roundtrip_fees": round(estimated_fees, 6),
        "estimated_roundtrip_slippage": round(estimated_slippage, 6),
        "estimated_roundtrip_cost": round(estimated_cost, 6),
        "estimated_net_pnl": round(net, 6),
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / len(group) * 100, 2) if group else 0,
        "avg_pnl_pct": round(avg_pct, 4),
    }


def _performance_breakdown_for_user(
    db: Session,
    user: User,
    *,
    since: datetime | None = None,
    hours: int | None = None,
) -> dict:
    user_mode = user.trading_mode or "paper"
    cutoff = since
    if hours is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    q = db.query(Trade).filter(
        Trade.user_id == user.id,
        Trade.mode == user_mode,
        Trade.status == TradeStatus.CLOSED,
    )
    if cutoff is not None:
        if cutoff.tzinfo is not None:
            cutoff = cutoff.astimezone(timezone.utc).replace(tzinfo=None)
        q = q.filter(Trade.closed_at >= cutoff)
    trades = q.all()
    by_strategy: dict[str, list[Trade]] = defaultdict(list)
    by_symbol: dict[str, list[Trade]] = defaultdict(list)
    for trade in trades:
        by_strategy[trade.strategy or "unknown"].append(trade)
        by_symbol[trade.symbol].append(trade)

    return {
        "mode": user_mode,
        "fee_pct_per_side": settings.paper_fee_pct,
        "slippage_pct_per_side": settings.paper_slippage_pct,
        "since": cutoff.isoformat() if cutoff else None,
        "overall": _summarize_trades(trades),
        "by_strategy": {
            key: _summarize_trades(group)
            for key, group in sorted(by_strategy.items())
        },
        "by_symbol": {
            key: _summarize_trades(group)
            for key, group in sorted(by_symbol.items())
        },
    }


def _symbol_candidate_status(active: bool, snap: dict | None, perf: dict | None) -> tuple[str, str]:
    if active:
        return "active", "Currently enabled"
    if snap is None:
        return "unknown", "Market analysis unavailable"

    regime = str(snap.get("regime") or "unknown")
    adx = float(snap.get("adx") or 0)
    volume_ratio = float(snap.get("volume_ratio") or 0)
    atr_pct = float(snap.get("atr_pct") or 0)
    trades = int((perf or {}).get("trades") or 0)
    est_net = (perf or {}).get("estimated_net_pnl")

    market_ok = regime == "trend" and adx >= 25 and volume_ratio >= 1.0 and atr_pct < 3.0
    reason = f"{regime} regime, ADX {adx:.1f}, volume {volume_ratio:.2f}x, ATR {atr_pct:.2f}%"
    if market_ok:
        if trades >= 10 and est_net is not None and float(est_net) < -3:
            return "watch", f"{reason}; historical net PnL remains weak"
        return "candidate", reason
    return "not_ready", reason


def _score_long_opportunity(
    *,
    symbol: str,
    active: bool,
    position_open: bool,
    snap: dict | None,
    perf_24h: dict | None,
    perf_7d: dict | None,
    perf_all_time: dict | None,
    news: dict | None,
) -> dict:
    score = 0.0
    reasons: list[str] = []
    blockers: list[str] = []

    regime = str((snap or {}).get("regime") or "unknown")
    adx = float((snap or {}).get("adx") or 0)
    volume_ratio = float((snap or {}).get("volume_ratio") or 0)
    atr_pct = float((snap or {}).get("atr_pct") or 0)
    bb_width_pct = float((snap or {}).get("bb_width_pct") or 0)

    if active:
        score += 8
    else:
        blockers.append("symbol_not_enabled")
    if position_open:
        blockers.append("position_already_open")

    if regime == "trend":
        score += 24
        reasons.append("trend regime")
    elif regime == "range":
        score += 8
        reasons.append("range regime: reversal only")
    elif regime == "defensive":
        score -= 10
        blockers.append("defensive_symbol_regime")
    else:
        blockers.append("regime_unknown")

    if adx >= 50:
        score += 20
        reasons.append(f"strong ADX {adx:.1f}")
    elif adx >= 30:
        score += 14
        reasons.append(f"valid ADX {adx:.1f}")
    elif adx >= 20:
        score += 5
    else:
        blockers.append(f"weak_adx {adx:.1f}")

    if volume_ratio >= 1.5:
        score += 18
        reasons.append(f"volume expansion {volume_ratio:.2f}x")
    elif volume_ratio >= 1.0:
        score += 10
        reasons.append(f"healthy volume {volume_ratio:.2f}x")
    elif volume_ratio >= 0.75:
        score += 2
        reasons.append(f"volume still below breakout level {volume_ratio:.2f}x")
    else:
        score -= 10
        blockers.append(f"low_volume {volume_ratio:.2f}x")

    if 0.25 <= atr_pct <= 1.8:
        score += 8
        reasons.append(f"tradable ATR {atr_pct:.2f}%")
    elif atr_pct > 2.5:
        score -= 6
        blockers.append(f"excessive_atr {atr_pct:.2f}%")

    if bb_width_pct >= 3:
        score += 8
        reasons.append(f"wide bands {bb_width_pct:.2f}%")
    elif bb_width_pct < 0.8:
        score -= 5
        blockers.append(f"compressed_bands {bb_width_pct:.2f}%")

    recent = perf_7d or {}
    recent_trades = int(recent.get("trades") or 0)
    recent_net = float(recent.get("estimated_net_pnl") or 0)
    recent_wr = float(recent.get("win_rate") or 0)
    if recent_trades >= 2:
        if recent_net > 0:
            score += min(14, 6 + recent_net)
            reasons.append(f"7d net positive {recent_net:.2f}")
        elif recent_net <= -3:
            score -= 14
            blockers.append(f"7d net weak {recent_net:.2f}")
        if recent_wr >= 50:
            score += 6
    else:
        reasons.append("limited recent symbol history")

    all_time = perf_all_time or {}
    all_trades = int(all_time.get("trades") or 0)
    all_net = float(all_time.get("estimated_net_pnl") or 0)
    if all_trades >= 10 and all_net <= -10:
        score -= 14
        blockers.append(f"all_time_net_weak {all_net:.2f}")
    elif all_trades >= 10 and all_net > 0:
        score += 5

    fear_greed = int((news or {}).get("fear_greed_value") or 50)
    news_score = float((news or {}).get("score") or 0)
    news_label = str((news or {}).get("label") or "unknown")
    if news_score > 0.15 and fear_greed >= 35:
        score += 10
        reasons.append("supportive news sentiment")
    elif news_score < -0.1 and fear_greed < 30:
        score -= 10
        blockers.append(f"macro_fear {news_label} F&G={fear_greed}")
        if regime == "trend" and adx >= 45 and volume_ratio >= 1.2:
            score += 8
            reasons.append("fear-market momentum candidate")

    score = max(0, min(100, round(score, 1)))
    if position_open:
        action = "HOLD_MANAGE"
        setup = "manage_open_position"
    elif score >= 75 and not blockers:
        action = "ATTACK"
        setup = "momentum_breakout_long"
    elif score >= 62 and len(blockers) <= 1:
        action = "WATCH_BREAKOUT"
        setup = "breakout_watch"
    elif score >= 48:
        action = "WATCH_REVERSAL"
        setup = "reversal_watch"
    else:
        action = "AVOID"
        setup = "no_edge"

    return {
        "symbol": symbol,
        "side": "LONG",
        "score": score,
        "action": action,
        "setup": setup,
        "active": active,
        "position_open": position_open,
        "regime": regime,
        "adx": round(adx, 2),
        "volume_ratio": round(volume_ratio, 2),
        "atr_pct": round(atr_pct, 3),
        "bb_width_pct": round(bb_width_pct, 3),
        "recent_net_pnl": round(recent_net, 6),
        "recent_win_rate": round(recent_wr, 2),
        "all_time_net_pnl": round(all_net, 6),
        "reasons": reasons[:5],
        "blockers": blockers[:5],
    }


def _score_short_opportunity(
    *,
    symbol: str,
    active: bool,
    position_open: bool,
    snap: dict | None,
    perf_24h: dict | None,
    perf_7d: dict | None,
    perf_all_time: dict | None,
    news: dict | None,
) -> dict:
    score = 0.0
    reasons: list[str] = []
    blockers: list[str] = []

    regime = str((snap or {}).get("regime") or "unknown")
    adx = float((snap or {}).get("adx") or 0)
    volume_ratio = float((snap or {}).get("volume_ratio") or 0)
    atr_pct = float((snap or {}).get("atr_pct") or 0)
    bb_width_pct = float((snap or {}).get("bb_width_pct") or 0)

    if active:
        score += 8
    else:
        blockers.append("symbol_not_enabled")
    if position_open:
        blockers.append("position_already_open")

    if regime == "trend":
        score += 20
        reasons.append("trend regime supports directional short test")
    elif regime == "defensive":
        score += 14
        reasons.append("defensive regime")
    elif regime == "range":
        score += 4
        reasons.append("range regime: short only at extremes")
    else:
        blockers.append("regime_unknown")

    if adx >= 45:
        score += 20
        reasons.append(f"strong downside-momentum ADX {adx:.1f}")
    elif adx >= 30:
        score += 14
        reasons.append(f"tradable ADX {adx:.1f}")
    elif adx >= 20:
        score += 5
    else:
        blockers.append(f"weak_adx {adx:.1f}")

    if volume_ratio >= 1.4:
        score += 18
        reasons.append(f"volume expansion {volume_ratio:.2f}x")
    elif volume_ratio >= 1.0:
        score += 10
        reasons.append(f"healthy volume {volume_ratio:.2f}x")
    elif volume_ratio < 0.7:
        score -= 8
        blockers.append(f"low_volume {volume_ratio:.2f}x")

    if 0.3 <= atr_pct <= 2.5:
        score += 8
        reasons.append(f"tradable ATR {atr_pct:.2f}%")
    elif atr_pct > 3.2:
        score -= 8
        blockers.append(f"excessive_atr {atr_pct:.2f}%")

    if bb_width_pct >= 2.5:
        score += 6
        reasons.append(f"wide bands {bb_width_pct:.2f}%")

    recent = perf_7d or {}
    recent_trades = int(recent.get("trades") or 0)
    recent_net = float(recent.get("estimated_net_pnl") or 0)
    recent_wr = float(recent.get("win_rate") or 0)
    if recent_trades >= 2 and recent_net <= -3:
        score += min(16, 6 + abs(recent_net))
        reasons.append(f"long edge failed 7d {recent_net:.2f}")
    elif recent_trades >= 2 and recent_net > 2:
        score -= 10
        blockers.append(f"long edge still works {recent_net:.2f}")

    all_time = perf_all_time or {}
    all_trades = int(all_time.get("trades") or 0)
    all_net = float(all_time.get("estimated_net_pnl") or 0)
    if all_trades >= 10 and all_net <= -10:
        score += 8
        reasons.append(f"historical long weakness {all_net:.2f}")

    fear_greed = int((news or {}).get("fear_greed_value") or 50)
    news_score = float((news or {}).get("score") or 0)
    news_label = str((news or {}).get("label") or "unknown")
    if news_score < -0.08 or fear_greed < 30:
        score += 18
        reasons.append(f"bearish macro {news_label} F&G={fear_greed}")
    elif news_score > 0.12 and fear_greed >= 45:
        score -= 12
        blockers.append("supportive_macro")

    score = max(0, min(100, round(score, 1)))
    if position_open:
        action = "HOLD_MANAGE"
        setup = "manage_open_position"
    elif score >= 75 and len(blockers) <= 1:
        action = "SHORT_ATTACK"
        setup = "paper_momentum_short"
    elif score >= 58 and len(blockers) <= 2:
        action = "SHORT_WATCH"
        setup = "paper_short_watch"
    else:
        action = "AVOID"
        setup = "no_short_edge"

    return {
        "symbol": symbol,
        "side": "SHORT",
        "score": score,
        "action": action,
        "setup": setup,
        "active": active,
        "position_open": position_open,
        "regime": regime,
        "adx": round(adx, 2),
        "volume_ratio": round(volume_ratio, 2),
        "atr_pct": round(atr_pct, 3),
        "bb_width_pct": round(bb_width_pct, 3),
        "recent_net_pnl": round(recent_net, 6),
        "recent_win_rate": round(recent_wr, 2),
        "all_time_net_pnl": round(all_net, 6),
        "reasons": reasons[:5],
        "blockers": blockers[:5],
    }


def _mark_to_market_for_user(db: Session, user: User, hours: int | None = None) -> dict:
    engine = get_engine()
    user_mode = user.trading_mode or "paper"
    realized = _performance_breakdown_for_user(db, user, hours=hours)
    open_trades = db.query(Trade).filter(
        Trade.user_id == user.id,
        Trade.mode == user_mode,
        Trade.status == TradeStatus.OPEN,
    ).all()

    positions = []
    unrealized_gross = 0.0
    unrealized_cost = 0.0
    exposure = 0.0
    now = datetime.now(timezone.utc)
    for trade in open_trades:
        current_price = engine.last_prices.get(trade.symbol) or trade.entry_price or 0
        entry_notional = (trade.entry_price or 0) * (trade.quantity or 0)
        exit_notional = current_price * (trade.quantity or 0)
        if trade.side == OrderSide.SELL:
            gross = ((trade.entry_price or current_price) - current_price) * (trade.quantity or 0)
        else:
            gross = (current_price - (trade.entry_price or current_price)) * (trade.quantity or 0)
        fees = (entry_notional + exit_notional) * (settings.paper_fee_pct / 100)
        slippage = (entry_notional + exit_notional) * (settings.paper_slippage_pct / 100)
        cost = fees + slippage
        net = gross - cost
        age_hours = 0.0
        if trade.opened_at:
            opened = trade.opened_at
            if opened.tzinfo is None:
                opened = opened.replace(tzinfo=timezone.utc)
            age_hours = max(0.0, (now - opened).total_seconds() / 3600)
        unrealized_gross += gross
        unrealized_cost += cost
        exposure += exit_notional
        positions.append({
            "id": trade.id,
            "symbol": trade.symbol,
            "strategy": trade.strategy,
            "entry_price": trade.entry_price,
            "current_price": current_price,
            "quantity": trade.quantity,
            "side": trade.side.value,
            "age_hours": round(age_hours, 2),
            "gross_pnl": round(gross, 6),
            "estimated_roundtrip_cost": round(cost, 6),
            "estimated_net_pnl": round(net, 6),
            "pnl_pct": round((gross / entry_notional * 100), 4) if entry_notional else 0,
            "opened_at": trade.opened_at,
        })

    realized_net = realized["overall"]["estimated_net_pnl"]
    unrealized_net = unrealized_gross - unrealized_cost
    return {
        "mode": user_mode,
        "since": realized.get("since"),
        "realized": realized["overall"],
        "open_positions": len(open_trades),
        "open_exposure": round(exposure, 6),
        "unrealized_gross_pnl": round(unrealized_gross, 6),
        "unrealized_estimated_cost": round(unrealized_cost, 6),
        "unrealized_estimated_net_pnl": round(unrealized_net, 6),
        "total_estimated_net_pnl": round(realized_net + unrealized_net, 6),
        "positions": positions,
    }


def _recent_diagnostics_context(lines: int = 3000) -> dict:
    import os

    events: list[dict] = []
    log_file = "logs/trading_bot.log"
    if os.path.exists(log_file):
        try:
            with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
            for line in all_lines[-lines:]:
                ev = _parse_log_line(line)
                if ev:
                    events.append(ev)
        except Exception:
            events = []

    block_events = [ev for ev in events if ev.get("type") == "block"]
    pass_events = [ev for ev in events if ev.get("type") == "pass"]
    fill_events = [ev for ev in events if ev.get("type") == "fill"]
    return {
        "sampled_events": len(events),
        "blocks": len(block_events),
        "passes": len(pass_events),
        "fills": len(fill_events),
        "block_sources": dict(Counter(ev.get("source", "unknown") for ev in block_events)),
        "block_reasons": dict(Counter(str(ev.get("reason", "unknown")) for ev in block_events).most_common(8)),
        "blocked_symbols": dict(Counter(ev.get("symbol", "") for ev in block_events if ev.get("symbol")).most_common(8)),
    }


def _top_opportunities_context(db: Session, user: User, limit: int = 6) -> dict:
    engine = get_engine()
    user_mode = user.trading_mode or "paper"
    perf_24h = _performance_breakdown_for_user(db, user, hours=24)
    perf_7d = _performance_breakdown_for_user(db, user, hours=24 * 7)
    perf_all = _performance_breakdown_for_user(db, user)
    active_symbols = set(getattr(engine, "symbols", []))
    symbols = sorted(DEFAULT_SYMBOL_CANDIDATES | active_symbols | set(perf_all.get("by_symbol", {}).keys()))
    open_rows = db.query(Trade.symbol, Trade.side).filter(
        Trade.user_id == user.id,
        Trade.mode == user_mode,
        Trade.status == TradeStatus.OPEN,
    ).all()
    open_symbols = {symbol for symbol, _side in open_rows}
    open_sides = {symbol: side for symbol, side in open_rows}

    live_snapshots: dict[str, dict] = {}
    global_regime = "unknown"
    news_snapshot: dict | None = None
    try:
        mc = get_meta_controller()
        regime_snapshot = mc.regime_service.global_snapshot()
        live_snapshots = regime_snapshot.get("symbols", {})
        global_regime = regime_snapshot.get("global_regime", "unknown")
        snap_obj = getattr(mc.news_sentiment, "snapshot", None)
        if snap_obj is not None:
            news_snapshot = snap_obj.to_dict()
    except HTTPException:
        pass

    rows = []
    for symbol in symbols:
        snap = live_snapshots.get(symbol)
        common = dict(
            symbol=symbol,
            active=symbol in active_symbols,
            position_open=symbol in open_symbols,
            snap=snap,
            perf_24h=perf_24h.get("by_symbol", {}).get(symbol),
            perf_7d=perf_7d.get("by_symbol", {}).get(symbol),
            perf_all_time=perf_all.get("by_symbol", {}).get(symbol),
            news=news_snapshot,
        )
        long_row = _score_long_opportunity(**common)
        short_row = _score_short_opportunity(**common)
        if symbol in open_sides:
            row = short_row if open_sides[symbol] == OrderSide.SELL else long_row
        else:
            row = short_row if short_row["score"] > long_row["score"] else long_row
        row["last_price"] = round(engine.last_prices.get(symbol) or 0, 8)
        rows.append(row)

    rows.sort(key=lambda x: (x["score"], x["active"]), reverse=True)
    return {
        "global_regime": global_regime,
        "top": rows[:limit],
        "attack_count": sum(1 for row in rows if row["action"] in {"ATTACK", "SHORT_ATTACK"}),
        "watch_count": sum(1 for row in rows if row["action"].startswith("WATCH")),
        "open_position_count": sum(1 for row in rows if row["position_open"]),
    }


def _tuning_performance_context(
    db: Session,
    user: User,
    *,
    opportunities_context: dict | None = None,
    diagnostics_context: dict | None = None,
) -> dict:
    return {
        "mode": user.trading_mode or "paper",
        "fee_pct_per_side": settings.paper_fee_pct,
        "slippage_pct_per_side": settings.paper_slippage_pct,
        "windows": {
            "recent_24h": _performance_breakdown_for_user(db, user, hours=24),
            "recent_7d": _performance_breakdown_for_user(db, user, hours=24 * 7),
            "all_time": _performance_breakdown_for_user(db, user),
        },
        "mark_to_market": {
            "recent_24h": _mark_to_market_for_user(db, user, hours=24),
            "recent_7d": _mark_to_market_for_user(db, user, hours=24 * 7),
            "all_time": _mark_to_market_for_user(db, user),
        },
        "opportunities": opportunities_context,
        "diagnostics": diagnostics_context,
    }


# ------------------------------------------------------------------ Auth
@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = db.query(User).filter(
        User.username == body.username, User.is_active == True
    ).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Invalid username or password")
    token, expires_in = create_token(user.username, user.role)
    user.last_login = datetime.now(timezone.utc)
    db.commit()
    logger.info("User '%s' (%s) logged in", user.username, user.role)
    # Set httpOnly cookie — not readable by JavaScript (XSS-safe)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=settings.frontend_url.startswith("https"),
        max_age=expires_in,
        path="/api",
    )
    return TokenResponse(
        expires_in=expires_in,
        session_timeout_minutes=settings.session_timeout_minutes,
        role=user.role, display_name=user.display_name or user.username,
    )


@router.post("/logout")
def logout(response: Response, _user: dict = Depends(require_auth)):
    response.delete_cookie(key=COOKIE_NAME, path="/api")
    return {"ok": True}


@router.get("/me")
def get_me(user: dict = Depends(require_auth), db: Session = Depends(get_db)):
    db_user = db.query(User).filter(User.username == user["username"]).first()
    if not db_user:
        raise HTTPException(404, "User not found")
    return {
        "username": db_user.username,
        "display_name": db_user.display_name or db_user.username,
        "role": db_user.role,
    }


# ------------------------------------------------------------------ User Management (admin only)
@router.get("/users", response_model=list[UserInfo])
def list_users(db: Session = Depends(get_db), _admin: dict = Depends(require_admin)):
    users = db.query(User).order_by(User.created_at).all()
    return [
        UserInfo(
            id=u.id, username=u.username, display_name=u.display_name,
            role=u.role, is_active=u.is_active,
            created_at=u.created_at, last_login=u.last_login,
        ) for u in users
    ]


@router.post("/users", response_model=UserInfo)
def create_user(body: UserCreate, db: Session = Depends(get_db),
                _admin: dict = Depends(require_admin)):
    if body.role not in ("admin", "user"):
        raise HTTPException(400, "Role must be admin or user")
    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(409, f"Username '{body.username}' already exists")
    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        display_name=body.display_name or body.username,
        role=body.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info("Admin created user '%s' with role '%s'", user.username, user.role)
    return UserInfo(
        id=user.id, username=user.username, display_name=user.display_name,
        role=user.role, is_active=user.is_active,
        created_at=user.created_at, last_login=user.last_login,
    )


@router.put("/users/{user_id}", response_model=UserInfo)
def update_user(user_id: int, body: UserUpdate, db: Session = Depends(get_db),
                _admin: dict = Depends(require_admin)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "User not found")
    if body.display_name is not None:
        user.display_name = body.display_name
    if body.role is not None:
        if body.role not in ("admin", "user"):
            raise HTTPException(400, "Role must be admin or user")
        user.role = body.role
    if body.password is not None:
        user.password_hash = hash_password(body.password)
    if body.is_active is not None:
        user.is_active = body.is_active
    db.commit()
    db.refresh(user)
    logger.info("Admin updated user '%s'", user.username)
    return UserInfo(
        id=user.id, username=user.username, display_name=user.display_name,
        role=user.role, is_active=user.is_active,
        created_at=user.created_at, last_login=user.last_login,
    )


@router.delete("/users/{user_id}")
def delete_user(user_id: int, db: Session = Depends(get_db),
                _admin: dict = Depends(require_admin)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "User not found")
    if user.role == "admin":
        admin_count = db.query(User).filter(User.role == "admin", User.is_active == True).count()
        if admin_count <= 1:
            raise HTTPException(400, "Cannot delete the last admin")
    db.delete(user)
    db.commit()
    logger.info("Admin deleted user '%s'", user.username)
    return {"ok": True}



def _get_user_id(user_info: dict, db: Session) -> int:
    """Resolve the user ID from the JWT payload."""
    u = db.query(User).filter(User.username == user_info["username"]).first()
    if not u:
        raise HTTPException(404, "User not found")
    return u.id


def _get_user_obj(user_info: dict, db: Session) -> User:
    u = db.query(User).filter(User.username == user_info["username"]).first()
    if not u:
        raise HTTPException(404, "User not found")
    return u


# ------------------------------------------------------------------ User Settings (API keys)
@router.get("/settings/keys")
def get_api_keys(db: Session = Depends(get_db), user_info: dict = Depends(require_auth)):
    user = _get_user_obj(user_info, db)
    return {
        "trading_enabled": user.trading_enabled,
        "trading_mode": user.trading_mode,
        "paper_initial_capital": user.paper_initial_capital,
        "trading_start_hour": user.trading_start_hour,
        "trading_end_hour": user.trading_end_hour,
        "has_live_keys": user.has_api_keys(live=True),
        "has_testnet_keys": user.has_api_keys(live=False),
        "binance_api_key": user.get_api_key(live=True)[:8] + "..." if user.has_api_keys(live=True) else "",
        "binance_testnet_api_key": user.get_api_key(live=False)[:8] + "..." if user.has_api_keys(live=False) else "",
        "telegram_chat_id": user.telegram_chat_id or "",
        "telegram_enabled": user.telegram_enabled,
        "telegram_min_level": user.telegram_min_level or "",
    }


@router.put("/settings/keys")
async def update_api_keys(body: dict, db: Session = Depends(get_db),
                          user_info: dict = Depends(require_auth)):
    from app.binance_client.rest_client import BinanceRestClient

    user = _get_user_obj(user_info, db)
    if "trading_enabled" in body:
        user.trading_enabled = bool(body["trading_enabled"])
    if "trading_mode" in body:
        if body["trading_mode"] not in ("dry_run", "paper", "live"):
            raise HTTPException(400, "Mode must be 'dry_run', 'paper' or 'live'")
        user.trading_mode = body["trading_mode"]
    if "paper_initial_capital" in body:
        user.paper_initial_capital = float(body["paper_initial_capital"])
    if "trading_start_hour" in body:
        val = body["trading_start_hour"]
        user.trading_start_hour = int(val) if val is not None and val != "" else None
    if "trading_end_hour" in body:
        val = body["trading_end_hour"]
        user.trading_end_hour = int(val) if val is not None and val != "" else None

    # Telegram per-user settings
    if "telegram_chat_id" in body:
        user.telegram_chat_id = str(body["telegram_chat_id"]).strip()
    if "telegram_enabled" in body:
        user.telegram_enabled = bool(body["telegram_enabled"])
    if "telegram_min_level" in body:
        val = str(body["telegram_min_level"] or "").strip().upper()
        if val not in ("", "INFO", "WARNING", "CRITICAL"):
            raise HTTPException(400, "telegram_min_level must be '', 'INFO', 'WARNING' or 'CRITICAL'")
        user.telegram_min_level = val

    # Validate and save API keys
    api_key = body.get("binance_api_key", "")
    api_secret = body.get("binance_api_secret", "")
    testnet_key = body.get("binance_testnet_api_key", "")
    testnet_secret = body.get("binance_testnet_api_secret", "")

    # Validate Live keys if provided
    if api_key and api_secret:
        client = BinanceRestClient(api_key=api_key, api_secret=api_secret, testnet=False)
        try:
            await client.get_account()
        except Exception:
            await client.close()
            raise HTTPException(400, "Chiavi Live API non valide / Live API keys invalid")
        await client.close()

    # Validate Testnet keys if provided
    if testnet_key and testnet_secret:
        client = BinanceRestClient(api_key=testnet_key, api_secret=testnet_secret, testnet=True)
        try:
            await client.get_account()
        except Exception:
            await client.close()
            raise HTTPException(400, "Chiavi Testnet API non valide / Testnet API keys invalid")
        await client.close()

    if api_key or api_secret or testnet_key or testnet_secret:
        user.set_api_keys(
            api_key=api_key or user.get_api_key(live=True),
            api_secret=api_secret or user.get_api_secret(live=True),
            testnet_key=testnet_key or user.get_api_key(live=False),
            testnet_secret=testnet_secret or user.get_api_secret(live=False),
        )
    db.commit()
    logger.info("User '%s' updated their settings", user.username)
    return {"ok": True}


# ------------------------------------------------------------------ Dashboard (per-user)
def _paper_equity_snapshot(db: Session, user: User) -> dict:
    engine = get_engine()
    portfolio = engine.paper_portfolio.get_or_create(db, user.id)
    mtm = _mark_to_market_for_user(db, user)
    closed_trades = db.query(Trade).filter(
        Trade.user_id == user.id,
        Trade.mode == "paper",
        Trade.status == TradeStatus.CLOSED,
    ).all()
    initial_capital = float(getattr(portfolio, "initial_capital", 0) or 0)
    total_pnl = sum(t.pnl or 0 for t in closed_trades)
    unrealized_net = float(mtm.get("unrealized_estimated_net_pnl", 0) or 0)
    total_equity = initial_capital + total_pnl + unrealized_net
    if initial_capital <= 0:
        total_equity = float(getattr(portfolio, "total_equity", 0) or 0) + unrealized_net
    return {
        "cash_balance": float(getattr(portfolio, "cash_balance", 0) or 0),
        "total_equity": round(total_equity, 6),
        "total_pnl": total_pnl,
        "total_trades": len(closed_trades),
        "winning_trades": sum(1 for t in closed_trades if (t.pnl or 0) > 0),
        "losing_trades": sum(1 for t in closed_trades if (t.pnl or 0) < 0),
    }


@router.get("/balance", response_model=BalanceResponse)
async def get_balance(db: Session = Depends(get_db), user_info: dict = Depends(require_auth)):
    from app.binance_client.rest_client import BinanceRestClient
    engine = get_engine()
    user = _get_user_obj(user_info, db)
    user_mode = user.trading_mode or "paper"
    is_live = user_mode == "live"
    if user_mode == "paper":
        return BalanceResponse(mode=user_mode, **_paper_equity_snapshot(db, user))

    cash_balance = 0.0   # USDT free — available to invest
    total_equity = 0.0   # Σ (free + locked) × market price for every asset

    if user.has_api_keys(live=is_live):
        client = BinanceRestClient(
            api_key=user.get_api_key(live=is_live),
            api_secret=user.get_api_secret(live=is_live),
            testnet=not is_live,
        )
        try:
            account = await client.get_account()
            for b in account.get("balances", []):
                asset = b["asset"]
                qty = float(b.get("free", 0)) + float(b.get("locked", 0))
                if qty <= 0:
                    continue
                if asset == "USDT":
                    cash_balance = float(b["free"])
                    total_equity += qty  # USDT price = 1
                else:
                    price = engine.last_prices.get(asset + "USDT", 0)
                    if price > 0:
                        total_equity += qty * price
        except Exception as exc:
            logger.warning("Failed to fetch Binance balance for user %d: %s",
                           user.id, exc)
        finally:
            await client.close()

    trades = db.query(Trade).filter(
        Trade.user_id == user.id, Trade.mode == user_mode
    ).all()
    closed_trades = [t for t in trades if t.status == TradeStatus.CLOSED]
    total_pnl = sum(t.pnl or 0 for t in closed_trades)
    winning = sum(1 for t in closed_trades if (t.pnl or 0) > 0)
    losing = sum(1 for t in closed_trades if (t.pnl or 0) < 0)
    return BalanceResponse(
        mode=user_mode,
        cash_balance=cash_balance,
        total_equity=total_equity,
        total_pnl=total_pnl,
        total_trades=len(closed_trades),
        winning_trades=winning,
        losing_trades=losing,
    )


@router.get("/positions", response_model=list[PositionResponse])
async def get_positions(db: Session = Depends(get_db), user_info: dict = Depends(require_auth)):
    from app.binance_client.rest_client import BinanceRestClient
    engine = get_engine()
    user = _get_user_obj(user_info, db)
    user_mode = user.trading_mode or "paper"
    is_live = user_mode == "live"

    open_trades = db.query(Trade).filter(
        Trade.user_id == user.id,
        Trade.status == TradeStatus.OPEN,
        Trade.mode == user_mode,
    ).all()

    result = []
    for t in open_trades:
        cp = engine.last_prices.get(t.symbol, 0)
        entry_notional = t.entry_price * t.quantity
        if cp and user_mode == "paper":
            is_short = t.side == OrderSide.SELL
            exit_price = cp * (
                1 + settings.paper_slippage_pct / 100
                if is_short else
                1 - settings.paper_slippage_pct / 100
            )
            exit_notional = exit_price * t.quantity
            entry_fee = entry_notional * (settings.paper_fee_pct / 100)
            exit_fee = exit_notional * (settings.paper_fee_pct / 100)
            if is_short:
                upnl = entry_notional - entry_fee - exit_notional - exit_fee
            else:
                upnl = exit_notional - exit_fee - entry_notional - entry_fee
            upnl_pct = (upnl / (entry_notional + entry_fee) * 100) if entry_notional else 0
            pos_value = exit_notional + exit_fee if is_short else exit_notional - exit_fee
        else:
            if t.side == OrderSide.SELL:
                upnl = (t.entry_price - cp) * t.quantity if cp else 0
                upnl_pct = ((t.entry_price - cp) / t.entry_price * 100) if (cp and t.entry_price) else 0
            else:
                upnl = (cp - t.entry_price) * t.quantity if cp else 0
                upnl_pct = ((cp - t.entry_price) / t.entry_price * 100) if (cp and t.entry_price) else 0
            pos_value = cp * t.quantity if cp else entry_notional
        result.append(PositionResponse(
            id=t.id, symbol=t.symbol, side=t.side.value,
            quantity=t.quantity, entry_price=t.entry_price,
            current_price=cp,
            position_value_usdt=pos_value,
            unrealized_pnl=upnl,
            unrealized_pnl_pct=upnl_pct,
            stop_loss=t.stop_loss, take_profit=t.take_profit,
            opened_at=t.opened_at,
        ))
    return result


@router.post("/positions/{trade_id}/close")
async def close_position_manual(
    trade_id: int,
    db: Session = Depends(get_db),
    user_info: dict = Depends(require_write),
):
    """Manually close a single open position at current market price."""
    from app.binance_client.rest_client import BinanceRestClient
    from app.trading_engine.order_manager import OrderManager

    engine = get_engine()
    user = _get_user_obj(user_info, db)
    user_mode = user.trading_mode or "paper"

    trade = db.query(Trade).filter(
        Trade.id == trade_id,
        Trade.user_id == user.id,
        Trade.status == TradeStatus.OPEN,
    ).first()
    if not trade:
        raise HTTPException(404, "Position not found or already closed")

    current_price = engine.last_prices.get(trade.symbol, 0)
    if current_price <= 0:
        raise HTTPException(400, f"No live price available for {trade.symbol}")

    if user_mode == "live":
        client = BinanceRestClient(
            api_key=user.get_api_key(live=True),
            api_secret=user.get_api_secret(live=True),
            testnet=False,
        )
        order_mgr = OrderManager(client, mode="live")
        try:
            await engine._close_trade(db, user, trade, current_price, "manual_close", client, order_mgr)
        finally:
            await client.close()

    elif user_mode == "paper" and user.has_api_keys(live=False):
        if trade.side == OrderSide.SELL:
            position = db.query(PaperPosition).filter(
                PaperPosition.user_id == user.id,
                PaperPosition.symbol == trade.symbol,
                PaperPosition.side == "SELL",
            ).first()
            if position:
                engine.paper_portfolio.close_position(db, position, current_price, "manual_close")
            else:
                entry_notional = trade.entry_price * trade.quantity
                exit_notional = current_price * trade.quantity
                fees = (entry_notional + exit_notional) * (settings.paper_fee_pct / 100)
                trade.exit_price = current_price
                trade.pnl = entry_notional - exit_notional - fees
                trade.pnl_pct = (trade.pnl / entry_notional) * 100 if entry_notional else 0
                trade.status = TradeStatus.CLOSED
                trade.closed_at = datetime.now(timezone.utc)
                db.commit()
        else:
            client = BinanceRestClient(
                api_key=user.get_api_key(live=False),
                api_secret=user.get_api_secret(live=False),
                testnet=True,
            )
            order_mgr = OrderManager(client, mode="paper")
            try:
                await engine._close_trade(db, user, trade, current_price, "manual_close", client, order_mgr)
            finally:
                await client.close()

    else:
        # Paper simulation — close via portfolio manager
        position = db.query(PaperPosition).filter(
            PaperPosition.user_id == user.id,
            PaperPosition.symbol == trade.symbol,
        ).first()
        if position:
            engine.paper_portfolio.close_position(db, position, current_price, "manual_close")
        else:
            if trade.side == OrderSide.SELL:
                entry_notional = trade.entry_price * trade.quantity
                exit_notional = current_price * trade.quantity
                fees = (entry_notional + exit_notional) * (settings.paper_fee_pct / 100)
                trade.exit_price = current_price
                trade.pnl = entry_notional - exit_notional - fees
                trade.pnl_pct = (trade.pnl / entry_notional) * 100 if entry_notional else 0
            else:
                trade.pnl = (current_price - trade.entry_price) * trade.quantity
                trade.pnl_pct = ((current_price - trade.entry_price) / trade.entry_price) * 100
            trade.exit_price = current_price
            trade.status = TradeStatus.CLOSED
            trade.closed_at = datetime.now(timezone.utc)
            db.commit()

    logger.info("User '%s': manually closed position #%d %s @ %.2f",
                user_info["username"], trade_id, trade.symbol, current_price)
    return {"ok": True, "closed_at_price": current_price}


@router.get("/orders", response_model=list[OrderResponse])
def get_orders(db: Session = Depends(get_db), limit: int = 50,
               user_info: dict = Depends(require_auth)):
    user = _get_user_obj(user_info, db)
    user_mode = user.trading_mode or "paper"
    orders = db.query(Order).filter(
        Order.user_id == user.id,
        Order.mode == user_mode,
    ).order_by(Order.created_at.desc()).limit(limit).all()
    return [
        OrderResponse(
            id=o.id, symbol=o.symbol, side=o.side.value,
            order_type=o.order_type.value, quantity=o.quantity,
            price=o.price, filled_price=o.filled_price,
            status=o.status.value, mode=o.mode,
            error_message=o.error_message, created_at=o.created_at,
        ) for o in orders
    ]


@router.get("/trades", response_model=list[TradeResponse])
def get_trades(db: Session = Depends(get_db), limit: int = 50,
               user_info: dict = Depends(require_auth)):
    user = _get_user_obj(user_info, db)
    user_mode = user.trading_mode or "paper"
    trades = db.query(Trade).filter(
        Trade.user_id == user.id,
        Trade.mode == user_mode,
    ).order_by(Trade.opened_at.desc()).limit(limit).all()
    result = []
    for t in trades:
        cost = _paper_cost_estimate(t)
        result.append(TradeResponse(
            id=t.id, symbol=t.symbol, side=t.side.value,
            entry_price=t.entry_price, exit_price=t.exit_price,
            quantity=t.quantity, stop_loss=t.stop_loss,
            take_profit=t.take_profit, pnl=t.pnl, pnl_pct=t.pnl_pct,
            estimated_roundtrip_fee=cost["estimated_roundtrip_fee"],
            estimated_roundtrip_slippage=cost["estimated_roundtrip_slippage"],
            estimated_net_pnl=cost["estimated_net_pnl"],
            estimated_net_pnl_pct=cost["estimated_net_pnl_pct"],
            status=t.status.value, mode=t.mode, strategy=t.strategy,
            opened_at=t.opened_at, closed_at=t.closed_at,
        ))
    return result


@router.get("/performance/breakdown")
def get_performance_breakdown(
    db: Session = Depends(get_db),
    user_info: dict = Depends(require_auth),
    since: datetime | None = Query(default=None),
    hours: int | None = Query(default=None, ge=1, le=24 * 90),
):
    user = _get_user_obj(user_info, db)
    return _performance_breakdown_for_user(db, user, since=since, hours=hours)


@router.get("/performance/mark-to-market")
def get_mark_to_market_performance(
    db: Session = Depends(get_db),
    user_info: dict = Depends(require_auth),
    hours: int | None = Query(default=None, ge=1, le=24 * 90),
):
    user = _get_user_obj(user_info, db)
    return _mark_to_market_for_user(db, user, hours=hours)


@router.get("/performance/kpi")
def get_performance_kpi(
    db: Session = Depends(get_db),
    _user: dict = Depends(require_auth),
    window_days: int = Query(default=30, ge=1, le=365),
):
    """Daily-loop KPI snapshot: overall + per-strategy (A/B) metrics, active
    alarms and review-cycle triggers. Same logic the Telegram report uses."""
    mc = get_meta_controller()
    kpi = mc.kpi_monitor.compute(db, window_days=window_days)
    regime = mc.regime_service.global_snapshot()
    alarms = mc.kpi_monitor.evaluate_alarms(kpi)
    triggers = mc.kpi_monitor.review_triggers(
        kpi,
        global_regime=regime.get("global_regime", "unknown"),
        global_direction=regime.get("global_direction", "flat"),
    )
    return {"kpi": kpi, "alarms": alarms, "review_triggers": triggers,
            "thresholds": mc.kpi_monitor.thresholds}


# ------------------------------------------------------------------ Price
@router.get("/price/{symbol}", response_model=PriceResponse)
async def get_price(symbol: str, _user: dict = Depends(require_auth)):
    engine = get_engine()
    try:
        data = await engine.market_client.get_ticker_price(symbol.upper())
        return PriceResponse(symbol=data["symbol"], price=float(data["price"]))
    except Exception as e:
        raise HTTPException(502, f"Could not fetch price: {e}")


# ------------------------------------------------------------------ Strategies
@router.get("/strategies", response_model=list[StrategyInfo])
def get_strategies(_admin: dict = Depends(require_admin)):
    engine = get_engine()
    return [
        StrategyInfo(name=s.name, enabled=s.enabled, params=s.get_params())
        for s in engine.strategies
    ]


@router.put("/strategies")
def update_strategy(body: StrategyUpdate, _admin: dict = Depends(require_admin)):
    engine = get_engine()
    strat = next((s for s in engine.strategies if s.name == body.name), None)
    if not strat:
        raise HTTPException(404, f"Strategy '{body.name}' not found")
    if body.enabled is not None:
        strat.enabled = body.enabled
    if body.params:
        strat.set_params(body.params)
    logger.info("Strategy '%s' updated: enabled=%s params=%s",
                body.name, strat.enabled, strat.get_params())
    # Persist all strategy params so they survive restarts
    save_strategy_params({
        s.name: {"enabled": s.enabled, "params": s.get_params()}
        for s in engine.strategies
    })
    return {"ok": True}


# ------------------------------------------------------------------ Risk
@router.get("/risk", response_model=RiskParams)
def get_risk_params(_admin: dict = Depends(require_admin)):
    return get_engine().risk_manager.get_params()


@router.put("/risk", response_model=RiskParams)
def update_risk_params(body: RiskParams, _admin: dict = Depends(require_admin)):
    engine = get_engine()
    engine.risk_manager.set_params(body.model_dump())
    save_risk_params(engine.risk_manager.get_params())
    return engine.risk_manager.get_params()


# ------------------------------------------------------------------ Signals log
@router.get("/signals", response_model=list[SignalResponse])
def get_signals(_user: dict = Depends(require_auth)):
    engine = get_engine()
    return engine.signals_log[-50:]


# ------------------------------------------------------------------ Log tail (admin only)
@router.get("/logs/tail")
def tail_logs(lines: int = 200, _admin: dict = Depends(require_admin)):
    """Return the last N lines of the application log file (admin only)."""
    import os
    lines = min(lines, 10000)  # cap to prevent OOM
    log_file = "logs/trading_bot.log"
    if not os.path.exists(log_file):
        raise HTTPException(404, "Log file not found")
    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        tail = all_lines[-lines:]
        return {
            "file": log_file,
            "total_lines": len(all_lines),
            "returned_lines": len(tail),
            "content": "".join(tail),
        }
    except Exception as exc:
        raise HTTPException(500, f"Failed to read log: {exc}")


# ------------------------------------------------------------------ Paper trading extras
@router.post("/paper/reset")
def reset_paper_portfolio(db: Session = Depends(get_db),
                          user_info: dict = Depends(require_auth)):
    engine = get_engine()
    user_id = _get_user_id(user_info, db)
    engine.paper_portfolio.reset(db, user_id)
    # Clear per-symbol cooldown timers so trading can resume immediately
    stale_keys = [k for k in engine._last_trade_time if k[0] == user_id]
    for k in stale_keys:
        del engine._last_trade_time[k]
    return {"ok": True}


@router.get("/paper/export")
def export_paper_trades(db: Session = Depends(get_db),
                        user_info: dict = Depends(require_auth)):
    engine = get_engine()
    user_id = _get_user_id(user_info, db)
    csv_data = engine.paper_portfolio.export_trades_csv(db, user_id)
    return PlainTextResponse(csv_data, media_type="text/csv", headers={
        "Content-Disposition": "attachment; filename=paper_trades.csv"
    })


# ------------------------------------------------------------------ Engine control
@router.get("/engine/status")
def engine_status(_user: dict = Depends(require_auth)):
    engine = get_engine()
    return {
        "running": engine.running,
        "symbols": engine.symbols,
        "last_prices": engine.last_prices,
        "strategies_count": len(engine.strategies),
    }


@router.get("/symbols/analysis")
async def symbols_analysis(db: Session = Depends(get_db), user_info: dict = Depends(require_admin)):
    from app.adaptive.market_regime_service import MarketRegimeService

    engine = get_engine()
    user = _get_user_obj(user_info, db)
    performance = _performance_breakdown_for_user(db, user)
    perf_by_symbol = performance.get("by_symbol", {})
    active_symbols = set(engine.symbols)
    symbols = sorted(DEFAULT_SYMBOL_CANDIDATES | active_symbols | set(perf_by_symbol.keys()))

    open_symbols = {
        symbol
        for (symbol,) in db.query(Trade.symbol).filter(
            Trade.user_id == user.id,
            Trade.mode == (user.trading_mode or "paper"),
            Trade.status == TradeStatus.OPEN,
        ).distinct().all()
    }
    live_snapshots: dict[str, dict] = {}
    try:
        live_snapshots = get_meta_controller().regime_service.global_snapshot().get("symbols", {})
    except HTTPException:
        live_snapshots = {}

    rows = []
    for symbol in symbols:
        snap = live_snapshots.get(symbol)
        last_price = engine.last_prices.get(symbol)
        analysis_error = None
        if snap is None:
            try:
                df = await engine.fetch_klines(symbol, limit=100)
                if not df.empty:
                    last_price = float(df["close"].iloc[-1])
                    snap = MarketRegimeService().compute(df, symbol).to_dict()
            except Exception as exc:
                logger.warning("Symbol analysis unavailable for %s: %s", symbol, exc)
                analysis_error = str(exc)

        perf = perf_by_symbol.get(symbol, {})
        status, reason = _symbol_candidate_status(symbol in active_symbols, snap, perf)
        if analysis_error:
            reason = f"Market analysis unavailable: {analysis_error}"

        rows.append({
            "symbol": symbol,
            "active": symbol in active_symbols,
            "position_open": symbol in open_symbols,
            "last_price": round(last_price, 8) if last_price is not None else None,
            "regime": snap.get("regime") if snap else None,
            "adx": snap.get("adx") if snap else None,
            "atr_pct": snap.get("atr_pct") if snap else None,
            "bb_width_pct": snap.get("bb_width_pct") if snap else None,
            "volume_ratio": snap.get("volume_ratio") if snap else None,
            "trades": perf.get("trades", 0),
            "win_rate": perf.get("win_rate", 0),
            "gross_pnl": perf.get("gross_pnl", 0),
            "estimated_net_pnl": perf.get("estimated_net_pnl", 0),
            "candidate_status": status,
            "candidate_reason": reason,
        })
    return rows


@router.get("/opportunities")
async def opportunities(db: Session = Depends(get_db), user_info: dict = Depends(require_admin)):
    from app.adaptive.market_regime_service import MarketRegimeService

    engine = get_engine()
    user = _get_user_obj(user_info, db)
    user_mode = user.trading_mode or "paper"

    perf_24h = _performance_breakdown_for_user(db, user, hours=24)
    perf_7d = _performance_breakdown_for_user(db, user, hours=24 * 7)
    perf_all = _performance_breakdown_for_user(db, user)
    active_symbols = set(engine.symbols)
    symbols = sorted(
        DEFAULT_SYMBOL_CANDIDATES
        | active_symbols
        | set(perf_all.get("by_symbol", {}).keys())
    )
    open_rows = db.query(Trade.symbol, Trade.side).filter(
        Trade.user_id == user.id,
        Trade.mode == user_mode,
        Trade.status == TradeStatus.OPEN,
    ).all()
    open_symbols = {symbol for symbol, _side in open_rows}
    open_sides = {symbol: side for symbol, side in open_rows}

    live_snapshots: dict[str, dict] = {}
    global_regime = "unknown"
    news_snapshot: dict | None = None
    try:
        mc = get_meta_controller()
        regime_snapshot = mc.regime_service.global_snapshot()
        live_snapshots = regime_snapshot.get("symbols", {})
        global_regime = regime_snapshot.get("global_regime", "unknown")
        snap_obj = getattr(mc.news_sentiment, "snapshot", None)
        if snap_obj is not None:
            news_snapshot = snap_obj.to_dict()
    except HTTPException:
        pass

    opportunities = []
    for symbol in symbols:
        snap = live_snapshots.get(symbol)
        if snap is None:
            try:
                df = await engine.fetch_klines(symbol, interval="15m", limit=150)
                if not df.empty:
                    engine.last_prices[symbol] = float(df["close"].iloc[-1])
                    snap = MarketRegimeService().compute(df, symbol).to_dict()
            except Exception as exc:
                logger.warning("Opportunity scan unavailable for %s: %s", symbol, exc)

        common = dict(
            symbol=symbol,
            active=symbol in active_symbols,
            position_open=symbol in open_symbols,
            snap=snap,
            perf_24h=perf_24h.get("by_symbol", {}).get(symbol),
            perf_7d=perf_7d.get("by_symbol", {}).get(symbol),
            perf_all_time=perf_all.get("by_symbol", {}).get(symbol),
            news=news_snapshot,
        )
        long_row = _score_long_opportunity(**common)
        short_row = _score_short_opportunity(**common)
        if symbol in open_sides:
            row = short_row if open_sides[symbol] == OrderSide.SELL else long_row
        else:
            row = short_row if short_row["score"] > long_row["score"] else long_row
        row["last_price"] = round(engine.last_prices.get(symbol) or 0, 8)
        opportunities.append(row)

    opportunities.sort(key=lambda x: (x["score"], x["active"]), reverse=True)
    top = opportunities[0] if opportunities else None
    attack_count = sum(1 for row in opportunities if row["action"] in {"ATTACK", "SHORT_ATTACK"})
    watch_count = sum(1 for row in opportunities if row["action"].startswith("WATCH"))
    short_watch_count = sum(1 for row in opportunities if row["action"] == "SHORT_WATCH")
    kill_switch = {}
    try:
        kill_switch = engine.guardrails.status().get("kill_switch", {})
    except Exception:
        kill_switch = {}

    if kill_switch.get("active"):
        posture = "PAUSED"
        summary = "Kill switch is active: scan only, no new entries until it expires."
    elif attack_count:
        posture = "ATTACK"
        summary = f"{attack_count} immediate opportunity detected, including paper-short candidates when present."
    elif watch_count or short_watch_count:
        posture = "STALK"
        summary = f"{watch_count + short_watch_count} symbols are worth stalking for long or paper-short confirmation."
    else:
        posture = "WAIT"
        summary = "No clean long or paper-short edge right now."

    return {
        "mode": user_mode,
        "global_regime": global_regime,
        "posture": posture,
        "summary": summary,
        "top_symbol": top["symbol"] if top else None,
        "news": news_snapshot,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "opportunities": opportunities,
    }


@router.post("/symbols/add")
def add_symbol(body: dict, db: Session = Depends(get_db), _user: dict = Depends(require_admin)):
    from app.models.symbol import TradingSymbol
    engine = get_engine()
    symbol = body.get("symbol", "").upper()
    if not symbol:
        raise HTTPException(400, "Symbol is required")
    engine.add_symbol(symbol)
    # Persist to DB so it survives restarts
    if not db.query(TradingSymbol).filter(TradingSymbol.symbol == symbol).first():
        db.add(TradingSymbol(symbol=symbol))
        db.commit()
    return {"symbols": engine.symbols}


@router.post("/symbols/remove")
def remove_symbol(body: dict, db: Session = Depends(get_db), _user: dict = Depends(require_admin)):
    from app.models.symbol import TradingSymbol
    engine = get_engine()
    symbol = body.get("symbol", "").upper()
    if not symbol:
        raise HTTPException(400, "Symbol is required")
    if len(engine.symbols) <= 1:
        raise HTTPException(400, "Cannot remove the last symbol")
    engine.remove_symbol(symbol)
    # Remove from DB
    row = db.query(TradingSymbol).filter(TradingSymbol.symbol == symbol).first()
    if row:
        db.delete(row)
        db.commit()
    return {"symbols": engine.symbols}


# ------------------------------------------------------------------ Clear API keys
@router.delete("/settings/keys")
def clear_api_keys(key_type: str = "all", db: Session = Depends(get_db),
                   user_info: dict = Depends(require_auth)):
    """
    Delete stored API keys for the current user.
    key_type: 'live' | 'testnet' | 'all'
    """
    user = _get_user_obj(user_info, db)
    if key_type in ("live", "all"):
        user.binance_api_key = ""
        user.binance_api_secret = ""
    if key_type in ("testnet", "all"):
        user.binance_testnet_api_key = ""
        user.binance_testnet_api_secret = ""
    db.commit()
    logger.info("User '%s' cleared %s API keys", user.username, key_type)
    return {"ok": True}


# ------------------------------------------------------------------ Adaptive layer
@router.get("/adaptive/status")
def adaptive_status(_user: dict = Depends(require_auth)):
    """Current adaptive layer status: regime, profile, performance, advisor."""
    mc = get_meta_controller()
    regime = mc.regime_service.global_snapshot()
    perf = mc.perf_monitor.snapshot.to_dict() if mc.perf_monitor.snapshot else {}
    advisor = mc.advisor.last_advice or {}
    # Guardrails status
    guardrails_status = {}
    if _engine:
        guardrails_status = _engine.guardrails.status()

    return {
        "active_profile": mc.profile_manager.active_profile,
        "regime": regime,
        "performance": perf,
        "advisor": advisor,
        "switch_history": mc.profile_manager.switch_history[-10:],
        "guardrails": guardrails_status,
    }


@router.get("/diagnostics")
def get_diagnostics(lines: int = 3000, _admin: dict = Depends(require_admin)):
    """Combined diagnostics payload: adaptive status + parsed log events (admin only)."""
    import os, re
    mc = get_meta_controller()
    engine = get_engine()

    # Adaptive status
    regime = mc.regime_service.global_snapshot()
    perf = mc.perf_monitor.snapshot.to_dict() if mc.perf_monitor.snapshot else {}
    advisor = mc.advisor.last_advice or {}
    guardrails = engine.guardrails.status() if engine else {}

    # Parse log file into structured events
    events: list[dict] = []
    log_file = "logs/trading_bot.log"
    if os.path.exists(log_file):
        try:
            with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
            for line in all_lines[-lines:]:
                ev = _parse_log_line(line)
                if ev:
                    events.append(ev)
        except Exception:
            pass

    return {
        "status": {
            "active_profile": mc.profile_manager.active_profile,
            "regime": regime, "performance": perf, "advisor": advisor,
            "switch_history": mc.profile_manager.switch_history[-10:],
            "guardrails": guardrails,
        },
        "events": events,
    }


def _parse_log_line(line: str) -> dict | None:
    """Parse a single log line into a structured event dict."""
    import re
    ts_m = re.match(r'^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', line)
    ts = ts_m.group(1) if ts_m else None
    m = None

    if (m := re.search(r'REGIME_SERVICE:\s*(\w+)\s*.*?(\w+)\s*\(ADX=([\d.]+)\s+ATR%=([\d.]+)\s+BB%=([\d.]+)\s+Vol=([\d.]+)', line)):
        return {"ts": ts, "type": "regime", "symbol": m.group(1), "level": "info",
                "regime": m.group(2).lower(), "adx": float(m.group(3)), "atr": float(m.group(4)),
                "bb": float(m.group(5)), "vol": float(m.group(6))}
    if (m := re.search(r'PERF_MONITOR:.*?PnL\s+1h=([-\d.]+)\s+6h=([-\d.]+)\s+24h=([-\d.]+)\s*\|\s*WR=([\d.]+)%?\s*\|\s*DD=([\d.]+)%?\s*\|\s*ConsecLoss=(\d+)\s*\|\s*Trades\/h=([\d.]+)', line)):
        return {"ts": ts, "type": "perf", "symbol": "", "level": "info",
                "pnl1h": float(m.group(1)), "pnl6h": float(m.group(2)), "pnl24h": float(m.group(3)),
                "wr": float(m.group(4)), "dd": float(m.group(5)), "consec": int(m.group(6)), "tph": float(m.group(7))}
    if (m := re.search(r'TRADE_GATE:\s*blocked\s*\|\s*symbol=(\w+)\s*\|\s*reason=(\S+)', line)):
        return {"ts": ts, "type": "block", "symbol": m.group(1), "level": "blocked", "reason": m.group(2), "source": "trade_gate"}
    if (m := re.search(r'TRADE_GATE:\s*passed\s*\|\s*symbol=(\w+)', line)):
        return {"ts": ts, "type": "pass", "symbol": m.group(1), "level": "passed", "source": "trade_gate"}
    if (m := re.search(r'DYNAMIC_SCORE:\s*blocked\s*\|\s*symbol=(\w+).*?score=([\d.]+)\s*<\s*min=([\d.]+)', line)):
        return {"ts": ts, "type": "block", "symbol": m.group(1), "level": "blocked",
                "reason": f"score_{m.group(2)}<{m.group(3)}", "source": "dynamic_score"}
    if (m := re.search(r'DYNAMIC_SCORE:\s*passed\s*\|\s*symbol=(\w+)', line)):
        return {"ts": ts, "type": "pass", "symbol": m.group(1), "level": "passed", "source": "dynamic_score"}
    if (m := re.search(r'Signal:\s*(BUY|SELL)\s+(\w+)\s+@\s*([\d.]+)\s*\[(\w+)\]', line)):
        return {"ts": ts, "type": "signal", "symbol": m.group(2), "level": "signal",
                "side": m.group(1), "price": float(m.group(3)), "strategy": m.group(4)}
    if (m := re.search(r'MARKET\s+(BUY|SELL)\s+filled.*?(\w+USDT)', line)) or \
       (m := re.search(r'\[paper.*?\]:\s*BUY\s+(\w+)', line)):
        sym = m.group(2) if m.lastindex and m.lastindex >= 2 else m.group(1)
        return {"ts": ts, "type": "fill", "symbol": sym, "level": "fill"}
    if (m := re.search(r'PROFILE:\s*(\w+)\s*->\s*(\w+)', line)):
        return {"ts": ts, "type": "profile", "symbol": "", "level": "profile", "from": m.group(1), "to": m.group(2)}
    if (m := re.search(r'KILL_SWITCH:\s*(activated|expired|active)', line)):
        return {"ts": ts, "type": "kill_switch", "symbol": "", "level": "blocked" if m.group(1) == "activated" else "info", "action": m.group(1)}
    if (m := re.search(r'SYMBOL_COOLDOWN:\s*blocked\s*\|\s*symbol=(\w+)', line)):
        return {"ts": ts, "type": "block", "symbol": m.group(1), "level": "blocked", "reason": "symbol_cooldown", "source": "cooldown"}
    if (m := re.search(r'ENTRY_THROTTLE:\s*blocked\s*\|\s*symbol=(\w+).*?reason=(\S+)', line)):
        return {"ts": ts, "type": "block", "symbol": m.group(1), "level": "blocked", "reason": m.group(2), "source": "throttle"}
    if (m := re.search(r'PAPER_SHORT:\s*skipped\s+(\w+USDT)\s*\|\s*(.+?)(?:\s*\[|$)', line)):
        return {"ts": ts, "type": "block", "symbol": m.group(1), "level": "blocked", "reason": m.group(2).strip(), "source": "paper_short"}
    if (m := re.search(r'\[paper/short\]:\s*SELL\s+(\w+USDT)', line)):
        return {"ts": ts, "type": "fill", "symbol": m.group(1), "level": "fill", "source": "paper_short"}
    if (m := re.search(r'PROFIT_LOCK:\s*(\w+USDT)\s+(BUY|SELL).*?pnl=([-\d.]+)%', line)):
        return {"ts": ts, "type": "risk", "symbol": m.group(1), "level": "info", "source": "profit_lock", "side": m.group(2), "pnl_pct": float(m.group(3))}
    if (m := re.search(r'STRATEGY_BREAKER:\s*blocked\s*\|\s*strategy=(\w+)', line)):
        return {"ts": ts, "type": "block", "symbol": "", "level": "blocked", "reason": f"breaker_{m.group(1)}", "source": "strategy_breaker"}
    if (m := re.search(r'REGIME_CHANGE:\s*(\w+)\s*->\s*(\w+)', line)):
        return {"ts": ts, "type": "regime_change", "symbol": "", "level": "info", "from": m.group(1), "to": m.group(2)}
    if (m := re.search(r'RISK_SCALING:.*?multiplier=([\d.]+)', line)):
        return {"ts": ts, "type": "risk", "symbol": "", "level": "info", "multiplier": float(m.group(1))}
    if (m := re.search(r'ORDER_VALIDATION:\s*skipped.*?(\w+USDT)\s*\|\s*(.+?)(?:\s*\[|$)', line)):
        return {"ts": ts, "type": "block", "symbol": m.group(1), "level": "blocked", "reason": f"validation_{m.group(2).strip()}", "source": "order_validation"}
    return None


@router.get("/adaptive/guardrails")
def guardrails_status_endpoint(_user: dict = Depends(require_auth)):
    """Detailed guardrails status: kill switch, cooldowns, stats, risk multiplier."""
    engine = get_engine()
    return engine.guardrails.status()


@router.post("/adaptive/guardrails/reload")
def reload_guardrails(_admin: dict = Depends(require_admin)):
    """Hot-reload guardrails config from disk (admin only)."""
    engine = get_engine()
    engine.guardrails.reload_config()
    return {"ok": True, "message": "Guardrails config reloaded"}


@router.get("/adaptive/guardrails/config")
def get_guardrails_config(_admin: dict = Depends(require_admin)):
    """Return the current guardrails config content (admin only)."""
    try:
        return _read_guardrails_config()
    except Exception as exc:
        raise HTTPException(500, f"Failed to read config: {exc}")


@router.put("/adaptive/guardrails/config")
def update_guardrails_config(body: dict, admin: dict = Depends(require_admin)):
    """Save new guardrails config and hot-reload (admin only)."""
    # Basic structure validation
    required_keys = {"kill_switch", "symbol_cooldown", "trade_gate", "dynamic_score",
                     "entry_throttle", "risk_scaling", "strategy_circuit_breaker",
                     "stale_position", "performance_gate", "paper_short"}
    missing = required_keys - set(body.keys())
    if missing:
        raise HTTPException(400, f"Missing required sections: {', '.join(missing)}")

    # Value range validation
    val_errors = validate_guardrails_values(body)
    if val_errors:
        raise HTTPException(400, f"Validation errors: {'; '.join(val_errors)}")

    # Read current config for audit diff
    old_cfg = {}
    try:
        old_cfg = _read_guardrails_config()
    except Exception:
        pass

    # Atomic write: write to temp file, then rename
    try:
        _write_guardrails_config(body)
    except Exception as exc:
        raise HTTPException(500, f"Failed to write config: {exc}")

    # Hot-reload into running engine
    engine = get_engine()
    engine.guardrails.reload_config()

    # Audit log
    changes = diff_configs(old_cfg, body)
    username = admin.get("username", "unknown")
    logger.info(
        "GUARDRAILS_AUDIT: user=%s | changes=%d | %s",
        username, len(changes),
        " | ".join(f"{c['path']}: {c['from']}→{c['to']}" for c in changes[:20]),
    )
    return {"ok": True}


@router.post("/adaptive/guardrails/config/reset")
def reset_guardrails_config(admin: dict = Depends(require_admin)):
    """Reset guardrails config to conservative defaults (admin only)."""
    defaults = {
        "kill_switch": {
            "consecutive_losses_threshold": 6, "low_win_rate_threshold": 15,
            "intraday_drawdown_threshold": 2.0, "pnl_24h_threshold": -6.0,
            "pnl_24h_pct_threshold": -0.3,
            "pause_minutes_losses": 90, "pause_minutes_drawdown": 120,
        },
        "symbol_cooldown": {
            "consecutive_losses_threshold": 3, "cooldown_minutes_losses": 60,
            "stoploss_cluster_count": 2, "stoploss_cluster_window_minutes": 90,
            "cooldown_minutes_cluster": 90,
        },
        "trade_gate": {
            "defensive": {"require_symbol_trend": True, "min_adx": 30, "min_volume_ratio": 1.6, "min_bb_width_pct": 1.2},
            "range": {"require_symbol_trend": True, "min_adx": 32, "min_volume_ratio": 1.8, "min_bb_width_pct": 1.4},
            "trend": {"require_symbol_trend": True, "min_adx": 25, "min_volume_ratio": 1.0, "min_bb_width_pct": 0.0},
            "volatile": {"require_symbol_trend": True, "min_adx": 28, "min_volume_ratio": 1.4, "min_bb_width_pct": 1.0},
            "block_entry_on_symbol_regime": ["range", "defensive"],
        },
        "dynamic_score": {
            "base_min_score": 80, "min_score_after_3_losses": 88,
            "min_score_after_5_losses": 92, "extra_score_in_bad_regime": 5,
            "bad_regimes": ["range", "defensive"], "max_score_cap": 95,
        },
        "entry_throttle": {
            "max_entries_per_symbol_per_candle": 1,
            "max_open_positions": 1,
            "max_entries_per_hour": {"defensive": 2, "range": 3, "trend": 5, "volatile": 3},
            "default_max_entries_per_hour": 3,
        },
        "risk_scaling": {
            "consecutive_losses_3_multiplier": 0.75, "consecutive_losses_5_multiplier": 0.50,
            "drawdown_threshold": 1.5, "drawdown_min_multiplier": 0.50,
        },
        "strategy_circuit_breaker": {
            "consecutive_losses_threshold": 4, "pause_minutes": 120,
        },
        "stale_position": {
            "enabled": True, "max_holding_hours": 48, "min_loss_pct": 0.5,
            "flat_holding_hours": 72, "flat_abs_pnl_pct": 0.2,
            "profit_lock_enabled": True, "profit_lock_trigger_pct": 3.0,
            "profit_lock_min_pct": 0.4, "profit_trail_start_pct": 4.5,
            "profit_trail_distance_pct": 1.2,
            "range_profit_exit_enabled": True,
            "range_profit_exit_min_pct": 0.8,
            "range_profit_exit_min_hours": 12,
        },
        "performance_gate": {
            "enabled": True, "recent_hours": 168,
            "symbol_min_recent_trades": 2, "symbol_max_recent_net_loss": -3.0,
            "symbol_min_all_time_trades": 10, "symbol_max_all_time_net_loss": -10.0,
            "strategy_min_recent_trades": 4, "strategy_max_recent_net_loss": -6.0,
        },
        "paper_short": {
            "enabled": True, "min_sell_score": 80,
            "require_bearish_news": False, "max_open_shorts": 2,
            "allow_with_open_long": True,
        },
    }

    try:
        _write_guardrails_config(defaults)
    except Exception as exc:
        raise HTTPException(500, f"Failed to write defaults: {exc}")

    engine = get_engine()
    engine.guardrails.reload_config()
    username = admin.get("username", "unknown")
    logger.info("GUARDRAILS_AUDIT: user=%s | action=reset_to_defaults", username)
    return {"ok": True, "config": defaults}


# ------------------------------------------------------------------ AI Tuning Advisor

@router.get("/adaptive/tuning/suggestions")
async def get_tuning_suggestions(db: Session = Depends(get_db), _admin: dict = Depends(require_admin)):
    """Generate fresh tuning suggestions based on current state (admin only)."""
    import json as _json
    mc = get_meta_controller()
    engine = get_engine()

    # Inject active_profile into perf dict so LLM prompts receive it
    perf = {
        **(mc.perf_monitor.snapshot.to_dict() if mc.perf_monitor.snapshot else {}),
        "active_profile": mc.profile_manager.active_profile,
    }
    regime = mc.regime_service.global_snapshot()
    guardrails_status = engine.guardrails.status()
    admin_user = _get_user_obj(_admin, db)
    performance_breakdown = _tuning_performance_context(
        db,
        admin_user,
        opportunities_context=_top_opportunities_context(db, admin_user),
        diagnostics_context=_recent_diagnostics_context(),
    )
    strategy_params = {
        s.name: s.get_params()
        for s in getattr(engine, "strategies", [])
        if hasattr(s, "get_params")
    }

    try:
        guardrails_config = _read_guardrails_config()
    except Exception:
        guardrails_config = {}

    # Guard against uninitialized snapshot (NPE fix)
    snap = mc.news_sentiment.snapshot
    news = snap.to_dict() if (snap is not None and snap.available) else None
    result = await mc.advisor.generate_tuning_suggestions(
        perf=perf,
        guardrails_status=guardrails_status,
        guardrails_config=guardrails_config,
        regime_snapshot=regime,
        news_sentiment=news,
        strategy_params=strategy_params or None,
        performance_breakdown=performance_breakdown,
    )

    return {
        "suggestions": result,
        "snapshot": {
            "global_regime": regime.get("global_regime", "unknown"),
            "active_profile": mc.profile_manager.active_profile,
            **perf,
            "total_blocked": guardrails_status.get("stats", {}).get("total_blocked", 0),
            "total_passed": guardrails_status.get("stats", {}).get("total_passed", 0),
            "performance_breakdown": performance_breakdown,
        },
    }


@router.post("/adaptive/tuning/suggestions/{suggestion_id}/apply")
def apply_tuning_suggestion(suggestion_id: int, db: Session = Depends(get_db),
                            admin: dict = Depends(require_admin)):
    """Apply a saved tuning suggestion to guardrails config (admin only)."""
    import json as _json
    from app.models.tuning_suggestion import TuningSuggestion

    # Whitelist of paths the advisor is allowed to modify (path injection guard)
    ALLOWED_TUNING_PATHS = {
        f"trade_gate.{regime}.{field}"
        for regime in ("defensive", "range", "trend", "volatile")
        for field in ("min_adx", "min_volume_ratio", "min_bb_width_pct")
    } | {
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
    engine = get_engine()
    allowed_strategy_paths = {
        f"strategy.{s.name}.enabled"
        for s in getattr(engine, "strategies", [])
    }

    suggestion = db.query(TuningSuggestion).filter(TuningSuggestion.id == suggestion_id).first()
    if not suggestion:
        raise HTTPException(404, "Suggestion not found")
    if suggestion.status != "new":
        raise HTTPException(400, f"Suggestion already {suggestion.status}")

    try:
        config = _read_guardrails_config()
    except Exception as exc:
        raise HTTPException(500, f"Failed to read config: {exc}")

    # Apply changes with whitelist validation and stale check
    changes = _json.loads(suggestion.changes_json or "[]")
    guardrail_changes = []
    strategy_changes = []
    for change in changes:
        path = change.get("path", "")

        # Security: reject unauthorized paths
        if path in ALLOWED_TUNING_PATHS:
            guardrail_changes.append(change)
        elif path in allowed_strategy_paths:
            strategy_changes.append(change)
        else:
            raise HTTPException(400, f"Unauthorized config path: '{path}'")

    for change in guardrail_changes:
        path = change.get("path", "")
        # Stale check: verify 'from' value matches current config before overwriting
        parts = path.split(".")
        current_val = config
        for p in parts:
            current_val = current_val.get(p) if isinstance(current_val, dict) else None
        expected_from = change.get("from")
        if expected_from is not None and current_val is not None and current_val != expected_from:
            raise HTTPException(
                409,
                f"Config changed since suggestion was generated: '{path}' "
                f"expected {expected_from}, current value is {current_val}. "
                "Re-generate the suggestion and try again.",
            )

        # Apply the change
        obj = config
        for p in parts[:-1]:
            obj = obj.setdefault(p, {})
        obj[parts[-1]] = change["to"]

    for change in strategy_changes:
        _, strategy_name, field = change["path"].split(".")
        if field != "enabled" or not isinstance(change.get("to"), bool):
            raise HTTPException(400, f"Invalid strategy change: '{change['path']}'")
        strat = next((s for s in engine.strategies if s.name == strategy_name), None)
        if not strat:
            raise HTTPException(404, f"Strategy '{strategy_name}' not found")
        expected_from = change.get("from")
        if expected_from is not None and bool(strat.enabled) != bool(expected_from):
            raise HTTPException(
                409,
                f"Strategy changed since suggestion was generated: '{strategy_name}' "
                f"expected {expected_from}, current value is {strat.enabled}. "
                "Re-generate the suggestion and try again.",
            )
        strat.enabled = change["to"]

    if guardrail_changes:
        # Validate
        val_errors = validate_guardrails_values(config)
        if val_errors:
            raise HTTPException(400, f"Validation errors: {'; '.join(val_errors)}")

        _write_guardrails_config(config)
        engine.guardrails.reload_config()

    if strategy_changes:
        save_strategy_params({
            s.name: {"enabled": s.enabled, "params": s.get_params()}
            for s in engine.strategies
        })

    # Mark as applied
    from datetime import datetime
    suggestion.status = "applied"
    suggestion.resolved_at = datetime.utcnow()
    suggestion.resolved_by = admin.get("username", "unknown")
    db.commit()

    username = admin.get("username", "unknown")
    logger.info(
        "GUARDRAILS_AUDIT: user=%s | action=apply_tuning_suggestion #%d | changes=%s",
        username, suggestion_id,
        " | ".join(f"{c['path']}: {c['from']}→{c['to']}" for c in changes),
    )
    return {"ok": True, "applied_changes": changes}


@router.post("/adaptive/tuning/suggestions/{suggestion_id}/reject")
def reject_tuning_suggestion(suggestion_id: int, db: Session = Depends(get_db),
                             admin: dict = Depends(require_admin)):
    """Reject a tuning suggestion (admin only)."""
    from app.models.tuning_suggestion import TuningSuggestion
    from datetime import datetime

    suggestion = db.query(TuningSuggestion).filter(TuningSuggestion.id == suggestion_id).first()
    if not suggestion:
        raise HTTPException(404, "Suggestion not found")
    if suggestion.status != "new":
        raise HTTPException(400, f"Suggestion already {suggestion.status}")

    suggestion.status = "rejected"
    suggestion.resolved_at = datetime.utcnow()
    suggestion.resolved_by = admin.get("username", "unknown")
    db.commit()
    return {"ok": True}


@router.post("/adaptive/tuning/generate")
async def generate_and_save_suggestion(db: Session = Depends(get_db),
                                       admin: dict = Depends(require_admin),
                                       body: dict | None = Body(default=None)):
    """Generate a tuning suggestion and save it to DB (admin only)."""
    import json as _json
    from app.models.tuning_suggestion import TuningSuggestion

    mc = get_meta_controller()
    engine = get_engine()
    language = "it"
    if isinstance(body, dict) and body.get("language") in {"it", "en"}:
        language = body["language"]

    # Inject active_profile into perf dict so LLM prompts receive it
    perf = {
        **(mc.perf_monitor.snapshot.to_dict() if mc.perf_monitor.snapshot else {}),
        "active_profile": mc.profile_manager.active_profile,
    }
    regime = mc.regime_service.global_snapshot()
    guardrails_status = engine.guardrails.status()
    admin_user = _get_user_obj(admin, db)
    performance_breakdown = _tuning_performance_context(
        db,
        admin_user,
        opportunities_context=_top_opportunities_context(db, admin_user),
        diagnostics_context=_recent_diagnostics_context(),
    )
    strategy_params = {
        s.name: s.get_params()
        for s in getattr(engine, "strategies", [])
        if hasattr(s, "get_params")
    }

    try:
        guardrails_config = _read_guardrails_config()
    except Exception:
        guardrails_config = {}

    # Guard against uninitialized snapshot (NPE fix)
    snap = mc.news_sentiment.snapshot
    news = snap.to_dict() if (snap is not None and snap.available) else None
    result = await mc.advisor.generate_tuning_suggestions(
        perf=perf,
        guardrails_status=guardrails_status,
        guardrails_config=guardrails_config,
        regime_snapshot=regime,
        news_sentiment=news,
        strategy_params=strategy_params or None,
        performance_breakdown=performance_breakdown,
        language=language,
    )

    if not result["changes"]:
        return {"ok": True, "suggestion": None, "reasoning": result["reasoning"]}

    suggestion = TuningSuggestion(
        global_regime=regime.get("global_regime", "unknown"),
        active_profile=mc.profile_manager.active_profile,
        consecutive_losses=perf.get("consecutive_losses", 0),
        win_rate=perf.get("win_rate_last_10", 0),
        drawdown=perf.get("drawdown_intraday", 0),
        trades_per_hour=perf.get("trades_per_hour", 0),
        total_blocked=guardrails_status.get("stats", {}).get("total_blocked", 0),
        total_passed=guardrails_status.get("stats", {}).get("total_passed", 0),
        changes_json=_json.dumps(result["changes"]),
        reasoning=result["reasoning"],
        confidence=result["confidence"],
        risk_level=result["risk_level"],
        source=result.get("source", "rules"),
    )
    db.add(suggestion)
    db.commit()
    db.refresh(suggestion)

    return {
        "ok": True,
        "suggestion": {
            "id": suggestion.id,
            "status": suggestion.status,
            "global_regime": suggestion.global_regime,
            "active_profile": suggestion.active_profile,
            "consecutive_losses": suggestion.consecutive_losses,
            "win_rate": suggestion.win_rate,
            "drawdown": suggestion.drawdown,
            "trades_per_hour": suggestion.trades_per_hour,
            "total_blocked": suggestion.total_blocked,
            "total_passed": suggestion.total_passed,
            "changes": result["changes"],
            "reasoning": result["reasoning"],
            "confidence": result["confidence"],
            "risk_level": result["risk_level"],
            "source": result.get("source", "rules"),
            "created_at": str(suggestion.created_at),
            "resolved_at": None,
            "resolved_by": None,
        },
    }


@router.get("/adaptive/tuning/history")
def get_tuning_history(limit: int = 20, db: Session = Depends(get_db),
                       _admin: dict = Depends(require_admin)):
    """Return recent tuning suggestions from DB (admin only)."""
    import json as _json
    from app.models.tuning_suggestion import TuningSuggestion

    # Expire stale "new" suggestions older than 24 hours
    from datetime import timedelta
    db.query(TuningSuggestion).filter(
        TuningSuggestion.status == "new",
        TuningSuggestion.created_at < datetime.utcnow() - timedelta(hours=24),
    ).update({"status": "expired"}, synchronize_session=False)
    db.commit()

    rows = db.query(TuningSuggestion).order_by(TuningSuggestion.created_at.desc()).limit(limit).all()
    return [
        {
            "id": r.id, "status": r.status, "created_at": str(r.created_at),
            "global_regime": r.global_regime, "active_profile": r.active_profile,
            "consecutive_losses": r.consecutive_losses, "win_rate": r.win_rate,
            "drawdown": r.drawdown, "trades_per_hour": r.trades_per_hour,
            "total_blocked": r.total_blocked, "total_passed": r.total_passed,
            "changes": _json.loads(r.changes_json or "[]"),
            "reasoning": r.reasoning, "confidence": r.confidence,
            "risk_level": r.risk_level, "source": r.source or "rules",
            "resolved_at": str(r.resolved_at) if r.resolved_at else None,
            "resolved_by": r.resolved_by,
        }
        for r in rows
    ]


@router.get("/adaptive/tuning/ollama-status")
async def llm_status(_admin: dict = Depends(require_admin)):
    """Check LLM provider status: DeepSeek API + Ollama local."""
    from app.adaptive.ollama_client import check_ollama, get_available_models
    from app.adaptive.deepseek_client import check_deepseek
    from app.config import settings

    deepseek_ok = await check_deepseek(settings.deepseek_api_key) if settings.deepseek_api_key else False
    ollama_ok = await check_ollama(settings.ollama_url)
    ollama_models = await get_available_models(settings.ollama_url) if ollama_ok else []

    return {
        "deepseek": {
            "available": deepseek_ok,
            "configured": bool(settings.deepseek_api_key),
            "model": settings.deepseek_model,
        },
        "ollama": {
            "available": ollama_ok,
            "url": settings.ollama_url,
            "configured_model": settings.ollama_model,
            "installed_models": ollama_models,
        },
        # For backward compat
        "available": deepseek_ok or ollama_ok,
        "configured_model": settings.deepseek_model if deepseek_ok else (settings.ollama_model if ollama_ok else "rules"),
    }

@router.get("/adaptive/news-sentiment")
async def get_news_sentiment(_user: dict = Depends(require_auth)):
    """Return current news sentiment snapshot. Triggers refresh if stale."""
    mc = get_meta_controller()
    if mc.news_sentiment.needs_refresh(interval_minutes=15):
        await mc.news_sentiment.fetch_and_score()
    return mc.news_sentiment.snapshot.to_dict()


@router.get("/adaptive/profiles")
def list_profiles(_user: dict = Depends(require_auth)):
    """Return all available profiles and the active one."""
    mc = get_meta_controller()
    return {
        "active": mc.profile_manager.active_profile,
        "profiles": mc.profile_manager.profiles,
        "switching_rules": mc.profile_manager.switching_rules,
    }


@router.post("/adaptive/profiles/{profile_name}/apply")
def apply_profile_manual(profile_name: str, db: Session = Depends(get_db),
                         _admin: dict = Depends(require_admin)):
    """Manually apply a profile (admin only). Bypasses switching rules."""
    mc = get_meta_controller()
    engine = get_engine()
    if profile_name not in mc.profile_manager.profiles:
        raise HTTPException(404, f"Profile '{profile_name}' not found")
    applied = mc.profile_manager.apply_profile(profile_name, engine, "manual override (admin)")
    if not applied:
        raise HTTPException(500, "Failed to apply profile")
    return {"ok": True, "active_profile": profile_name}


@router.get("/approvals")
def list_approvals(db: Session = Depends(get_db), _user: dict = Depends(require_auth)):
    """List all approval requests."""
    mc = get_meta_controller()
    reqs = mc.approval_service.get_all(db)
    return [
        {
            "id": r.id,
            "request_type": r.request_type,
            "from_profile": r.from_profile,
            "to_profile": r.to_profile,
            "reason": r.reason,
            "status": r.status,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
            "resolved_by": r.resolved_by,
            "expires_at": r.expires_at.isoformat() if r.expires_at else None,
        }
        for r in reqs
    ]


@router.get("/approvals/pending")
def pending_approvals(db: Session = Depends(get_db), _user: dict = Depends(require_auth)):
    """List pending approval requests."""
    mc = get_meta_controller()
    reqs = mc.approval_service.get_pending(db)
    return [
        {
            "id": r.id,
            "from_profile": r.from_profile,
            "to_profile": r.to_profile,
            "reason": r.reason,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "expires_at": r.expires_at.isoformat() if r.expires_at else None,
        }
        for r in reqs
    ]


@router.post("/approvals/{request_id}/approve")
def approve_request(request_id: int, db: Session = Depends(get_db),
                    user_info: dict = Depends(require_admin)):
    """Approve a pending profile switch request (admin only)."""
    mc = get_meta_controller()
    req = mc.approval_service.approve(db, request_id, resolved_by=user_info["username"])
    if not req:
        raise HTTPException(404, "Approval request not found")
    return {"ok": True, "status": req.status, "id": req.id}


@router.post("/approvals/{request_id}/reject")
def reject_request(request_id: int, db: Session = Depends(get_db),
                   user_info: dict = Depends(require_admin)):
    """Reject a pending profile switch request (admin only)."""
    mc = get_meta_controller()
    req = mc.approval_service.reject(db, request_id, resolved_by=user_info["username"])
    if not req:
        raise HTTPException(404, "Approval request not found")
    return {"ok": True, "status": req.status, "id": req.id}


@router.put("/adaptive/profiles/{profile_name}")
def update_profile(profile_name: str, body: dict, _admin: dict = Depends(require_admin)):
    """Update a profile's parameters (admin only). Persisted to profiles.json."""
    mc = get_meta_controller()
    pm = mc.profile_manager
    if profile_name not in pm.profiles:
        raise HTTPException(404, f"Profile '{profile_name}' not found")
    profile = pm.profiles[profile_name]
    # Merge incoming fields
    if "risk" in body:
        profile.setdefault("risk", {}).update(body["risk"])
    if "strategies" in body:
        for strat_name, strat_params in body["strategies"].items():
            profile.setdefault("strategies", {}).setdefault(strat_name, {}).update(strat_params)
    if "description" in body:
        profile["description"] = body["description"]
    if "auto_apply" in body:
        profile["auto_apply"] = bool(body["auto_apply"])
    if "requires_approval" in body:
        profile["requires_approval"] = bool(body["requires_approval"])
    if "regime" in body:
        profile.setdefault("regime", {}).update(body["regime"])
    # Save to disk
    pm._data["profiles"][profile_name] = profile
    pm._profiles[profile_name] = profile
    pm._save_active_profile()
    logger.info("Profile '%s' updated via API: %s", profile_name, list(body.keys()))
    return {"ok": True, "profile": profile}


@router.put("/adaptive/switching-rules")
def update_switching_rules(body: dict, _admin: dict = Depends(require_admin)):
    """Update switching rules (admin only). Persisted to profiles.json."""
    mc = get_meta_controller()
    pm = mc.profile_manager
    for key in ("cooldown_minutes", "min_trades_for_upgrade",
                "max_profile_changes_per_day", "hysteresis_minutes"):
        if key in body:
            pm._switching_rules[key] = int(body[key])
    pm._data["switching_rules"] = pm._switching_rules
    pm._save_active_profile()
    logger.info("Switching rules updated: %s", pm._switching_rules)
    return {"ok": True, "switching_rules": pm._switching_rules}


@router.post("/adaptive/telegram/test")
async def test_telegram(db: Session = Depends(get_db), user_info: dict = Depends(require_auth)):
    """Send a test Telegram notification to the current user."""
    mc = get_meta_controller()
    user = _get_user_obj(user_info, db)
    if not user.telegram_chat_id:
        raise HTTPException(400, "Telegram chat_id not configured for this user")
    sent = await mc.notifier.send(
        "<b>Test Notification</b>\n\nCryptoBot Telegram is working!",
        level="INFO", chat_id=user.telegram_chat_id, deduplicate=False,
    )
    if not sent:
        raise HTTPException(502, "Failed to send Telegram message — check bot token")
    return {"ok": True, "message": "Test message sent"}


# ------------------------------------------------------------------ Assets
@router.get("/assets")
async def get_assets(db: Session = Depends(get_db), user_info: dict = Depends(require_auth)):
    from app.binance_client.rest_client import BinanceRestClient
    engine = get_engine()
    user = _get_user_obj(user_info, db)
    user_mode = user.trading_mode or "paper"
    is_live = user_mode == "live"

    result = []
    if not user.has_api_keys(live=is_live):
        return result

    client = BinanceRestClient(
        api_key=user.get_api_key(live=is_live),
        api_secret=user.get_api_secret(live=is_live),
        testnet=not is_live,
    )
    try:
        account = await client.get_account()
        balances = account.get("balances", [])
        for b in balances:
            asset = b["asset"]
            free = float(b.get("free", 0))
            locked = float(b.get("locked", 0))
            total = free + locked
            if total <= 0:
                continue
            if asset == "USDT":
                price = 1.0
            else:
                price = engine.last_prices.get(asset + "USDT", 0.0)
                # LD* tokens are Binance Earn locked assets (e.g. LDETH = locked ETH)
                # Try the underlying asset price by stripping the "LD" prefix
                if price == 0 and asset.startswith("LD"):
                    price = engine.last_prices.get(asset[2:] + "USDT", 0.0)
                # Assets with unknown price are still shown (value = 0)
            result.append({
                "asset": asset,
                "free": free,
                "locked": locked,
                "total": total,
                "price_usdt": price,
                "value_usdt": total * price,
            })
    except Exception as exc:
        logger.warning("Failed to fetch assets for user %d: %s", user.id, exc)
    finally:
        await client.close()

    result.sort(key=lambda x: x["value_usdt"], reverse=True)
    return result


# ------------------------------------------------------------------ Embient Skills
@router.get("/skills/summary")
def skills_summary(_admin: dict = Depends(require_admin)):
    if not _skills_library:
        return {"total_skills": 0, "categories": {}}
    return _skills_library.summary()


@router.get("/skills")
def list_skills(category: str | None = None, _admin: dict = Depends(require_admin)):
    if not _skills_library:
        return []
    if category:
        return [s.to_dict() for s in _skills_library.get_by_category(category)]
    return _skills_library.list_all()


@router.get("/skills/{name}")
def get_skill(name: str, _admin: dict = Depends(require_admin)):
    if not _skills_library:
        raise HTTPException(404, "Skills library not loaded")
    skill = _skills_library.get(name)
    if not skill:
        raise HTTPException(404, f"Skill '{name}' not found")
    return skill.to_dict()


@router.post("/skills/sync")
def sync_skills_endpoint(_admin: dict = Depends(require_admin)):
    """Pull latest skills from upstream repo and reload library (admin only)."""
    from app.embient_skills.sync import sync_skills
    result = sync_skills()
    if result["status"] == "ok" and _skills_library:
        if result["added"] > 0 or result["updated"] > 0:
            _skills_library.reload()
    return result


@router.get("/skills/sync/status")
def skills_sync_status(_admin: dict = Depends(require_admin)):
    """Return the last skills sync status."""
    from app.embient_skills.sync import get_sync_status
    return get_sync_status()
