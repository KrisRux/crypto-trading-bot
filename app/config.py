"""
Application configuration loaded from environment variables / .env file.
"""

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    # Binance Live
    binance_api_key: str = Field(default="", alias="BINANCE_API_KEY")
    binance_api_secret: str = Field(default="", alias="BINANCE_API_SECRET")

    # Binance Testnet
    binance_testnet_api_key: str = Field(default="", alias="BINANCE_TESTNET_API_KEY")
    binance_testnet_api_secret: str = Field(default="", alias="BINANCE_TESTNET_API_SECRET")

    # Trading mode: "live" or "paper"
    trading_mode: str = Field(default="paper", alias="TRADING_MODE")

    # Paper trading
    paper_initial_capital: float = Field(default=10000.0, alias="PAPER_INITIAL_CAPITAL")

    # Database
    database_url: str = Field(default="sqlite:///./trading_bot.db", alias="DATABASE_URL")

    # Risk management
    max_position_size_pct: float = Field(default=2.0, alias="MAX_POSITION_SIZE_PCT")
    default_stop_loss_pct: float = Field(default=3.0, alias="DEFAULT_STOP_LOSS_PCT")
    default_take_profit_pct: float = Field(default=5.0, alias="DEFAULT_TAKE_PROFIT_PCT")

    # Trading symbols (comma-separated, e.g. "BTCUSDT,ETHUSDT")
    symbols: str = Field(default="BTCUSDT,ETHUSDT", alias="SYMBOLS")

    # Logging
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # Server
    backend_host: str = Field(default="0.0.0.0", alias="BACKEND_HOST")
    backend_port: int = Field(default=8000, alias="BACKEND_PORT")
    frontend_url: str = Field(default="http://localhost:5173", alias="FRONTEND_URL")

    model_config = {"env_file": ".env", "extra": "ignore"}

    @property
    def is_live(self) -> bool:
        return self.trading_mode == "live"

    @property
    def active_api_key(self) -> str:
        return self.binance_api_key if self.is_live else self.binance_testnet_api_key

    @property
    def active_api_secret(self) -> str:
        return self.binance_api_secret if self.is_live else self.binance_testnet_api_secret

    @property
    def symbol_list(self) -> list[str]:
        return [s.strip().upper() for s in self.symbols.split(",") if s.strip()]


settings = Settings()
