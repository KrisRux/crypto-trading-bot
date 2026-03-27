"""
FastAPI routes for the trading bot web application.
"""

import logging
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.trade import Trade, Order
from app.models.portfolio import PaperPortfolio, PaperPosition
from app.api.schemas import (
    ModeResponse, ModeSwitch, BalanceResponse, PositionResponse,
    OrderResponse, TradeResponse, StrategyInfo, StrategyUpdate,
    RiskParams, SignalResponse, PriceResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

# The trading engine and skills library are injected at startup (see main.py)
_engine = None
_skills_library = None


def set_engine(engine, skills_library=None):
    global _engine, _skills_library
    _engine = engine
    _skills_library = skills_library


def get_engine():
    if _engine is None:
        raise HTTPException(500, "Trading engine not initialized")
    return _engine


# ------------------------------------------------------------------ Mode
@router.get("/mode", response_model=ModeResponse)
def get_mode():
    return {"mode": get_engine().mode}


@router.post("/mode", response_model=ModeResponse)
def switch_mode(body: ModeSwitch):
    engine = get_engine()
    try:
        engine.switch_mode(body.mode)
    except ValueError as e:
        raise HTTPException(400, str(e))
    logger.info("Mode switched to %s via API", body.mode)
    return {"mode": engine.mode}


# ------------------------------------------------------------------ Dashboard
@router.get("/balance", response_model=BalanceResponse)
def get_balance(db: Session = Depends(get_db)):
    engine = get_engine()
    if engine.mode == "paper":
        portfolio = engine.paper_portfolio.get_or_create(db)
        return BalanceResponse(
            mode="paper",
            cash_balance=portfolio.cash_balance,
            total_equity=portfolio.total_equity,
            total_pnl=portfolio.total_pnl,
            total_trades=portfolio.total_trades,
            winning_trades=portfolio.winning_trades,
            losing_trades=portfolio.losing_trades,
        )
    else:
        # For live mode, we return data from closed trades
        trades = db.query(Trade).filter(Trade.mode == "live").all()
        total_pnl = sum(t.pnl or 0 for t in trades)
        winning = sum(1 for t in trades if (t.pnl or 0) > 0)
        losing = sum(1 for t in trades if (t.pnl or 0) < 0)
        return BalanceResponse(
            mode="live",
            cash_balance=0,  # Fetched async separately
            total_equity=0,
            total_pnl=total_pnl,
            total_trades=len(trades),
            winning_trades=winning,
            losing_trades=losing,
        )


@router.get("/positions", response_model=list[PositionResponse])
def get_positions(db: Session = Depends(get_db)):
    engine = get_engine()
    if engine.mode == "paper":
        portfolio = engine.paper_portfolio.get_or_create(db)
        positions = db.query(PaperPosition).filter(
            PaperPosition.portfolio_id == portfolio.id
        ).all()
        return [
            PositionResponse(
                id=p.id, symbol=p.symbol, side=p.side, quantity=p.quantity,
                entry_price=p.entry_price, current_price=p.current_price,
                unrealized_pnl=p.unrealized_pnl,
                stop_loss=p.stop_loss, take_profit=p.take_profit,
                opened_at=p.opened_at,
            ) for p in positions
        ]
    else:
        trades = db.query(Trade).filter(
            Trade.mode == "live", Trade.status == "OPEN"
        ).all()
        return [
            PositionResponse(
                id=t.id, symbol=t.symbol, side=t.side.value,
                quantity=t.quantity, entry_price=t.entry_price,
                current_price=engine.last_price,
                unrealized_pnl=(engine.last_price - t.entry_price) * t.quantity,
                stop_loss=t.stop_loss, take_profit=t.take_profit,
                opened_at=t.opened_at,
            ) for t in trades
        ]


@router.get("/orders", response_model=list[OrderResponse])
def get_orders(db: Session = Depends(get_db), limit: int = 50):
    engine = get_engine()
    orders = db.query(Order).filter(
        Order.mode == engine.mode
    ).order_by(Order.created_at.desc()).limit(limit).all()
    return [
        OrderResponse(
            id=o.id, symbol=o.symbol, side=o.side.value,
            order_type=o.order_type.value, quantity=o.quantity,
            price=o.price, filled_price=o.filled_price,
            status=o.status.value, mode=o.mode,
            error_message=o.error_message, created_at=o.created_at,
        ) for o in orders
    ]


@router.get("/trades", response_model=list[TradeResponse])
def get_trades(db: Session = Depends(get_db), limit: int = 50):
    engine = get_engine()
    trades = db.query(Trade).filter(
        Trade.mode == engine.mode
    ).order_by(Trade.opened_at.desc()).limit(limit).all()
    return [
        TradeResponse(
            id=t.id, symbol=t.symbol, side=t.side.value,
            entry_price=t.entry_price, exit_price=t.exit_price,
            quantity=t.quantity, stop_loss=t.stop_loss,
            take_profit=t.take_profit, pnl=t.pnl, pnl_pct=t.pnl_pct,
            status=t.status.value, mode=t.mode, strategy=t.strategy,
            opened_at=t.opened_at, closed_at=t.closed_at,
        ) for t in trades
    ]


# ------------------------------------------------------------------ Price
@router.get("/price/{symbol}", response_model=PriceResponse)
async def get_price(symbol: str):
    engine = get_engine()
    try:
        data = await engine.market_client.get_ticker_price(symbol.upper())
        return PriceResponse(symbol=data["symbol"], price=float(data["price"]))
    except Exception as e:
        raise HTTPException(502, f"Could not fetch price: {e}")


# ------------------------------------------------------------------ Strategies
@router.get("/strategies", response_model=list[StrategyInfo])
def get_strategies():
    engine = get_engine()
    return [
        StrategyInfo(name=s.name, enabled=s.enabled, params=s.get_params())
        for s in engine.strategies
    ]


@router.put("/strategies")
def update_strategy(body: StrategyUpdate):
    engine = get_engine()
    strat = next((s for s in engine.strategies if s.name == body.name), None)
    if not strat:
        raise HTTPException(404, f"Strategy '{body.name}' not found")
    if body.enabled is not None:
        strat.enabled = body.enabled
    if body.params:
        strat.set_params(body.params)
    logger.info("Strategy '%s' updated: enabled=%s params=%s",
                body.name, strat.enabled, strat.get_params())
    return {"ok": True}


# ------------------------------------------------------------------ Risk
@router.get("/risk", response_model=RiskParams)
def get_risk_params():
    return get_engine().risk_manager.get_params()


@router.put("/risk", response_model=RiskParams)
def update_risk_params(body: RiskParams):
    engine = get_engine()
    engine.risk_manager.set_params(body.model_dump())
    return engine.risk_manager.get_params()


# ------------------------------------------------------------------ Signals log
@router.get("/signals", response_model=list[SignalResponse])
def get_signals():
    engine = get_engine()
    return engine.signals_log[-50:]


# ------------------------------------------------------------------ Paper trading extras
@router.post("/paper/reset")
def reset_paper_portfolio(db: Session = Depends(get_db)):
    engine = get_engine()
    if engine.mode != "paper":
        raise HTTPException(400, "Can only reset in paper mode")
    engine.paper_portfolio.reset(db)
    return {"ok": True}


@router.get("/paper/export")
def export_paper_trades(db: Session = Depends(get_db)):
    engine = get_engine()
    csv_data = engine.paper_portfolio.export_trades_csv(db)
    return PlainTextResponse(csv_data, media_type="text/csv", headers={
        "Content-Disposition": "attachment; filename=paper_trades.csv"
    })


# ------------------------------------------------------------------ Engine control
@router.get("/engine/status")
def engine_status():
    engine = get_engine()
    return {
        "running": engine.running,
        "mode": engine.mode,
        "symbols": engine.symbols,
        "last_prices": engine.last_prices,
        "strategies_count": len(engine.strategies),
    }


@router.post("/symbols/add")
def add_symbol(body: dict):
    engine = get_engine()
    symbol = body.get("symbol", "").upper()
    if not symbol:
        raise HTTPException(400, "Symbol is required")
    engine.add_symbol(symbol)
    return {"symbols": engine.symbols}


@router.post("/symbols/remove")
def remove_symbol(body: dict):
    engine = get_engine()
    symbol = body.get("symbol", "").upper()
    if not symbol:
        raise HTTPException(400, "Symbol is required")
    if len(engine.symbols) <= 1:
        raise HTTPException(400, "Cannot remove the last symbol")
    engine.remove_symbol(symbol)
    return {"symbols": engine.symbols}


# ------------------------------------------------------------------ Embient Skills
@router.get("/skills/summary")
def skills_summary():
    if not _skills_library:
        return {"total_skills": 0, "categories": {}}
    return _skills_library.summary()


@router.get("/skills")
def list_skills(category: str | None = None):
    if not _skills_library:
        return []
    if category:
        return [s.to_dict() for s in _skills_library.get_by_category(category)]
    return _skills_library.list_all()


@router.get("/skills/{name}")
def get_skill(name: str):
    if not _skills_library:
        raise HTTPException(404, "Skills library not loaded")
    skill = _skills_library.get(name)
    if not skill:
        raise HTTPException(404, f"Skill '{name}' not found")
    return skill.to_dict()
