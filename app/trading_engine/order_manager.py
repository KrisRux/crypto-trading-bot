"""
Order execution module.

Handles the actual placement of orders on Binance (live mode) or delegation
to the paper trading engine (paper mode).
"""

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.trade import Order, OrderSide, OrderType, OrderStatus
from app.binance_client.rest_client import BinanceRestClient

logger = logging.getLogger(__name__)


class OrderManager:
    def __init__(self, client: BinanceRestClient, mode: str = "paper"):
        self.client = client
        self.mode = mode  # "live" or "paper"

    async def place_market_order(self, db: Session, symbol: str, side: str,
                                 quantity: float) -> Order:
        """Place a market order and record it in the database."""
        order = Order(
            symbol=symbol,
            side=OrderSide(side),
            order_type=OrderType.MARKET,
            quantity=quantity,
            mode=self.mode,
        )
        db.add(order)

        if self.mode == "live":
            try:
                result = await self.client.place_order(
                    symbol=symbol, side=side, order_type="MARKET", quantity=quantity
                )
                order.exchange_order_id = str(result.get("orderId", ""))
                order.filled_price = float(result.get("fills", [{}])[0].get("price", 0))
                order.status = OrderStatus.FILLED
                logger.info("Live MARKET %s order filled: %s qty=%.6f @ %.2f",
                            side, symbol, quantity, order.filled_price)
            except Exception as exc:
                order.status = OrderStatus.FAILED
                order.error_message = str(exc)[:500]
                logger.error("Live order failed: %s", exc)
        else:
            # Paper mode: filled immediately at "current" price (set by caller)
            order.status = OrderStatus.FILLED
            logger.info("Paper MARKET %s order filled: %s qty=%.6f",
                        side, symbol, quantity)

        order.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(order)
        return order

    async def place_limit_order(self, db: Session, symbol: str, side: str,
                                quantity: float, price: float) -> Order:
        """Place a limit order."""
        order = Order(
            symbol=symbol,
            side=OrderSide(side),
            order_type=OrderType.LIMIT,
            quantity=quantity,
            price=price,
            mode=self.mode,
        )
        db.add(order)

        if self.mode == "live":
            try:
                result = await self.client.place_order(
                    symbol=symbol, side=side, order_type="LIMIT",
                    quantity=quantity, price=price
                )
                order.exchange_order_id = str(result.get("orderId", ""))
                order.status = OrderStatus.PENDING  # Limit orders wait for fill
                logger.info("Live LIMIT %s order placed: %s qty=%.6f @ %.2f",
                            side, symbol, quantity, price)
            except Exception as exc:
                order.status = OrderStatus.FAILED
                order.error_message = str(exc)[:500]
                logger.error("Live limit order failed: %s", exc)
        else:
            order.status = OrderStatus.PENDING
            logger.info("Paper LIMIT %s order placed: %s qty=%.6f @ %.2f",
                        side, symbol, quantity, price)

        order.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(order)
        return order
