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
