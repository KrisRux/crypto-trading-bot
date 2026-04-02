"""
Application configuration loaded from environment variables / .env file.

NOTE: Binance API keys and trading mode are per-user (stored in the database).
This file only contains server-wide settings.
"""

import logging
from pydantic_settings import BaseSettings
from pydantic import Field

logger = logging.getLogger(__name__)

_DEFAULT_JWT_SECRET = "change-this-secret-key-in-production"


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
    jwt_secret: str = Field(default=_DEFAULT_JWT_SECRET, alias="JWT_SECRET")
    jwt_expiry_hours: int = Field(default=24, alias="JWT_EXPIRY_HOURS")
    session_timeout_minutes: int = Field(default=30, alias="SESSION_TIMEOUT_MINUTES")

    # Encryption key for API keys stored in DB (Fernet base64-urlsafe 32-byte key).
    # Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # If left empty an ephemeral key is generated at startup (keys won't survive restarts).
    encryption_key: str = Field(default="", alias="ENCRYPTION_KEY")

    # Telegram notifications (bot token is server-wide; chat_id is per-user in DB)
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")

    # Server
    backend_host: str = Field(default="0.0.0.0", alias="BACKEND_HOST")
    backend_port: int = Field(default=8000, alias="BACKEND_PORT")
    frontend_url: str = Field(default="http://localhost:5173", alias="FRONTEND_URL")

    model_config = {"env_file": ".env", "extra": "ignore"}

    @property
    def symbol_list(self) -> list[str]:
        return [s.strip().upper() for s in self.symbols.split(",") if s.strip()]

    def warn_insecure_defaults(self):
        """Log warnings for any insecure default values still in use."""
        if self.jwt_secret == _DEFAULT_JWT_SECRET:
            logger.warning(
                "JWT_SECRET is set to the default value — this is insecure. "
                "Set a strong random secret in .env: "
                "python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        if self.auth_password == "changeme":
            logger.warning(
                "AUTH_PASSWORD is 'changeme' — change it in .env before deploying."
            )
        if not self.encryption_key:
            logger.warning(
                "ENCRYPTION_KEY is not set — API keys will be unreadable after restart. "
                "Generate one with: "
                "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )


settings = Settings()
