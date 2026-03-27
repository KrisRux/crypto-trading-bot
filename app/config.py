"""
Application configuration loaded from environment variables / .env file.

NOTE: Binance API keys and trading mode are per-user (stored in the database).
This file only contains server-wide settings.
"""

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    # Database
    database_url: str = Field(default="sqlite:///./trading_bot.db", alias="DATABASE_URL")

    # Trading symbols (comma-separated, e.g. "BTCUSDT,ETHUSDT")
    symbols: str = Field(default="BTCUSDT,ETHUSDT", alias="SYMBOLS")

    # Risk management defaults
    max_position_size_pct: float = Field(default=2.0, alias="MAX_POSITION_SIZE_PCT")
    default_stop_loss_pct: float = Field(default=3.0, alias="DEFAULT_STOP_LOSS_PCT")
    default_take_profit_pct: float = Field(default=5.0, alias="DEFAULT_TAKE_PROFIT_PCT")

    # Logging
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # Authentication — admin user seeded on first startup
    auth_username: str = Field(default="admin", alias="AUTH_USERNAME")
    auth_password: str = Field(default="changeme", alias="AUTH_PASSWORD")
    jwt_secret: str = Field(default="change-this-secret-key-in-production", alias="JWT_SECRET")
    jwt_expiry_hours: int = Field(default=24, alias="JWT_EXPIRY_HOURS")
    session_timeout_minutes: int = Field(default=30, alias="SESSION_TIMEOUT_MINUTES")

    # Server
    backend_host: str = Field(default="0.0.0.0", alias="BACKEND_HOST")
    backend_port: int = Field(default=8000, alias="BACKEND_PORT")
    frontend_url: str = Field(default="http://localhost:5173", alias="FRONTEND_URL")

    model_config = {"env_file": ".env", "extra": "ignore"}

    @property
    def symbol_list(self) -> list[str]:
        return [s.strip().upper() for s in self.symbols.split(",") if s.strip()]


settings = Settings()
