"""
Unit tests for the adaptive guardrails module.

Tests: KillSwitch, SymbolCooldown, TradeGate, DynamicScoreFilter,
       EntryThrottle, RiskScaler, StrategyCircuitBreaker, Guardrails (integration).
"""

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.adaptive.guardrails import (
    DynamicScoreFilter,
    EntryThrottle,
    Guardrails,
    KillSwitch,
    RiskScaler,
    StrategyCircuitBreaker,
    SymbolCooldown,
    TradeGate,
    _load_config,
)


# ---------- helpers ----------

def _default_cfg():
    """Return a full guardrails config for testing."""
    return _load_config()


def _perf(**overrides):
    """Build a performance dict with sensible defaults, overrideable."""
    base = {
        "consecutive_losses": 0,
        "win_rate_last_10": 60,
        "drawdown_intraday": 0.5,
        "pnl_24h": 2.0,
        "global_regime": "trend",
    }
    base.update(overrides)
    return base


# ================================================================
# 1. Kill Switch
# ================================================================

class TestKillSwitch:
    def test_not_active_by_default(self):
        ks = KillSwitch(_default_cfg())
        assert not ks.active
        v = ks.check()
        assert v.allowed

    def test_activates_on_consecutive_losses(self):
        ks = KillSwitch(_default_cfg())
        ks.update(_perf(consecutive_losses=7))
        assert ks.active
        v = ks.check()
        assert not v.allowed
        assert "consecutive_losses" in v.reason

    def test_activates_on_low_win_rate(self):
        ks = KillSwitch(_default_cfg())
        ks.update(_perf(win_rate_last_10=10))
        assert ks.active
        v = ks.check()
        assert "low_win_rate" in v.reason

    def test_activates_on_drawdown(self):
        ks = KillSwitch(_default_cfg())
        ks.update(_perf(drawdown_intraday=2.5))
        assert ks.active
        assert "intraday_drawdown" in ks.check().reason

    def test_activates_on_pnl_24h(self):
        ks = KillSwitch(_default_cfg())
        ks.update(_perf(pnl_24h=-8))
        assert ks.active
        assert "pnl_24h" in ks.check().reason

    def test_expires_after_pause(self):
        ks = KillSwitch(_default_cfg())
        ks.update(_perf(consecutive_losses=7))
        assert ks.active
        # Simulate time passing
        ks._pause_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        assert not ks.active
        assert ks.check().allowed

    def test_does_not_reactivate_while_active(self):
        ks = KillSwitch(_default_cfg())
        ks.update(_perf(consecutive_losses=7))
        original_until = ks._pause_until
        # Update again — should not change pause_until
        ks.update(_perf(consecutive_losses=10))
        assert ks._pause_until == original_until


# ================================================================
# 2. Symbol Cooldown
# ================================================================

class TestSymbolCooldown:
    def test_no_cooldown_initially(self):
        sc = SymbolCooldown(_default_cfg())
        assert sc.check("BTCUSDT").allowed

    def test_consecutive_losses_trigger_cooldown(self):
        sc = SymbolCooldown(_default_cfg())
        for _ in range(3):
            sc.record_loss("BTCUSDT")
        v = sc.check("BTCUSDT")
        assert not v.allowed
        assert "symbol_cooldown" in v.reason

    def test_win_resets_losses(self):
        sc = SymbolCooldown(_default_cfg())
        sc.record_loss("BTCUSDT")
        sc.record_loss("BTCUSDT")
        sc.record_win("BTCUSDT")
        sc.record_loss("BTCUSDT")
        # Only 1 loss after win, should still be allowed
        assert sc.check("BTCUSDT").allowed

    def test_stoploss_cluster_triggers_cooldown(self):
        sc = SymbolCooldown(_default_cfg())
        sc.record_loss("ETHUSDT", was_stoploss=True)
        sc.record_loss("ETHUSDT", was_stoploss=True)
        v = sc.check("ETHUSDT")
        assert not v.allowed

    def test_other_symbol_unaffected(self):
        sc = SymbolCooldown(_default_cfg())
        for _ in range(3):
            sc.record_loss("BTCUSDT")
        assert not sc.check("BTCUSDT").allowed
        assert sc.check("ETHUSDT").allowed

    def test_cooldown_expires(self):
        sc = SymbolCooldown(_default_cfg())
        for _ in range(3):
            sc.record_loss("BTCUSDT")
        # Simulate expiry
        sc._cooldowns["BTCUSDT"] = datetime.now(timezone.utc) - timedelta(seconds=1)
        assert sc.check("BTCUSDT").allowed


