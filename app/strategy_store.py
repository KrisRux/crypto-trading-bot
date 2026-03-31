"""
Strategy and risk parameter persistence.

Saves to JSON files in the working directory so params survive restarts.
Files: strategy_params.json, risk_params.json
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

_STRATEGY_FILE = "strategy_params.json"
_RISK_FILE = "risk_params.json"


def load_strategy_params() -> dict:
    """Return {strategy_name: {enabled, params}} from disk, or {} if not found."""
    try:
        if os.path.exists(_STRATEGY_FILE):
            with open(_STRATEGY_FILE, "r") as f:
                return json.load(f)
    except Exception:
        logger.exception("Failed to load strategy params from %s", _STRATEGY_FILE)
    return {}


def save_strategy_params(data: dict) -> None:
    """Persist {strategy_name: {enabled, params}} to disk."""
    try:
        with open(_STRATEGY_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        logger.exception("Failed to save strategy params to %s", _STRATEGY_FILE)


def load_risk_params() -> dict:
    """Return risk params dict from disk, or {} if not found."""
    try:
        if os.path.exists(_RISK_FILE):
            with open(_RISK_FILE, "r") as f:
                return json.load(f)
    except Exception:
        logger.exception("Failed to load risk params from %s", _RISK_FILE)
    return {}


def save_risk_params(data: dict) -> None:
    """Persist risk params dict to disk."""
    try:
        with open(_RISK_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        logger.exception("Failed to save risk params to %s", _RISK_FILE)
