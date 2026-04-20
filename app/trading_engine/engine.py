"""
Core Trading Engine — multi-user, multi-symbol.

Market data is shared (one fetch per symbol). Strategy signals are shared.
Execution is per-user: each active user with paper mode gets their own
portfolio; users with live keys get real orders on their own Binance account.
"""

import asyncio
import logging
import math
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
from app.strategies.indicators import Indicators
from app.trading_engine.order_manager import OrderManager
from app.trading_engine.risk_manager import RiskManager
from app.adaptive.guardrails import Guardrails

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
        # Binance symbol filters loaded from exchangeInfo at startup
        self._symbol_filters: dict[str, dict] = {}  # symbol -> {step_size, min_qty, max_qty, min_notional}
        # Cooldown: track last BUY execution time per (user_id, symbol) to avoid overtrading
        self._last_trade_time: dict[tuple[int, str], datetime] = {}
        self._trade_cooldown_minutes: int = 15
        # Adaptive layer (set externally after construction)
        self.meta_controller = None
        # Guardrails — centralized pre-trade validation
        self.guardrails = Guardrails()

    # ADX period (shared with embient) — threshold read dynamically from embient strategy
    _ADX_PERIOD = 14

    @property
    def _trend_adx_threshold(self) -> float:
        """Read ADX trend threshold from the embient strategy instance (synced with profile)."""
        for strat in self.strategies:
            if strat.name == "embient_enhanced" and hasattr(strat, "adx_trend_threshold"):
                return float(strat.adx_trend_threshold)
        return 25.0  # fallback

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

    def _run_strategies(self, df: pd.DataFrame, symbol: str,
                        precomputed_adx: float | None = None) -> list[Signal]:
        all_signals: list[Signal] = []
        for strat in self.strategies:
            if not strat.enabled:
                continue
            try:
                signals = strat.generate_signals(df, symbol, precomputed_adx=precomputed_adx)
                all_signals.extend(signals)
            except Exception:
                logger.exception("Strategy %s error on %s", strat.name, symbol)
        return all_signals

    # ------------------------------------------------------------------
    # Per-user execution
    # ------------------------------------------------------------------

    async def _execute_for_user(self, db: Session, user: User,
                                symbol: str, signals: list[Signal],
                                current_price: float,
                                candle_high: float | None = None,
                                candle_low: float | None = None):
        """Route execution to paper or live path based on user's trading mode."""
        user_mode = user.trading_mode or "paper"
        within_hours = user.is_within_trading_hours()

        if user_mode == "dry_run":
            await self._execute_dry_run(db, user, symbol, signals, current_price, within_hours)
        elif user_mode == "paper":
            await self._execute_paper(db, user, symbol, signals, current_price, within_hours,
                                      candle_high=candle_high, candle_low=candle_low)
        elif user_mode == "live":
            if not user.has_api_keys(live=True):
                return
            await self._execute_live(db, user, symbol, signals, current_price, within_hours,
                                     candle_high=candle_high, candle_low=candle_low)

    async def _execute_dry_run(self, db: Session, user: User,
                               symbol: str, signals: list[Signal],
                               current_price: float, within_hours: bool):
        """
        Dry-run mode: full signal processing with verbose logging, zero execution.
        No trades opened, no DB writes, no API calls.
        Use this to validate strategy behaviour before going paper or live.
        """
        actionable = [s for s in signals if s.signal_type != SignalType.HOLD]
        if not actionable:
            return

        buy_signals  = [s for s in actionable if s.signal_type == SignalType.BUY]
        sell_signals = [s for s in actionable if s.signal_type == SignalType.SELL]

        if buy_signals and sell_signals:
            resolved_buy, resolved_sell = self._resolve_signals(
                buy_signals, sell_signals, symbol, [], current_price
            )
            if not resolved_buy and not resolved_sell:
                logger.info("[DRY-RUN] User %d %s: conflict unresolved — would skip", user.id, symbol)
                return
            buy_signals, sell_signals = resolved_buy, resolved_sell

        if not within_hours:
            direction = "BUY" if buy_signals else "SELL"
            logger.info(
                "[DRY-RUN] User %d %s: %s signal but outside trading hours — would skip",
                user.id, symbol, direction,
            )
            return

        if buy_signals:
            signal = buy_signals[0]
            portfolio = self.paper_portfolio.get_or_create(
                db, user.id, user.paper_initial_capital
            )
            sim_qty     = self._round_qty(
                symbol,
                self.risk_manager.calculate_position_size(portfolio.cash_balance, current_price)
            )
            sim_sl      = self.risk_manager.calculate_stop_loss(current_price)
            sim_tp      = self.risk_manager.calculate_take_profit(current_price)
            sim_usd     = sim_qty * current_price
            cash        = portfolio.cash_balance or 1.0
            alloc_pct   = sim_usd / cash * 100
            risk_usd    = sim_usd * (self.risk_manager.default_sl_pct / 100)
            risk_pct    = risk_usd / cash * 100
            reward_usd  = sim_usd * (self.risk_manager.default_tp_pct / 100)
            logger.info(
                "[DRY-RUN] User %d WOULD BUY %s: qty=%.6f @ %.2f | "
                "allocated=%.2f USDT (%.1f%% cash) | SL=%.2f TP=%.2f | "
                "max_loss=%.2f USDT (%.3f%% capital) | max_gain=%.2f USDT | "
                "score: %s",
                user.id, symbol, sim_qty, current_price,
                sim_usd, alloc_pct, sim_sl, sim_tp,
                risk_usd, risk_pct, reward_usd,
                signal.reason,
            )

        elif sell_signals:
            signal = sell_signals[0]
            logger.info(
                "[DRY-RUN] User %d WOULD SELL %s @ %.2f | score: %s",
                user.id, symbol, current_price, signal.reason,
            )

    # ------------------------------------------------------------------
    # Regime detection + gate
    # ------------------------------------------------------------------

    def _apply_regime_gate(
        self, signals: list[Signal], adx: float | None, symbol: str
    ) -> list[Signal]:
        """
        Pre-filter signals based on ADX regime BEFORE conflict resolution.

        TREND (ADX >= 25):
          rsi_reversal → BLOCKED entirely (no contrarian entries in trend)
          macd_crossover → kept only if direction matches embient signal
          embient_enhanced → pass through

        RANGE (ADX < 25):
          rsi_reversal → OK
          embient_enhanced → only if score >= 80
          macd_crossover → pass through

        ADX None → no filtering.
        """
        if adx is None or not signals:
            return signals

        threshold = self._trend_adx_threshold

        if adx >= threshold:
            # Determine embient direction (if any signal was generated)
            embient_sigs = [s for s in signals if s.strategy_name == "embient_enhanced"]
            embient_dir = embient_sigs[0].signal_type if embient_sigs else None

            filtered = []
            for sig in signals:
                if sig.strategy_name == "rsi_reversal":
                    logger.info(
                        "REGIME: TREND (ADX=%.1f) → %s reversal BLOCKED [rsi_reversal]",
                        adx, sig.signal_type.value,
                    )
                    continue
                if sig.strategy_name == "macd_crossover":
                    macd_mode = (sig.metadata or {}).get("mode", "independent")
                    if macd_mode == "confirm_only":
                        if embient_dir is None:
                            logger.info(
                                "REGIME: TREND (ADX=%.1f) → MACD %s blocked (confirm_only, no embient signal)",
                                adx, sig.signal_type.value,
                            )
                            continue
                        if sig.signal_type != embient_dir:
                            logger.info(
                                "REGIME: TREND (ADX=%.1f) → MACD %s blocked (confirm_only, misaligned embient=%s)",
                                adx, sig.signal_type.value, embient_dir.value,
                            )
                            continue
                    # independent / standalone: MACD passes without depending on embient
                filtered.append(sig)
            return filtered

        else:
            # RANGE: embient only if score >= 80; MACD confirm_only needs embient
            embient_sigs = [s for s in signals if s.strategy_name == "embient_enhanced"]
            embient_dir = embient_sigs[0].signal_type if embient_sigs else None

            # Read current embient thresholds from the live strategy instance —
            # single source of truth, kept in sync with the active profile.
            embient_strat = next(
                (s for s in self.strategies if s.name == "embient_enhanced"), None,
            )
            range_buy_th  = getattr(embient_strat, "range_buy_threshold", 80.0)
            range_sell_th = getattr(embient_strat, "range_sell_threshold", 75.0)

            filtered = []
            for sig in signals:
                if sig.strategy_name == "embient_enhanced":
                    if sig.signal_type == SignalType.BUY:
                        score = float((sig.metadata or {}).get("buy_score", 0))
                        min_th = range_buy_th
                    else:
                        score = float((sig.metadata or {}).get("sell_score", 0))
                        min_th = range_sell_th
                    if score < min_th:
                        logger.info(
                            "REGIME: RANGE (ADX=%.1f) → embient %s blocked (score=%.0f<%.0f)",
                            adx, sig.signal_type.value, score, min_th,
                        )
                        continue
                if sig.strategy_name == "macd_crossover":
                    macd_mode = (sig.metadata or {}).get("mode", "independent")
                    if macd_mode == "confirm_only" and embient_dir is None:
                        logger.info(
                            "REGIME: RANGE (ADX=%.1f) → MACD %s blocked (confirm_only, no embient signal)",
                            adx, sig.signal_type.value,
                        )
                        continue
                filtered.append(sig)
            return filtered

    # ------------------------------------------------------------------
    # Guardrails integration
    # ------------------------------------------------------------------

    def _is_deeply_bearish_market(self) -> tuple[bool, str]:
        """
        Check the macro safety gate: BUY entries are skipped when both news
        sentiment is clearly negative AND the Fear & Greed index is low.

        Returns (blocked, reason). Silent (False, "") when news data is
        unavailable — we don't block entries based on missing data.
        """
        if not self.meta_controller:
            return (False, "")
        snap = getattr(self.meta_controller.news_sentiment, "snapshot", None)
        if snap is None or not getattr(snap, "available", False):
            return (False, "")
        sentiment = float(getattr(snap, "score", 0) or 0)
        fg_value = int(getattr(snap, "fear_greed_value", 50) or 50)
        if sentiment < -0.3 and fg_value < 30:
            return (True, f"sentiment={sentiment:.2f} F&G={fg_value}")
        return (False, "")

    def _apply_guardrails(self, signals: list[Signal], symbol: str,
                          global_regime: str, sym_snap,
                          user_id: int = 0) -> list[Signal]:
        """
        Filter BUY signals through the centralized guardrails.
        SELL signals always pass (we never block exits).
        """
        filtered = []
        bearish_block, bearish_reason = self._is_deeply_bearish_market()
        for sig in signals:
            if sig.signal_type != SignalType.BUY:
                filtered.append(sig)
                continue

            # Macro safety gate: no long entries in deeply bearish conditions
            if bearish_block:
                logger.info(
                    "BUY skipped: bearish sentiment gate | symbol=%s | %s",
                    symbol, bearish_reason,
                )
                continue

            # Extract score from signal metadata
            score = None
            meta = sig.metadata or {}
            if "buy_score" in meta:
                score = float(meta["buy_score"])
            elif sig.confidence and sig.confidence > 0:
                score = sig.confidence * 100  # normalize 0-1 to 0-100

            verdict = self.guardrails.can_open_new_trade(
                symbol=symbol,
                global_regime=global_regime,
                symbol_regime=sym_snap.regime,
                adx=sym_snap.adx,
                volume_ratio=sym_snap.volume_ratio,
                bb_width_pct=sym_snap.bb_width_pct,
                signal_score=score,
                strategy_name=sig.strategy_name,
                user_id=user_id,
            )
            if verdict.allowed:
                filtered.append(sig)
            # else: already logged inside guardrails

        return filtered

    def _record_trade_close(self, trade: Trade, reason: str):
        """Record a trade close result in guardrails for cooldown/breaker tracking."""
        is_win = (trade.pnl or 0) > 0
        was_stoploss = (reason == "sl")
        strategy = trade.strategy or "unknown"
        self.guardrails.record_trade_result(
            trade.symbol, strategy, is_win, was_stoploss=was_stoploss,
        )
        if is_win:
            logger.debug("GUARDRAILS: recorded WIN for %s [%s]", trade.symbol, strategy)
        else:
            logger.debug("GUARDRAILS: recorded LOSS for %s [%s] (sl=%s)",
                          trade.symbol, strategy, was_stoploss)

    # ------------------------------------------------------------------
    # Signal arbitration
    # ------------------------------------------------------------------

    def _resolve_signals(
        self,
        buy_signals: list[Signal],
        sell_signals: list[Signal],
        symbol: str,
        open_trades: list,
        current_price: float,
    ) -> tuple[list[Signal], list[Signal]]:
        """
        Resolve conflicting BUY vs SELL signals using ADX-based priority.

        ADX >= 25 (TREND):
          embient_enhanced has absolute priority for new entries.
          rsi_reversal SELL allowed only as exit of an open profitable position.

        ADX < 25 (RANGE):
          rsi_reversal has priority for contrarian entries.
          embient wins only if its score >= 75.

        Returns (resolved_buy, resolved_sell).
        Both empty = skip (unresolvable conflict).
        """
        if not (buy_signals and sell_signals):
            return buy_signals, sell_signals

        embient_buy  = next((s for s in buy_signals  if s.strategy_name == "embient_enhanced"), None)
        embient_sell = next((s for s in sell_signals if s.strategy_name == "embient_enhanced"), None)
        rsi_buy      = next((s for s in buy_signals  if s.strategy_name == "rsi_reversal"),     None)
        rsi_sell     = next((s for s in sell_signals if s.strategy_name == "rsi_reversal"),     None)

        # ADX lives in embient metadata
        embient_any = embient_buy or embient_sell
        adx: float | None = (embient_any.metadata or {}).get("adx") if embient_any else None

        if adx is None:
            logger.info("[%s] conflict: ADX unavailable — skip", symbol)
            return [], []

        threshold = self._trend_adx_threshold

        if adx >= threshold:
            # ── TREND: embient priority ──────────────────────────────
            # rsi SELL allowed as exit only if there is an open profitable position
            if rsi_sell and open_trades:
                profitable = next(
                    (t for t in open_trades if current_price > t.entry_price > 0),
                    None,
                )
                if profitable:
                    profit_pct = (current_price - profitable.entry_price) / profitable.entry_price * 100
                    logger.info(
                        "[%s] conflict resolved: reversal exit only (ADX=%.1f trend, profit=+%.2f%%)",
                        symbol, adx, profit_pct,
                    )
                    return [], [rsi_sell]

            # embient entry wins
            if embient_buy:
                logger.info(
                    "[%s] conflict resolved: embient wins (ADX=%.1f trend mode), reversal ignored",
                    symbol, adx,
                )
                return [embient_buy], []
            if embient_sell:
                logger.info(
                    "[%s] conflict resolved: embient wins (ADX=%.1f trend mode), reversal ignored",
                    symbol, adx,
                )
                return [], [embient_sell]

            logger.info("[%s] conflict: trend mode, no embient signal — skip", symbol)
            return [], []

        else:
            # ── RANGE (ADX < 25): rsi priority ──────────────────────
            # embient wins only if score >= 75

            if embient_buy:
                score = float((embient_buy.metadata or {}).get("buy_score", 0))
                if score >= 80:
                    logger.info(
                        "[%s] conflict resolved: embient wins (ADX=%.1f range, score=%.0f>=80)",
                        symbol, adx, score,
                    )
                    return [embient_buy], []
                logger.info(
                    "[%s] conflict resolved: reversal wins (ADX=%.1f range, embient BUY score=%.0f<80)",
                    symbol, adx, score,
                )
            elif embient_sell:
                score = float((embient_sell.metadata or {}).get("sell_score", 0))
                if score >= 80:
                    logger.info(
                        "[%s] conflict resolved: embient wins (ADX=%.1f range, score=%.0f>=80)",
                        symbol, adx, score,
                    )
                    return [], [embient_sell]
                logger.info(
                    "[%s] conflict resolved: reversal wins (ADX=%.1f range, embient SELL score=%.0f<80)",
                    symbol, adx, score,
                )
            else:
                logger.info(
                    "[%s] conflict resolved: reversal priority (ADX=%.1f range mode)",
                    symbol, adx,
                )

            return ([rsi_buy] if rsi_buy else []), ([rsi_sell] if rsi_sell else [])

    async def _execute_paper(self, db: Session, user: User,
                             symbol: str, signals: list[Signal],
                             current_price: float, within_hours: bool,
                             candle_high: float | None = None,
                             candle_low: float | None = None):
        """
        Paper trading — two sub-paths:
          • Testnet (preferred): real orders sent to Binance Testnet if keys are configured.
          • Simulation (fallback): local virtual portfolio, no API calls.
        Both paths track trades in the DB and share the same risk / cooldown logic.
        """
        if user.has_api_keys(live=False):
            await self._execute_paper_testnet(db, user, symbol, signals, current_price, within_hours,
                                              candle_high=candle_high, candle_low=candle_low)
        else:
            await self._execute_paper_simulated(db, user, symbol, signals, current_price, within_hours,
                                                candle_high=candle_high, candle_low=candle_low)

    async def _execute_paper_testnet(self, db: Session, user: User,
                                     symbol: str, signals: list[Signal],
                                     current_price: float, within_hours: bool,
                                     candle_high: float | None = None,
                                     candle_low: float | None = None):
        """Paper via Binance Testnet: real orders, virtual money, DB tracking."""
        client = BinanceRestClient(
            api_key=user.get_api_key(live=False),
            api_secret=user.get_api_secret(live=False),
            testnet=True,
        )
        order_mgr = OrderManager(client, mode="paper")

        # TP/SL on open paper positions (always, even outside trading hours)
        open_trades = db.query(Trade).filter(
            Trade.user_id == user.id,
            Trade.status == TradeStatus.OPEN,
            Trade.mode == "paper",
            Trade.symbol == symbol,
        ).all()
        for trade in open_trades:
            result = self.risk_manager.should_close_position(
                trade.entry_price, current_price, trade.stop_loss, trade.take_profit,
                candle_high=candle_high, candle_low=candle_low,
            )
            if result:
                await self._close_trade(db, user, trade, current_price, result, client, order_mgr)

        if not within_hours:
            await client.close()
            return

        actionable = [s for s in signals if s.signal_type != SignalType.HOLD]
        buy_signals  = [s for s in actionable if s.signal_type == SignalType.BUY]
        sell_signals = [s for s in actionable if s.signal_type == SignalType.SELL]

        if buy_signals and sell_signals:
            buy_signals, sell_signals = self._resolve_signals(
                buy_signals, sell_signals, symbol, open_trades, current_price
            )
            if not buy_signals and not sell_signals:
                await client.close()
                return

        try:
            if buy_signals:
                existing = db.query(Trade).filter(
                    Trade.user_id == user.id,
                    Trade.status == TradeStatus.OPEN,
                    Trade.mode == "paper",
                    Trade.symbol == symbol,
                ).first()
                if existing:
                    return

                cooldown_key = (user.id, symbol)
                last_trade = self._last_trade_time.get(cooldown_key)
                if last_trade is not None:
                    elapsed = datetime.now(timezone.utc) - last_trade
                    if elapsed < timedelta(minutes=self._trade_cooldown_minutes):
                        logger.info(
                            "COOLDOWN: %s skipped (%.0f min remaining) [user=%d paper/testnet]",
                            symbol,
                            (timedelta(minutes=self._trade_cooldown_minutes) - elapsed).seconds / 60,
                            user.id,
                        )
                        return

                account = await client.get_account()
                usdt = next(
                    (b for b in account.get("balances", []) if b["asset"] == "USDT"),
                    {"free": "0"},
                )
                capital = float(usdt["free"])
                base_qty = self.risk_manager.calculate_position_size(capital, current_price)
                risk_mult = self.guardrails.get_risk_multiplier()
                qty = self._round_qty(symbol, base_qty * risk_mult)
                if risk_mult < 1.0:
                    logger.info("RISK_SCALING: applied multiplier=%.2f to %s qty=%.6f→%.6f",
                                risk_mult, symbol, base_qty, qty)
                if qty <= 0:
                    return
                valid, reason = self._validate_qty(symbol, qty, current_price)
                if not valid:
                    logger.info("ORDER_VALIDATION: skipped BUY %s | %s [user=%d paper/testnet]",
                                symbol, reason, user.id)
                    return

                sl = self._round_price(symbol, self.risk_manager.calculate_stop_loss(current_price))
                tp = self._round_price(symbol, self.risk_manager.calculate_take_profit(current_price))
                order = await order_mgr.place_market_order(db, symbol, "BUY", qty)
                if order.status != OrderStatus.FILLED:
                    logger.warning("User %d [paper/testnet]: BUY not filled, skip", user.id)
                    return

                order.user_id = user.id
                self._last_trade_time[cooldown_key] = datetime.now(timezone.utc)
                # Record entry in throttle
                self.guardrails.entry_throttle.record_entry(symbol, user_id=user.id)
                filled_price = order.filled_price or current_price
                trade = Trade(
                    user_id=user.id, symbol=symbol, side=OrderSide.BUY,
                    entry_price=filled_price, quantity=qty,
                    stop_loss=sl, take_profit=tp,
                    status=TradeStatus.OPEN, mode="paper",
                    strategy=buy_signals[0].strategy_name, entry_order_id=order.id,
                )
                db.add(trade)
                db.commit()
                logger.info("User %d [paper/testnet]: BUY %s qty=%.6f @ %.2f (risk_mult=%.2f)",
                            user.id, symbol, qty, filled_price, risk_mult)

            elif sell_signals:
                open_trades = db.query(Trade).filter(
                    Trade.user_id == user.id,
                    Trade.status == TradeStatus.OPEN,
                    Trade.mode == "paper",
                    Trade.symbol == symbol,
                ).all()
                if not open_trades:
                    logger.info("User %d [paper/testnet]: SELL %s — no open positions", user.id, symbol)
                for trade in open_trades:
                    await self._close_trade(db, user, trade, current_price, "signal_sell",
                                            client, order_mgr)
        except Exception:
            logger.exception("User %d [paper/testnet]: order failed for %s", user.id, symbol)
        finally:
            await client.close()

    async def _execute_paper_simulated(self, db: Session, user: User,
                                       symbol: str, signals: list[Signal],
                                       current_price: float, within_hours: bool,
                                       candle_high: float | None = None,
                                       candle_low: float | None = None):
        """Paper simulation fallback — no API keys needed, virtual portfolio in DB."""
        # Check TP/SL on open paper positions (always, even outside trading hours)
        closed_positions = self.paper_portfolio.check_tp_sl_symbol(
            db, user.id, symbol, current_price,
            candle_high=candle_high, candle_low=candle_low,
        )
        for pos, reason in (closed_positions or []):
            # Record in guardrails for cooldown/breaker tracking
            pnl = (current_price - pos.entry_price) * pos.quantity if pos.entry_price else 0
            is_win = pnl > 0
            was_sl = (reason == "stop_loss")
            # PaperPosition has no 'strategy' — look up the associated Trade record
            assoc_trade = db.query(Trade).filter(
                Trade.user_id == user.id,
                Trade.symbol == symbol,
                Trade.mode == "paper",
                Trade.status == TradeStatus.CLOSED,
            ).order_by(Trade.closed_at.desc()).first()
            strat_name = assoc_trade.strategy if assoc_trade and assoc_trade.strategy else "unknown"
            self.guardrails.record_trade_result(
                symbol, strat_name, is_win, was_stoploss=was_sl,
            )

        if not within_hours:
            return

        actionable = [s for s in signals if s.signal_type != SignalType.HOLD]
        buy_signals = [s for s in actionable if s.signal_type == SignalType.BUY]
        sell_signals = [s for s in actionable if s.signal_type == SignalType.SELL]

        open_trades_sim = db.query(Trade).filter(
            Trade.user_id == user.id,
            Trade.status == TradeStatus.OPEN,
            Trade.mode == "paper",
            Trade.symbol == symbol,
        ).all()

        if buy_signals and sell_signals:
            buy_signals, sell_signals = self._resolve_signals(
                buy_signals, sell_signals, symbol, open_trades_sim, current_price
            )
            if not buy_signals and not sell_signals:
                return

        if buy_signals:
            existing = next(iter(open_trades_sim), None)
            if existing:
                return

            cooldown_key = (user.id, symbol)
            last_trade = self._last_trade_time.get(cooldown_key)
            if last_trade is not None:
                elapsed = datetime.now(timezone.utc) - last_trade
                if elapsed < timedelta(minutes=self._trade_cooldown_minutes):
                    logger.info(
                        "COOLDOWN: %s skipped (%.0f min remaining) [user=%d paper/sim]",
                        symbol,
                        (timedelta(minutes=self._trade_cooldown_minutes) - elapsed).seconds / 60,
                        user.id,
                    )
                    return

            portfolio = self.paper_portfolio.get_or_create(
                db, user.id, user.paper_initial_capital
            )
            base_qty = self.risk_manager.calculate_position_size(portfolio.cash_balance, current_price)
            risk_mult = self.guardrails.get_risk_multiplier()
            qty = self._round_qty(symbol, base_qty * risk_mult)
            if risk_mult < 1.0:
                logger.info("RISK_SCALING: applied multiplier=%.2f to %s qty=%.6f→%.6f",
                            risk_mult, symbol, base_qty, qty)
            if qty <= 0:
                return
            valid, reason = self._validate_qty(symbol, qty, current_price)
            if not valid:
                logger.info("ORDER_VALIDATION: skipped BUY %s | %s [user=%d paper/sim]",
                            symbol, reason, user.id)
                return
            sl = self._round_price(symbol, self.risk_manager.calculate_stop_loss(current_price))
            tp = self._round_price(symbol, self.risk_manager.calculate_take_profit(current_price))
            self.paper_portfolio.open_position(db, user.id, symbol, qty, current_price, sl, tp)
            self._last_trade_time[cooldown_key] = datetime.now(timezone.utc)
            self.guardrails.entry_throttle.record_entry(symbol, user_id=user.id)

        elif sell_signals:
            # Record which trades were open before closing (for guardrails tracking)
            open_before_close = db.query(Trade).filter(
                Trade.user_id == user.id,
                Trade.status == TradeStatus.OPEN,
                Trade.mode == "paper",
                Trade.symbol == symbol,
            ).all()
            self.paper_portfolio.close_all_positions(db, user.id, symbol, current_price)
            # Record results in guardrails
            for t in open_before_close:
                db.refresh(t)  # re-read after close_all_positions committed
                if t.status == TradeStatus.CLOSED:
                    self._record_trade_close(t, "signal_sell")

    async def _execute_live(self, db: Session, user: User,
                            symbol: str, signals: list[Signal],
                            current_price: float, within_hours: bool,
                            candle_high: float | None = None,
                            candle_low: float | None = None):
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
                trade.stop_loss, trade.take_profit,
                candle_high=candle_high, candle_low=candle_low,
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
            buy_signals, sell_signals = self._resolve_signals(
                buy_signals, sell_signals, symbol, open_trades, current_price
            )
            if not buy_signals and not sell_signals:
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
        """Fetch LOT_SIZE + MIN_NOTIONAL filters from Binance exchangeInfo."""
        try:
            info = await self.market_client.get_exchange_info()
            for s in info.get("symbols", []):
                self._parse_symbol_filters(s)
            logger.info("Loaded filters for %d symbols", len(self._symbol_filters))
        except Exception:
            logger.exception("Failed to load symbol filters, using defaults")

    async def _load_lot_size_for(self, symbol: str):
        """Fetch and cache filters for a single newly-added symbol."""
        try:
            info = await self.market_client.get_exchange_info()
            for s in info.get("symbols", []):
                if s["symbol"] == symbol:
                    self._parse_symbol_filters(s)
                    logger.info("Loaded filters for %s: %s", symbol, self._symbol_filters.get(symbol))
                    break
        except Exception:
            logger.exception("Failed to load filters for %s, using defaults", symbol)

    def _parse_symbol_filters(self, sym_info: dict):
        """Extract LOT_SIZE, PRICE_FILTER, and MIN_NOTIONAL from exchangeInfo."""
        sym = sym_info["symbol"]
        filt = {"step_size": 0.0001, "min_qty": 0.0001, "max_qty": 99999999.0,
                "min_notional": 10.0, "tick_size": 0.01}
        for f in sym_info.get("filters", []):
            if f["filterType"] == "LOT_SIZE":
                filt["step_size"] = float(f["stepSize"])
                filt["min_qty"] = float(f["minQty"])
                filt["max_qty"] = float(f["maxQty"])
            elif f["filterType"] == "PRICE_FILTER":
                filt["tick_size"] = float(f.get("tickSize", 0.01))
            elif f["filterType"] == "NOTIONAL":
                filt["min_notional"] = float(f.get("minNotional", 10.0))
            elif f["filterType"] == "MIN_NOTIONAL":
                filt["min_notional"] = float(f.get("minNotional", 10.0))
        self._symbol_filters[sym] = filt

    def _round_qty(self, symbol: str, qty: float) -> float:
        """Round quantity DOWN to the nearest valid stepSize multiple."""
        filt = self._symbol_filters.get(symbol)
        if not filt:
            return round(qty, 4)
        step = filt["step_size"]
        if step <= 0:
            return round(qty, 4)
        # Floor to step_size multiple (never round UP — Binance rejects overshoot)
        return math.floor(qty / step) * step

    def _round_price(self, symbol: str, price: float) -> float:
        """Round price to Binance-allowed tick size."""
        filt = self._symbol_filters.get(symbol)
        if not filt:
            return round(price, 2)
        tick = filt.get("tick_size", 0.01)
        if tick <= 0:
            return round(price, 2)
        return round(math.floor(price / tick) * tick, 8)

    def _validate_qty(self, symbol: str, qty: float, price: float) -> tuple[bool, str]:
        """
        Validate qty against Binance LOT_SIZE and MIN_NOTIONAL filters.
        Returns (is_valid, reason). If invalid, reason explains why.
        """
        filt = self._symbol_filters.get(symbol)
        if not filt:
            # No filters loaded — allow (Binance will reject if wrong)
            return True, ""

        if qty < filt["min_qty"]:
            return False, f"qty={qty:.8f} < minQty={filt['min_qty']}"

        if qty > filt["max_qty"]:
            return False, f"qty={qty:.8f} > maxQty={filt['max_qty']}"

        notional = qty * price
        if notional < filt["min_notional"]:
            return False, f"notional={notional:.2f} < minNotional={filt['min_notional']}"

        return True, ""

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
                        logger.info(
                            "COOLDOWN: %s skipped (%.0f min remaining) [user=%d live]",
                            signal.symbol,
                            (timedelta(minutes=self._trade_cooldown_minutes) - elapsed).seconds / 60,
                            user.id,
                        )
                        return

                account = await client.get_account()
                usdt = next(
                    (b for b in account.get("balances", []) if b["asset"] == "USDT"),
                    {"free": "0"}
                )
                capital = float(usdt["free"])
                base_qty = self.risk_manager.calculate_position_size(capital, signal.price)
                risk_mult = self.guardrails.get_risk_multiplier()
                qty = self._round_qty(signal.symbol, base_qty * risk_mult)
                if risk_mult < 1.0:
                    logger.info("RISK_SCALING: applied multiplier=%.2f to %s qty=%.6f→%.6f",
                                risk_mult, signal.symbol, base_qty, qty)
                if qty <= 0:
                    return
                valid, reason = self._validate_qty(signal.symbol, qty, signal.price)
                if not valid:
                    logger.info("ORDER_VALIDATION: skipped BUY %s | %s [user=%d live]",
                                signal.symbol, reason, user.id)
                    return

                sl = self._round_price(signal.symbol, self.risk_manager.calculate_stop_loss(signal.price))
                tp = self._round_price(signal.symbol, self.risk_manager.calculate_take_profit(signal.price))
                order = await order_mgr.place_market_order(db, signal.symbol, "BUY", qty)
                if order.status != OrderStatus.FILLED:
                    logger.warning("User %d: BUY order not filled, skipping trade", user.id)
                    return

                order.user_id = user.id
                self._last_trade_time[(user.id, signal.symbol)] = datetime.now(timezone.utc)
                self.guardrails.entry_throttle.record_entry(signal.symbol, user_id=user.id)
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
                logger.info("User %d [live]: BUY %s qty=%.6f @ %.2f (risk_mult=%.2f)",
                            user.id, signal.symbol, qty, filled_price, risk_mult)

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
            logger.info("User %d [%s]: Trade #%d closed (%s) PnL=%.2f",
                        user.id, order_mgr.mode, trade.id, reason, trade.pnl)
            # Record result in guardrails (for cooldown/breaker tracking)
            self._record_trade_close(trade, reason)
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

            cycle_dataframes: dict[str, pd.DataFrame] = {}

            # Candle key for entry throttle (aligned to 15m candle boundaries)
            now_utc = datetime.now(timezone.utc)
            candle_minute = (now_utc.minute // 15) * 15
            candle_key = now_utc.strftime(f"%Y%m%d_%H{candle_minute:02d}")
            self.guardrails.new_candle(candle_key)

            # Compute regime snapshots FIRST so guardrails can use them
            regime_snapshots: dict[str, object] = {}
            global_regime = "unknown"
            if self.meta_controller:
                regime_svc = self.meta_controller.regime_service
            else:
                regime_svc = None

            for symbol in self.symbols:
                try:
                    df = await self.fetch_klines(symbol, interval="15m", limit=150)
                    if df.empty:
                        continue

                    cycle_dataframes[symbol] = df

                    # Compute regime for this symbol (needed by guardrails)
                    if regime_svc:
                        snap = regime_svc.compute(df, symbol)
                        regime_snapshots[symbol] = snap

                except Exception:
                    logger.exception("Error fetching/computing regime for %s", symbol)

            # Get global regime
            if regime_svc:
                global_regime = regime_svc.global_regime()

            # Update guardrails with latest performance snapshot (computed by meta_controller)
            if self.meta_controller:
                if self.meta_controller.perf_monitor.snapshot:
                    perf_dict = self.meta_controller.perf_monitor.snapshot.to_dict()
                    perf_dict["global_regime"] = global_regime
                    self.guardrails.update_performance(perf_dict)

            for symbol in self.symbols:
                try:
                    df = cycle_dataframes.get(symbol)
                    if df is None or df.empty:
                        continue

                    current_price = float(df["close"].iloc[-1])
                    candle_high   = float(df["high"].iloc[-1])
                    candle_low    = float(df["low"].iloc[-1])
                    # Supplement with live WebSocket price for the current open candle
                    ws_price = self.last_prices.get(symbol, 0.0)
                    if ws_price > 0:
                        candle_high = max(candle_high, ws_price)
                        candle_low  = min(candle_low,  ws_price)
                        current_price = ws_price
                    self.last_prices[symbol] = current_price

                    # Use ADX from pre-computed regime snapshot (single computation)
                    sym_snap = regime_snapshots.get(symbol)
                    adx = sym_snap.adx if sym_snap else None

                    # Generate signals (shared across all users) — pass pre-computed ADX
                    signals = self._run_strategies(df, symbol, precomputed_adx=adx)

                    # Regime gate: filter signals based on ADX before execution
                    if adx is not None and signals:
                        regime_label = "TREND" if adx >= self._trend_adx_threshold else "RANGE"
                        logger.info("REGIME: %s (ADX=%.1f) [%s]", regime_label, adx, symbol)
                    signals = self._apply_regime_gate(signals, adx, symbol)

                    # Guardrails: filter BUY signals through centralized gate
                    if signals:
                        if sym_snap:
                            signals = self._apply_guardrails(signals, symbol, global_regime, sym_snap)
                        else:
                            # No regime data — apply kill switch and throttle checks only
                            # (conservative: block BUY if kill switch is active)
                            buy_signals_present = any(s.signal_type == SignalType.BUY for s in signals)
                            if buy_signals_present:
                                ks_verdict = self.guardrails.kill_switch.check()
                                if not ks_verdict.allowed:
                                    signals = [s for s in signals if s.signal_type != SignalType.BUY]
                                    logger.warning(
                                        "GUARDRAILS: no regime data for %s, kill switch active — BUY blocked",
                                        symbol,
                                    )

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
                                db, user, symbol, signals, current_price,
                                candle_high=candle_high, candle_low=candle_low,
                            )
                        except Exception:
                            logger.exception("Error executing for user %d on %s",
                                             user.id, symbol)

                except Exception:
                    logger.exception("Error processing symbol %s", symbol)

            # Adaptive layer: evaluate regime, performance, profile after cycle
            if self.meta_controller:
                try:
                    await self.meta_controller.evaluate(db, cycle_dataframes)
                except Exception:
                    logger.exception("MetaController evaluation failed")
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

