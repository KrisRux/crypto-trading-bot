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

logger = logging.getLogger(__name__)


class PaperPortfolioManager:

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
                      stop_loss: float, take_profit: float):
        portfolio = self.get_or_create(db, user_id)
        cost = quantity * price

        if cost > portfolio.cash_balance:
            logger.warning("User %d: insufficient paper balance (need %.2f, have %.2f)",
                           user_id, cost, portfolio.cash_balance)
            return None

        order = Order(
            user_id=user_id, symbol=symbol, side=OrderSide.BUY,
            order_type=OrderType.MARKET, quantity=quantity,
            filled_price=price, status=OrderStatus.FILLED, mode="paper",
        )
        db.add(order)
        db.flush()

        position = PaperPosition(
            portfolio_id=portfolio.id, user_id=user_id, symbol=symbol,
            side="BUY", quantity=quantity, entry_price=price,
            current_price=price, stop_loss=stop_loss, take_profit=take_profit,
        )
        db.add(position)

        trade = Trade(
            user_id=user_id, symbol=symbol, side=OrderSide.BUY,
            entry_price=price, quantity=quantity, stop_loss=stop_loss,
            take_profit=take_profit, status=TradeStatus.OPEN,
            mode="paper", strategy="paper", entry_order_id=order.id,
        )
        db.add(trade)

        portfolio.cash_balance -= cost
        portfolio.total_equity = portfolio.cash_balance + cost
        portfolio.total_trades += 1
        db.commit()

        logger.info("User %d: Paper BUY %s qty=%.6f @ %.2f",
                     user_id, symbol, quantity, price)
        return position

    def close_position(self, db: Session, position: PaperPosition,
                       exit_price: float, reason: str = "manual"):
        portfolio = db.query(PaperPortfolio).filter(
            PaperPortfolio.user_id == position.user_id
        ).first()
        if not portfolio:
            return

        proceeds = position.quantity * exit_price
        cost = position.quantity * position.entry_price
        pnl = proceeds - cost
        pnl_pct = (pnl / cost) * 100 if cost > 0 else 0

        order = Order(
            user_id=position.user_id, symbol=position.symbol,
            side=OrderSide.SELL, order_type=OrderType.MARKET,
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
        ).first()
        if open_trade:
            open_trade.exit_price = exit_price
            open_trade.pnl = pnl
            open_trade.pnl_pct = pnl_pct
            open_trade.status = TradeStatus.CLOSED
            open_trade.exit_order_id = order.id
            open_trade.closed_at = datetime.now(timezone.utc)

        portfolio.cash_balance += proceeds
        portfolio.total_pnl += pnl
        if pnl > 0:
            portfolio.winning_trades += 1
        else:
            portfolio.losing_trades += 1

        db.delete(position)
        db.commit()

        logger.info("User %d: Paper SELL (%s) %s qty=%.6f @ %.2f PnL=%.2f",
                     position.user_id, reason, position.symbol,
                     position.quantity, exit_price, pnl)

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
            pos.unrealized_pnl = (current_price - pos.entry_price) * pos.quantity

            if pos.take_profit and (high >= pos.take_profit or current_price >= pos.take_profit):
                self.close_position(db, pos, current_price, "take_profit")
                closed.append((pos, "take_profit"))
            elif pos.stop_loss and (low <= pos.stop_loss or current_price <= pos.stop_loss):
                self.close_position(db, pos, current_price, "stop_loss")
                closed.append((pos, "stop_loss"))

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