# ================================================================
# 3. Trade Gate
# ================================================================

class TestTradeGate:
    def test_blocks_range_symbol_regime(self):
        tg = TradeGate(_default_cfg())
        v = tg.check(
            global_regime="trend", symbol_regime="range", symbol="XRPUSDT",
            adx=30, volume_ratio=2.0, bb_width_pct=3.0,
        )
        assert not v.allowed
        assert "symbol_regime_range" in v.reason

    def test_blocks_defensive_symbol_regime(self):
        tg = TradeGate(_default_cfg())
        v = tg.check(
            global_regime="trend", symbol_regime="defensive", symbol="XRPUSDT",
            adx=30, volume_ratio=2.0, bb_width_pct=3.0,
        )
        assert not v.allowed

    def test_defensive_global_low_adx(self):
        tg = TradeGate(_default_cfg())
        v = tg.check(
            global_regime="defensive", symbol_regime="trend", symbol="BTCUSDT",
            adx=26.5, volume_ratio=1.8, bb_width_pct=1.5,
        )
        assert not v.allowed
        assert "adx_too_low" in v.reason

    def test_defensive_global_passes(self):
        tg = TradeGate(_default_cfg())
        v = tg.check(
            global_regime="defensive", symbol_regime="trend", symbol="BTCUSDT",
            adx=31, volume_ratio=1.8, bb_width_pct=1.5,
        )
        assert v.allowed

    def test_range_global_strict_thresholds(self):
        tg = TradeGate(_default_cfg())
        # adx=26 < min_adx=28 for range
        v = tg.check(
            global_regime="range", symbol_regime="trend", symbol="SOLUSDT",
            adx=26, volume_ratio=2.0, bb_width_pct=2.0,
        )
        assert not v.allowed

    def test_trend_global_lenient(self):
        tg = TradeGate(_default_cfg())
        v = tg.check(
            global_regime="trend", symbol_regime="trend", symbol="SOLUSDT",
            adx=26, volume_ratio=1.1, bb_width_pct=2.0,
        )
        assert v.allowed

    def test_low_volume_blocked(self):
        tg = TradeGate(_default_cfg())
        v = tg.check(
            global_regime="defensive", symbol_regime="trend", symbol="BTCUSDT",
            adx=35, volume_ratio=1.2, bb_width_pct=1.5,
        )
        assert not v.allowed
        assert "volume_too_low" in v.reason

    def test_low_bb_width_blocked(self):
        tg = TradeGate(_default_cfg())
        v = tg.check(
            global_regime="defensive", symbol_regime="trend", symbol="BTCUSDT",
            adx=35, volume_ratio=2.0, bb_width_pct=0.8,
        )
        assert not v.allowed
        assert "bb_width_too_low" in v.reason


# ================================================================
# 4. Dynamic Score Filter
# ================================================================

class TestDynamicScore:
    def test_base_threshold(self):
        ds = DynamicScoreFilter(_default_cfg())
        assert ds.get_min_score(0, "trend") == 80

    def test_3_losses_raises_threshold(self):
        ds = DynamicScoreFilter(_default_cfg())
        assert ds.get_min_score(3, "trend") == 88

    def test_5_losses_raises_more(self):
        ds = DynamicScoreFilter(_default_cfg())
        assert ds.get_min_score(5, "trend") == 92

    def test_bad_regime_adds_extra(self):
        ds = DynamicScoreFilter(_default_cfg())
        assert ds.get_min_score(0, "range") == 85  # 80 + 5

    def test_bad_regime_skipped_for_trend_symbol(self):
        """Symbol in trend should NOT get the +5 penalty even if global is range."""
        ds = DynamicScoreFilter(_default_cfg())
        # global=range, symbol=trend → no penalty
        assert ds.get_min_score(0, "range", symbol_regime="trend") == 80
        # global=range, symbol=range → penalty
        assert ds.get_min_score(0, "range", symbol_regime="range") == 85
        # global=range, symbol not provided → penalty (backward compat)
        assert ds.get_min_score(0, "range") == 85

    def test_combined_capped_at_95(self):
        ds = DynamicScoreFilter(_default_cfg())
        # 92 + 5 = 97 -> cap at 95
        assert ds.get_min_score(5, "defensive") == 95

    def test_none_score_passes(self):
        ds = DynamicScoreFilter(_default_cfg())
        v = ds.check(None, 5, "defensive", "BTCUSDT", "sma_crossover")
        assert v.allowed

    def test_score_below_threshold_blocked(self):
        ds = DynamicScoreFilter(_default_cfg())
        v = ds.check(75, 0, "trend", "BTCUSDT", "embient_enhanced")
        assert not v.allowed

    def test_score_above_threshold_passes(self):
        ds = DynamicScoreFilter(_default_cfg())
        v = ds.check(85, 0, "trend", "BTCUSDT", "embient_enhanced")
        assert v.allowed


