"""
Paper Trading Portfolio Manager — per-user.

Each user has their own virtual portfolio with separate cash, positions, and PnL.
"""

import csv
import io
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.portfolio import PaperPortfolio, PaperPosition
from app.models.trade import Trade, TradeStatus, OrderSide, Order, OrderType, OrderStatus
from app.config import settings
from app.pnl import compute_pnl

logger = logging.getLogger(__name__)


class PaperPortfolioManager:
    def __init__(self, fee_pct: float | None = None, slippage_pct: float | None = None):
        self.fee_pct = settings.paper_fee_pct if fee_pct is None else fee_pct
        self.slippage_pct = settings.paper_slippage_pct if slippage_pct is None else slippage_pct

    def _apply_slippage(self, price: float, side: OrderSide) -> float:
        mult = 1 + self.slippage_pct / 100 if side == OrderSide.BUY else 1 - self.slippage_pct / 100
        return price * mult

    def _fee(self, notional: float) -> float:
        return notional * (self.fee_pct / 100)

    def get_or_create(self, db: Session, user_id: int,
                      initial_capital: float = 10000.0) -> PaperPortfolio:
        portfolio = db.query(PaperPortfolio).filter(
            PaperPortfolio.user_id == user_id
        ).first()
        if not portfolio:
            portfolio = PaperPortfolio(
                user_id=user_id,
                initial_capital=initial_capital,
                cash_balance=initial_capital,
                total_equity=initial_capital,
            )
            db.add(portfolio)
            db.commit()
            db.refresh(portfolio)
            logger.info("Created paper portfolio for user %d with %.2f USDT",
                        user_id, initial_capital)
        return portfolio

    def open_position(self, db: Session, user_id: int, symbol: str,
                      quantity: float, price: float,
                      stop_loss: float, take_profit: float,
                      side: OrderSide = OrderSide.BUY,
                      strategy: str = "paper"):
        portfolio = self.get_or_create(db, user_id)
        # Quoted entry price. Fees AND slippage are charged once, at close, via
        # app.pnl.compute_pnl — one cost model shared across the whole bot.
        fill_price = price
        notional = quantity * fill_price

        if notional > portfolio.cash_balance:
            logger.warning("User %d: insufficient paper balance (need %.2f, have %.2f)",
                           user_id, notional, portfolio.cash_balance)
            return None

        order = Order(
            user_id=user_id, symbol=symbol, side=side,
            order_type=OrderType.MARKET, quantity=quantity,
            filled_price=fill_price, status=OrderStatus.FILLED, mode="paper",
        )
        db.add(order)
        db.flush()

        position = PaperPosition(
            portfolio_id=portfolio.id, user_id=user_id, symbol=symbol,
            side=side.value, quantity=quantity, entry_price=fill_price,
            current_price=fill_price, stop_loss=stop_loss, take_profit=take_profit,
        )
        db.add(position)

        trade = Trade(
            user_id=user_id, symbol=symbol, side=side,
            entry_price=fill_price, quantity=quantity, stop_loss=stop_loss,
            take_profit=take_profit, status=TradeStatus.OPEN,
            mode="paper", strategy=strategy, entry_order_id=order.id,
        )
        db.add(trade)

        # Reserve the position notional from cash; round-trip cost realised at close.
        portfolio.cash_balance -= notional
        portfolio.total_equity = portfolio.cash_balance + notional
        portfolio.total_trades += 1
        db.commit()

        logger.info("User %d: Paper %s %s qty=%.6f @ %.4f (notional=%.2f reserved)",
                     user_id, side.value, symbol, quantity, fill_price, notional)
        return position

    def close_position(self, db: Session, position: PaperPosition,
                       exit_price: float, reason: str = "manual"):
        portfolio = db.query(PaperPortfolio).filter(
            PaperPortfolio.user_id == position.user_id
        ).first()
        if not portfolio:
            return

        entry_side = OrderSide.SELL if position.side == "SELL" else OrderSide.BUY
        exit_side = OrderSide.BUY if entry_side == OrderSide.SELL else OrderSide.SELL

        # Single source of truth for fees/slippage/net (app.pnl). Quoted exit price;
        # the full round-trip cost (both legs) is charged here.
        r = compute_pnl(
            entry_side.value, position.entry_price, exit_price, position.quantity,
            self.fee_pct, self.slippage_pct,
        )
        notional = position.quantity * position.entry_price

        order = Order(
            user_id=position.user_id, symbol=position.symbol,
            side=exit_side, order_type=OrderType.MARKET,
            quantity=position.quantity, filled_price=exit_price,
            status=OrderStatus.FILLED, mode="paper",
        )
        db.add(order)
        db.flush()

        open_trade = db.query(Trade).filter(
            Trade.user_id == position.user_id,
            Trade.symbol == position.symbol,
            Trade.status == TradeStatus.OPEN,
            Trade.mode == "paper",
            Trade.side == entry_side,
        ).order_by(Trade.opened_at.asc()).first()
        if open_trade:
            open_trade.exit_price = exit_price
            open_trade.gross_pnl = r.gross_pnl
            open_trade.fee = r.fee
            open_trade.slippage = r.slippage
            open_trade.pnl = r.net_pnl
            open_trade.pnl_pct = r.net_pnl_pct
            open_trade.exit_reason = reason
            open_trade.status = TradeStatus.CLOSED
            open_trade.exit_order_id = order.id
            open_trade.closed_at = datetime.now(timezone.utc)

        # Return the reserved notional plus the NET result to cash.
        portfolio.cash_balance += notional + r.net_pnl
        portfolio.total_pnl += r.net_pnl
        if r.net_pnl > 0:
            portfolio.winning_trades += 1
        else:
            portfolio.losing_trades += 1

        # Keep total_equity ~in sync (the API endpoint still does live mark-to-market).
        other_open = db.query(PaperPosition).filter(
            PaperPosition.user_id == position.user_id,
            PaperPosition.id != position.id,
        ).all()
        open_notional = sum((p.quantity or 0) * (p.entry_price or 0) for p in other_open)
        portfolio.total_equity = portfolio.cash_balance + open_notional

        db.delete(position)
        db.commit()

        logger.info(
            "User %d: Paper close %s (%s) %s qty=%.6f @ %.4f "
            "gross=%.4f fee=%.4f slip=%.4f NET=%.4f",
            position.user_id, position.side, reason, position.symbol,
            position.quantity, exit_price, r.gross_pnl, r.fee, r.slippage, r.net_pnl,
        )

    def close_all_positions(self, db: Session, user_id: int,
                            symbol: str, exit_price: float):
        portfolio = self.get_or_create(db, user_id)
        positions = db.query(PaperPosition).filter(
            PaperPosition.user_id == user_id,
            PaperPosition.symbol == symbol,
        ).all()
        for pos in positions:
            self.close_position(db, pos, exit_price, reason="signal_sell")

    def check_tp_sl_symbol(self, db: Session, user_id: int,
                           symbol: str, current_price: float,
                           candle_high: float | None = None,
                           candle_low: float | None = None) -> list[tuple]:
        positions = db.query(PaperPosition).filter(
            PaperPosition.user_id == user_id,
            PaperPosition.symbol == symbol,
        ).all()

        high = candle_high if candle_high is not None else current_price
        low  = candle_low  if candle_low  is not None else current_price

        closed = []
        for pos in positions:
            pos.current_price = current_price
            if pos.side == "SELL":
                pos.unrealized_pnl = (pos.entry_price - current_price) * pos.quantity
                # short: SL (price up = loss) checked FIRST, then TP; exit AT the level
                if pos.stop_loss and (high >= pos.stop_loss or current_price >= pos.stop_loss):
                    self.close_position(db, pos, pos.stop_loss, "stop_loss")
                    closed.append((pos, "stop_loss"))
                elif pos.take_profit and (low <= pos.take_profit or current_price <= pos.take_profit):
                    self.close_position(db, pos, pos.take_profit, "take_profit")
                    closed.append((pos, "take_profit"))
            else:
                pos.unrealized_pnl = (current_price - pos.entry_price) * pos.quantity
                # long: SL (price down = loss) checked FIRST, then TP; exit AT the level
                if pos.stop_loss and (low <= pos.stop_loss or current_price <= pos.stop_loss):
                    self.close_position(db, pos, pos.stop_loss, "stop_loss")
                    closed.append((pos, "stop_loss"))
                elif pos.take_profit and (high >= pos.take_profit or current_price >= pos.take_profit):
                    self.close_position(db, pos, pos.take_profit, "take_profit")
                    closed.append((pos, "take_profit"))

        if not closed:
            db.commit()
        return closed

    def reset(self, db: Session, user_id: int):
        portfolio = self.get_or_create(db, user_id)
        # Delete open paper positions
        db.query(PaperPosition).filter(
            PaperPosition.user_id == user_id
        ).delete(synchronize_session=False)
        # Delete all paper trades so PnL/win-rate stats recalculate from zero
        db.query(Trade).filter(
            Trade.user_id == user_id,
            Trade.mode == "paper",
        ).delete(synchronize_session=False)
        # Delete paper orders too — previously orphaned on reset, which made the
        # orders table drift out of sync with trades (e.g. 1040 orders / 248 trades).
        db.query(Order).filter(
            Order.user_id == user_id,
            Order.mode == "paper",
        ).delete(synchronize_session=False)
        # Reset portfolio counters
        portfolio.cash_balance = portfolio.initial_capital
        portfolio.total_equity = portfolio.initial_capital
        portfolio.total_pnl = 0.0
        portfolio.total_trades = 0
        portfolio.winning_trades = 0
        portfolio.losing_trades = 0
        db.commit()
        logger.info("User %d: paper portfolio reset", user_id)

    def export_trades_csv(self, db: Session, user_id: int) -> str:
        trades = db.query(Trade).filter(
            Trade.user_id == user_id, Trade.mode == "paper"
        ).order_by(Trade.opened_at).all()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "id", "symbol", "side", "entry_price", "exit_price",
            "quantity", "pnl", "pnl_pct", "status", "strategy",
            "opened_at", "closed_at",
        ])
        for t in trades:
            writer.writerow([
                t.id, t.symbol, t.side.value if t.side else "",
                t.entry_price, t.exit_price, t.quantity,
                t.pnl, t.pnl_pct, t.status.value if t.status else "",
                t.strategy, t.opened_at, t.closed_at,
            ])
        return output.getvalue()
