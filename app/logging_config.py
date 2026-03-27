"""
Structured logging configuration.
"""

import logging
import logging.handlers
import os
from app.config import settings


def setup_logging():
    os.makedirs("logs", exist_ok=True)

    log_format = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    # Root logger
    root = logging.getLogger()
    root.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))

    # Console handler
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(log_format, datefmt=date_format))
    root.addHandler(console)

    # Rotating file handler (max 5 MB, keep 5 backups)
    file_handler = logging.handlers.RotatingFileHandler(
        "logs/trading_bot.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(log_format, datefmt=date_format))
    root.addHandler(file_handler)

    # Silence noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
