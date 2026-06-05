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


def _backfill_trade_accounting(eng):
    """One-time, idempotent backfill of the net-PnL columns.

    Historically ``trades.pnl`` stored the price-only (GROSS) PnL and fees were
    never subtracted from recorded results. This splits each historical CLOSED
    trade into ``gross_pnl`` + ``fee`` + ``slippage`` and rewrites ``pnl`` as the
    NET result, so every consumer (balance, breakdown, kill-switch) reads one
    consistent number. Only rows whose ``gross_pnl`` is still NULL are touched,
    so re-running on every startup is a no-op. The original gross value is kept
    in ``gross_pnl`` (auditable / reversible).
    """
    import logging
    from app.config import settings
    _log = logging.getLogger(__name__)
    fee_rate = settings.paper_fee_pct / 100.0
    slip_rate = settings.paper_slippage_pct / 100.0
    with eng.connect() as conn:
        cols = {row[1] for row in conn.execute(sa.text("PRAGMA table_info(trades)"))}
        if "gross_pnl" not in cols:
            return
        rows = conn.execute(sa.text(
            "SELECT id, entry_price, exit_price, quantity, pnl FROM trades "
            "WHERE status = 'CLOSED' AND pnl IS NOT NULL AND gross_pnl IS NULL"
        )).fetchall()
        n = 0
        for tid, entry, exitp, qty, pnl in rows:
            if entry is None or exitp is None or qty is None:
                continue
            notional = (abs(entry) + abs(exitp)) * abs(qty)  # entry + exit legs
            fee = notional * fee_rate
            slip = notional * slip_rate
            net = pnl - fee - slip
            base = abs(entry) * abs(qty)
            net_pct = (net / base * 100.0) if base else 0.0
            conn.execute(sa.text(
                "UPDATE trades SET gross_pnl = :g, fee = :f, slippage = :s, "
                "pnl = :net, pnl_pct = :p WHERE id = :id"
            ), {"g": pnl, "f": fee, "s": slip, "net": net, "p": net_pct, "id": tid})
            n += 1
        if n:
            conn.commit()
            _log.warning(
                "Backfill: rewrote %d trades to NET pnl (gross kept in gross_pnl). "
                "Reported PnL/equity now includes fees+slippage.", n,
            )


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
        ("telegram_min_level", "VARCHAR DEFAULT ''"),
    ])
    _migrate_add_columns(engine, "tuning_suggestions", [
        ("source", "VARCHAR DEFAULT 'rules'"),
    ])
    # Net-PnL accounting columns: pnl now stores NET (after fees+slippage),
    # gross_pnl preserves the price-only PnL. Backfilled by reconcile_trade_accounting().
    _migrate_add_columns(engine, "trades", [
        ("gross_pnl", "FLOAT"),
        ("fee", "FLOAT"),
        ("slippage", "FLOAT"),
        ("exit_reason", "VARCHAR"),
    ])
    _backfill_trade_accounting(engine)

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
