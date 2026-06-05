"""
Order execution module.

ALL orders are sent to Binance (testnet or production).
The mode label ("paper" or "live") is only used for DB tagging.
The client passed to OrderManager determines the actual endpoint.

Execution quality features:
- VWAP fills: a market order's filled_price is the volume-weighted average of
  ALL reported fills, not just the first one.
- Slippage guard: when an expected price is supplied, an anomalous deviation of
  the VWAP fill is logged and recorded on the order (the order is NOT failed —
  it has already executed on the exchange).
- Maker orders: place_maker_order posts a LIMIT GTX (post-only) order offset
  from a reference price so it always rests as a maker.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.models.trade import Order, OrderSide, OrderType, OrderStatus
from app.binance_client.rest_client import BinanceRestClient

logger = logging.getLogger(__name__)


class OrderManager:
    def __init__(self, client: BinanceRestClient, mode: str = "paper"):
        self.client = client
        self.mode = mode  # "paper" or "live" — for DB tagging only

    @staticmethod
    def average_fill_price(fills: list) -> float:
        """
        Volume-weighted average price (VWAP) across all fills of an order.

        Returns sum(price * qty) / sum(qty) over every fill. Each fill is a
        dict like {"price": "...", "qty": "..."} as returned by Binance.
        Returns 0.0 if there are no fills or the total quantity is zero.
        """
        total_qty = 0.0
        total_notional = 0.0
        for fill in fills or []:
            try:
                price = float(fill.get("price", 0) or 0)
                qty = float(fill.get("qty", 0) or 0)
            except (TypeError, ValueError):
                continue
            total_notional += price * qty
            total_qty += qty
        if total_qty <= 0:
            return 0.0
        return total_notional / total_qty

    async def place_market_order(self, db: Session, symbol: str, side: str,
                                 quantity: float,
                                 expected_price: float | None = None,
                                 max_slippage_pct: float | None = None) -> Order:
        """
        Place a real market order on Binance (testnet or production).

        The filled price is computed as the volume-weighted average (VWAP) over
        ALL reported fills, falling back to result["price"] when no fills are
        returned.

        Slippage guard: if ``expected_price`` is provided and the VWAP fill
        deviates from it by more than ``max_slippage_pct`` (default
        ``settings.max_slippage_pct``), a WARNING is logged and a note is written
        to ``order.error_message``. The order is NOT failed — it has already
        executed — the anomalous slippage is only recorded.
        """
        if max_slippage_pct is None:
            max_slippage_pct = settings.max_slippage_pct

        order = Order(
            symbol=symbol,
            side=OrderSide(side),
            order_type=OrderType.MARKET,
            quantity=quantity,
            mode=self.mode,
        )
        db.add(order)

        try:
            result = await self.client.place_order(
                symbol=symbol, side=side, order_type="MARKET", quantity=quantity
            )
            order.exchange_order_id = str(result.get("orderId", ""))
            fills = result.get("fills", [])
            vwap = self.average_fill_price(fills)
            if vwap > 0:
                order.filled_price = vwap
            else:
                order.filled_price = float(result.get("price", 0) or 0)
            order.status = OrderStatus.FILLED
            logger.info("[%s] MARKET %s filled: %s qty=%.6f @ %.2f",
                        self.mode.upper(), side, symbol, quantity,
                        order.filled_price or 0)

            # Slippage guard — record (do not fail) anomalous deviation.
            self._check_slippage(order, expected_price, max_slippage_pct)
        except Exception as exc:
            order.status = OrderStatus.FAILED
            order.error_message = str(exc)[:500]
            logger.error("[%s] MARKET %s failed for %s: %s",
                         self.mode.upper(), side, symbol, exc)

        order.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(order)
        return order

    def _check_slippage(self, order: Order, expected_price: float | None,
                        max_slippage_pct: float) -> None:
        """
        Flag (without failing) a market fill whose VWAP deviates from the
        expected price by more than ``max_slippage_pct``.

        The order is already FILLED on the exchange — this only logs a WARNING
        and appends a note to ``order.error_message`` for later auditing.
        """
        if expected_price is None or not expected_price or not order.filled_price:
            return
        slippage_pct = abs(order.filled_price - expected_price) / expected_price * 100
        if slippage_pct > max_slippage_pct:
            note = (f"slippage {slippage_pct:.3f}% > {max_slippage_pct:.3f}% "
                    f"(expected {expected_price:.2f}, filled {order.filled_price:.2f})")
            logger.warning("[%s] %s %s SLIPPAGE GUARD: %s",
                           self.mode.upper(), order.side.value, order.symbol, note)
            order.error_message = note[:500]

    async def place_limit_order(self, db: Session, symbol: str, side: str,
                                quantity: float, price: float) -> Order:
        """Place a real limit order on Binance (testnet or production)."""
        order = Order(
            symbol=symbol,
            side=OrderSide(side),
            order_type=OrderType.LIMIT,
            quantity=quantity,
            price=price,
            mode=self.mode,
        )
        db.add(order)

        try:
            result = await self.client.place_order(
                symbol=symbol, side=side, order_type="LIMIT",
                quantity=quantity, price=price
            )
            order.exchange_order_id = str(result.get("orderId", ""))
            order.status = OrderStatus.PENDING
            logger.info("[%s] LIMIT %s placed: %s qty=%.6f @ %.2f",
                        self.mode.upper(), side, symbol, quantity, price)
        except Exception as exc:
            order.status = OrderStatus.FAILED
            order.error_message = str(exc)[:500]
            logger.error("[%s] LIMIT %s failed for %s: %s",
                         self.mode.upper(), side, symbol, exc)

        order.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(order)
        return order

    async def place_maker_order(self, db: Session, symbol: str, side: str,
                                quantity: float, ref_price: float) -> Order:
        """
        Place a LIMIT post-only (maker) order on Binance.

        Uses time_in_force="GTX" — GTX = Good-Til-Crossing (post-only): the
        order is rejected by the exchange if it would immediately match (take
        liquidity), guaranteeing maker status and the lower maker fee.

        The limit price is offset from ``ref_price`` by
        ``settings.maker_limit_offset_pct`` so the order rests in the book:
        - BUY: placed BELOW ref_price (bid side)
        - SELL: placed ABOVE ref_price (ask side)

        Returns the Order with status PENDING (resting on the book) — or FAILED
        if the exchange rejects the request.
        """
        offset = settings.maker_limit_offset_pct / 100.0
        side_enum = OrderSide(side)
        if side_enum == OrderSide.BUY:
            price = ref_price * (1 - offset)
        else:
            price = ref_price * (1 + offset)

        order = Order(
            symbol=symbol,
            side=side_enum,
            order_type=OrderType.LIMIT,
            quantity=quantity,
            price=price,
            mode=self.mode,
        )
        db.add(order)

        try:
            result = await self.client.place_order(
                symbol=symbol, side=side, order_type="LIMIT",
                time_in_force="GTX", price=price, quantity=quantity
            )
            order.exchange_order_id = str(result.get("orderId", ""))
            order.status = OrderStatus.PENDING
            logger.info("[%s] MAKER (LIMIT GTX) %s placed: %s qty=%.6f @ %.2f "
                        "(ref=%.2f, offset=%.3f%%)",
                        self.mode.upper(), side, symbol, quantity, price,
                        ref_price, settings.maker_limit_offset_pct)
        except Exception as exc:
            order.status = OrderStatus.FAILED
            order.error_message = str(exc)[:500]
            logger.error("[%s] MAKER (LIMIT GTX) %s failed for %s: %s",
                         self.mode.upper(), side, symbol, exc)

        order.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(order)
        return order

    async def smart_entry(self, db: Session, symbol: str, side: str,
                          quantity: float, ref_price: float,
                          prefer_maker: bool) -> Order:
        """
        Route an entry to the cheapest viable execution.

        - prefer_maker=True  -> place_maker_order (LIMIT GTX post-only, resting).
        - prefer_maker=False -> place_market_order with the slippage guard armed
          (expected_price=ref_price).
        """
        if prefer_maker:
            return await self.place_maker_order(db, symbol, side, quantity, ref_price)
        return await self.place_market_order(
            db, symbol, side, quantity, expected_price=ref_price
        )
