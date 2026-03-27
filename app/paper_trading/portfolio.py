"""
Paper Trading Portfolio Manager.

Manages a virtual portfolio: tracks cash, positions, PnL, and trade history
without ever sending real orders to the exchange. Uses real-time prices from
the Binance data feed.
"""

import csv
import io
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.models.portfolio import PaperPortfolio, PaperPosition
from app.models.trade import Trade, TradeStatus, OrderSide, Order, OrderType, OrderStatus

logger = logging.getLogger(__name__)


class PaperPortfolioManager:
    def get_or_create(self, db: Session, name: str = "default") -> PaperPortfolio:
        """Get existing portfolio or create a new one with initial capital."""
        portfolio = db.query(PaperPortfolio).filter(
            PaperPortfolio.name == name
        ).first()
        if not portfolio:
            portfolio = PaperPortfolio(
                name=name,
                initial_capital=settings.paper_initial_capital,
                cash_balance=settings.paper_initial_capital,
                total_equity=settings.paper_initial_capital,
            )
            db.add(portfolio)
            db.commit()
            db.refresh(portfolio)
            logger.info("Created paper portfolio '%s' with %.2f USDT",
                        name, settings.paper_initial_capital)
        return portfolio

    def open_position(self, db: Session, symbol: str, quantity: float,
                      price: float, stop_loss: float, take_profit: float):
        """Open a new paper position and deduct cash."""
        portfolio = self.get_or_create(db)
        cost = quantity * price

        if cost > portfolio.cash_balance:
            logger.warning("Insufficient paper balance: need %.2f, have %.2f",
                           cost, portfolio.cash_balance)
            return None

        # Record the order
        order = Order(
            symbol=symbol, side=OrderSide.BUY, order_type=OrderType.MARKET,
            quantity=quantity, filled_price=price, status=OrderStatus.FILLED,
            mode="paper",
        )
        db.add(order)
        db.flush()

        # Create position
        position = PaperPosition(
            portfolio_id=portfolio.id,
            symbol=symbol,
            side="BUY",
            quantity=quantity,
            entry_price=price,
            current_price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )
        db.add(position)

        # Record trade
        trade = Trade(
            symbol=symbol, side=OrderSide.BUY, entry_price=price,
            quantity=quantity, stop_loss=stop_loss, take_profit=take_profit,
            status=TradeStatus.OPEN, mode="paper", strategy="paper",
            entry_order_id=order.id,
        )
        db.add(trade)

        # Update portfolio
        portfolio.cash_balance -= cost
        portfolio.total_equity = portfolio.cash_balance + cost
        portfolio.total_trades += 1
        db.commit()

        logger.info("Paper BUY: %s qty=%.6f @ %.2f (cost=%.2f)", symbol, quantity, price, cost)
        return position

    def close_position(self, db: Session, position: PaperPosition,
                       exit_price: float, reason: str = "manual"):
        """Close a paper position and record PnL."""
        portfolio = db.query(PaperPortfolio).get(position.portfolio_id)
        if not portfolio:
            return

        proceeds = position.quantity * exit_price
        cost = position.quantity * position.entry_price
        pnl = proceeds - cost
        pnl_pct = (pnl / cost) * 100 if cost > 0 else 0

        # Record sell order
        order = Order(
            symbol=position.symbol, side=OrderSide.SELL, order_type=OrderType.MARKET,
            quantity=position.quantity, filled_price=exit_price,
            status=OrderStatus.FILLED, mode="paper",
        )
        db.add(order)
        db.flush()

        # Update the matching trade
        open_trade = db.query(Trade).filter(
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

        # Update portfolio
        portfolio.cash_balance += proceeds
        portfolio.total_pnl += pnl
        if pnl > 0:
            portfolio.winning_trades += 1
        else:
            portfolio.losing_trades += 1

        # Remove position
        db.delete(position)
        db.commit()

        logger.info("Paper SELL (%s): %s qty=%.6f @ %.2f PnL=%.2f (%.2f%%)",
                     reason, position.symbol, position.quantity, exit_price, pnl, pnl_pct)

    def close_all_positions(self, db: Session, symbol: str, exit_price: float):
        """Close all open positions for a symbol."""
        portfolio = self.get_or_create(db)
        positions = db.query(PaperPosition).filter(
            PaperPosition.portfolio_id == portfolio.id,
            PaperPosition.symbol == symbol,
        ).all()
        for pos in positions:
            self.close_position(db, pos, exit_price, reason="signal_sell")

    def check_tp_sl_symbol(self, db: Session, symbol: str, current_price: float) -> list[tuple]:
        """Check positions for a specific symbol for TP/SL triggers."""
        portfolio = self.get_or_create(db)
        positions = db.query(PaperPosition).filter(
            PaperPosition.portfolio_id == portfolio.id,
            PaperPosition.symbol == symbol,
        ).all()

        closed = []
        for pos in positions:
            pos.current_price = current_price
            pos.unrealized_pnl = (current_price - pos.entry_price) * pos.quantity

            if pos.take_profit and current_price >= pos.take_profit:
                self.close_position(db, pos, current_price, "take_profit")
                closed.append((pos, "take_profit"))
            elif pos.stop_loss and current_price <= pos.stop_loss:
                self.close_position(db, pos, current_price, "stop_loss")
                closed.append((pos, "stop_loss"))

        if not closed:
            db.commit()
        return closed

    def check_tp_sl(self, db: Session, current_price: float) -> list[tuple]:
        """Check all open positions for TP/SL triggers. Returns list of (position, reason)."""
        portfolio = self.get_or_create(db)
        positions = db.query(PaperPosition).filter(
            PaperPosition.portfolio_id == portfolio.id,
        ).all()

        closed = []
        for pos in positions:
            pos.current_price = current_price
            pos.unrealized_pnl = (current_price - pos.entry_price) * pos.quantity

            if pos.take_profit and current_price >= pos.take_profit:
                self.close_position(db, pos, current_price, "take_profit")
                closed.append((pos, "take_profit"))
            elif pos.stop_loss and current_price <= pos.stop_loss:
                self.close_position(db, pos, current_price, "stop_loss")
                closed.append((pos, "stop_loss"))

        if not closed:
            db.commit()  # persist updated current_price / unrealized_pnl
        return closed

    def update_equity(self, db: Session, prices: dict[str, float]):
        """Recalculate total equity based on current prices."""
        portfolio = self.get_or_create(db)
        positions = db.query(PaperPosition).filter(
            PaperPosition.portfolio_id == portfolio.id,
        ).all()
        position_value = sum(
            pos.quantity * prices.get(pos.symbol, pos.entry_price)
            for pos in positions
        )
        portfolio.total_equity = portfolio.cash_balance + position_value
        db.commit()

    def reset(self, db: Session):
        """Reset the paper portfolio to initial state."""
        portfolio = self.get_or_create(db)
        # Delete all positions
        db.query(PaperPosition).filter(
            PaperPosition.portfolio_id == portfolio.id
        ).delete()
        # Reset portfolio values
        portfolio.cash_balance = portfolio.initial_capital
        portfolio.total_equity = portfolio.initial_capital
        portfolio.total_pnl = 0.0
        portfolio.total_trades = 0
        portfolio.winning_trades = 0
        portfolio.losing_trades = 0
        db.commit()
        logger.info("Paper portfolio reset to %.2f USDT", portfolio.initial_capital)

    def export_trades_csv(self, db: Session) -> str:
        """Export all paper trades to a CSV string."""
        trades = db.query(Trade).filter(Trade.mode == "paper").order_by(Trade.opened_at).all()
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
