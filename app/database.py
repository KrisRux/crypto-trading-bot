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
    """Create all tables and seed admin user if needed."""
    from app.models import trade, portfolio, user  # noqa: F401
    from app.models.user import User, hash_password
    from app.config import settings

    Base.metadata.create_all(bind=engine)

    # Seed the admin user from .env if it doesn't exist yet
    db = SessionLocal()
    try:
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
    finally:
        db.close()
