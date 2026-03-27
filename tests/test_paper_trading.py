"""
Tests for the paper trading portfolio manager.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.trade import Trade, Order
from app.models.portfolio import PaperPortfolio, PaperPosition
from app.paper_trading.portfolio import PaperPortfolioManager


@pytest.fixture
def db_session():
    """Create an in-memory SQLite database for testing."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def manager():
    return PaperPortfolioManager()


def test_create_portfolio(db_session, manager):
    portfolio = manager.get_or_create(db_session)
    assert portfolio is not None
    assert portfolio.cash_balance == 10000.0
    assert portfolio.total_equity == 10000.0
    assert portfolio.total_pnl == 0.0


def test_create_portfolio_idempotent(db_session, manager):
    p1 = manager.get_or_create(db_session)
    p2 = manager.get_or_create(db_session)
    assert p1.id == p2.id


def test_open_position(db_session, manager):
    manager.get_or_create(db_session)
    pos = manager.open_position(db_session, "BTCUSDT", 0.1, 50000, 48500, 52500)
    assert pos is not None
    assert pos.symbol == "BTCUSDT"
    assert pos.quantity == 0.1
    assert pos.entry_price == 50000

    portfolio = manager.get_or_create(db_session)
    assert portfolio.cash_balance == pytest.approx(10000 - 0.1 * 50000)


def test_open_position_insufficient_funds(db_session, manager):
    manager.get_or_create(db_session)
    # Try to buy way more than we can afford
    pos = manager.open_position(db_session, "BTCUSDT", 100, 50000, 48000, 52000)
    assert pos is None


def test_close_position_profit(db_session, manager):
    manager.get_or_create(db_session)
    pos = manager.open_position(db_session, "BTCUSDT", 0.1, 50000, 48500, 52500)
    assert pos is not None

    # Close at a higher price (profit)
    manager.close_position(db_session, pos, 52000, "manual")

    portfolio = manager.get_or_create(db_session)
    expected_pnl = (52000 - 50000) * 0.1  # 200 USDT profit
    assert portfolio.total_pnl == pytest.approx(expected_pnl)
    assert portfolio.winning_trades == 1
    assert portfolio.losing_trades == 0
    # Cash should be back: initial - cost + proceeds
    assert portfolio.cash_balance == pytest.approx(10000 - 5000 + 5200)


def test_close_position_loss(db_session, manager):
    manager.get_or_create(db_session)
    pos = manager.open_position(db_session, "BTCUSDT", 0.1, 50000, 48500, 52500)
    assert pos is not None

    manager.close_position(db_session, pos, 49000, "stop_loss")

    portfolio = manager.get_or_create(db_session)
    expected_pnl = (49000 - 50000) * 0.1  # -100 USDT loss
    assert portfolio.total_pnl == pytest.approx(expected_pnl)
    assert portfolio.winning_trades == 0
    assert portfolio.losing_trades == 1


def test_check_tp_sl_take_profit(db_session, manager):
    manager.get_or_create(db_session)
    manager.open_position(db_session, "BTCUSDT", 0.1, 50000, 48000, 52000)

    # Price hits take profit
    closed = manager.check_tp_sl(db_session, 52500)
    assert len(closed) == 1
    assert closed[0][1] == "take_profit"


def test_check_tp_sl_stop_loss(db_session, manager):
    manager.get_or_create(db_session)
    manager.open_position(db_session, "BTCUSDT", 0.1, 50000, 48000, 52000)

    # Price hits stop loss
    closed = manager.check_tp_sl(db_session, 47500)
    assert len(closed) == 1
    assert closed[0][1] == "stop_loss"


def test_check_tp_sl_no_trigger(db_session, manager):
    manager.get_or_create(db_session)
    manager.open_position(db_session, "BTCUSDT", 0.1, 50000, 48000, 52000)

    # Price between SL and TP
    closed = manager.check_tp_sl(db_session, 50500)
    assert len(closed) == 0


def test_reset_portfolio(db_session, manager):
    manager.get_or_create(db_session)
    manager.open_position(db_session, "BTCUSDT", 0.1, 50000, 48000, 52000)

    manager.reset(db_session)

    portfolio = manager.get_or_create(db_session)
    assert portfolio.cash_balance == 10000.0
    assert portfolio.total_pnl == 0.0
    assert portfolio.total_trades == 0

    positions = db_session.query(PaperPosition).all()
    assert len(positions) == 0


def test_export_csv(db_session, manager):
    manager.get_or_create(db_session)
    manager.open_position(db_session, "BTCUSDT", 0.1, 50000, 48000, 52000)

    csv_data = manager.export_trades_csv(db_session)
    assert "BTCUSDT" in csv_data
    assert "BUY" in csv_data
    lines = csv_data.strip().split("\n")
    assert len(lines) >= 2  # header + at least 1 trade
