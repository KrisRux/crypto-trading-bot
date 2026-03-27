"""
Core Trading Engine.

Orchestrates the complete trading loop for multiple symbols:
1. Fetch market data (klines) from Binance for each symbol.
2. Run all enabled strategies to generate signals.
3. Apply risk management rules.
4. Execute orders (live or paper).
5. Monitor open positions for TP/SL hits.
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
from app.paper_trading.portfolio import PaperPortfolioManager
from app.strategies.base import Strategy, Signal, SignalType
from app.trading_engine.order_manager import OrderManager
from app.trading_engine.risk_manager import RiskManager

logger = logging.getLogger(__name__)


class TradingEngine:
    """
    Main engine that ties together data feeds, strategies, risk management,
    and order execution. Supports multiple trading symbols simultaneously.
    """

    def __init__(self):
        self.mode = settings.trading_mode  # "live" or "paper"
        is_testnet = not settings.is_live

        # Client for trading orders (testnet in paper mode, live in live mode)
        self.client = BinanceRestClient(
            api_key=settings.active_api_key,
            api_secret=settings.active_api_secret,
            testnet=is_testnet,
        )
        # Client for market data — always uses live Binance (public, no auth)
        self.market_client = BinanceRestClient(
            api_key="", api_secret="", testnet=False,
        )
        self.ws = BinanceWebSocket(testnet=is_testnet)
        self.order_manager = OrderManager(self.client, mode=self.mode)
        self.risk_manager = RiskManager(
            max_position_pct=settings.max_position_size_pct,
            default_sl_pct=settings.default_stop_loss_pct,
            default_tp_pct=settings.default_take_profit_pct,
        )
        self.paper_portfolio = PaperPortfolioManager()

        # Registered strategies
        self.strategies: list[Strategy] = []

        # Multi-symbol support
        self.symbols: list[str] = settings.symbol_list
        self.running = False
        self.last_prices: dict[str, float] = {s: 0.0 for s in self.symbols}
        self.signals_log: list[dict] = []  # recent signals for the UI

    # Convenience property for backward compat
    @property
    def last_price(self) -> float:
        if self.last_prices:
            return next(iter(self.last_prices.values()))
        return 0.0

    # ------------------------------------------------------------------
    # Symbol management
    # ------------------------------------------------------------------

    def add_symbol(self, symbol: str):
        symbol = symbol.upper()
        if symbol not in self.symbols:
            self.symbols.append(symbol)
            self.last_prices[symbol] = 0.0
            logger.info("Added symbol: %s", symbol)

    def remove_symbol(self, symbol: str):
        symbol = symbol.upper()
        if symbol in self.symbols and len(self.symbols) > 1:
            self.symbols.remove(symbol)
            self.last_prices.pop(symbol, None)
            logger.info("Removed symbol: %s", symbol)

    # ------------------------------------------------------------------
    # Strategy registration
    # ------------------------------------------------------------------

    def register_strategy(self, strategy: Strategy):
        self.strategies.append(strategy)
        logger.info("Registered strategy: %s", strategy.name)

    # ------------------------------------------------------------------
    # Data helpers
    # ------------------------------------------------------------------

    async def fetch_klines(self, symbol: str,
                           interval: str = "1m", limit: int = 100) -> pd.DataFrame:
        """Fetch klines from live Binance (public data, no auth needed)."""
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

    # ------------------------------------------------------------------
    # Signal processing
    # ------------------------------------------------------------------

    def _run_strategies(self, df: pd.DataFrame, symbol: str) -> list[Signal]:
        """Run all enabled strategies on a specific symbol and collect signals."""
        all_signals: list[Signal] = []
        for strat in self.strategies:
            if not strat.enabled:
                continue
            try:
                signals = strat.generate_signals(df, symbol)
                all_signals.extend(signals)
            except Exception:
                logger.exception("Strategy %s raised an error on %s", strat.name, symbol)
        return all_signals

    # ------------------------------------------------------------------
    # Position monitoring (TP/SL check)
    # ------------------------------------------------------------------

    async def _check_open_positions(self, db: Session, symbol: str, current_price: float):
        """Close positions that hit take-profit or stop-loss for a specific symbol."""
        if self.mode == "paper":
            closed = self.paper_portfolio.check_tp_sl_symbol(db, symbol, current_price)
            for pos, reason in closed:
                logger.info("Paper position closed (%s): %s @ %.2f",
                            reason, pos.symbol, current_price)
        else:
            open_trades = db.query(Trade).filter(
                Trade.status == TradeStatus.OPEN,
                Trade.mode == "live",
                Trade.symbol == symbol,
            ).all()
            for trade in open_trades:
                result = self.risk_manager.should_close_position(
                    trade.entry_price, current_price, trade.stop_loss, trade.take_profit
                )
                if result:
                    await self._close_trade(db, trade, current_price, result)

    async def _close_trade(self, db: Session, trade: Trade,
                           exit_price: float, reason: str):
        """Close a trade (sell the position)."""
        order = await self.order_manager.place_market_order(
            db, trade.symbol, "SELL", trade.quantity
        )
        trade.exit_price = exit_price
        trade.pnl = (exit_price - trade.entry_price) * trade.quantity
        trade.pnl_pct = ((exit_price - trade.entry_price) / trade.entry_price) * 100
        trade.status = TradeStatus.CLOSED
        trade.exit_order_id = order.id
        trade.closed_at = datetime.now(timezone.utc)
        db.commit()
        logger.info("Trade #%d closed (%s): %s PnL=%.2f USDT (%.2f%%)",
                     trade.id, reason, trade.symbol, trade.pnl, trade.pnl_pct)

    # ------------------------------------------------------------------
    # Signal execution
    # ------------------------------------------------------------------

    async def _execute_signal(self, db: Session, signal: Signal, capital: float):
        """Turn a BUY/SELL signal into an actual order + trade record."""
        if signal.signal_type == SignalType.HOLD:
            return

        # Log the signal for the UI
        self.signals_log.append({
            "time": datetime.now(timezone.utc).isoformat(),
            "type": signal.signal_type.value,
            "symbol": signal.symbol,
            "price": signal.price,
            "strategy": signal.strategy_name,
            "reason": signal.reason,
        })
        # Keep only last 200 signals in memory
        self.signals_log = self.signals_log[-200:]

        if signal.signal_type == SignalType.BUY:
            qty = self.risk_manager.calculate_position_size(capital, signal.price)
            if qty <= 0:
                return

            sl = self.risk_manager.calculate_stop_loss(signal.price)
            tp = self.risk_manager.calculate_take_profit(signal.price)

            if self.mode == "paper":
                self.paper_portfolio.open_position(
                    db, signal.symbol, qty, signal.price, sl, tp
                )
            else:
                order = await self.order_manager.place_market_order(
                    db, signal.symbol, "BUY", qty
                )
                filled_price = order.filled_price or signal.price
                trade = Trade(
                    symbol=signal.symbol,
                    side=OrderSide.BUY,
                    entry_price=filled_price,
                    quantity=qty,
                    stop_loss=sl,
                    take_profit=tp,
                    status=TradeStatus.OPEN,
                    mode="live",
                    strategy=signal.strategy_name,
                    entry_order_id=order.id,
                )
                db.add(trade)
                db.commit()

            logger.info("Opened %s position: %s qty=%.6f SL=%.2f TP=%.2f",
                        self.mode, signal.symbol, qty, sl, tp)

        elif signal.signal_type == SignalType.SELL:
            if self.mode == "paper":
                self.paper_portfolio.close_all_positions(db, signal.symbol, signal.price)
            else:
                open_trades = db.query(Trade).filter(
                    Trade.status == TradeStatus.OPEN,
                    Trade.mode == "live",
                    Trade.symbol == signal.symbol,
                ).all()
                for trade in open_trades:
                    await self._close_trade(db, trade, signal.price, "signal_sell")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _on_price_update(self, msg: dict):
        """Called by the WebSocket on every trade event."""
        if "p" in msg and "s" in msg:
            symbol = msg["s"].upper()
            self.last_prices[symbol] = float(msg["p"])

    async def run_cycle(self):
        """Execute one trading cycle for all symbols."""
        db = SessionLocal()
        try:
            # Determine available capital once per cycle
            if self.mode == "paper":
                portfolio = self.paper_portfolio.get_or_create(db)
                capital = portfolio.cash_balance
            else:
                account = await self.client.get_account()
                usdt_balance = next(
                    (b for b in account.get("balances", []) if b["asset"] == "USDT"),
                    {"free": "0"}
                )
                capital = float(usdt_balance["free"])

            # Process each symbol
            for symbol in self.symbols:
                try:
                    df = await self.fetch_klines(symbol)
                    if df.empty:
                        continue

                    current_price = float(df["close"].iloc[-1])
                    self.last_prices[symbol] = current_price

                    # Check existing positions for TP/SL
                    await self._check_open_positions(db, symbol, current_price)

                    # Generate signals
                    signals = self._run_strategies(df, symbol)

                    for signal in signals:
                        logger.info("Signal: %s %s @ %.2f [%s] %s",
                                    signal.signal_type.value, signal.symbol,
                                    signal.price, signal.strategy_name, signal.reason)
                        await self._execute_signal(db, signal, capital)

                        # Update capital after execution (paper mode)
                        if self.mode == "paper":
                            portfolio = self.paper_portfolio.get_or_create(db)
                            capital = portfolio.cash_balance
                except Exception:
                    logger.exception("Error processing symbol %s", symbol)

        except Exception:
            logger.exception("Error in trading cycle")
        finally:
            db.close()

    async def start(self):
        """Start the trading engine: WebSocket + periodic cycle."""
        self.running = True
        logger.info("Trading engine starting in %s mode for %s",
                     self.mode, ", ".join(self.symbols))

        # Initialize paper portfolio if in paper mode
        if self.mode == "paper":
            db = SessionLocal()
            self.paper_portfolio.get_or_create(db)
            db.close()

        # Start WebSocket for real-time prices on all symbols
        streams = [f"{s.lower()}@trade" for s in self.symbols]
        self.ws.on_message(self._on_price_update)
        await self.ws.start(streams)

        # Run trading cycles every 30 seconds
        while self.running:
            await self.run_cycle()
            await asyncio.sleep(30)

    async def stop(self):
        self.running = False
        await self.ws.stop()
        await self.client.close()
        await self.market_client.close()
        logger.info("Trading engine stopped")

    # ------------------------------------------------------------------
    # Mode switching
    # ------------------------------------------------------------------

    def switch_mode(self, new_mode: str):
        """Switch between 'live' and 'paper' mode."""
        if new_mode not in ("live", "paper"):
            raise ValueError("Mode must be 'live' or 'paper'")
        self.mode = new_mode
        self.order_manager.mode = new_mode
        is_testnet = new_mode == "paper"
        self.client = BinanceRestClient(
            api_key=settings.binance_api_key if new_mode == "live" else settings.binance_testnet_api_key,
            api_secret=settings.binance_api_secret if new_mode == "live" else settings.binance_testnet_api_secret,
            testnet=is_testnet,
        )
        self.order_manager.client = self.client
        logger.info("Switched to %s mode", new_mode)
