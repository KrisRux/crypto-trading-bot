"""
Performance Monitor — rolling metrics from trade history.

Computes:
  pnl_1h, pnl_6h, pnl_24h  — realized PnL in time windows
  win_rate_last_10           — % of winning trades in last 10
  drawdown_intraday          — max intraday drawdown %
  consecutive_losses         — current streak of losing trades
  trades_per_hour            — trade frequency
  cooldown_hits              — count of cooldown blocks (from engine)
  api_error_count            — count of API errors (from log counter)

Stores a snapshot in memory and logs it.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.portfolio import PaperPortfolio
from app.models.trade import Trade, TradeStatus

logger = logging.getLogger(__name__)


@dataclass
class PerformanceSnapshot:
    pnl_1h: float = 0.0
    pnl_6h: float = 0.0
    pnl_24h: float = 0.0
    pnl_1h_pct: float = 0.0
    pnl_6h_pct: float = 0.0
    pnl_24h_pct: float = 0.0
    win_rate_last_10: float = 0.0
    drawdown_intraday: float = 0.0
    consecutive_losses: int = 0
    trades_per_hour: float = 0.0
    cooldown_hits: int = 0
    api_error_count: int = 0
    total_recent_trades: int = 0  # total closed trades in last 24h (for min_trades_for_upgrade)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "pnl_1h": round(self.pnl_1h, 4),
            "pnl_6h": round(self.pnl_6h, 4),
            "pnl_24h": round(self.pnl_24h, 4),
            "pnl_1h_pct": round(self.pnl_1h_pct, 4),
            "pnl_6h_pct": round(self.pnl_6h_pct, 4),
            "pnl_24h_pct": round(self.pnl_24h_pct, 4),
            "win_rate_last_10": round(self.win_rate_last_10, 1),
            "drawdown_intraday": round(self.drawdown_intraday, 3),
            "consecutive_losses": self.consecutive_losses,
            "trades_per_hour": round(self.trades_per_hour, 2),
            "cooldown_hits": self.cooldown_hits,
            "api_error_count": self.api_error_count,
            "total_recent_trades": self.total_recent_trades,
            "timestamp": self.timestamp,
        }


class PerformanceMonitor:
    """Calculates rolling performance metrics from the DB."""

    def __init__(self):
        self._snapshot: PerformanceSnapshot | None = None
        self._cooldown_hits: int = 0
        self._api_errors: int = 0

    @property
    def snapshot(self) -> PerformanceSnapshot | None:
        return self._snapshot

    def increment_cooldown_hit(self):
        self._cooldown_hits += 1

    def increment_api_error(self):
        self._api_errors += 1

    def compute(self, db: Session,
                consec_reset_cutoff: datetime | None = None) -> PerformanceSnapshot:
        """
        Compute all metrics from closed trades in the DB.

        Args:
            db: database session
            consec_reset_cutoff: if provided, only trades closed AFTER this
                timestamp are counted towards ``consecutive_losses``. Used by
                the kill switch to avoid counting stale losses that caused the
                previous pause.
        """
        now = datetime.now(timezone.utc)

        # Fetch recent closed trades (last 48h + extra for streak calc)
        cutoff_48h = now - timedelta(hours=48)

        # Strip tzinfo for comparison with naive DB timestamps (SQLite)
        cutoff_48h_naive = cutoff_48h.replace(tzinfo=None)
        now_naive = now.replace(tzinfo=None)

        # EXCLUDE the futures_testnet research track: this snapshot drives the
        # SPOT kill-switch and profile switching, so a long/short futures paper
        # loss must never trip the spot live risk controls.
        trades = (
            db.query(Trade)
            .filter(
                Trade.status == TradeStatus.CLOSED,
                Trade.closed_at >= cutoff_48h_naive,
                Trade.mode != "futures_testnet",
            )
            .order_by(Trade.closed_at.desc())
            .all()
        )

        # PnL in time windows
        cutoff_1h = now_naive - timedelta(hours=1)
        cutoff_6h = now_naive - timedelta(hours=6)
        cutoff_24h = now_naive - timedelta(hours=24)
        pnl_1h = sum(t.pnl or 0 for t in trades if t.closed_at and t.closed_at >= cutoff_1h)
        pnl_6h = sum(t.pnl or 0 for t in trades if t.closed_at and t.closed_at >= cutoff_6h)
        pnl_24h = sum(t.pnl or 0 for t in trades if t.closed_at and t.closed_at >= cutoff_24h)
        capital_base = self._capital_base(db)
        pnl_1h_pct = pnl_1h / capital_base * 100 if capital_base else 0.0
        pnl_6h_pct = pnl_6h / capital_base * 100 if capital_base else 0.0
        pnl_24h_pct = pnl_24h / capital_base * 100 if capital_base else 0.0

        # Total recent trades (last 24h) — for min_trades_for_upgrade check
        total_recent_trades = sum(1 for t in trades if t.closed_at and t.closed_at >= cutoff_24h)

        # Win rate last 10
        last_10 = trades[:10]
        if last_10:
            wins = sum(1 for t in last_10 if (t.pnl or 0) > 0)
            win_rate = wins / len(last_10) * 100
        else:
            win_rate = 0.0

        # Consecutive losses (from most recent).
        # If the kill switch just expired, exclude trades older than the
        # reset cutoff so a fresh streak is required to re-trigger it.
        consec_trades = trades
        if consec_reset_cutoff is not None:
            cutoff_naive = consec_reset_cutoff.replace(tzinfo=None)
            consec_trades = [
                t for t in trades
                if t.closed_at and t.closed_at >= cutoff_naive
            ]
        consec_losses = 0
        for t in consec_trades:
            if (t.pnl or 0) < 0:
                consec_losses += 1
            else:
                break

        # Trades per hour (last 6h)
        trades_6h = [t for t in trades if t.closed_at and t.closed_at >= cutoff_6h]
        trades_per_hour = len(trades_6h) / 6.0

        # Intraday drawdown (cumulative PnL curve from midnight UTC)
        midnight = now_naive.replace(hour=0, minute=0, second=0, microsecond=0)
        today_trades = [
            t for t in reversed(trades)
            if t.closed_at and t.closed_at >= midnight
        ]
        drawdown = self._calc_drawdown(today_trades, capital_base)

        snap = PerformanceSnapshot(
            pnl_1h=pnl_1h,
            pnl_6h=pnl_6h,
            pnl_24h=pnl_24h,
            pnl_1h_pct=pnl_1h_pct,
            pnl_6h_pct=pnl_6h_pct,
            pnl_24h_pct=pnl_24h_pct,
            win_rate_last_10=win_rate,
            drawdown_intraday=drawdown,
            consecutive_losses=consec_losses,
            trades_per_hour=trades_per_hour,
            cooldown_hits=self._cooldown_hits,
            api_error_count=self._api_errors,
            total_recent_trades=total_recent_trades,
        )
        self._snapshot = snap
        logger.info(
            "PERF_MONITOR: PnL 1h=%.2f 6h=%.2f 24h=%.2f | PnL%% 6h=%.3f 24h=%.3f | WR=%.0f%% | "
            "DD=%.2f%% | ConsecLoss=%d | Trades/h=%.1f | Cooldowns=%d | Errors=%d",
            pnl_1h, pnl_6h, pnl_24h, pnl_6h_pct, pnl_24h_pct, win_rate, drawdown,
            consec_losses, trades_per_hour, self._cooldown_hits, self._api_errors,
        )
        return snap

    def _capital_base(self, db: Session) -> float:
        portfolios = db.query(PaperPortfolio).all()
        capital = sum(float(p.initial_capital or 0) for p in portfolios)
        return capital if capital > 0 else 10000.0

    def _calc_drawdown(self, trades_chronological: list[Trade], capital_base: float = 10000.0) -> float:
        """Max intraday drawdown as account-equity %, not per-trade PnL %."""
        if not trades_chronological:
            return 0.0
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        for t in trades_chronological:
            cumulative += (t.pnl or 0)
            if cumulative > peak:
                peak = cumulative
            dd = peak - cumulative
            if dd > max_dd:
                max_dd = dd
        return max_dd / capital_base * 100 if capital_base else 0.0

    def reset_counters(self):
        """Reset per-cycle counters (cooldown hits, API errors). Call daily."""
        self._cooldown_hits = 0
        self._api_errors = 0
