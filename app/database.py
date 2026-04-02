"""
Database setup with SQLAlchemy async engine.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from app.config import settings


# For SQLite, use check_same_thread=False
connect_args = {}
if settings.database_url.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine = create_engine(settings.database_url, connect_args=connect_args, echo=False)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency that yields a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables, seed admin user, and seed default symbols if needed."""
    from app.models import trade, portfolio, user, symbol, approval  # noqa: F401
    from app.models.user import User, hash_password
    from app.models.symbol import TradingSymbol
    from app.config import settings

    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        # Seed the admin user from .env if it doesn't exist yet
        admin = db.query(User).filter(User.username == settings.auth_username).first()
        if not admin:
            admin = User(
                username=settings.auth_username,
                password_hash=hash_password(settings.auth_password),
                display_name="Admin",
                role="admin",
            )
            db.add(admin)
            db.commit()

        # Seed default symbols from .env if the table is empty
        if db.query(TradingSymbol).count() == 0:
            for sym in settings.symbol_list:
                db.add(TradingSymbol(symbol=sym))
            db.commit()
    finally:
        db.close()


def load_symbols_from_db() -> list[str]:
    """Return the list of active trading symbols from the database."""
    from app.models.symbol import TradingSymbol
    db = SessionLocal()
    try:
        rows = db.query(TradingSymbol).all()
        return [r.symbol for r in rows]
    finally:
        db.close()
