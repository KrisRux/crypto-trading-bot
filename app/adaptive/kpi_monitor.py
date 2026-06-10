"""
KPI Monitor — the permanent improvement loop's measurement layer.

Computes the daily KPI set from closed trades, evaluates alarm thresholds and
deterministic REVIEW triggers, and formats the daily Telegram report. This is
the operational answer to the 2026-06 profitability review: the bot lost for
months while every individual safeguard looked green — these KPIs make the
*system-level* questions ("is the edge real? are costs eating it? which
strategy is pulling its weight?") visible every day.

Design rules (consistent with the project):
* Pure computation (`compute_from_trades`) separated from the DB wrapper
  (`compute`) so tests need no database.
* Thresholds live in ``config/kpi.json`` (hot-editable, code defaults as
  fallback) — same philosophy as profiles/guardrails.
* Review triggers NEVER change parameters. They only notify that a human (or
  an assisted review session) must start a new improvement cycle.
"""

from __future__ import annotations

import json
import logging
import os
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

KPI_CONFIG_FILE = os.path.join(
    os.path.dirname(__file__), "..", "..", "config", "kpi.json"
)

DEFAULT_THRESHOLDS = {
    # Alarms (evaluated on the rolling 30d window unless stated otherwise)
    "min_trades_for_quality_alarms": 20,   # PF/WR/expectancy need a sample
    "expectancy_min_usdt": 0.0,            # WARNING below this
    "profit_factor_min": 1.0,              # WARNING below this
    "win_rate_min_pct": 25.0,              # WARNING below this
    "cost_ratio_max": 0.5,                 # WARNING: costs > 50% of gross profits
    "max_drawdown_pct_max": 2.0,           # CRITICAL: 30d DD on capital base
    "trades_per_day_max": 4.0,             # WARNING: overtrading
    "net_pnl_30d_pct_min": -2.0,           # CRITICAL below this

    # Review-cycle triggers (stricter — they demand a new improvement cycle)
    "review_profit_factor_below": 0.8,
    "review_min_trades": 30,
    "review_net_pnl_30d_pct_below": -2.0,
    "review_strategy_expectancy_below": 0.0,
    "review_strategy_min_trades": 20,
    "review_idle_days_in_uptrend": 14,

    # Reporting
    "report_hour_utc": 6,
    "capital_base_fallback": 10000.0,
}


def _safe_div(a: float, b: float) -> float | None:
    return (a / b) if b else None


