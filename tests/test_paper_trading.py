"""
Tests for the per-user paper trading portfolio manager.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.trade import Trade, Order
from app.models.portfolio import PaperPortfolio, PaperPosition
from app.models.user import User, hash_password
from app.paper_trading.portfolio import PaperPortfolioManager

TEST_USER_ID = 1


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    # Create a test user
    user = User(
        id=TEST_USER_ID, username="testuser",
        password_hash=hash_password("test"), role="user",
    )
    session.add(user)
    session.commit()
    yield session
    session.close()


@pytest.fixture
def manager():
    return PaperPortfolioManager()


def test_create_portfolio(db_session, manager):
    portfolio = manager.get_or_create(db_session, TEST_USER_ID)
    assert portfolio is not None
    assert portfolio.user_id == TEST_USER_ID
    assert portfolio.cash_balance == 10000.0
    assert portfolio.total_equity == 10000.0


def test_create_portfolio_idempotent(db_session, manager):
    p1 = manager.get_or_create(db_session, TEST_USER_ID)
    p2 = manager.get_or_create(db_session, TEST_USER_ID)
    assert p1.id == p2.id


def test_create_portfolio_custom_capital(db_session, manager):
    portfolio = manager.get_or_create(db_session, TEST_USER_ID, initial_capital=50000)
    assert portfolio.initial_capital == 50000.0
    assert portfolio.cash_balance == 50000.0


def test_separate_portfolios_per_user(db_session, manager):
    """Each user gets their own portfolio."""
    user2 = User(id=2, username="user2", password_hash=hash_password("x"), role="user")
    db_session.add(user2)
    db_session.commit()

    p1 = manager.get_or_create(db_session, TEST_USER_ID)
    p2 = manager.get_or_create(db_session, 2, initial_capital=5000)
    assert p1.id != p2.id
    assert p1.cash_balance == 10000.0
    assert p2.cash_balance == 5000.0


def test_open_position(db_session, manager):
    manager.get_or_create(db_session, TEST_USER_ID)
    pos = manager.open_position(db_session, TEST_USER_ID, "BTCUSDT", 0.1, 50000, 48500, 52500)
    assert pos is not None
    assert pos.symbol == "BTCUSDT"
    assert pos.user_id == TEST_USER_ID

    portfolio = manager.get_or_create(db_session, TEST_USER_ID)
    assert portfolio.cash_balance == pytest.approx(10000 - 0.1 * 50000)


def test_open_position_insufficient_funds(db_session, manager):
    manager.get_or_create(db_session, TEST_USER_ID)
    pos = manager.open_position(db_session, TEST_USER_ID, "BTCUSDT", 100, 50000, 48000, 52000)
    assert pos is None


def test_close_position_profit(db_session, manager):
    manager.get_or_create(db_session, TEST_USER_ID)
    pos = manager.open_position(db_session, TEST_USER_ID, "BTCUSDT", 0.1, 50000, 48500, 52500)
    assert pos is not None

    manager.close_position(db_session, pos, 52000, "manual")

    portfolio = manager.get_or_create(db_session, TEST_USER_ID)
    expected_pnl = (52000 - 50000) * 0.1
    assert portfolio.total_pnl == pytest.approx(expected_pnl)
    assert portfolio.winning_trades == 1
    assert portfolio.losing_trades == 0


def test_close_position_loss(db_session, manager):
    manager.get_or_create(db_session, TEST_USER_ID)
    pos = manager.open_position(db_session, TEST_USER_ID, "BTCUSDT", 0.1, 50000, 48500, 52500)
    assert pos is not None

    manager.close_position(db_session, pos, 49000, "stop_loss")

    portfolio = manager.get_or_create(db_session, TEST_USER_ID)
    expected_pnl = (49000 - 50000) * 0.1
    assert portfolio.total_pnl == pytest.approx(expected_pnl)
    assert portfolio.winning_trades == 0
    assert portfolio.losing_trades == 1


def test_check_tp_sl_take_profit(db_session, manager):
    manager.get_or_create(db_session, TEST_USER_ID)
    manager.open_position(db_session, TEST_USER_ID, "BTCUSDT", 0.1, 50000, 48000, 52000)

    closed = manager.check_tp_sl_symbol(db_session, TEST_USER_ID, "BTCUSDT", 52500)
    assert len(closed) == 1
    assert closed[0][1] == "take_profit"


def test_check_tp_sl_stop_loss(db_session, manager):
    manager.get_or_create(db_session, TEST_USER_ID)
    manager.open_position(db_session, TEST_USER_ID, "BTCUSDT", 0.1, 50000, 48000, 52000)

    closed = manager.check_tp_sl_symbol(db_session, TEST_USER_ID, "BTCUSDT", 47500)
    assert len(closed) == 1
    assert closed[0][1] == "stop_loss"


def test_check_tp_sl_no_trigger(db_session, manager):
    manager.get_or_create(db_session, TEST_USER_ID)
    manager.open_position(db_session, TEST_USER_ID, "BTCUSDT", 0.1, 50000, 48000, 52000)

    closed = manager.check_tp_sl_symbol(db_session, TEST_USER_ID, "BTCUSDT", 50500)
    assert len(closed) == 0


def test_positions_isolated_between_users(db_session, manager):
    """User 1's positions are not visible to user 2."""
    user2 = User(id=2, username="user2", password_hash=hash_password("x"), role="user")
    db_session.add(user2)
    db_session.commit()

    manager.get_or_create(db_session, TEST_USER_ID)
    manager.get_or_create(db_session, 2)
    manager.open_position(db_session, TEST_USER_ID, "BTCUSDT", 0.1, 50000, 48000, 52000)

    # User 2 should have no positions
    closed = manager.check_tp_sl_symbol(db_session, 2, "BTCUSDT", 52500)
    assert len(closed) == 0

    # User 1 should have the position
    closed = manager.check_tp_sl_symbol(db_session, TEST_USER_ID, "BTCUSDT", 52500)
    assert len(closed) == 1


def test_reset_portfolio(db_session, manager):
    manager.get_or_create(db_session, TEST_USER_ID)
    manager.open_position(db_session, TEST_USER_ID, "BTCUSDT", 0.1, 50000, 48000, 52000)

    manager.reset(db_session, TEST_USER_ID)

    portfolio = manager.get_or_create(db_session, TEST_USER_ID)
    assert portfolio.cash_balance == 10000.0
    assert portfolio.total_pnl == 0.0
    assert portfolio.total_trades == 0

    positions = db_session.query(PaperPosition).filter(
        PaperPosition.user_id == TEST_USER_ID
    ).all()
    assert len(positions) == 0


def test_export_csv(db_session, manager):
    manager.get_or_create(db_session, TEST_USER_ID)
    manager.open_position(db_session, TEST_USER_ID, "BTCUSDT", 0.1, 50000, 48000, 52000)

    csv_data = manager.export_trades_csv(db_session, TEST_USER_ID)
    assert "BTCUSDT" in csv_data
    assert "BUY" in csv_data
    lines = csv_data.strip().split("\n")
    assert len(lines) >= 2
