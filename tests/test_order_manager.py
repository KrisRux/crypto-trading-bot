"""
Tests for OrderManager execution-quality features (NO NETWORK):
- VWAP fills across multiple fills
- Slippage guard (records, does not fail)
- Maker (LIMIT GTX post-only) order pricing and persistence
- smart_entry routing

A FakeClient stands in for BinanceRestClient.place_order so nothing touches
the network. An in-memory SQLite session (same pattern as
tests/test_paper_trading.py) persists the Order rows.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.database import Base
from app.models.trade import Order, OrderStatus, OrderType
from app.trading_engine.order_manager import OrderManager


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


class FakeClient:
    """
    Stand-in for BinanceRestClient. Records every place_order call and returns
    a canned result:
      - MARKET -> multi-fill result (to exercise the VWAP) or a single
        configurable fill set.
      - LIMIT  -> an ACK (orderId, status NEW), no fills.
    """

    def __init__(self, market_fills=None, market_price=None):
        # Default: three fills with different prices/qtys.
        # VWAP = (100*1 + 102*2 + 104*1) / 4 = 408/... -> see test below.
        self.market_fills = market_fills if market_fills is not None else [
            {"price": "100.0", "qty": "1.0"},
            {"price": "102.0", "qty": "2.0"},
            {"price": "104.0", "qty": "1.0"},
        ]
        self.market_price = market_price
        self.calls = []

    async def place_order(self, symbol, side, order_type, quantity,
                          price=None, time_in_force=None):
        self.calls.append({
            "symbol": symbol, "side": side, "order_type": order_type,
            "quantity": quantity, "price": price, "time_in_force": time_in_force,
        })
        if order_type == "MARKET":
            result = {"orderId": 111, "status": "FILLED"}
            if self.market_fills is not None:
                result["fills"] = self.market_fills
            if self.market_price is not None:
                result["price"] = self.market_price
            return result
        # LIMIT (incl. GTX post-only) — exchange ACK, no fills yet.
        return {"orderId": 222, "status": "NEW"}


# --------------------------------------------------------------------------
# average_fill_price (VWAP) helper
# --------------------------------------------------------------------------

def test_average_fill_price_vwap():
    fills = [
        {"price": "100.0", "qty": "1.0"},
        {"price": "102.0", "qty": "2.0"},
        {"price": "104.0", "qty": "1.0"},
    ]
    # (100*1 + 102*2 + 104*1) / (1+2+1) = (100 + 204 + 104) / 4 = 408/4 = 102.0
    assert OrderManager.average_fill_price(fills) == pytest.approx(102.0)


def test_average_fill_price_empty_returns_zero():
    assert OrderManager.average_fill_price([]) == 0.0
    assert OrderManager.average_fill_price(None) == 0.0


def test_average_fill_price_not_first_fill():
    """VWAP must weight by qty, not just take fills[0]."""
    fills = [
        {"price": "100.0", "qty": "1.0"},
        {"price": "200.0", "qty": "9.0"},
    ]
    # fills[0] would give 100; VWAP = (100 + 1800)/10 = 190
    vwap = OrderManager.average_fill_price(fills)
    assert vwap == pytest.approx(190.0)
    assert vwap != pytest.approx(100.0)


# --------------------------------------------------------------------------
# place_market_order — VWAP fills
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_market_order_uses_vwap_over_all_fills(db_session):
    client = FakeClient()  # default 3 fills -> VWAP 102.0
    om = OrderManager(client, mode="paper")

    order = await om.place_market_order(db_session, "BTCUSDT", "BUY", 4.0)

    assert order.status == OrderStatus.FILLED
    assert order.filled_price == pytest.approx(102.0)
    assert order.exchange_order_id == "111"
    # Persisted
    fetched = db_session.query(Order).filter(Order.id == order.id).one()
    assert fetched.filled_price == pytest.approx(102.0)
    assert fetched.status == OrderStatus.FILLED


@pytest.mark.asyncio
async def test_market_order_fallback_to_result_price_when_no_fills(db_session):
    client = FakeClient(market_fills=[], market_price="123.45")
    om = OrderManager(client, mode="paper")

    order = await om.place_market_order(db_session, "BTCUSDT", "BUY", 1.0)

    assert order.status == OrderStatus.FILLED
    assert order.filled_price == pytest.approx(123.45)


# --------------------------------------------------------------------------
# Slippage guard
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_slippage_guard_flags_when_over_threshold(db_session):
    client = FakeClient()  # VWAP = 102.0
    om = OrderManager(client, mode="paper")

    # Expected 100 -> slippage = 2% which is >> default 0.3%; pass explicit too.
    order = await om.place_market_order(
        db_session, "BTCUSDT", "BUY", 4.0,
        expected_price=100.0, max_slippage_pct=0.3,
    )

    # Order still FILLED (NOT failed) — slippage is only recorded.
    assert order.status == OrderStatus.FILLED
    assert order.error_message is not None
    assert "slippage" in order.error_message.lower()


@pytest.mark.asyncio
async def test_slippage_guard_silent_within_threshold(db_session):
    client = FakeClient()  # VWAP = 102.0
    om = OrderManager(client, mode="paper")

    # Expected 102 -> 0% slippage, well within threshold.
    order = await om.place_market_order(
        db_session, "BTCUSDT", "BUY", 4.0,
        expected_price=102.0, max_slippage_pct=0.3,
    )
    assert order.status == OrderStatus.FILLED
    assert order.error_message is None


@pytest.mark.asyncio
async def test_slippage_guard_uses_settings_default(db_session, monkeypatch):
    monkeypatch.setattr(settings, "max_slippage_pct", 0.3, raising=False)
    client = FakeClient()  # VWAP = 102.0, expected 100 -> 2% > 0.3%
    om = OrderManager(client, mode="paper")

    order = await om.place_market_order(
        db_session, "BTCUSDT", "BUY", 4.0, expected_price=100.0,
    )
    assert order.status == OrderStatus.FILLED
    assert order.error_message is not None
    assert "slippage" in order.error_message.lower()


@pytest.mark.asyncio
async def test_no_slippage_check_without_expected_price(db_session):
    client = FakeClient()
    om = OrderManager(client, mode="paper")
    order = await om.place_market_order(db_session, "BTCUSDT", "BUY", 4.0)
    assert order.status == OrderStatus.FILLED
    assert order.error_message is None


# --------------------------------------------------------------------------
# place_maker_order — LIMIT GTX post-only with offset price
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_maker_buy_priced_below_ref(db_session, monkeypatch):
    monkeypatch.setattr(settings, "maker_limit_offset_pct", 0.05, raising=False)
    client = FakeClient()
    om = OrderManager(client, mode="live")

    ref = 100.0
    order = await om.place_maker_order(db_session, "BTCUSDT", "BUY", 1.0, ref)

    expected_price = ref * (1 - 0.05 / 100.0)  # 99.95
    assert order.order_type == OrderType.LIMIT
    assert order.status == OrderStatus.PENDING
    assert order.price == pytest.approx(expected_price)
    assert order.price < ref  # maker buy rests below ref

    # The client received LIMIT + GTX + the offset price.
    call = client.calls[-1]
    assert call["order_type"] == "LIMIT"
    assert call["time_in_force"] == "GTX"
    assert call["price"] == pytest.approx(expected_price)
    assert call["side"] == "BUY"


@pytest.mark.asyncio
async def test_maker_sell_priced_above_ref(db_session, monkeypatch):
    monkeypatch.setattr(settings, "maker_limit_offset_pct", 0.05, raising=False)
    client = FakeClient()
    om = OrderManager(client, mode="live")

    ref = 100.0
    order = await om.place_maker_order(db_session, "BTCUSDT", "SELL", 1.0, ref)

    expected_price = ref * (1 + 0.05 / 100.0)  # 100.05
    assert order.status == OrderStatus.PENDING
    assert order.price == pytest.approx(expected_price)
    assert order.price > ref  # maker sell rests above ref

    call = client.calls[-1]
    assert call["order_type"] == "LIMIT"
    assert call["time_in_force"] == "GTX"
    assert call["price"] == pytest.approx(expected_price)
    assert call["side"] == "SELL"


@pytest.mark.asyncio
async def test_maker_order_persisted_pending(db_session):
    client = FakeClient()
    om = OrderManager(client, mode="live")
    order = await om.place_maker_order(db_session, "ETHUSDT", "BUY", 2.0, 3000.0)

    fetched = db_session.query(Order).filter(Order.id == order.id).one()
    assert fetched.status == OrderStatus.PENDING
    assert fetched.order_type == OrderType.LIMIT
    assert fetched.exchange_order_id == "222"


# --------------------------------------------------------------------------
# smart_entry routing
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_smart_entry_prefer_maker_routes_to_maker(db_session, monkeypatch):
    monkeypatch.setattr(settings, "maker_limit_offset_pct", 0.05, raising=False)
    client = FakeClient()
    om = OrderManager(client, mode="live")

    order = await om.smart_entry(db_session, "BTCUSDT", "BUY", 1.0, 100.0,
                                 prefer_maker=True)

    assert order.order_type == OrderType.LIMIT
    assert order.status == OrderStatus.PENDING
    assert client.calls[-1]["time_in_force"] == "GTX"


@pytest.mark.asyncio
async def test_smart_entry_market_arms_slippage_guard(db_session, monkeypatch):
    monkeypatch.setattr(settings, "max_slippage_pct", 0.3, raising=False)
    client = FakeClient()  # VWAP 102 vs ref 100 -> 2% slippage
    om = OrderManager(client, mode="live")

    order = await om.smart_entry(db_session, "BTCUSDT", "BUY", 4.0, 100.0,
                                 prefer_maker=False)

    assert order.order_type == OrderType.MARKET
    assert order.status == OrderStatus.FILLED
    # expected_price=ref_price=100 was passed, so the guard fired.
    assert order.error_message is not None
    assert "slippage" in order.error_message.lower()
