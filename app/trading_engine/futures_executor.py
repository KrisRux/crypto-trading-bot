"""
Futures TESTNET executor — the long/short paper track (Option B, Phase 2).

Runs the validated ``regime_breakout_ls`` strategy on a closed 4h frame and
opens/closes LONG **and SHORT** positions on the Binance USD-M futures TESTNET.
This is the only place in the system that can hold a short — and it is paper
(testnet) only. The spot live engine never calls into here unless a user's
``trading_mode`` is explicitly ``"futures_testnet"`` and testnet keys are set.

State model
-----------
The open position is tracked by an OPEN ``Trade`` row with
``mode="futures_testnet"`` (one per user+symbol). That row is the source of
truth for stop checks and stop-and-reverse decisions; the exchange position is
reconciled opportunistically. PnL is booked NET through ``app.pnl.compute_pnl``
exactly like every other path (shorts handled by the side sign).

Stop-and-reverse mapping (matches the back-tester):
* BUY  signal: short open → close it; flat → open long
* SELL signal: long open  → close it; flat → open short
Plus a hard ATR stop checked every cycle against the mark price.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.models.trade import Trade, TradeStatus, OrderSide
from app.pnl import compute_pnl
from app.strategies.base import Signal, SignalType
from app.strategies.regime_breakout_ls import RegimeBreakoutLongShort

logger = logging.getLogger(__name__)

MODE = "futures_testnet"


class FuturesTestnetExecutor:
    def __init__(self, strategy: RegimeBreakoutLongShort | None = None,
                 fee_pct: float | None = None):
        self.strategy = strategy or RegimeBreakoutLongShort()
        # Futures taker fee ≈ 0.04% (lower than spot); slippage modelled small.
        self.fee_pct = fee_pct if fee_pct is not None else 0.04
        self.slippage_pct = settings.paper_slippage_pct

    # ------------------------------------------------------------------

    def _open_trade(self, db: Session, user_id: int, symbol: str):
        return (db.query(Trade)
                .filter(Trade.user_id == user_id, Trade.symbol == symbol,
                        Trade.mode == MODE, Trade.status == TradeStatus.OPEN)
                .first())

    def _atr_price(self, signal: Signal, price: float) -> float:
        atr_pct = float((signal.metadata or {}).get("atr_pct") or 0.0)
        return atr_pct / 100.0 * price if atr_pct > 0 else 0.0

    def _size(self, balance: float, price: float, stop: float) -> float:
        """Risk-based qty: lose risk_pct of balance at the stop, capped by the
        max-position notional. 1x leverage assumed (notional <= balance cap)."""
        stop_dist = abs(price - stop)
        if price <= 0 or stop_dist <= 0 or balance <= 0:
            return 0.0
        risk_amount = balance * (settings.risk_pct_per_trade / 100.0)
        qty = risk_amount / stop_dist
        max_notional = balance * (settings.max_position_size_pct / 100.0)
        if qty * price > max_notional:
            qty = max_notional / price
        return qty

    async def _close(self, db: Session, client, trade: Trade, exit_price: float,
                     reason: str):
        """Close an open futures position with a reduceOnly market order."""
        close_side = "BUY" if trade.side == OrderSide.SELL else "SELL"
        try:
            order = await client.place_market_order(
                trade.symbol, close_side, trade.quantity, reduce_only=True)
        except Exception:
            logger.exception("FUTURES: close order failed for trade #%s", trade.id)
            return
        fill = float(order.get("avgPrice") or order.get("price") or exit_price) or exit_price
        r = compute_pnl(trade.side.value, trade.entry_price, fill,
                        trade.quantity, self.fee_pct, self.slippage_pct)
        trade.exit_price = fill
        trade.gross_pnl = r.gross_pnl
        trade.fee = r.fee
        trade.slippage = r.slippage
        trade.pnl = r.net_pnl
        trade.pnl_pct = r.net_pnl_pct
        trade.exit_reason = reason
        trade.status = TradeStatus.CLOSED
        trade.closed_at = datetime.now(timezone.utc)
        db.commit()
        logger.info("FUTURES [%s]: closed #%s %s %s NET=%.2f (%s)", MODE,
                    trade.id, trade.symbol, trade.side.value, r.net_pnl, reason)

    async def _open(self, db: Session, client, user_id: int, symbol: str,
                    signal: Signal, price: float):
        side = "BUY" if signal.signal_type == SignalType.BUY else "SELL"
        atr_price = self._atr_price(signal, price)
        if side == "SELL":
            stop = price + settings.atr_sl_mult * atr_price if atr_price else price * 1.03
        else:
            stop = price - settings.atr_sl_mult * atr_price if atr_price else price * 0.97
        try:
            balance = await client.get_balance("USDT")
            qty = self._size(balance, price, stop)
            if qty <= 0:
                return
            await client.set_leverage(symbol)
            order = await client.place_market_order(symbol, side, qty)
        except Exception:
            logger.exception("FUTURES: open order failed for %s %s", side, symbol)
            return
        fill = float(order.get("avgPrice") or order.get("price") or price) or price
        trade = Trade(user_id=user_id, symbol=symbol,
                      side=OrderSide.BUY if side == "BUY" else OrderSide.SELL,
                      entry_price=fill, quantity=qty, stop_loss=stop,
                      status=TradeStatus.OPEN, mode=MODE,
                      strategy=self.strategy.name)
        db.add(trade)
        db.commit()
        logger.info("FUTURES [%s]: opened %s %s qty=%.6f @ %.2f SL=%.2f", MODE,
                    side, symbol, qty, fill, stop)

    def _stop_breached(self, trade: Trade, mark: float) -> bool:
        if not trade.stop_loss:
            return False
        if trade.side == OrderSide.SELL:        # short: stop is above entry
            return mark >= trade.stop_loss
        return mark <= trade.stop_loss          # long: stop is below entry

    async def run(self, db: Session, user, symbol: str, df_4h, client):
        """One evaluation for (user, symbol) on the latest closed 4h bar.

        Order of operations: (1) hard-stop check on any open position using the
        mark price, (2) strategy decision → stop-and-reverse open/close.
        """
        try:
            mark = await client.get_mark_price(symbol)
        except Exception:
            logger.exception("FUTURES: mark price failed for %s", symbol)
            return

        open_trade = self._open_trade(db, user.id, symbol)

        # 1) hard ATR stop. After a stop-out we stay flat for this bar — never
        # re-enter the same cycle (that would just fight the stop we just hit).
        if open_trade and self._stop_breached(open_trade, mark):
            await self._close(db, client, open_trade, mark, "stop_loss")
            return

        # 2) strategy decision on closed 4h data
        if df_4h is None or len(df_4h) < self.strategy.min_history_bars:
            return
        signals = self.strategy.generate_signals(df_4h, symbol)
        if not signals:
            return
        sig = signals[0]

        if sig.signal_type == SignalType.BUY:
            if open_trade and open_trade.side == OrderSide.SELL:
                await self._close(db, client, open_trade, mark, "signal_reverse")
            elif not open_trade:
                await self._open(db, client, user.id, symbol, sig, mark)
        elif sig.signal_type == SignalType.SELL:
            if open_trade and open_trade.side == OrderSide.BUY:
                await self._close(db, client, open_trade, mark, "signal_reverse")
            elif not open_trade:
                await self._open(db, client, user.id, symbol, sig, mark)
