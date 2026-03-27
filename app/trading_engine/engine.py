"""
Core Trading Engine — multi-user, multi-symbol.

Market data is shared (one fetch per symbol). Strategy signals are shared.
Execution is per-user: each active user with paper mode gets their own
portfolio; users with live keys get real orders on their own Binance account.
"""

import asyncio
import logging
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy.orm import Session

from app.binance_client.rest_client import BinanceRestClient
from app.binance_client.ws_client import BinanceWebSocket
from app.config import settings
from app.database import SessionLocal
from app.models.trade import Trade, TradeStatus, OrderSide
from app.models.user import User
from app.paper_trading.portfolio import PaperPortfolioManager
from app.strategies.base import Strategy, Signal, SignalType
from app.trading_engine.order_manager import OrderManager
from app.trading_engine.risk_manager import RiskManager

logger = logging.getLogger(__name__)


class TradingEngine:
    def __init__(self):
        # Shared market data client (public, no auth)
        self.market_client = BinanceRestClient(
            api_key="", api_secret="", testnet=False,
        )
        self.ws = BinanceWebSocket(testnet=False)
        self.risk_manager = RiskManager(
            max_position_pct=settings.max_position_size_pct,
            default_sl_pct=settings.default_stop_loss_pct,
            default_tp_pct=settings.default_take_profit_pct,
        )
        self.paper_portfolio = PaperPortfolioManager()

        self.strategies: list[Strategy] = []
        self.symbols: list[str] = settings.symbol_list
        self.running = False
        self.last_prices: dict[str, float] = {s: 0.0 for s in self.symbols}
        self.signals_log: list[dict] = []

    @property
    def last_price(self) -> float:
        if self.last_prices:
            return next(iter(self.last_prices.values()))
        return 0.0

    def add_symbol(self, symbol: str):
        symbol = symbol.upper()
        if symbol not in self.symbols:
            self.symbols.append(symbol)
            self.last_prices[symbol] = 0.0

    def remove_symbol(self, symbol: str):
        symbol = symbol.upper()
        if symbol in self.symbols and len(self.symbols) > 1:
            self.symbols.remove(symbol)
            self.last_prices.pop(symbol, None)

    def register_strategy(self, strategy: Strategy):
        self.strategies.append(strategy)
        logger.info("Registered strategy: %s", strategy.name)

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    async def fetch_klines(self, symbol: str,
                           interval: str = "1m", limit: int = 100) -> pd.DataFrame:
        raw = await self.market_client.get_klines(symbol, interval, limit)
        df = pd.DataFrame(raw, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades", "taker_buy_base",
            "taker_buy_quote", "ignore",
        ])
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)
        df["datetime"] = pd.to_datetime(df["open_time"], unit="ms")
        df.set_index("datetime", inplace=True)
        return df

    def _run_strategies(self, df: pd.DataFrame, symbol: str) -> list[Signal]:
        all_signals: list[Signal] = []
        for strat in self.strategies:
            if not strat.enabled:
                continue
            try:
                signals = strat.generate_signals(df, symbol)
                all_signals.extend(signals)
            except Exception:
                logger.exception("Strategy %s error on %s", strat.name, symbol)
        return all_signals

    # ------------------------------------------------------------------
    # Per-user execution
    # ------------------------------------------------------------------

    async def _execute_for_user(self, db: Session, user: User,
                                symbol: str, signals: list[Signal],
                                current_price: float):
        """Execute signals for a specific user."""
        user_mode = user.trading_mode or "paper"

        # Live mode requires API keys; paper mode doesn't (orders are simulated)
        if user_mode == "live" and not user.has_api_keys(live=True):
            return

        # Check trading schedule — TP/SL always checked, but new trades only during hours
        within_hours = user.is_within_trading_hours()

        # Check existing positions for TP/SL (always, even outside hours)
        if user_mode == "paper":
            closed = self.paper_portfolio.check_tp_sl_symbol(
                db, user.id, symbol, current_price
            )
            for pos, reason in closed:
                logger.info("User %d: position closed (%s) %s @ %.2f",
                            user.id, reason, pos.symbol, current_price)
        else:
            open_trades = db.query(Trade).filter(
                Trade.user_id == user.id,
                Trade.status == TradeStatus.OPEN,
                Trade.mode == "live",
                Trade.symbol == symbol,
            ).all()
            for trade in open_trades:
                result = self.risk_manager.should_close_position(
                    trade.entry_price, current_price,
                    trade.stop_loss, trade.take_profit
                )
                if result:
                    await self._close_live_trade(db, user, trade, current_price, result)

        # Execute signals (only during trading hours)
        if not within_hours:
            return

        for signal in signals:
            if signal.signal_type == SignalType.HOLD:
                continue

            if user_mode == "paper":
                capital = self.paper_portfolio.get_or_create(
                    db, user.id, user.paper_initial_capital
                ).cash_balance
                await self._execute_paper(db, user.id, signal, capital)
            else:
                await self._execute_live(db, user, signal)

    async def _execute_paper(self, db: Session, user_id: int,
                             signal: Signal, capital: float):
        if signal.signal_type == SignalType.BUY:
            qty = self.risk_manager.calculate_position_size(capital, signal.price)
            if qty <= 0:
                return
            sl = self.risk_manager.calculate_stop_loss(signal.price)
            tp = self.risk_manager.calculate_take_profit(signal.price)
            self.paper_portfolio.open_position(
                db, user_id, signal.symbol, qty, signal.price, sl, tp
            )
        elif signal.signal_type == SignalType.SELL:
            self.paper_portfolio.close_all_positions(
                db, user_id, signal.symbol, signal.price
            )

    async def _execute_live(self, db: Session, user: User, signal: Signal):
        """Execute a live order using the user's own Binance API keys."""
        client = BinanceRestClient(
            api_key=user.get_api_key(live=True),
            api_secret=user.get_api_secret(live=True),
            testnet=False,
        )
        order_mgr = OrderManager(client, mode="live")

        try:
            if signal.signal_type == SignalType.BUY:
                account = await client.get_account()
                usdt = next(
                    (b for b in account.get("balances", []) if b["asset"] == "USDT"),
                    {"free": "0"}
                )
                capital = float(usdt["free"])
                qty = self.risk_manager.calculate_position_size(capital, signal.price)
                if qty <= 0:
                    return

                sl = self.risk_manager.calculate_stop_loss(signal.price)
                tp = self.risk_manager.calculate_take_profit(signal.price)
                order = await order_mgr.place_market_order(
                    db, signal.symbol, "BUY", qty
                )
                order.user_id = user.id
                filled_price = order.filled_price or signal.price
                trade = Trade(
                    user_id=user.id, symbol=signal.symbol, side=OrderSide.BUY,
                    entry_price=filled_price, quantity=qty,
                    stop_loss=sl, take_profit=tp,
                    status=TradeStatus.OPEN, mode="live",
                    strategy=signal.strategy_name, entry_order_id=order.id,
                )
                db.add(trade)
                db.commit()
                logger.info("User %d: Live BUY %s qty=%.6f @ %.2f",
                            user.id, signal.symbol, qty, filled_price)

            elif signal.signal_type == SignalType.SELL:
                open_trades = db.query(Trade).filter(
                    Trade.user_id == user.id,
                    Trade.status == TradeStatus.OPEN,
                    Trade.mode == "live",
                    Trade.symbol == signal.symbol,
                ).all()
                for trade in open_trades:
                    await self._close_live_trade(
                        db, user, trade, signal.price, "signal_sell"
                    )
        except Exception:
            logger.exception("User %d: live order failed for %s",
                             user.id, signal.symbol)
        finally:
            await client.close()

    async def _close_live_trade(self, db: Session, user: User,
                                trade: Trade, exit_price: float, reason: str):
        client = BinanceRestClient(
            api_key=user.get_api_key(live=True),
            api_secret=user.get_api_secret(live=True),
            testnet=False,
        )
        order_mgr = OrderManager(client, mode="live")
        try:
            order = await order_mgr.place_market_order(
                db, trade.symbol, "SELL", trade.quantity
            )
            order.user_id = user.id
            trade.exit_price = exit_price
            trade.pnl = (exit_price - trade.entry_price) * trade.quantity
            trade.pnl_pct = ((exit_price - trade.entry_price) / trade.entry_price) * 100
            trade.status = TradeStatus.CLOSED
            trade.exit_order_id = order.id
            trade.closed_at = datetime.now(timezone.utc)
            db.commit()
            logger.info("User %d: Trade #%d closed (%s) PnL=%.2f",
                         user.id, trade.id, reason, trade.pnl)
        except Exception:
            logger.exception("User %d: failed to close trade #%d", user.id, trade.id)
        finally:
            await client.close()

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _on_price_update(self, msg: dict):
        if "p" in msg and "s" in msg:
            self.last_prices[msg["s"].upper()] = float(msg["p"])

    async def run_cycle(self):
        db = SessionLocal()
        try:
            # Get only users who have explicitly enabled trading
            active_users = db.query(User).filter(
                User.is_active == True,
                User.trading_enabled == True,
            ).all()

            for symbol in self.symbols:
                try:
                    df = await self.fetch_klines(symbol)
                    if df.empty:
                        continue

                    current_price = float(df["close"].iloc[-1])
                    self.last_prices[symbol] = current_price

                    # Generate signals (shared across all users)
                    signals = self._run_strategies(df, symbol)

                    # Log signals
                    for signal in signals:
                        self.signals_log.append({
                            "time": datetime.now(timezone.utc).isoformat(),
                            "type": signal.signal_type.value,
                            "symbol": signal.symbol,
                            "price": signal.price,
                            "strategy": signal.strategy_name,
                            "reason": signal.reason,
                        })
                        logger.info("Signal: %s %s @ %.2f [%s]",
                                    signal.signal_type.value, signal.symbol,
                                    signal.price, signal.strategy_name)

                    self.signals_log = self.signals_log[-200:]

                    # Execute for each active user
                    for user in active_users:
                        try:
                            await self._execute_for_user(
                                db, user, symbol, signals, current_price
                            )
                        except Exception:
                            logger.exception("Error executing for user %d on %s",
                                             user.id, symbol)

                except Exception:
                    logger.exception("Error processing symbol %s", symbol)
        except Exception:
            logger.exception("Error in trading cycle")
        finally:
            db.close()

    async def start(self):
        self.running = True
        logger.info("Trading engine starting for %s", ", ".join(self.symbols))

        # Init paper portfolios for users with trading enabled
        db = SessionLocal()
        users = db.query(User).filter(
            User.is_active == True, User.trading_enabled == True
        ).all()
        for user in users:
            self.paper_portfolio.get_or_create(
                db, user.id, user.paper_initial_capital
            )
        db.close()

        streams = [f"{s.lower()}@trade" for s in self.symbols]
        self.ws.on_message(self._on_price_update)
        await self.ws.start(streams)

        while self.running:
            await self.run_cycle()
            await asyncio.sleep(30)

    async def stop(self):
        self.running = False
        await self.ws.stop()
        await self.market_client.close()
        logger.info("Trading engine stopped")

