"""
Database setup with SQLAlchemy async engine.
"""

import sqlalchemy as sa
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


def _migrate_add_columns(eng, table: str, columns: list[tuple[str, str]]):
    """Add columns to an existing table if they don't exist (SQLite safe)."""
    import logging
    _log = logging.getLogger(__name__)
    with eng.connect() as conn:
        result = conn.execute(sa.text(f"PRAGMA table_info({table})"))
        existing = {row[1] for row in result}
        for col_name, col_def in columns:
            if col_name not in existing:
                conn.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}"))
                conn.commit()
                _log.info("Migration: added column %s.%s", table, col_name)


def init_db():
    """Create all tables, seed admin user, and seed default symbols if needed."""
    from app.models import trade, portfolio, user, symbol, approval, tuning_suggestion  # noqa: F401
    from app.models.user import User, hash_password
    from app.models.symbol import TradingSymbol
    from app.config import settings

    Base.metadata.create_all(bind=engine)

    # Lightweight migration: add columns that don't exist yet on older DBs
    _migrate_add_columns(engine, "users", [
        ("telegram_chat_id", "VARCHAR DEFAULT ''"),
        ("telegram_enabled", "BOOLEAN DEFAULT 0"),
    ])

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