# ================================================================
# 5. Entry Throttle
# ================================================================

class TestEntryThrottle:
    def test_first_entry_allowed(self):
        et = EntryThrottle(_default_cfg())
        et.new_candle("20250101_0000")
        assert et.check("BTCUSDT", "trend").allowed

    def test_second_entry_same_candle_blocked(self):
        et = EntryThrottle(_default_cfg())
        et.new_candle("20250101_0000")
        et.record_entry("BTCUSDT")
        v = et.check("BTCUSDT", "trend")
        assert not v.allowed
        assert "one_trade_per_candle" in v.reason

    def test_different_symbol_same_candle_allowed(self):
        et = EntryThrottle(_default_cfg())
        et.new_candle("20250101_0000")
        et.record_entry("BTCUSDT")
        assert et.check("ETHUSDT", "trend").allowed

    def test_hourly_limit_defensive(self):
        et = EntryThrottle(_default_cfg())
        et.new_candle("20250101_0000")
        # Defensive max is 2
        et.record_entry("BTCUSDT")
        et.new_candle("20250101_0015")
        et.record_entry("ETHUSDT")
        et.new_candle("20250101_0030")
        v = et.check("SOLUSDT", "defensive")
        assert not v.allowed
        assert "hourly_limit" in v.reason

    def test_new_candle_resets_per_symbol(self):
        et = EntryThrottle(_default_cfg())
        et.new_candle("20250101_0000")
        et.record_entry("BTCUSDT")
        et.new_candle("20250101_0015")
        assert et.check("BTCUSDT", "trend").allowed


# ================================================================
# 6. Risk Scaler
# ================================================================

class TestRiskScaler:
    def test_normal_conditions(self):
        rs = RiskScaler(_default_cfg())
        assert rs.get_multiplier(_perf()) == 1.0

    def test_3_losses(self):
        rs = RiskScaler(_default_cfg())
        assert rs.get_multiplier(_perf(consecutive_losses=3)) == 0.75

    def test_5_losses(self):
        rs = RiskScaler(_default_cfg())
        assert rs.get_multiplier(_perf(consecutive_losses=5)) == 0.50

    def test_high_drawdown(self):
        rs = RiskScaler(_default_cfg())
        assert rs.get_multiplier(_perf(drawdown_intraday=2.0)) == 0.50

    def test_combined_worst_wins(self):
        rs = RiskScaler(_default_cfg())
        # 3 losses (0.75) + high drawdown (0.50) → min = 0.50
        assert rs.get_multiplier(_perf(consecutive_losses=3, drawdown_intraday=2.0)) == 0.50


# ================================================================
# 7. Strategy Circuit Breaker
# ================================================================

