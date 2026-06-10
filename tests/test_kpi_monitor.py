"""
Tests for the permanent improvement loop's KPI monitor (app/adaptive/kpi_monitor.py).

Pure-computation tests on trade-like objects — no DB, no network. The numbers
mirror the failure modes found in the 2026-06 profitability review (negative
expectancy, cost ratio > 1, per-strategy divergence, bot asleep in a bull).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.adaptive.kpi_monitor import KPIMonitor, DEFAULT_THRESHOLDS


NOW = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)


def _trade(pnl, *, strategy="stratA", gross=None, fee=0.2, slippage=0.04,
           days_ago=1.0, entry=100.0, qty=1.0):
    return SimpleNamespace(
        pnl=pnl, gross_pnl=gross if gross is not None else pnl + fee + slippage,
        fee=fee, slippage=slippage, pnl_pct=pnl / (entry * qty) * 100,
        strategy=strategy, entry_price=entry, quantity=qty,
        closed_at=NOW - timedelta(days=days_ago),
    )


@pytest.fixture
def monitor(tmp_path):
    return KPIMonitor(config_path=str(tmp_path / "missing_kpi.json"))


def test_defaults_loaded_without_config(monitor):
    assert monitor.thresholds == DEFAULT_THRESHOLDS


def test_overall_metrics(monitor):
    trades = [_trade(2.0), _trade(-1.0), _trade(-0.5), _trade(1.5)]
    kpi = monitor.compute_from_trades(trades, [], 10_000.0, now=NOW)
    o = kpi["overall"]
    assert o["trades"] == 4
    assert o["net_pnl"] == pytest.approx(2.0)
    assert o["win_rate"] == 50.0
    assert o["profit_factor"] == pytest.approx(3.5 / 1.5, abs=1e-3)
    assert o["expectancy"] == pytest.approx(0.5)
    assert o["costs"] == pytest.approx(4 * 0.24)
    assert kpi["exposure_pct"] == 0.0


def test_per_strategy_attribution_is_the_ab_table(monitor):
    trades = ([_trade(1.0, strategy="regime_breakout") for _ in range(3)]
              + [_trade(-1.0, strategy="embient_enhanced") for _ in range(3)])
    kpi = monitor.compute_from_trades(trades, [], 10_000.0, now=NOW)
    s = kpi["strategies"]
    assert s["regime_breakout"]["net_pnl"] == pytest.approx(3.0)
    assert s["embient_enhanced"]["net_pnl"] == pytest.approx(-3.0)
    assert s["embient_enhanced"]["win_rate"] == 0.0


def test_alarms_fire_on_april_like_numbers(monitor):
    # 24 trades, negative expectancy, costs > gross profits — the live April
    # profile. Every quality alarm must fire.
    trades = ([_trade(0.3, fee=0.3, slippage=0.06) for _ in range(6)]
              + [_trade(-0.4, fee=0.3, slippage=0.06) for _ in range(18)])
    kpi = monitor.compute_from_trades(trades, [], 10_000.0, now=NOW)
    keys = {a["key"] for a in monitor.evaluate_alarms(kpi)}
    assert {"expectancy", "profit_factor", "cost_ratio"} <= keys


def test_no_quality_alarms_below_min_sample(monitor):
    trades = [_trade(-1.0) for _ in range(5)]  # only 5 trades
    kpi = monitor.compute_from_trades(trades, [], 10_000.0, now=NOW)
    keys = {a["key"] for a in monitor.evaluate_alarms(kpi)}
    assert "expectancy" not in keys and "profit_factor" not in keys


def test_drawdown_alarm_is_critical(monitor):
    # One -250 USDT trade on 10k capital = 2.5% DD > 2% threshold.
    trades = [_trade(-250.0, entry=10_000.0, qty=1.0)] + [
        _trade(0.5, days_ago=d) for d in (2, 3)]
    kpi = monitor.compute_from_trades(trades, [], 10_000.0, now=NOW)
    dd = [a for a in monitor.evaluate_alarms(kpi) if a["key"] == "drawdown"]
    assert dd and dd[0]["level"] == "CRITICAL"


def test_review_trigger_pf_collapse(monitor):
    trades = ([_trade(0.5) for _ in range(8)]
              + [_trade(-0.6) for _ in range(24)])  # 32 trades, PF ~0.28
    kpi = monitor.compute_from_trades(trades, [], 10_000.0, now=NOW)
    keys = {t["key"] for t in monitor.review_triggers(kpi)}
    assert "pf_collapse" in keys


def test_review_trigger_negative_strategy(monitor):
    trades = [_trade(-0.2, strategy="embient_enhanced") for _ in range(20)]
    kpi = monitor.compute_from_trades(trades, [], 10_000.0, now=NOW)
    keys = {t["key"] for t in monitor.review_triggers(kpi)}
    assert "strategy_negative:embient_enhanced" in keys


def test_review_trigger_asleep_in_bull(monitor):
    trades = [_trade(1.0, days_ago=20.0)]  # last trade 20 days ago
    kpi = monitor.compute_from_trades(trades, [], 10_000.0, now=NOW)
    trg = monitor.review_triggers(kpi, global_regime="trend",
                                  global_direction="up")
    assert any(t["key"] == "asleep_in_bull" for t in trg)
    # Same idleness in a bear is FINE (flat-in-bear is the designed behaviour).
    trg_bear = monitor.review_triggers(kpi, global_regime="trend",
                                       global_direction="down")
    assert not any(t["key"] == "asleep_in_bull" for t in trg_bear)


def test_trigger_dedup_24h(monitor):
    triggers = [{"key": "pf_collapse", "message": "x"}]
    assert monitor.unnotified_triggers(triggers, NOW) == triggers
    assert monitor.unnotified_triggers(triggers, NOW + timedelta(hours=1)) == []
    assert monitor.unnotified_triggers(
        triggers, NOW + timedelta(hours=25)) == triggers


def test_report_formatting_contains_ab_table_and_alarms(monitor):
    trades = ([_trade(1.0, strategy="regime_breakout") for _ in range(3)]
              + [_trade(-0.4, strategy="embient_enhanced") for _ in range(21)])
    kpi = monitor.compute_from_trades(trades, [], 10_000.0, now=NOW)
    alarms = monitor.evaluate_alarms(kpi)
    triggers = monitor.review_triggers(kpi)
    text = monitor.format_report(kpi, alarms, triggers)
    assert "regime_breakout" in text
    assert "embient_enhanced" in text
    assert "Allarmi" in text
    assert "ciclo di revisione" in text


def test_config_file_overrides_defaults(tmp_path):
    cfg = tmp_path / "kpi.json"
    cfg.write_text('{"trades_per_day_max": 1.0}')
    mon = KPIMonitor(config_path=str(cfg))
    assert mon.thresholds["trades_per_day_max"] == 1.0
    assert mon.thresholds["profit_factor_min"] == DEFAULT_THRESHOLDS["profit_factor_min"]
