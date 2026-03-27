"""
FastAPI routes for the trading bot web application.
All routes (except /api/login) require JWT authentication.
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
from app.api.auth import (
    LoginRequest, TokenResponse, UserCreate, UserUpdate, UserInfo,
    create_token, require_auth, require_admin, require_write,
)
from app.models.user import User, verify_password, hash_password
from app.config import settings
from datetime import datetime, timezone

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

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


# ------------------------------------------------------------------ Auth
@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(
        User.username == body.username, User.is_active == True
    ).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Invalid username or password")
    token, expires_in = create_token(user.username, user.role)
    user.last_login = datetime.now(timezone.utc)
    db.commit()
    logger.info("User '%s' (%s) logged in", user.username, user.role)
    return TokenResponse(
        access_token=token, expires_in=expires_in,
        session_timeout_minutes=settings.session_timeout_minutes,
        role=user.role, display_name=user.display_name or user.username,
    )


@router.get("/me")
def get_me(user: dict = Depends(require_auth), db: Session = Depends(get_db)):
    db_user = db.query(User).filter(User.username == user["username"]).first()
    if not db_user:
        raise HTTPException(404, "User not found")
    return {
        "username": db_user.username,
        "display_name": db_user.display_name or db_user.username,
        "role": db_user.role,
    }


# ------------------------------------------------------------------ User Management (admin only)
@router.get("/users", response_model=list[UserInfo])
def list_users(db: Session = Depends(get_db), _admin: dict = Depends(require_admin)):
    users = db.query(User).order_by(User.created_at).all()
    return [
        UserInfo(
            id=u.id, username=u.username, display_name=u.display_name,
            role=u.role, is_active=u.is_active,
            created_at=u.created_at, last_login=u.last_login,
        ) for u in users
    ]


@router.post("/users", response_model=UserInfo)
def create_user(body: UserCreate, db: Session = Depends(get_db),
                _admin: dict = Depends(require_admin)):
    if body.role not in ("admin", "user", "guest"):
        raise HTTPException(400, "Role must be admin, user, or guest")
    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(409, f"Username '{body.username}' already exists")
    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        display_name=body.display_name or body.username,
        role=body.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info("Admin created user '%s' with role '%s'", user.username, user.role)
    return UserInfo(
        id=user.id, username=user.username, display_name=user.display_name,
        role=user.role, is_active=user.is_active,
        created_at=user.created_at, last_login=user.last_login,
    )


@router.put("/users/{user_id}", response_model=UserInfo)
def update_user(user_id: int, body: UserUpdate, db: Session = Depends(get_db),
                _admin: dict = Depends(require_admin)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "User not found")
    if body.display_name is not None:
        user.display_name = body.display_name
    if body.role is not None:
        if body.role not in ("admin", "user", "guest"):
            raise HTTPException(400, "Role must be admin, user, or guest")
        user.role = body.role
    if body.password is not None:
        user.password_hash = hash_password(body.password)
    if body.is_active is not None:
        user.is_active = body.is_active
    db.commit()
    db.refresh(user)
    logger.info("Admin updated user '%s'", user.username)
    return UserInfo(
        id=user.id, username=user.username, display_name=user.display_name,
        role=user.role, is_active=user.is_active,
        created_at=user.created_at, last_login=user.last_login,
    )


@router.delete("/users/{user_id}")
def delete_user(user_id: int, db: Session = Depends(get_db),
                _admin: dict = Depends(require_admin)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "User not found")
    if user.role == "admin":
        admin_count = db.query(User).filter(User.role == "admin", User.is_active == True).count()
        if admin_count <= 1:
            raise HTTPException(400, "Cannot delete the last admin")
    db.delete(user)
    db.commit()
    logger.info("Admin deleted user '%s'", user.username)
    return {"ok": True}


# ------------------------------------------------------------------ Mode
@router.get("/mode", response_model=ModeResponse)
def get_mode(_user: dict = Depends(require_auth)):
    return {"mode": get_engine().mode}


@router.post("/mode", response_model=ModeResponse)
def switch_mode(body: ModeSwitch, _user: dict = Depends(require_admin)):
    engine = get_engine()
    try:
        engine.switch_mode(body.mode)
    except ValueError as e:
        raise HTTPException(400, str(e))
    logger.info("Mode switched to %s via API", body.mode)
    return {"mode": engine.mode}


def _get_user_id(user_info: dict, db: Session) -> int:
    """Resolve the user ID from the JWT payload."""
    u = db.query(User).filter(User.username == user_info["username"]).first()
    if not u:
        raise HTTPException(404, "User not found")
    return u.id


def _get_user_obj(user_info: dict, db: Session) -> User:
    u = db.query(User).filter(User.username == user_info["username"]).first()
    if not u:
        raise HTTPException(404, "User not found")
    return u


# ------------------------------------------------------------------ User Settings (API keys)
@router.get("/settings/keys")
def get_api_keys(db: Session = Depends(get_db), user_info: dict = Depends(require_auth)):
    user = _get_user_obj(user_info, db)
    return {
        "trading_mode": user.trading_mode,
        "paper_initial_capital": user.paper_initial_capital,
        "has_live_keys": user.has_api_keys(live=True),
        "has_testnet_keys": user.has_api_keys(live=False),
        "binance_api_key": user.get_api_key(live=True)[:8] + "..." if user.has_api_keys(live=True) else "",
        "binance_testnet_api_key": user.get_api_key(live=False)[:8] + "..." if user.has_api_keys(live=False) else "",
    }


@router.put("/settings/keys")
def update_api_keys(body: dict, db: Session = Depends(get_db),
                    user_info: dict = Depends(require_auth)):
    user = _get_user_obj(user_info, db)
    if "trading_mode" in body:
        if body["trading_mode"] not in ("paper", "live"):
            raise HTTPException(400, "Mode must be 'paper' or 'live'")
        user.trading_mode = body["trading_mode"]
    if "paper_initial_capital" in body:
        user.paper_initial_capital = float(body["paper_initial_capital"])
    # Only update keys if provided (non-empty)
    api_key = body.get("binance_api_key", "")
    api_secret = body.get("binance_api_secret", "")
    testnet_key = body.get("binance_testnet_api_key", "")
    testnet_secret = body.get("binance_testnet_api_secret", "")
    if api_key or api_secret or testnet_key or testnet_secret:
        user.set_api_keys(
            api_key=api_key or user.get_api_key(live=True),
            api_secret=api_secret or user.get_api_secret(live=True),
            testnet_key=testnet_key or user.get_api_key(live=False),
            testnet_secret=testnet_secret or user.get_api_secret(live=False),
        )
    db.commit()
    logger.info("User '%s' updated their settings", user.username)
    return {"ok": True}


# ------------------------------------------------------------------ Dashboard (per-user)
@router.get("/balance", response_model=BalanceResponse)
def get_balance(db: Session = Depends(get_db), user_info: dict = Depends(require_auth)):
    engine = get_engine()
    user = _get_user_obj(user_info, db)
    user_mode = user.trading_mode or "paper"

    if user_mode == "paper":
        portfolio = engine.paper_portfolio.get_or_create(
            db, user.id, user.paper_initial_capital
        )
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
        trades = db.query(Trade).filter(
            Trade.user_id == user.id, Trade.mode == "live"
        ).all()
        total_pnl = sum(t.pnl or 0 for t in trades)
        winning = sum(1 for t in trades if (t.pnl or 0) > 0)
        losing = sum(1 for t in trades if (t.pnl or 0) < 0)
        return BalanceResponse(
            mode="live", cash_balance=0, total_equity=0,
            total_pnl=total_pnl, total_trades=len(trades),
            winning_trades=winning, losing_trades=losing,
        )


@router.get("/positions", response_model=list[PositionResponse])
def get_positions(db: Session = Depends(get_db), user_info: dict = Depends(require_auth)):
    engine = get_engine()
    user = _get_user_obj(user_info, db)

    positions = db.query(PaperPosition).filter(
        PaperPosition.user_id == user.id
    ).all()
    open_trades = db.query(Trade).filter(
        Trade.user_id == user.id, Trade.status == TradeStatus.OPEN
    ).all()

    result = []
    for p in positions:
        result.append(PositionResponse(
            id=p.id, symbol=p.symbol, side=p.side, quantity=p.quantity,
            entry_price=p.entry_price, current_price=p.current_price,
            unrealized_pnl=p.unrealized_pnl,
            stop_loss=p.stop_loss, take_profit=p.take_profit,
            opened_at=p.opened_at,
        ))
    for t in open_trades:
        if t.mode == "live":
            cp = engine.last_prices.get(t.symbol, 0)
            result.append(PositionResponse(
                id=t.id, symbol=t.symbol, side=t.side.value,
                quantity=t.quantity, entry_price=t.entry_price,
                current_price=cp,
                unrealized_pnl=(cp - t.entry_price) * t.quantity if cp else 0,
                stop_loss=t.stop_loss, take_profit=t.take_profit,
                opened_at=t.opened_at,
            ))
    return result


@router.get("/orders", response_model=list[OrderResponse])
def get_orders(db: Session = Depends(get_db), limit: int = 50,
               user_info: dict = Depends(require_auth)):
    user_id = _get_user_id(user_info, db)
    orders = db.query(Order).filter(
        Order.user_id == user_id
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
def get_trades(db: Session = Depends(get_db), limit: int = 50,
               user_info: dict = Depends(require_auth)):
    user_id = _get_user_id(user_info, db)
    trades = db.query(Trade).filter(
        Trade.user_id == user_id
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
async def get_price(symbol: str, _user: dict = Depends(require_auth)):
    engine = get_engine()
    try:
        data = await engine.market_client.get_ticker_price(symbol.upper())
        return PriceResponse(symbol=data["symbol"], price=float(data["price"]))
    except Exception as e:
        raise HTTPException(502, f"Could not fetch price: {e}")


# ------------------------------------------------------------------ Strategies
@router.get("/strategies", response_model=list[StrategyInfo])
def get_strategies(_user: dict = Depends(require_auth)):
    engine = get_engine()
    return [
        StrategyInfo(name=s.name, enabled=s.enabled, params=s.get_params())
        for s in engine.strategies
    ]


@router.put("/strategies")
def update_strategy(body: StrategyUpdate, _user: dict = Depends(require_write)):
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
def get_risk_params(_user: dict = Depends(require_auth)):
    return get_engine().risk_manager.get_params()


@router.put("/risk", response_model=RiskParams)
def update_risk_params(body: RiskParams, _user: dict = Depends(require_write)):
    engine = get_engine()
    engine.risk_manager.set_params(body.model_dump())
    return engine.risk_manager.get_params()


# ------------------------------------------------------------------ Signals log
@router.get("/signals", response_model=list[SignalResponse])
def get_signals(_user: dict = Depends(require_auth)):
    engine = get_engine()
    return engine.signals_log[-50:]


# ------------------------------------------------------------------ Paper trading extras
@router.post("/paper/reset")
def reset_paper_portfolio(db: Session = Depends(get_db),
                          user_info: dict = Depends(require_auth)):
    engine = get_engine()
    user_id = _get_user_id(user_info, db)
    engine.paper_portfolio.reset(db, user_id)
    return {"ok": True}


@router.get("/paper/export")
def export_paper_trades(db: Session = Depends(get_db),
                        user_info: dict = Depends(require_auth)):
    engine = get_engine()
    user_id = _get_user_id(user_info, db)
    csv_data = engine.paper_portfolio.export_trades_csv(db, user_id)
    return PlainTextResponse(csv_data, media_type="text/csv", headers={
        "Content-Disposition": "attachment; filename=paper_trades.csv"
    })


# ------------------------------------------------------------------ Engine control
@router.get("/engine/status")
def engine_status(_user: dict = Depends(require_auth)):
    engine = get_engine()
    return {
        "running": engine.running,
        "mode": engine.mode,
        "symbols": engine.symbols,
        "last_prices": engine.last_prices,
        "strategies_count": len(engine.strategies),
    }


@router.post("/symbols/add")
def add_symbol(body: dict, _user: dict = Depends(require_admin)):
    engine = get_engine()
    symbol = body.get("symbol", "").upper()
    if not symbol:
        raise HTTPException(400, "Symbol is required")
    engine.add_symbol(symbol)
    return {"symbols": engine.symbols}


@router.post("/symbols/remove")
def remove_symbol(body: dict, _user: dict = Depends(require_admin)):
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
def skills_summary(_user: dict = Depends(require_auth)):
    if not _skills_library:
        return {"total_skills": 0, "categories": {}}
    return _skills_library.summary()


@router.get("/skills")
def list_skills(category: str | None = None, _user: dict = Depends(require_auth)):
    if not _skills_library:
        return []
    if category:
        return [s.to_dict() for s in _skills_library.get_by_category(category)]
    return _skills_library.list_all()


@router.get("/skills/{name}")
def get_skill(name: str, _user: dict = Depends(require_auth)):
    if not _skills_library:
        raise HTTPException(404, "Skills library not loaded")
    skill = _skills_library.get(name)
    if not skill:
        raise HTTPException(404, f"Skill '{name}' not found")
    return skill.to_dict()
