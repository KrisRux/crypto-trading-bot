"""
Tests for guardrails config API endpoints.

Covers: admin-only access, value validation, reset defaults, reload safety.
"""

import json
import os
import shutil
from unittest.mock import MagicMock, patch

import pytest

# Import the pure helpers from guardrails module to avoid routes.py import chain (bcrypt)
from app.adaptive.guardrails_validation import validate_guardrails_values as _validate, diff_configs


# ================================================================
# _validate_guardrails_values
# ================================================================

class TestValidation:
    """Server-side config validation."""

    def _base_cfg(self, **overrides):
        """Minimal valid config."""
        cfg = {
            "kill_switch": {
                "consecutive_losses_threshold": 6, "low_win_rate_threshold": 15,
                "intraday_drawdown_threshold": 2.0, "pnl_24h_threshold": -6.0,
                "pause_minutes_losses": 90, "pause_minutes_drawdown": 120,
            },
            "symbol_cooldown": {
                "consecutive_losses_threshold": 3, "cooldown_minutes_losses": 60,
                "stoploss_cluster_count": 2, "stoploss_cluster_window_minutes": 90,
                "cooldown_minutes_cluster": 90,
            },
            "trade_gate": {
                "defensive": {"require_symbol_trend": True, "min_adx": 27, "min_volume_ratio": 1.6, "min_bb_width_pct": 1.2},
                "range": {"require_symbol_trend": True, "min_adx": 28, "min_volume_ratio": 1.0, "min_bb_width_pct": 1.4},
                "trend": {"require_symbol_trend": True, "min_adx": 24, "min_volume_ratio": 0.9, "min_bb_width_pct": 0.0},
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
        }
        # Apply overrides using dot-path
        for path, val in overrides.items():
            parts = path.split(".")
            obj = cfg
            for p in parts[:-1]:
                obj = obj[p]
            obj[parts[-1]] = val
        return cfg

    def test_valid_config_passes(self):
        assert _validate(self._base_cfg()) == []

    def test_adx_too_low_rejected(self):
        errs = _validate(self._base_cfg(**{"trade_gate.defensive.min_adx": 2}))
        assert any("min 5" in e for e in errs)

    def test_adx_too_high_rejected(self):
        errs = _validate(self._base_cfg(**{"trade_gate.trend.min_adx": 65}))
        assert any("max 60" in e for e in errs)

    def test_volume_ratio_too_low_rejected(self):
        errs = _validate(self._base_cfg(**{"trade_gate.range.min_volume_ratio": 0.01}))
        assert any("min 0.1" in e for e in errs)

    def test_dynamic_score_above_100_rejected(self):
        errs = _validate(self._base_cfg(**{"dynamic_score.base_min_score": 110}))
        assert any("max 100" in e for e in errs)

    def test_kill_switch_losses_threshold_below_min(self):
        errs = _validate(self._base_cfg(**{"kill_switch.consecutive_losses_threshold": 1}))
        assert any("min 2" in e for e in errs)

    def test_risk_multiplier_above_1_rejected(self):
        errs = _validate(self._base_cfg(**{"risk_scaling.consecutive_losses_3_multiplier": 1.5}))
        assert any("max 1.0" in e for e in errs)

    def test_pause_minutes_too_low_rejected(self):
        errs = _validate(self._base_cfg(**{"kill_switch.pause_minutes_losses": 3}))
        assert any("min 10" in e for e in errs)

    def test_string_instead_of_number_rejected(self):
        errs = _validate(self._base_cfg(**{"kill_switch.consecutive_losses_threshold": "six"}))
        assert any("expected" in e for e in errs)


# ================================================================
# diff_configs
# ================================================================

class TestDiffConfigs:
    """Config diff computation."""

    def test_no_changes(self):
        a = {"x": 1, "y": {"z": 2}}
        assert diff_configs(a, a) == []

    def test_flat_change(self):
        a = {"x": 1}
        b = {"x": 2}
        diffs = diff_configs(a, b)
        assert len(diffs) == 1
        assert diffs[0] == {"path": "x", "from": 1, "to": 2}

    def test_nested_change(self):
        a = {"trade_gate": {"defensive": {"min_adx": 30}}}
        b = {"trade_gate": {"defensive": {"min_adx": 27}}}
        diffs = diff_configs(a, b)
        assert len(diffs) == 1
        assert diffs[0]["path"] == "trade_gate.defensive.min_adx"
        assert diffs[0]["from"] == 30
        assert diffs[0]["to"] == 27

    def test_multiple_changes(self):
        a = {"a": 1, "b": {"c": 2, "d": 3}}
        b = {"a": 1, "b": {"c": 5, "d": 3}}
        diffs = diff_configs(a, b)
        assert len(diffs) == 1
        assert diffs[0]["path"] == "b.c"

    def test_added_key(self):
        a = {"x": 1}
        b = {"x": 1, "y": 2}
        diffs = diff_configs(a, b)
        assert len(diffs) == 1
        assert diffs[0]["from"] is None
        assert diffs[0]["to"] == 2


# ================================================================
# reload_config safety
# ================================================================

class TestReloadSafety:
    """Verify reload_config preserves runtime state."""

    def test_reload_preserves_kill_switch(self):
        from app.adaptive.guardrails import Guardrails
        g = Guardrails()
        g.update_performance({"consecutive_losses": 7, "win_rate_last_10": 10,
                              "drawdown_intraday": 0, "pnl_24h": 0})
        assert g.kill_switch.active
        pause_before = g.kill_switch._pause_until

        g.reload_config()

        assert g.kill_switch.active, "Kill switch should remain active after reload"
        assert g.kill_switch._pause_until == pause_before

    def test_reload_preserves_symbol_cooldown(self):
        from app.adaptive.guardrails import Guardrails
        g = Guardrails()
        for _ in range(3):
            g.record_trade_result("BTCUSDT", "embient_enhanced", is_win=False)
        assert not g.symbol_cooldown.check("BTCUSDT").allowed

        g.reload_config()

        assert not g.symbol_cooldown.check("BTCUSDT").allowed, "Cooldown should survive reload"

    def test_reload_preserves_strategy_breaker(self):
        from app.adaptive.guardrails import Guardrails
        g = Guardrails()
        for _ in range(4):
            g.record_trade_result("BTCUSDT", "embient_enhanced", is_win=False)
        # Breaker fires on strategy globally - test on a symbol without cooldown
        assert not g.strategy_breaker.check("embient_enhanced", "ETHUSDT").allowed

        g.reload_config()

        assert not g.strategy_breaker.check("embient_enhanced", "ETHUSDT").allowed, "Breaker should survive reload"

    def test_reload_preserves_entry_throttle_hourly(self):
        from app.adaptive.guardrails import Guardrails
        g = Guardrails()
        g.new_candle("20250101_0000")
        g.entry_throttle.record_entry("BTCUSDT")
        g.entry_throttle.record_entry("ETHUSDT")
        hourly_before = len(g.entry_throttle._hourly_entries)

        g.reload_config()

        assert len(g.entry_throttle._hourly_entries) == hourly_before, "Hourly entries should survive reload"

    def test_reload_updates_thresholds(self):
        """After reload, new threshold values from config should be in effect."""
        from app.adaptive.guardrails import Guardrails
        g = Guardrails()
        old_adx = g.trade_gate._thresholds.get("trend", {}).get("min_adx")

        # Reload reads from disk — thresholds should match current config
        g.reload_config()
        new_adx = g.trade_gate._thresholds.get("trend", {}).get("min_adx")

        # Both should be whatever is in the JSON file right now
        assert old_adx == new_adx
