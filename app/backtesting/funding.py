"""
Perpetual-futures FUNDING cost modelling for the back-tester.

Why this exists
---------------
The long/short research variant (``regime_breakout_ls``) can only be run live on
a FUTURES account, where holding a position across each 8h funding timestamp
pays or receives the funding rate. On spot there is no funding — so the live
long-only system never needs this — but any honest evaluation of the short side
MUST include it, because over a multi-month short the accrued funding is
material and is NOT uniformly a cost: when funding is positive (the common case)
a SHORT *receives* it and a LONG pays it.

This module fetches the REAL historical funding rates from Binance USD-M
Futures (``GET /fapi/v1/fundingRate``, every 8h) and turns a held position into
a funding cash-flow. Network access mirrors ``data.load_klines_rest`` (injectable
getter → tests never hit the network).

Sign convention (cost subtracted from net PnL):
    LONG  pays funding when rate > 0  → cost = +rate * notional
    SHORT pays funding when rate < 0  → cost = -rate * notional
A positive ``funding_cost`` reduces net PnL; a negative one (a credit) adds to it.
"""

from __future__ import annotations

import logging

import pandas as pd

from app.backtesting.data import _build_http_getter

logger = logging.getLogger(__name__)

FAPI_BASE = "https://fapi.binance.com"
_FUNDING_INTERVAL_MS = 8 * 60 * 60 * 1000  # Binance funding cadence (8h)
_MAX_LIMIT = 1000


def load_funding_rates(symbol: str, start_time_ms: int, end_time_ms: int, *,
                       session=None, base_url: str | None = None,
                       pause_s: float = 0.2) -> pd.Series:
    """Fetch the historical funding-rate series for ``symbol`` in the window.

    Returns a float Series indexed by funding timestamp (UTC). Empty Series if
    the endpoint yields nothing (e.g. symbol has no perpetual).
    """
    host = base_url or FAPI_BASE
    url = f"{host}/fapi/v1/fundingRate"
    get_json = _build_http_getter(session)

    times: list[int] = []
    rates: list[float] = []
    cursor = start_time_ms
    while cursor < end_time_ms:
        rows = get_json(url, {"symbol": symbol.upper(), "startTime": cursor,
                              "endTime": end_time_ms, "limit": _MAX_LIMIT})
        if not rows:
            break
        for r in rows:
            times.append(int(r["fundingTime"]))
            rates.append(float(r["fundingRate"]))
        last = int(rows[-1]["fundingTime"])
        nxt = last + _FUNDING_INTERVAL_MS
        if nxt <= cursor:
            break
        cursor = nxt
        if len(rows) < _MAX_LIMIT:
            break
        if pause_s:
            import time as _t
            _t.sleep(pause_s)

    if not times:
        return pd.Series(dtype=float)
    s = pd.Series(rates, index=pd.to_datetime(times, unit="ms"), name="funding")
    return s[~s.index.duplicated(keep="last")].sort_index()


def funding_cost(side: str, notional: float, entry_time, exit_time,
                 funding_rates: pd.Series) -> float:
    """Funding paid (>0) or received (<0) over a position's holding period.

    Sums the funding rates whose timestamp falls in ``(entry_time, exit_time]``
    and applies the side sign on the position notional.
    """
    if funding_rates is None or len(funding_rates) == 0 or notional <= 0:
        return 0.0
    et = pd.Timestamp(entry_time)
    xt = pd.Timestamp(exit_time)
    if xt <= et:
        return 0.0
    window = funding_rates[(funding_rates.index > et) & (funding_rates.index <= xt)]
    if window.empty:
        return 0.0
    rate_sum = float(window.sum())
    direction = 1.0 if str(side).upper() == "BUY" else -1.0
    return direction * rate_sum * notional
