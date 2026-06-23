"""
Tests for the flat-state heartbeat (self-explanation when the bot is idle).

The advisor's deterministic fallback must ALWAYS produce a sensible offline
message (DeepSeek is best-effort), must correctly read a downtrend as "this is
correct, not a fault", and must never imply forcing trades.
"""

from __future__ import annotations

import asyncio

import pytest

from app.adaptive.llm_advisor import LLMAdvisor


def _ctx(direction="down", days=18.0, dirs=None):
    return {
        "days_since_last_trade": days,
        "open_positions": 0,
        "global_regime": "trend",
        "global_direction": direction,
        "active_profile": "defensive",
        "symbol_directions": dirs or {
            "BTCUSDT": "down", "ETHUSDT": "down", "BNBUSDT": "down",
            "XRPUSDT": "down", "SOLUSDT": "down",
        },
    }


def test_fallback_downtrend_reads_as_correct():
    advisor = LLMAdvisor()
    text = advisor._fallback_inactivity_text(_ctx())
    low = text.lower()
    assert "18" in text                        # days surfaced
    assert "ribassist" in low or "downtrend" in low
    assert "non un guasto" in low or "comportamento atteso" in low
    # Must never imply forcing trades / loosening.
    assert "forz" not in low
    assert "ema200" in low.replace(" ", "")     # explains the BUY condition


def test_fallback_mixed_regime_message():
    advisor = LLMAdvisor()
    dirs = {"BTCUSDT": "up", "ETHUSDT": "flat", "BNBUSDT": "up"}
    text = advisor._fallback_inactivity_text(_ctx(direction="up", days=6.0, dirs=dirs))
    assert "6" in text
    assert "breakout" in text.lower()


def test_explain_inactivity_uses_fallback_without_api_key(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "deepseek_api_key", "")
    advisor = LLMAdvisor()
    result = asyncio.run(advisor.explain_inactivity(_ctx()))
    assert result["source"] == "fallback"
    assert result["text"]
    assert "timestamp" in result


def test_explain_inactivity_prefers_deepseek_when_available(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "deepseek_api_key", "sk-test")

    async def fake_narrate(context, api_key, model):
        return "Spiegazione DeepSeek: mercato ribassista, tutto regolare."

    import app.adaptive.deepseek_client as dc
    monkeypatch.setattr(dc, "narrate_inactivity", fake_narrate)

    advisor = LLMAdvisor()
    result = asyncio.run(advisor.explain_inactivity(_ctx()))
    assert result["source"] == "deepseek"
    assert "DeepSeek" in result["text"]


def test_explain_inactivity_falls_back_when_deepseek_returns_none(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "deepseek_api_key", "sk-test")

    async def fake_narrate(context, api_key, model):
        return None  # API failure / empty

    import app.adaptive.deepseek_client as dc
    monkeypatch.setattr(dc, "narrate_inactivity", fake_narrate)

    advisor = LLMAdvisor()
    result = asyncio.run(advisor.explain_inactivity(_ctx()))
    assert result["source"] == "fallback"
    assert result["text"]
