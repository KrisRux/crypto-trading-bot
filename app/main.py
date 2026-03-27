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

setup_logging()
logger = logging.getLogger(__name__)

# Global engine reference
engine: TradingEngine | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the trading engine on app startup, stop on shutdown."""
    global engine

    # Initialize database tables
    init_db()
    logger.info("Database initialized")

    # Create and configure the trading engine
    engine = TradingEngine()

    # Register default strategies
    engine.register_strategy(SmaCrossoverStrategy(fast_period=10, slow_period=30))
    engine.register_strategy(RsiStrategy(period=14, oversold=30, overbought=70))
    engine.register_strategy(MacdStrategy(fast=12, slow=26, signal=9))
    engine.register_strategy(EmbientEnhancedStrategy(buy_threshold=60, sell_threshold=60))

    # Load Embient skills library
    skills_library = SkillsLibrary()
    logger.info("Embient skills: %s", skills_library.summary())

    # Make engine and skills available to API routes
    set_engine(engine, skills_library)

    # Start the engine in the background
    engine_task = asyncio.create_task(engine.start())
    logger.info("Trading engine started in %s mode", engine.mode)

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
    return {"status": "ok", "name": "Crypto Trading Bot", "mode": settings.trading_mode}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=settings.backend_host,
                port=settings.backend_port, reload=True)
