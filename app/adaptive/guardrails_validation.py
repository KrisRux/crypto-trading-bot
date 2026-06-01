"""
Guardrails config validation and diff helpers.

Separated from routes.py so tests can import without pulling in the
full FastAPI/SQLAlchemy dependency chain.
"""


def validate_guardrails_values(cfg: dict) -> list[str]:
    """
    Validate guardrails config values.
    Returns a list of error strings. Empty list = valid.
    """
    errors: list[str] = []

    def _check(path: str, val, min_v=None, max_v=None, typ=None):
        if typ and not isinstance(val, typ):
            errors.append(f"{path}: expected {typ.__name__ if not isinstance(typ, tuple) else '/'.join(t.__name__ for t in typ)}, got {type(val).__name__}")
            return
        if min_v is not None and val < min_v:
            errors.append(f"{path}: {val} < min {min_v}")
        if max_v is not None and val > max_v:
            errors.append(f"{path}: {val} > max {max_v}")

    # Trade gate regimes
    for regime in ("defensive", "range", "trend", "volatile"):
        g = cfg.get("trade_gate", {}).get(regime, {})
        if not g:
            continue
        _check(f"trade_gate.{regime}.min_adx", g.get("min_adx", 25), 5, 60, (int, float))
        _check(f"trade_gate.{regime}.min_volume_ratio", g.get("min_volume_ratio", 1), 0.1, 5, (int, float))
        _check(f"trade_gate.{regime}.min_bb_width_pct", g.get("min_bb_width_pct", 0), 0, 10, (int, float))

    # Dynamic score
    ds = cfg.get("dynamic_score", {})
    _check("dynamic_score.base_min_score", ds.get("base_min_score", 80), 0, 100, (int, float))
    _check("dynamic_score.max_score_cap", ds.get("max_score_cap", 95), 50, 100, (int, float))

    # Kill switch
    ks = cfg.get("kill_switch", {})
    _check("kill_switch.consecutive_losses_threshold", ks.get("consecutive_losses_threshold", 6), 2, 20, (int,))
    _check("kill_switch.pause_minutes_losses", ks.get("pause_minutes_losses", 90), 10, 480, (int,))
    _check("kill_switch.pause_minutes_drawdown", ks.get("pause_minutes_drawdown", 120), 10, 480, (int,))

    # Risk scaling
    rs = cfg.get("risk_scaling", {})
    _check("risk_scaling.consecutive_losses_3_multiplier", rs.get("consecutive_losses_3_multiplier", 0.75), 0.1, 1.0, (int, float))
    _check("risk_scaling.consecutive_losses_5_multiplier", rs.get("consecutive_losses_5_multiplier", 0.50), 0.1, 1.0, (int, float))

    # Entry limits
    et = cfg.get("entry_throttle", {})
    _check("entry_throttle.max_open_positions", et.get("max_open_positions", 1), 1, 20, (int,))

    # Stale position exit
    sp = cfg.get("stale_position", {})
    _check("stale_position.max_holding_hours", sp.get("max_holding_hours", 48), 1, 24 * 14, (int, float))
    _check("stale_position.min_loss_pct", sp.get("min_loss_pct", 0.5), 0, 20, (int, float))
    _check("stale_position.flat_holding_hours", sp.get("flat_holding_hours", 72), 1, 24 * 21, (int, float))
    _check("stale_position.flat_abs_pnl_pct", sp.get("flat_abs_pnl_pct", 0.2), 0, 5, (int, float))
    _check("stale_position.profit_lock_trigger_pct", sp.get("profit_lock_trigger_pct", 3.0), 0.1, 50, (int, float))
    _check("stale_position.profit_lock_min_pct", sp.get("profit_lock_min_pct", 0.4), 0, 20, (int, float))
    _check("stale_position.profit_trail_start_pct", sp.get("profit_trail_start_pct", 4.5), 0.1, 50, (int, float))
    _check("stale_position.profit_trail_distance_pct", sp.get("profit_trail_distance_pct", 1.2), 0.1, 20, (int, float))
    _check("stale_position.range_profit_exit_min_pct", sp.get("range_profit_exit_min_pct", 0.8), 0.1, 20, (int, float))
    _check("stale_position.range_profit_exit_min_hours", sp.get("range_profit_exit_min_hours", 12), 0, 24 * 14, (int, float))

    # Performance gate
    pg = cfg.get("performance_gate", {})
    _check("performance_gate.recent_hours", pg.get("recent_hours", 168), 1, 24 * 30, (int,))
    _check("performance_gate.symbol_min_recent_trades", pg.get("symbol_min_recent_trades", 2), 1, 100, (int,))
    _check("performance_gate.symbol_max_recent_net_loss", pg.get("symbol_max_recent_net_loss", -3.0), -500, 0, (int, float))
    _check("performance_gate.symbol_min_all_time_trades", pg.get("symbol_min_all_time_trades", 10), 1, 1000, (int,))
    _check("performance_gate.symbol_max_all_time_net_loss", pg.get("symbol_max_all_time_net_loss", -10.0), -1000, 0, (int, float))
    _check("performance_gate.strategy_min_recent_trades", pg.get("strategy_min_recent_trades", 4), 1, 100, (int,))
    _check("performance_gate.strategy_max_recent_net_loss", pg.get("strategy_max_recent_net_loss", -6.0), -500, 0, (int, float))

    # Paper short simulation
    ps = cfg.get("paper_short", {})
    _check("paper_short.min_sell_score", ps.get("min_sell_score", 80), 50, 100, (int, float))
    _check("paper_short.max_open_shorts", ps.get("max_open_shorts", 1), 1, 20, (int,))

    return errors


def diff_configs(old: dict, new: dict, prefix: str = "") -> list[dict]:
    """Compute flat list of changes between two config dicts."""
    diffs: list[dict] = []
    all_keys = set(list(old.keys()) + list(new.keys()))
    for k in sorted(all_keys):
        path = f"{prefix}.{k}" if prefix else k
        ov, nv = old.get(k), new.get(k)
        if isinstance(ov, dict) and isinstance(nv, dict):
            diffs.extend(diff_configs(ov, nv, path))
        elif ov != nv:
            diffs.append({"path": path, "from": ov, "to": nv})
    return diffs