class KPIMonitor:
    def __init__(self, config_path: str = KPI_CONFIG_FILE):
        self._config_path = os.path.abspath(config_path)
        self.thresholds = dict(DEFAULT_THRESHOLDS)
        self.reload()
        # 24h dedup for review-trigger notifications
        self._trigger_notified_at: dict[str, datetime] = {}

    def reload(self):
        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            self.thresholds = {**DEFAULT_THRESHOLDS, **loaded}
            logger.info("KPI thresholds loaded from %s", self._config_path)
        except FileNotFoundError:
            logger.info("kpi.json not found — using code defaults")
        except Exception:
            logger.exception("Failed to load %s — using code defaults",
                             self._config_path)

    # ------------------------------------------------------------------
    # Computation (pure — testable without a DB)
    # ------------------------------------------------------------------

    @staticmethod
    def _bucket_metrics(trades: list) -> dict:
        """KPI block for one bucket of closed trades (overall or per strategy)."""
        n = len(trades)
        if n == 0:
            return {"trades": 0}
        pnls = [float(t.pnl or 0) for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        gross = [float(getattr(t, "gross_pnl", None) or t.pnl or 0) for t in trades]
        costs = [float(getattr(t, "fee", None) or 0)
                 + float(getattr(t, "slippage", None) or 0) for t in trades]
        pcts = [float(t.pnl_pct or 0) for t in trades]

        # Max drawdown on the cumulative net-PnL curve (USDT)
        eq = peak = max_dd = 0.0
        for t in sorted(trades, key=lambda x: getattr(x, "closed_at", 0) or 0):
            eq += float(t.pnl or 0)
            peak = max(peak, eq)
            max_dd = min(max_dd, eq - peak)

        gross_profit_total = sum(p for p in gross if p > 0)
        return {
            "trades": n,
            "net_pnl": round(sum(pnls), 4),
            "gross_pnl": round(sum(gross), 4),
            "costs": round(sum(costs), 4),
            # Costs relative to the gross profits they erode. None with no winners.
            "cost_ratio": (round(sum(costs) / gross_profit_total, 3)
                           if gross_profit_total > 0 else None),
            "expectancy": round(sum(pnls) / n, 4),
            "win_rate": round(len(wins) / n * 100, 1),
            "profit_factor": (round(sum(wins) / abs(sum(losses)), 3)
                              if losses and sum(losses) != 0 else None),
            "payoff_ratio": (round(abs(statistics.mean(wins) / statistics.mean(losses)), 2)
                             if wins and losses and statistics.mean(losses) != 0 else None),
            "sharpe_per_trade": (round(statistics.mean(pcts) / statistics.stdev(pcts), 3)
                                 if n >= 3 and statistics.stdev(pcts) > 0 else None),
            "max_drawdown_usdt": round(max_dd, 4),
        }

    def compute_from_trades(self, closed_trades: list, open_trades: list,
                            capital_base: float, *,
                            now: datetime | None = None,
                            window_days: int = 30) -> dict:
        """Build the full KPI snapshot from trade-like objects.

        ``closed_trades`` must already be filtered to the window by the caller
        (the DB wrapper does this); ``capital_base`` normalises percentages.
        """
        now = now or datetime.now(timezone.utc)
        cap = capital_base or self.thresholds["capital_base_fallback"]

        overall = self._bucket_metrics(closed_trades)
        overall["window_days"] = window_days
        overall["trades_per_day"] = round(overall.get("trades", 0) / window_days, 2)
        overall["net_pnl_pct"] = (round(overall.get("net_pnl", 0.0) / cap * 100, 3)
                                  if overall.get("trades") else 0.0)
        overall["max_drawdown_pct"] = (
            round(abs(overall.get("max_drawdown_usdt", 0.0)) / cap * 100, 3)
            if overall.get("trades") else 0.0)

        by_strategy: dict[str, list] = defaultdict(list)
        for t in closed_trades:
            by_strategy[getattr(t, "strategy", None) or "unknown"].append(t)
        strategies = {name: self._bucket_metrics(ts)
                      for name, ts in sorted(by_strategy.items())}

        exposure_notional = sum(
            float(t.entry_price or 0) * float(t.quantity or 0) for t in open_trades
        )
        last_close = max(
            (getattr(t, "closed_at", None) for t in closed_trades
             if getattr(t, "closed_at", None) is not None),
            default=None,
        )
        if last_close is not None and last_close.tzinfo is None:
            last_close = last_close.replace(tzinfo=timezone.utc)

        return {
            "timestamp": now.isoformat(),
            "capital_base": cap,
            "overall": overall,
            "strategies": strategies,
            "open_positions": len(open_trades),
            "exposure_notional": round(exposure_notional, 2),
            "exposure_pct": round(exposure_notional / cap * 100, 2),
            "days_since_last_trade": (round((now - last_close).total_seconds() / 86400, 1)
                                      if last_close else None),
        }

    def compute(self, db, *, window_days: int = 30) -> dict:
        """DB wrapper: query closed/open trades and delegate to the pure core."""
        from app.models.trade import Trade, TradeStatus
        from app.models.paper_trading import PaperPortfolio

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=window_days)
        closed = (db.query(Trade)
                  .filter(Trade.status == TradeStatus.CLOSED,
                          Trade.closed_at >= cutoff)
                  .all())
        open_trades = db.query(Trade).filter(Trade.status == TradeStatus.OPEN).all()
        cap = sum(p.initial_capital or 0
                  for p in db.query(PaperPortfolio).all()) or 0.0
        return self.compute_from_trades(closed, open_trades, cap,
                                        now=now, window_days=window_days)

    # ------------------------------------------------------------------
    # Alarms + review triggers (deterministic)
    # ------------------------------------------------------------------

    def evaluate_alarms(self, kpi: dict) -> list[dict]:
        """Daily alarm set. Returns [{key, level, message}]."""
        th = self.thresholds
        o = kpi.get("overall", {})
        n = o.get("trades", 0)
        alarms: list[dict] = []

        def add(key, level, msg):
            alarms.append({"key": key, "level": level, "message": msg})

        if n >= th["min_trades_for_quality_alarms"]:
            if o.get("expectancy", 0) < th["expectancy_min_usdt"]:
                add("expectancy", "WARNING",
                    f"Expectancy {o['expectancy']:+.3f} USDT/trade < "
                    f"{th['expectancy_min_usdt']:+.1f} su {n} trade")
            pf = o.get("profit_factor")
            if pf is not None and pf < th["profit_factor_min"]:
                add("profit_factor", "WARNING",
                    f"Profit factor {pf:.2f} < {th['profit_factor_min']:.2f}")
            if o.get("win_rate", 100) < th["win_rate_min_pct"]:
                add("win_rate", "WARNING",
                    f"Win rate {o['win_rate']:.0f}% < {th['win_rate_min_pct']:.0f}%")
        cr = o.get("cost_ratio")
        if cr is not None and cr > th["cost_ratio_max"]:
            add("cost_ratio", "WARNING",
                f"Cost ratio {cr:.0%}: i costi mangiano piu del "
                f"{th['cost_ratio_max']:.0%} dei profitti lordi")
        if o.get("max_drawdown_pct", 0) > th["max_drawdown_pct_max"]:
            add("drawdown", "CRITICAL",
                f"Drawdown 30d {o['max_drawdown_pct']:.2f}% > "
                f"{th['max_drawdown_pct_max']:.1f}% del capitale")
        if o.get("trades_per_day", 0) > th["trades_per_day_max"]:
            add("turnover", "WARNING",
                f"Turnover {o['trades_per_day']:.1f} trade/giorno > "
                f"{th['trades_per_day_max']:.1f} (overtrading)")
        if o.get("net_pnl_pct", 0) < th["net_pnl_30d_pct_min"]:
            add("net_pnl", "CRITICAL",
                f"PnL 30d {o['net_pnl_pct']:+.2f}% < {th['net_pnl_30d_pct_min']:+.1f}%")
        return alarms

    def review_triggers(self, kpi: dict, *,
                        global_regime: str = "unknown",
                        global_direction: str = "flat") -> list[dict]:
        """Conditions that demand a NEW improvement cycle (never auto-change)."""
        th = self.thresholds
        o = kpi.get("overall", {})
        triggers: list[dict] = []

        def add(key, msg):
            triggers.append({"key": key, "message": msg})

        pf = o.get("profit_factor")
        if (o.get("trades", 0) >= th["review_min_trades"]
                and pf is not None and pf < th["review_profit_factor_below"]):
            add("pf_collapse",
                f"PF {pf:.2f} < {th['review_profit_factor_below']:.2f} su "
                f"{o['trades']} trade: l'edge non sta reggendo")
        if o.get("net_pnl_pct", 0) < th["review_net_pnl_30d_pct_below"]:
            add("net_loss",
                f"PnL 30gg {o['net_pnl_pct']:+.2f}%: rivedere strategia/parametri")
        for name, s in kpi.get("strategies", {}).items():
            if (s.get("trades", 0) >= th["review_strategy_min_trades"]
                    and s.get("expectancy", 0) < th["review_strategy_expectancy_below"]):
                add(f"strategy_negative:{name}",
                    f"Strategia '{name}': expectancy {s['expectancy']:+.3f} USDT "
                    f"su {s['trades']} trade — candidata alla disattivazione")
        idle = kpi.get("days_since_last_trade")
        if (idle is not None and idle >= th["review_idle_days_in_uptrend"]
                and global_regime == "trend" and global_direction == "up"):
            add("asleep_in_bull",
                f"Nessun trade da {idle:.0f} giorni con mercato in uptrend: "
                f"verificare filtri/gate troppo stretti")
        return triggers

    def unnotified_triggers(self, triggers: list[dict],
                            now: datetime | None = None) -> list[dict]:
        """Filter triggers already notified in the last 24h (and mark the rest)."""
        now = now or datetime.now(timezone.utc)
        fresh = []
        for trg in triggers:
            last = self._trigger_notified_at.get(trg["key"])
            if last is None or (now - last) >= timedelta(hours=24):
                self._trigger_notified_at[trg["key"]] = now
                fresh.append(trg)
        return fresh

    # ------------------------------------------------------------------
    # Report formatting (Telegram HTML)
    # ------------------------------------------------------------------

    @staticmethod
    def format_report(kpi: dict, alarms: list[dict],
                      triggers: list[dict]) -> str:
        o = kpi.get("overall", {})

        def fmt(v, spec="{:.2f}", none="n/d"):
            return spec.format(v) if v is not None else none

        lines = [
            "\U0001F4C8 <b>Report KPI giornaliero (30gg)</b>",
            "",
            f"<b>Net PnL:</b> {o.get('net_pnl', 0):+.2f} USDT "
            f"({o.get('net_pnl_pct', 0):+.2f}%)",
            f"<b>Trade:</b> {o.get('trades', 0)} "
            f"({o.get('trades_per_day', 0):.1f}/giorno)",
            f"<b>Expectancy:</b> {o.get('expectancy', 0):+.3f} USDT/trade",
            f"<b>PF:</b> {fmt(o.get('profit_factor'))} | "
            f"<b>WR:</b> {o.get('win_rate', 0):.0f}% | "
            f"<b>Payoff:</b> {fmt(o.get('payoff_ratio'))}",
            f"<b>Cost ratio:</b> {fmt(o.get('cost_ratio'), '{:.0%}')} | "
            f"<b>DD:</b> {o.get('max_drawdown_pct', 0):.2f}%",
            f"<b>Esposizione:</b> {kpi.get('open_positions', 0)} posizioni "
            f"({kpi.get('exposure_pct', 0):.1f}% del capitale)",
        ]

        strategies = {n: s for n, s in kpi.get("strategies", {}).items()
                      if s.get("trades", 0) > 0}
        if strategies:
            lines += ["", "<b>A/B per strategia (30gg):</b>"]
            for name, s in sorted(strategies.items(),
                                  key=lambda kv: -(kv[1].get("net_pnl", 0))):
                pf = s.get("profit_factor")
                lines.append(
                    f"  <code>{name[:18]:<18}</code> n={s['trades']:<3} "
                    f"net={s['net_pnl']:+7.2f} wr={s['win_rate']:.0f}% "
                    f"pf={fmt(pf, '{:.2f}')}"
                )

        if alarms:
            lines += ["", "⚠️ <b>Allarmi:</b>"]
            lines += [f"  [{a['level']}] {a['message']}" for a in alarms]
        if triggers:
            lines += ["", "\U0001F501 <b>Serve un ciclo di revisione:</b>"]
            lines += [f"  - {t['message']}" for t in triggers]
        if not alarms and not triggers:
            lines += ["", "✅ Nessun allarme attivo."]
        return "\n".join(lines)
