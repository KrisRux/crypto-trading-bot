"""
FastAPI application entrypoint.

Initializes the database, registers strategies, starts the trading engine
in the background, and serves the REST API.
"""

import asyncio
import logging

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import init_db
from app.logging_config import setup_logging
from app.api.routes import router, set_engine
from app.trading_engine.engine import TradingEngine
from app.strategies.sma_crossover import SmaCrossoverStrategy
from app.strategies.rsi_strategy import RsiStrategy
from app.strategies.macd_strategy import MacdStrategy
from app.strategies.embient_enhanced import EmbientEnhancedStrategy
from app.embient_skills.loader import SkillsLibrary
from app.strategy_store import load_strategy_params, load_risk_params
from app.adaptive.meta_controller import MetaController

setup_logging()
logger = logging.getLogger(__name__)

# Global engine reference
engine: TradingEngine | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the trading engine on app startup, stop on shutdown."""
    global engine

    # Warn about insecure default configuration values
    settings.warn_insecure_defaults()

    # Initialize database tables
    init_db()
    logger.info("Database initialized")

    # Load Embient skills library first (needed to configure EmbientEnhancedStrategy)
    skills_library = SkillsLibrary()
    logger.info("Embient skills: %s", skills_library.summary())

    # Create and configure the trading engine
    engine = TradingEngine()

    # Register strategies with class defaults (may be overridden by saved params below)
    engine.register_strategy(SmaCrossoverStrategy())
    engine.register_strategy(RsiStrategy())
    engine.register_strategy(MacdStrategy())
    engine.register_strategy(EmbientEnhancedStrategy(skills_library=skills_library))

    # Restore persisted params saved by the UI (survives restarts)
    saved_strategies = load_strategy_params()
    for strat in engine.strategies:
        if strat.name in saved_strategies:
            saved = saved_strategies[strat.name]
            strat.enabled = saved.get("enabled", strat.enabled)
            if "params" in saved:
                strat.set_params(saved["params"])
            logger.info("Strategy '%s': restored saved params", strat.name)

    saved_risk = load_risk_params()
    if saved_risk:
        engine.risk_manager.set_params(saved_risk)
        logger.info("Risk manager: restored saved params")

    # Initialize adaptive layer (meta-controller)
    meta_controller = MetaController(
        engine,
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
    )
    engine.meta_controller = meta_controller

    # Apply active profile from config on startup
    active_profile = meta_controller.profile_manager.active_profile
    if active_profile != "normal":
        meta_controller.profile_manager.apply_profile(active_profile, engine, "startup restore")
        logger.info("Restored active profile: %s", active_profile)

    # Make engine, skills, and meta_controller available to API routes
    set_engine(engine, skills_library, meta_controller)

    # Start the engine in the background
    engine_task = asyncio.create_task(engine.start())
    logger.info("Trading engine started")

    yield  # App is running

    # Shutdown
    logger.info("Shutting down trading engine...")
    await engine.stop()
    engine_task.cancel()
    try:
        await engine_task
    except asyncio.CancelledError:
        pass
    logger.info("Shutdown complete")


app = FastAPI(
    title="Crypto Trading Bot",
    description="Automated cryptocurrency trading bot with Binance integration",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS – allow frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url, "http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/")
def root():
    return {"status": "ok", "name": "Crypto Trading Bot"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=settings.backend_host,
                port=settings.backend_port, reload=True)