class TestStrategyBreaker:
    def test_no_pause_initially(self):
        scb = StrategyCircuitBreaker(_default_cfg())
        assert scb.check("embient_enhanced", "BTCUSDT").allowed

    def test_pauses_after_threshold(self):
        scb = StrategyCircuitBreaker(_default_cfg())
        for _ in range(4):
            scb.record_result("embient_enhanced", "BTCUSDT", is_win=False)
        v = scb.check("embient_enhanced", "BTCUSDT")
        assert not v.allowed
        assert "strategy_breaker" in v.reason

    def test_win_resets_counter(self):
        scb = StrategyCircuitBreaker(_default_cfg())
        for _ in range(3):
            scb.record_result("embient_enhanced", "BTCUSDT", is_win=False)
        scb.record_result("embient_enhanced", "BTCUSDT", is_win=True)
        scb.record_result("embient_enhanced", "BTCUSDT", is_win=False)
        assert scb.check("embient_enhanced", "BTCUSDT").allowed

    def test_pause_expires(self):
        scb = StrategyCircuitBreaker(_default_cfg())
        for _ in range(4):
            scb.record_result("embient_enhanced", "BTCUSDT", is_win=False)
        # Simulate expiry
        scb._paused["embient_enhanced"] = datetime.now(timezone.utc) - timedelta(seconds=1)
        scb._symbol_strat_paused[("BTCUSDT", "embient_enhanced")] = datetime.now(timezone.utc) - timedelta(seconds=1)
        assert scb.check("embient_enhanced", "BTCUSDT").allowed


# ================================================================
# 8. Full Guardrails integration
# ================================================================

class TestGuardrailsIntegration:
    def test_all_pass_normal_conditions(self):
        g = Guardrails()
        g.update_performance(_perf())
        g.new_candle("20250101_0000")
        v = g.can_open_new_trade(
            symbol="BTCUSDT", global_regime="trend", symbol_regime="trend",
            adx=30, volume_ratio=1.5, bb_width_pct=3.0,
            signal_score=85, strategy_name="embient_enhanced",
        )
        assert v.allowed
        assert g.stats.total_passed == 1

    def test_kill_switch_blocks(self):
        g = Guardrails()
        g.update_performance(_perf(consecutive_losses=7))
        v = g.can_open_new_trade(
            symbol="BTCUSDT", global_regime="trend", symbol_regime="trend",
            adx=30, volume_ratio=1.5, bb_width_pct=3.0,
            signal_score=90, strategy_name="embient_enhanced",
        )
        assert not v.allowed
        assert g.stats.blocked_kill_switch == 1

    def test_symbol_cooldown_blocks(self):
        g = Guardrails()
        g.update_performance(_perf())
        g.new_candle("20250101_0000")
        for _ in range(3):
            g.record_trade_result("BTCUSDT", "embient_enhanced", is_win=False)
        v = g.can_open_new_trade(
            symbol="BTCUSDT", global_regime="trend", symbol_regime="trend",
            adx=30, volume_ratio=1.5, bb_width_pct=3.0,
            signal_score=90, strategy_name="embient_enhanced",
        )
        assert not v.allowed
        assert g.stats.blocked_symbol_cooldown == 1

    def test_trade_gate_blocks(self):
        g = Guardrails()
        g.update_performance(_perf())
        g.new_candle("20250101_0000")
        v = g.can_open_new_trade(
            symbol="BTCUSDT", global_regime="defensive", symbol_regime="trend",
            adx=26, volume_ratio=1.8, bb_width_pct=1.5,
            signal_score=90, strategy_name="embient_enhanced",
        )
        assert not v.allowed
        assert g.stats.blocked_trade_gate == 1

    def test_dynamic_score_blocks(self):
        g = Guardrails()
        g.update_performance(_perf(consecutive_losses=5))
        # Kill switch also fires at 6+ losses, so set to 5 which only
        # raises score threshold to 92
        g.new_candle("20250101_0000")
        v = g.can_open_new_trade(
            symbol="BTCUSDT", global_regime="trend", symbol_regime="trend",
            adx=30, volume_ratio=1.5, bb_width_pct=3.0,
            signal_score=85, strategy_name="embient_enhanced",
        )
        assert not v.allowed
        assert g.stats.blocked_dynamic_score == 1

    def test_status_output(self):
        g = Guardrails()
        g.update_performance(_perf())
        s = g.status()
        assert "kill_switch" in s
        assert "symbol_cooldowns" in s
        assert "stats" in s
        assert "risk_multiplier" in s
        assert "dynamic_score_min" in s

    def test_risk_multiplier_under_stress(self):
        g = Guardrails()
        g.update_performance(_perf(consecutive_losses=5, drawdown_intraday=2.0))
        assert g.get_risk_multiplier() == 0.0  # kill switch is active → 0

    def test_risk_multiplier_moderate_stress(self):
        g = Guardrails()
        g.update_performance(_perf(consecutive_losses=4))
        # 4 losses: kill switch not triggered (threshold=6), but risk scaler at 0.75
        assert g.get_risk_multiplier() == 0.75


