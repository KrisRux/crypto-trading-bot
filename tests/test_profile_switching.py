"""
Anti-thrashing tests for ProfileManager.

The live bot flipped defensive<->normal 10 times in 5 days (Apr 2026) because:
  - the recovery rule fired on a win-rate computed over tiny samples,
  - the regime rule fired on single noisy snapshots,
  - nothing stopped a switch from being undone minutes later.

These tests pin the three countermeasures: minimum sample + persistence on
recovery, dampening on the regime rule, and the asymmetric flip-flop guard.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.adaptive.profile_manager import ProfileManager


PROFILES = {
    "profiles": {
        "normal": {"auto_apply": True, "requires_approval": False},
        "defensive": {"auto_apply": True, "requires_approval": False},
        "aggressive_trend": {"auto_apply": False, "requires_approval": True},
    },
    "switching_rules": {
        "cooldown_minutes": 0,
        "hysteresis_minutes": 0,
        "max_profile_changes_per_day": 10,
        "min_trades_for_recovery": 5,
        "recovery_persistence_minutes": 120,
        "regime_dampening_minutes": 30,
        "flip_flop_block_minutes": 240,
    },
    "active_profile": "normal",
}

CLEAN_PERF = {
    "win_rate_last_10": 70,
    "drawdown_intraday": 0.0,
    "consecutive_losses": 0,
    "total_recent_trades": 8,
    "api_error_count": 0,
    "pnl_6h_pct": 0.1,
}

BAD_PERF = {
    "win_rate_last_10": 20,
    "drawdown_intraday": 2.0,
    "consecutive_losses": 3,
    "total_recent_trades": 8,
    "api_error_count": 0,
    "pnl_6h_pct": -0.5,
}


@pytest.fixture
def manager(tmp_path):
    profiles_file = tmp_path / "profiles.json"
    profiles_file.write_text(json.dumps(PROFILES))
    state_file = tmp_path / "profile_state.json"
    return ProfileManager(str(profiles_file), str(state_file))


def _set_active(mgr, name):
    mgr._active_profile = name


def test_recovery_requires_min_sample(manager):
    _set_active(manager, "defensive")
    perf = dict(CLEAN_PERF, total_recent_trades=2)  # WR=70% but on 2 trades
    assert manager.evaluate_switch(perf, "trend") is None


def test_recovery_blocked_while_regime_defensive(manager):
    _set_active(manager, "defensive")
    # Clean metrics but the regime itself is still defensive: loosening now
    # would be flipped straight back by Rule 5 (the historical thrash loop).
    assert manager.evaluate_switch(CLEAN_PERF, "defensive") is None


def test_recovery_needs_persistence_window(manager):
    _set_active(manager, "defensive")
    # First clean evaluation only ARMS the recovery — no switch yet.
    assert manager.evaluate_switch(CLEAN_PERF, "trend") is None
    # Still inside the persistence window -> still no switch.
    assert manager.evaluate_switch(CLEAN_PERF, "trend") is None
    # Simulate the window having elapsed.
    manager._recovery_pending_since = (
        datetime.now(timezone.utc) - timedelta(minutes=121)
    )
    result = manager.evaluate_switch(CLEAN_PERF, "trend")
    assert result is not None and result["to"] == "normal"


def test_recovery_persistence_resets_on_dirty_eval(manager):
    _set_active(manager, "defensive")
    assert manager.evaluate_switch(CLEAN_PERF, "trend") is None
    # A dirty evaluation must reset the timer...
    assert manager.evaluate_switch(BAD_PERF, "trend") is None
    assert manager._recovery_pending_since is None
    # ...so an immediately-clean eval starts the window from scratch.
    assert manager.evaluate_switch(CLEAN_PERF, "trend") is None


def test_regime_rule_is_dampened(manager):
    _set_active(manager, "normal")
    # First defensive snapshot: timer armed, no switch.
    assert manager.evaluate_switch(CLEAN_PERF, "defensive") is None
    # After the dampening window the switch fires.
    manager._bad_regime_since = (
        datetime.now(timezone.utc) - timedelta(minutes=31)
    )
    result = manager.evaluate_switch(CLEAN_PERF, "defensive")
    assert result is not None and result["to"] == "defensive"


def test_regime_dampening_resets_when_regime_heals(manager):
    _set_active(manager, "normal")
    assert manager.evaluate_switch(CLEAN_PERF, "defensive") is None
    assert manager._bad_regime_since is not None
    manager.evaluate_switch(CLEAN_PERF, "trend")  # healthy snapshot resets
    assert manager._bad_regime_since is None


def test_flip_flop_guard_blocks_quick_undo(manager):
    _set_active(manager, "normal")
    # History: defensive -> normal 10 minutes ago.
    manager._switch_history = [{
        "from": "defensive", "to": "normal",
        "at": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(),
        "reason": "test",
    }]
    # Going back to defensive IS allowed (tightening is never delayed)...
    manager._bad_regime_since = (
        datetime.now(timezone.utc) - timedelta(minutes=31)
    )
    result = manager.evaluate_switch(CLEAN_PERF, "defensive")
    assert result is not None and result["to"] == "defensive"

    # ...but the reverse (undoing a tightening with a loosening) is blocked.
    _set_active(manager, "defensive")
    manager._switch_history = [{
        "from": "normal", "to": "defensive",
        "at": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(),
        "reason": "test",
    }]
    manager._recovery_pending_since = (
        datetime.now(timezone.utc) - timedelta(minutes=121)
    )
    assert manager.evaluate_switch(CLEAN_PERF, "trend") is None


def test_april_26_thrash_scenario_cannot_recur(manager):
    """Replay of the real 26 Apr 2026 log: 4 switches in 8h30. With the new
    rules at most ONE switch can happen in that window from the same inputs."""
    _set_active(manager, "normal")
    switches = 0
    now = datetime.now(timezone.utc)

    # 14:02 defensive regime appears -> only arms dampening
    if manager.evaluate_switch(CLEAN_PERF, "defensive"):
        switches += 1
    # 14:35 still defensive -> dampening elapsed -> tighten (legitimate)
    manager._bad_regime_since = now - timedelta(minutes=33)
    res = manager.evaluate_switch(CLEAN_PERF, "defensive")
    if res:
        switches += 1
        manager.apply_profile = None  # not needed; emulate switch manually
        manager._active_profile = "defensive"
        manager._switch_history.append({
            "from": "normal", "to": "defensive",
            "at": now.isoformat(), "reason": "regime",
        })
        manager._last_switch_time = now
    # 16:00 WR snaps back to 100% on 3 trades -> recovery must NOT fire
    perf_small = dict(CLEAN_PERF, win_rate_last_10=100, total_recent_trades=3)
    if manager.evaluate_switch(perf_small, "trend"):
        switches += 1
    # 19:30 clean but flip-flop guard window still open -> no loosening
    manager._recovery_pending_since = now - timedelta(minutes=125)
    if manager.evaluate_switch(CLEAN_PERF, "trend"):
        switches += 1

    assert switches == 1, f"expected a single switch, got {switches}"
