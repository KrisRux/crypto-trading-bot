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
    paper_fee_pct: float = Field(default=0.1, alias="PAPER_FEE_PCT")
    paper_slippage_pct: float = Field(default=0.02, alias="PAPER_SLIPPAGE_PCT")

    # --- Live execution fees (Binance Spot). Taker = MARKET, Maker = LIMIT post-only.
    # With the BNB fee discount taker ≈ 0.075%. Override per deployment in .env. ---
    taker_fee_pct: float = Field(default=0.1, alias="TAKER_FEE_PCT")
    maker_fee_pct: float = Field(default=0.1, alias="MAKER_FEE_PCT")
    # Prefer LIMIT post-only (maker) orders for non-urgent entries to halve fees.
    prefer_maker_orders: bool = Field(default=False, alias="PREFER_MAKER_ORDERS")
    maker_limit_offset_pct: float = Field(default=0.05, alias="MAKER_LIMIT_OFFSET_PCT")
    maker_fill_timeout_s: int = Field(default=20, alias="MAKER_FILL_TIMEOUT_S")
    # Reject/flag a market fill if observed VWAP slippage exceeds this %.
    max_slippage_pct: float = Field(default=0.3, alias="MAX_SLIPPAGE_PCT")
    # Binance signed-request recvWindow (ms) + server-time drift sync.
    binance_recv_window: int = Field(default=5000, alias="BINANCE_RECV_WINDOW")

    # --- Risk v2: ATR-based stops & risk-based position sizing ---
    use_atr_stops: bool = Field(default=True, alias="USE_ATR_STOPS")
    atr_sl_mult: float = Field(default=2.0, alias="ATR_SL_MULT")
    atr_tp_mult: float = Field(default=3.0, alias="ATR_TP_MULT")
    # Size positions so each trade risks a fixed % of equity (derived from SL distance),
    # capped by max_position_size_pct as a hard notional ceiling.
    risk_based_sizing: bool = Field(default=True, alias="RISK_BASED_SIZING")
    risk_pct_per_trade: float = Field(default=0.5, alias="RISK_PCT_PER_TRADE")

    # --- Multi-timeframe trend filter (avoid buying against the higher-TF trend) ---
    mtf_filter_enabled: bool = Field(default=True, alias="MTF_FILTER_ENABLED")
    mtf_interval: str = Field(default="1h", alias="MTF_INTERVAL")
    mtf_ema_period: int = Field(default=200, alias="MTF_EMA_PERIOD")
    # Allow a very strong local BUY setup to pass even while the slower
    # higher-timeframe EMA is still down. This avoids missing early recoveries
    # after selloffs while keeping weak counter-trend longs blocked.
    mtf_countertrend_override_enabled: bool = Field(default=True, alias="MTF_COUNTERTREND_OVERRIDE_ENABLED")
    mtf_countertrend_min_score: float = Field(default=90.0, alias="MTF_COUNTERTREND_MIN_SCORE")
    mtf_countertrend_min_adx: float = Field(default=32.0, alias="MTF_COUNTERTREND_MIN_ADX")
    mtf_countertrend_min_volume_ratio: float = Field(default=1.3, alias="MTF_COUNTERTREND_MIN_VOLUME_RATIO")
    mtf_countertrend_risk_multiplier: float = Field(default=0.5, alias="MTF_COUNTERTREND_RISK_MULTIPLIER")

    # --- Bear-market protection ---
    # Block new longs when the symbol's higher-timeframe trend is down.
    flat_in_bear: bool = Field(default=True, alias="FLAT_IN_BEAR")
    # Keep paper consistent with live SPOT reality (no synthetic shorts that can't
    # be executed on a spot account). Set False only to study short hypotheticals.
    disable_paper_shorts: bool = Field(default=True, alias="DISABLE_PAPER_SHORTS")

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
    # Global minimum severity to deliver over Telegram. Values: INFO | WARNING | CRITICAL.
    # Per-user override: users.telegram_min_level (empty string = inherit this global).
    telegram_min_level: str = Field(default="WARNING", alias="TELEGRAM_MIN_LEVEL")

    # AI Tuning Advisor — LLM providers (optional, fallback chain: DeepSeek → Ollama → rules)
    deepseek_api_key: str = Field(default="", alias="DEEPSEEK_API_KEY")
    deepseek_model: str = Field(default="deepseek-chat", alias="DEEPSEEK_MODEL")
    ollama_url: str = Field(default="http://localhost:11434", alias="OLLAMA_URL")
    ollama_model: str = Field(default="mistral", alias="OLLAMA_MODEL")
    # When True, the meta controller auto-applies LLM tuning suggestions with
    # confidence >= threshold and non-high risk. When False, suggestions are
    # only logged (advisory mode).
    enable_llm_tuning: bool = Field(default=False, alias="ENABLE_LLM_TUNING")
    llm_tuning_min_confidence: float = Field(default=0.6, alias="LLM_TUNING_MIN_CONFIDENCE")

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