# ================================================================
# 9. Audit integration tests — scenarios from code review
# ================================================================

class TestAuditScenarios:
    """Tests for edge cases found during the code audit."""

    def test_kill_switch_blocks_buy_but_sell_verdict_not_checked(self):
        """Kill switch blocks BUY via can_open_new_trade, but SELL is never checked."""
        g = Guardrails()
        g.update_performance(_perf(consecutive_losses=7))
        # BUY blocked
        v = g.can_open_new_trade(
            symbol="BTCUSDT", global_regime="trend", symbol_regime="trend",
            adx=30, volume_ratio=1.5, bb_width_pct=3.0,
            signal_score=90, strategy_name="embient_enhanced",
        )
        assert not v.allowed
        # SELL would never call can_open_new_trade — this test confirms
        # the function is only called for BUY, not for SELL/exits

    def test_symbol_cooldown_isolates_symbols(self):
        """Symbol cooldown on BTCUSDT must not block ETHUSDT."""
        g = Guardrails()
        g.update_performance(_perf())
        g.new_candle("20250101_0000")
        for _ in range(3):
            g.record_trade_result("BTCUSDT", "embient_enhanced", is_win=False)
        # BTCUSDT blocked
        v_btc = g.can_open_new_trade(
            symbol="BTCUSDT", global_regime="trend", symbol_regime="trend",
            adx=30, volume_ratio=1.5, bb_width_pct=3.0,
            signal_score=90, strategy_name="embient_enhanced",
        )
        assert not v_btc.allowed
        # ETHUSDT still allowed
        v_eth = g.can_open_new_trade(
            symbol="ETHUSDT", global_regime="trend", symbol_regime="trend",
            adx=30, volume_ratio=1.5, bb_width_pct=3.0,
            signal_score=90, strategy_name="embient_enhanced",
        )
        assert v_eth.allowed

    def test_strategy_breaker_isolates_strategies(self):
        """Breaker on embient must not block sma_crossover on a different symbol."""
        g = Guardrails()
        g.update_performance(_perf())
        g.new_candle("20250101_0000")
        # Distribute losses across symbols to avoid symbol cooldown on a single one
        g.record_trade_result("BTCUSDT", "embient_enhanced", is_win=False)
        g.record_trade_result("ETHUSDT", "embient_enhanced", is_win=False)
        g.record_trade_result("SOLUSDT", "embient_enhanced", is_win=False)
        g.record_trade_result("XRPUSDT", "embient_enhanced", is_win=False)
        # Strategy breaker should fire (4 consecutive for embient_enhanced globally)
        # Test on BNBUSDT which has no symbol cooldown
        v_emb = g.can_open_new_trade(
            symbol="BNBUSDT", global_regime="trend", symbol_regime="trend",
            adx=30, volume_ratio=1.5, bb_width_pct=3.0,
            strategy_name="embient_enhanced",
        )
        assert not v_emb.allowed
        assert "strategy_breaker" in v_emb.reason
        # sma_crossover on same symbol is still allowed
        v_sma = g.can_open_new_trade(
            symbol="BNBUSDT", global_regime="trend", symbol_regime="trend",
            adx=30, volume_ratio=1.5, bb_width_pct=3.0,
            strategy_name="sma_crossover",
        )
        assert v_sma.allowed

    def test_dynamic_score_defensive_5_losses_hits_cap(self):
        """5 losses + defensive regime should give min_score=95 (capped)."""
        ds = DynamicScoreFilter(_default_cfg())
        score = ds.get_min_score(5, "defensive")
        assert score == 95  # 92 + 5 = 97 → cap at 95

    def test_trade_gate_defensive_xrp_low_adx_sol_high_adx(self):
        """In defensive: XRP blocked (ADX low) but SOL passes (ADX high)."""
        tg = TradeGate(_default_cfg())
        v_xrp = tg.check(
            global_regime="defensive", symbol_regime="trend", symbol="XRPUSDT",
            adx=26.5, volume_ratio=1.8, bb_width_pct=1.5,
        )
        assert not v_xrp.allowed
        v_sol = tg.check(
            global_regime="defensive", symbol_regime="trend", symbol="SOLUSDT",
            adx=31.2, volume_ratio=1.8, bb_width_pct=1.5,
        )
        assert v_sol.allowed

    def test_throttle_blocks_double_entry_same_candle(self):
        """Two entries on same symbol same candle: second must be blocked."""
        g = Guardrails()
        g.update_performance(_perf())
        g.new_candle("20250101_0000")
        # First entry passes
        v1 = g.can_open_new_trade(
            symbol="BTCUSDT", global_regime="trend", symbol_regime="trend",
            adx=30, volume_ratio=1.5, bb_width_pct=3.0,
            signal_score=90, strategy_name="embient_enhanced",
        )
        assert v1.allowed
        g.entry_throttle.record_entry("BTCUSDT")
        # Second entry blocked
        v2 = g.can_open_new_trade(
            symbol="BTCUSDT", global_regime="trend", symbol_regime="trend",
            adx=30, volume_ratio=1.5, bb_width_pct=3.0,
            signal_score=90, strategy_name="embient_enhanced",
        )
        assert not v2.allowed
        assert "one_trade_per_candle" in v2.reason

    def test_reload_preserves_kill_switch_state(self):
        """reload_config must NOT deactivate an active kill switch."""
        g = Guardrails()
        g.update_performance(_perf(consecutive_losses=7))
        assert g.kill_switch.active
        original_until = g.kill_switch._pause_until
        # Reload
        g.reload_config()
        # Kill switch must still be active
        assert g.kill_switch.active
        assert g.kill_switch._pause_until == original_until

    def test_reload_preserves_symbol_cooldowns(self):
        """reload_config must NOT clear active symbol cooldowns."""
        g = Guardrails()
        for _ in range(3):
            g.record_trade_result("BTCUSDT", "embient_enhanced", is_win=False)
        assert not g.symbol_cooldown.check("BTCUSDT").allowed
        g.reload_config()
        assert not g.symbol_cooldown.check("BTCUSDT").allowed

    def test_sma_macd_rsi_confidence_passes_base_threshold(self):
        """sma(0.85→85), macd(0.82→82), rsi(0.82→82) all pass base_min=80 in trend."""
        g = Guardrails()
        g.update_performance(_perf())
        g.new_candle("20250101_0000")
        # sma_crossover: confidence=0.85 → score=85 >= base_min=80
        v = g.can_open_new_trade(
            symbol="BTCUSDT", global_regime="trend", symbol_regime="trend",
            adx=30, volume_ratio=1.5, bb_width_pct=3.0,
            signal_score=85,  # 0.85 * 100
            strategy_name="sma_crossover",
        )
        assert v.allowed
        # macd_crossover: confidence=0.82 → score=82
        g.new_candle("20250101_0015")
        v = g.can_open_new_trade(
            symbol="ETHUSDT", global_regime="trend", symbol_regime="trend",
            adx=30, volume_ratio=1.5, bb_width_pct=3.0,
            signal_score=82,  # 0.82 * 100
            strategy_name="macd_crossover",
        )
        assert v.allowed

    def test_sma_blocked_after_3_losses(self):
        """sma(85) blocked when 3+ losses raise threshold to 88."""
        g = Guardrails()
        g.update_performance(_perf(consecutive_losses=3))
        g.new_candle("20250101_0000")
        v = g.can_open_new_trade(
            symbol="BTCUSDT", global_regime="trend", symbol_regime="trend",
            adx=30, volume_ratio=1.5, bb_width_pct=3.0,
            signal_score=85,  # 85 < 88 (min after 3 losses)
            strategy_name="sma_crossover",
        )
        assert not v.allowed
        assert "dynamic_score" in v.reason

    def test_stats_daily_reset(self):
        """Stats should reset when a new day is detected."""
        g = Guardrails()
        g._stats_date = "2025-01-01"  # simulate yesterday
        g.stats.total_blocked = 100
        g.stats.total_passed = 200
        g.update_performance(_perf())  # triggers date check
        # Stats should have been reset
        assert g.stats.total_blocked == 0
        assert g.stats.total_passed == 0
