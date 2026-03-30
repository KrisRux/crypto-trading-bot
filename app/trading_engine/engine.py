"""
Core Trading Engine — multi-user, multi-symbol.

Market data is shared (one fetch per symbol). Strategy signals are shared.
Execution is per-user: each active user with paper mode gets their own
portfolio; users with live keys get real orders on their own Binance account.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import pandas as pd
from sqlalchemy.orm import Session

from app.binance_client.rest_client import BinanceRestClient
from app.binance_client.ws_client import BinanceWebSocket
from app.config import settings
from app.database import SessionLocal, load_symbols_from_db
from app.models.trade import Trade, TradeStatus, OrderSide, OrderStatus
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
        self.ws = BinanceWebSocket()
        self.risk_manager = RiskManager(
            max_position_pct=settings.max_position_size_pct,
            default_sl_pct=settings.default_stop_loss_pct,
            default_tp_pct=settings.default_take_profit_pct,
        )
        self.paper_portfolio = PaperPortfolioManager()

        self.strategies: list[Strategy] = []
        # Load symbols from DB (persisted) — falls back to .env defaults if DB is empty
        self.symbols: list[str] = load_symbols_from_db() or settings.symbol_list
        self.running = False
        self.last_prices: dict[str, float] = {s: 0.0 for s in self.symbols}
        self.signals_log: list[dict] = []
        # Lot size (step size decimals) per symbol, loaded from Binance at startup
        self._qty_decimals: dict[str, int] = {}
        # Cooldown: track last BUY execution time per (user_id, symbol) to avoid overtrading
        self._last_trade_time: dict[tuple[int, str], datetime] = {}
        self._trade_cooldown_minutes: int = 30

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
            # Load lot size for the new symbol immediately (non-blocking best-effort)
            asyncio.get_event_loop().create_task(self._load_lot_size_for(symbol))

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
        """Route execution to paper or live path based on user's trading mode."""
        user_mode = user.trading_mode or "paper"
        within_hours = user.is_within_trading_hours()

        if user_mode == "paper":
            await self._execute_paper(db, user, symbol, signals, current_price, within_hours)
        else:
            if not user.has_api_keys(live=True):
                return
            await self._execute_live(db, user, symbol, signals, current_price, within_hours)

    async def _execute_paper(self, db: Session, user: User,
                             symbol: str, signals: list[Signal],
                             current_price: float, within_hours: bool):
        """
        Paper trading: simulate orders using live market prices.
        No API keys required — all state stored in the local DB.
        """
        # Check TP/SL on open paper positions (always, even outside trading hours)
        self.paper_portfolio.check_tp_sl_symbol(db, user.id, symbol, current_price)

        if not within_hours:
            return

        actionable = [s for s in signals if s.signal_type != SignalType.HOLD]
        buy_signals = [s for s in actionable if s.signal_type == SignalType.BUY]
        sell_signals = [s for s in actionable if s.signal_type == SignalType.SELL]

        if buy_signals and sell_signals:
            logger.info(
                "User %d [paper]: conflicting signals on %s — skipping",
                user.id, symbol,
            )
            return

        if buy_signals:
            # Skip if there is already an open paper position on this symbol
            existing = db.query(Trade).filter(
                Trade.user_id == user.id,
                Trade.status == TradeStatus.OPEN,
                Trade.mode == "paper",
                Trade.symbol == symbol,
            ).first()
            if existing:
                return

            # Cooldown: skip if a trade was opened too recently on this symbol
            cooldown_key = (user.id, symbol)
            last_trade = self._last_trade_time.get(cooldown_key)
            if last_trade is not None:
                elapsed = datetime.now(timezone.utc) - last_trade
                if elapsed < timedelta(minutes=self._trade_cooldown_minutes):
                    logger.debug(
                        "User %d [paper]: cooldown active for %s (%.0f min remaining)",
                        user.id, symbol,
                        (timedelta(minutes=self._trade_cooldown_minutes) - elapsed).seconds / 60,
                    )
                    return

            portfolio = self.paper_portfolio.get_or_create(
                db, user.id, user.paper_initial_capital
            )
            qty = self._round_qty(
                symbol,
                self.risk_manager.calculate_position_size(portfolio.cash_balance, current_price)
            )
            if qty <= 0:
                return
            sl = self.risk_manager.calculate_stop_loss(current_price)
            tp = self.risk_manager.calculate_take_profit(current_price)
            self.paper_portfolio.open_position(db, user.id, symbol, qty, current_price, sl, tp)
            self._last_trade_time[cooldown_key] = datetime.now(timezone.utc)

        elif sell_signals:
            self.paper_portfolio.close_all_positions(db, user.id, symbol, current_price)

    async def _execute_live(self, db: Session, user: User,
                            symbol: str, signals: list[Signal],
                            current_price: float, within_hours: bool):
        """Live trading: place real orders on Binance using the user's API keys."""
        client = BinanceRestClient(
            api_key=user.get_api_key(live=True),
            api_secret=user.get_api_secret(live=True),
            testnet=False,
        )
        order_mgr = OrderManager(client, mode="live")

        # Check existing positions for TP/SL (always, even outside hours)
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
                await self._close_trade(db, user, trade, current_price, result, client, order_mgr)

        if not within_hours:
            await client.close()
            return

        actionable = [s for s in signals if s.signal_type != SignalType.HOLD]
        buy_signals = [s for s in actionable if s.signal_type == SignalType.BUY]
        sell_signals = [s for s in actionable if s.signal_type == SignalType.SELL]

        if buy_signals and sell_signals:
            logger.info(
                "User %d [live]: conflicting signals on %s — skipping",
                user.id, symbol,
            )
            await client.close()
            return

        try:
            if buy_signals:
                await self._execute_order(db, user, buy_signals[0], client, order_mgr)
            elif sell_signals:
                await self._execute_order(db, user, sell_signals[0], client, order_mgr)
        finally:
            await client.close()

    async def _load_lot_sizes(self):
        """Fetch step sizes from Binance exchangeInfo for all symbols."""
        try:
            info = await self.market_client.get_exchange_info()
            for s in info.get("symbols", []):
                sym = s["symbol"]
                for f in s.get("filters", []):
                    if f["filterType"] == "LOT_SIZE":
                        step = f["stepSize"]  # e.g. "0.00100000"
                        # Count decimals: "0.00100000" -> 3
                        if "." in step:
                            stripped = step.rstrip("0")
                            decimals = len(stripped.split(".")[1]) if "." in stripped else 0
                        else:
                            decimals = 0
                        self._qty_decimals[sym] = decimals
                        break
            logger.info("Loaded lot sizes for %d symbols", len(self._qty_decimals))
        except Exception:
            logger.exception("Failed to load lot sizes, using defaults")

    async def _load_lot_size_for(self, symbol: str):
        """Fetch and cache the lot size for a single newly-added symbol."""
        try:
            info = await self.market_client.get_exchange_info()
            for s in info.get("symbols", []):
                if s["symbol"] != symbol:
                    continue
                for f in s.get("filters", []):
                    if f["filterType"] == "LOT_SIZE":
                        step = f["stepSize"]
                        if "." in step:
                            stripped = step.rstrip("0")
                            decimals = len(stripped.split(".")[1]) if "." in stripped else 0
                        else:
                            decimals = 0
                        self._qty_decimals[symbol] = decimals
                        logger.info("Loaded lot size for %s: %d decimals", symbol, decimals)
                break
        except Exception:
            logger.exception("Failed to load lot size for %s, using default", symbol)

    def _round_qty(self, symbol: str, qty: float) -> float:
        """Round quantity to Binance-allowed step size."""
        decimals = self._qty_decimals.get(symbol, 4)
        return round(qty, decimals)

    async def _execute_order(self, db: Session, user: User, signal: Signal,
                             client: BinanceRestClient, order_mgr: "OrderManager"):
        """Place a real BUY/SELL order on Binance live account."""
        try:
            if signal.signal_type == SignalType.BUY:
                existing = db.query(Trade).filter(
                    Trade.user_id == user.id,
                    Trade.status == TradeStatus.OPEN,
                    Trade.mode == "live",
                    Trade.symbol == signal.symbol,
                ).first()
                if existing:
                    logger.debug("User %d: skip BUY %s — position already open (trade #%d)",
                                 user.id, signal.symbol, existing.id)
                    return

                # Cooldown: skip if a trade was opened too recently on this symbol
                cooldown_key = (user.id, signal.symbol)
                last_trade = self._last_trade_time.get(cooldown_key)
                if last_trade is not None:
                    elapsed = datetime.now(timezone.utc) - last_trade
                    if elapsed < timedelta(minutes=self._trade_cooldown_minutes):
                        logger.debug(
                            "User %d [live]: cooldown active for %s (%.0f min remaining)",
                            user.id, signal.symbol,
                            (timedelta(minutes=self._trade_cooldown_minutes) - elapsed).seconds / 60,
                        )
                        return

                account = await client.get_account()
                usdt = next(
                    (b for b in account.get("balances", []) if b["asset"] == "USDT"),
                    {"free": "0"}
                )
                capital = float(usdt["free"])
                qty = self._round_qty(
                    signal.symbol,
                    self.risk_manager.calculate_position_size(capital, signal.price)
                )
                if qty <= 0:
                    return

                sl = self.risk_manager.calculate_stop_loss(signal.price)
                tp = self.risk_manager.calculate_take_profit(signal.price)
                order = await order_mgr.place_market_order(db, signal.symbol, "BUY", qty)
                if order.status != OrderStatus.FILLED:
                    logger.warning("User %d: BUY order not filled, skipping trade", user.id)
                    return

                order.user_id = user.id
                self._last_trade_time[(user.id, signal.symbol)] = datetime.now(timezone.utc)
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
                logger.info("User %d [live]: BUY %s qty=%.6f @ %.2f",
                            user.id, signal.symbol, qty, filled_price)

            elif signal.signal_type == SignalType.SELL:
                open_trades = db.query(Trade).filter(
                    Trade.user_id == user.id,
                    Trade.status == TradeStatus.OPEN,
                    Trade.mode == "live",
                    Trade.symbol == signal.symbol,
                ).all()
                if not open_trades:
                    logger.info("User %d [live]: SELL %s — no open positions to close",
                                user.id, signal.symbol)
                for trade in open_trades:
                    await self._close_trade(db, user, trade, signal.price, "signal_sell",
                                            client, order_mgr)
        except Exception:
            logger.exception("User %d [live]: order failed for %s", user.id, signal.symbol)

    async def _close_trade(self, db: Session, user: User,
                           trade: Trade, exit_price: float, reason: str,
                           client: BinanceRestClient, order_mgr: "OrderManager"):
        """Close a live trade by placing a SELL order on Binance."""
        try:
            sell_qty = self._round_qty(trade.symbol, trade.quantity)
            order = await order_mgr.place_market_order(db, trade.symbol, "SELL", sell_qty)
            if order.status != OrderStatus.FILLED:
                logger.warning("User %d: SELL order not filled for trade #%d",
                               user.id, trade.id)
                return
            order.user_id = user.id
            trade.exit_price = exit_price
            trade.pnl = (exit_price - trade.entry_price) * trade.quantity
            trade.pnl_pct = ((exit_price - trade.entry_price) / trade.entry_price) * 100
            trade.status = TradeStatus.CLOSED
            trade.exit_order_id = order.id
            trade.closed_at = datetime.now(timezone.utc)
            db.commit()
            logger.info("User %d [live]: Trade #%d closed (%s) PnL=%.2f",
                        user.id, trade.id, reason, trade.pnl)
        except Exception:
            logger.exception("User %d: failed to close trade #%d", user.id, trade.id)

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
                    df = await self.fetch_klines(symbol, interval="15m", limit=150)
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

        # Load lot sizes from Binance for proper quantity rounding
        await self._load_lot_sizes()

        streams = [f"{s.lower()}@trade" for s in self.symbols]
        self.ws.on_message(self._on_price_update)
        await self.ws.start(streams)

        while self.running:
            await self.run_cycle()
            await asyncio.sleep(900)  # 15 minutes — matches the 15m candle interval

    async def stop(self):
        self.running = False
        await self.ws.stop()
        await self.market_client.close()
        logger.info("Trading engine stopped")

