"""
Order execution module.

ALL orders are sent to Binance (testnet or production).
The mode label ("paper" or "live") is only used for DB tagging.
The client passed to OrderManager determines the actual endpoint.
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
        self.mode = mode  # "paper" or "live" — for DB tagging only

    async def place_market_order(self, db: Session, symbol: str, side: str,
                                 quantity: float) -> Order:
        """Place a real market order on Binance (testnet or production)."""
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
            if fills:
                order.filled_price = float(fills[0].get("price", 0))
            else:
                order.filled_price = float(result.get("price", 0))
            order.status = OrderStatus.FILLED
            logger.info("[%s] MARKET %s filled: %s qty=%.6f @ %.2f",
                        self.mode.upper(), side, symbol, quantity,
                        order.filled_price or 0)
        except Exception as exc:
            order.status = OrderStatus.FAILED
            order.error_message = str(exc)[:500]
            logger.error("[%s] MARKET %s failed for %s: %s",
                         self.mode.upper(), side, symbol, exc)

        order.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(order)
        return order

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
