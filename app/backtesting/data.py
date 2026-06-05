"""
Historical kline data loading for the back-tester.

Two entry points, deliberately separated so the test-suite never touches the
network:

* :func:`load_klines_rest` — fetches real historical candles from the Binance
  Spot REST endpoint ``GET /api/v3/klines`` with pagination (max 1000 candles
  per request, walked forward via ``startTime``/``endTime``). Used by the CLI.
* :func:`load_dataframe` / :func:`load_csv` — normalise an already-materialised
  DataFrame or CSV file into the canonical OHLCV shape. **No network.** This is
  what the tests use with synthetic data.

Canonical shape returned by every loader here (matches the live engine's
``fetch_klines`` so strategies behave identically):

    columns : ["open", "high", "low", "close", "volume"]  (all float)
    index   : DatetimeIndex named "datetime" (UTC, candle OPEN time), sorted

The Binance kline REST schema (per the official docs) is::

    [ open_time, open, high, low, close, volume, close_time,
      quote_volume, trades, taker_buy_base, taker_buy_quote, ignore ]

Reference: https://binance-docs.github.io/apidocs/spot/en/#kline-candlestick-data
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LIVE_BASE = "https://api.binance.com"
TESTNET_BASE = "https://testnet.binance.vision"

OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]

# Raw 12-column schema returned by /api/v3/klines.
_RAW_KLINE_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "trades", "taker_buy_base",
    "taker_buy_quote", "ignore",
]

# Interval string -> milliseconds. Used to size pagination windows and to
# translate ``--days`` into a candle count. Mirrors Binance's accepted values.
_INTERVAL_MS = {
    "1m": 60_000,
    "3m": 3 * 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "30m": 30 * 60_000,
    "1h": 60 * 60_000,
    "2h": 2 * 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "6h": 6 * 60 * 60_000,
    "8h": 8 * 60 * 60_000,
    "12h": 12 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
    "3d": 3 * 24 * 60 * 60_000,
    "1w": 7 * 24 * 60 * 60_000,
}

_MAX_LIMIT = 1000  # Binance hard cap per klines request.


def interval_to_ms(interval: str) -> int:
    """Return the millisecond duration of one candle of ``interval``."""
    try:
        return _INTERVAL_MS[interval]
    except KeyError:
        raise ValueError(
            f"Unsupported interval '{interval}'. Supported: {sorted(_INTERVAL_MS)}"
        )


# ---------------------------------------------------------------------------
# Normalisation (no network) — the shared back-end of every loader
# ---------------------------------------------------------------------------

def normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """
    Coerce an arbitrary OHLCV DataFrame into the canonical shape.

    Accepts either:
      * a frame that already has open/high/low/close[/volume] columns
        (case-insensitive), with any index, or
      * a raw 12-column Binance kline frame (``open_time`` first), or
      * a frame carrying an explicit ``open_time``/``datetime``/``timestamp``
        column to use as the index.

    Returns a NEW frame (the caller's input is never mutated) with float OHLCV
    columns and a sorted ``DatetimeIndex`` named ``datetime``. ``volume``
    defaults to 0.0 when absent.
    """
    if df is None or len(df) == 0:
        raise ValueError("normalize_ohlcv received an empty DataFrame")

    out = df.copy()

    # Raw Binance 12-col payload (e.g. straight from get_klines) → name columns.
    if "open_time" not in out.columns and list(out.columns[:6]) == list(range(6)):
        # positional integer columns (e.g. pd.DataFrame(raw_list))
        ncols = out.shape[1]
        names = _RAW_KLINE_COLUMNS[:ncols]
        out.columns = names + list(out.columns[len(names):])

    # Case-insensitive column lookup.
    lower = {str(c).lower(): c for c in out.columns}

    def _col(name: str):
        return lower.get(name)

    # --- choose / build the datetime index ---
    if not isinstance(out.index, pd.DatetimeIndex):
        ts_col = _col("open_time") or _col("datetime") or _col("timestamp") or _col("time")
        if ts_col is not None:
            series = out[ts_col]
            if pd.api.types.is_numeric_dtype(series):
                # Heuristic: ms epochs are ~1e12, s epochs ~1e9.
                unit = "ms" if float(series.iloc[0]) > 1e11 else "s"
                idx = pd.to_datetime(series, unit=unit)
            else:
                idx = pd.to_datetime(series)
            out = out.set_index(idx)
        else:
            # No timestamp anywhere — synthesise a 1-minute RangeIndex so the
            # frame is still usable (tests may pass a bare OHLCV frame).
            out = out.set_index(
                pd.date_range("2020-01-01", periods=len(out), freq="min")
            )

    out.index = pd.DatetimeIndex(out.index)
    out.index.name = "datetime"

    # --- pull OHLCV columns (case-insensitive), coerce to float ---
    data = {}
    for field in ("open", "high", "low", "close"):
        src = _col(field)
        if src is None:
            raise ValueError(f"normalize_ohlcv: missing required column '{field}'")
        data[field] = pd.to_numeric(out[src], errors="coerce").astype(float)

    vol_src = _col("volume")
    data["volume"] = (
        pd.to_numeric(out[vol_src], errors="coerce").astype(float)
        if vol_src is not None
        else pd.Series(0.0, index=out.index)
    )

    result = pd.DataFrame(data, index=out.index)[OHLCV_COLUMNS]
    result = result[~result.index.duplicated(keep="last")].sort_index()
    # Drop rows where any OHLC is NaN (corrupt candle).
    result = result.dropna(subset=["open", "high", "low", "close"])
    return result


def load_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise a ready-made DataFrame. Thin alias over :func:`normalize_ohlcv`."""
    return normalize_ohlcv(df)


def load_csv(path: str | Path, **read_csv_kwargs) -> pd.DataFrame:
    """
    Load OHLCV candles from a CSV file and normalise them. No network.

    The CSV must contain open/high/low/close columns (case-insensitive) plus
    optionally volume and a timestamp column (``open_time``/``datetime``/
    ``timestamp``). Extra columns are ignored.
    """
    raw = pd.read_csv(path, **read_csv_kwargs)
    return normalize_ohlcv(raw)


# ---------------------------------------------------------------------------
# Binance REST loader (network) — CLI / real use only
# ---------------------------------------------------------------------------

def _raw_klines_to_df(rows: list[list]) -> pd.DataFrame:
    """Turn a list of raw 12-field kline rows into a canonical OHLCV frame."""
    df = pd.DataFrame(rows, columns=_RAW_KLINE_COLUMNS)
    for col in OHLCV_COLUMNS:
        df[col] = df[col].astype(float)
    df["datetime"] = pd.to_datetime(df["open_time"], unit="ms")
    df = df.set_index("datetime")[OHLCV_COLUMNS]
    return df


def load_klines_rest(
    symbol: str,
    interval: str = "15m",
    days: float | None = None,
    *,
    start_time_ms: int | None = None,
    end_time_ms: int | None = None,
    limit: int | None = None,
    testnet: bool = False,
    base_url: str | None = None,
    pause_s: float = 0.25,
    session=None,
) -> pd.DataFrame:
    """
    Download historical candles from Binance Spot REST with pagination.

    Walks forward from ``start_time_ms`` to ``end_time_ms`` in pages of up to
    1000 candles (the API hard cap), concatenating the results. Uses
    ``requests`` if available, else ``urllib`` from the standard library — so no
    new pip dependency is introduced.

    Args:
        symbol:        e.g. "BTCUSDT".
        interval:      Binance interval string (e.g. "15m", "1h", "1d").
        days:          look-back window in days, ending now. Mutually convenient
                       with ``start_time_ms``/``end_time_ms`` (explicit times
                       win if provided).
        start_time_ms: explicit window start (epoch ms). Derived from ``days``
                       if omitted.
        end_time_ms:   explicit window end (epoch ms). Defaults to "now".
        limit:         hard cap on the total number of candles to return
                       (newest-biased: the tail is kept). ``None`` = no cap.
        testnet:       use the testnet host instead of the live host.
        base_url:      override the host entirely (takes priority over testnet).
        pause_s:       sleep between pages to stay under the REST rate limit.
        session:       optional pre-built ``requests.Session`` (injectable for
                       tests / reuse).

    Returns:
        Canonical OHLCV DataFrame (see module docstring), sorted ascending.

    Raises:
        ValueError on bad arguments; RuntimeError / network errors propagate
        from the HTTP layer.
    """
    interval_ms = interval_to_ms(interval)

    now_ms = int(time.time() * 1000)
    if end_time_ms is None:
        end_time_ms = now_ms
    if start_time_ms is None:
        if days is None:
            raise ValueError("Provide either `days` or `start_time_ms`.")
        start_time_ms = end_time_ms - int(days * 24 * 60 * 60 * 1000)
    if start_time_ms >= end_time_ms:
        raise ValueError("start_time_ms must be < end_time_ms")

    host = base_url or (TESTNET_BASE if testnet else LIVE_BASE)
    url = f"{host}/api/v3/klines"
    _get_json = _build_http_getter(session)

    frames: list[pd.DataFrame] = []
    cursor = start_time_ms
    total = 0
    page = 0
    while cursor < end_time_ms:
        params = {
            "symbol": symbol.upper(),
            "interval": interval,
            "startTime": cursor,
            "endTime": end_time_ms,
            "limit": _MAX_LIMIT,
        }
        rows = _get_json(url, params)
        if not rows:
            break
        frames.append(_raw_klines_to_df(rows))
        total += len(rows)
        page += 1

        last_open = int(rows[-1][0])
        # Advance the cursor one candle past the last open time we received.
        next_cursor = last_open + interval_ms
        if next_cursor <= cursor:  # safety: no forward progress
            break
        cursor = next_cursor

        logger.info(
            "klines %s %s: page %d (+%d rows, %d total)",
            symbol, interval, page, len(rows), total,
        )
        if len(rows) < _MAX_LIMIT:
            break  # last (partial) page reached
        if pause_s:
            time.sleep(pause_s)

    if not frames:
        raise RuntimeError(f"No klines returned for {symbol} {interval}")

    df = pd.concat(frames)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    if limit is not None and len(df) > limit:
        df = df.iloc[-limit:]
    logger.info("Loaded %d candles for %s %s", len(df), symbol, interval)
    return df


def _build_http_getter(session=None):
    """
    Return a ``get_json(url, params) -> list`` callable.

    Resolution order:
      1. an explicitly injected ``session`` (duck-typed: must expose
         ``.get(url, params=..., timeout=...)`` returning an object with
         ``.raise_for_status()`` and ``.json()``) — used as-is. This is the
         hook the tests use to avoid the network entirely.
      2. ``httpx`` (already a project dependency).
      3. ``requests`` if present.
      4. ``urllib`` from the standard library.

    No new pip dependency is ever required.
    """
    # 1) Injected session wins — never touch the network in tests.
    if session is not None:
        def _get(url: str, params: dict):
            resp = session.get(url, params=params, timeout=15)
            resp.raise_for_status()
            return resp.json()
        return _get

    # 2) httpx (shipped with the bot).
    try:  # pragma: no cover - network path
        import httpx  # type: ignore

        client = httpx.Client(timeout=15.0)

        def _get(url: str, params: dict):
            resp = client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
        return _get
    except Exception:  # pragma: no cover
        pass

    # 3) requests, if it happens to be installed.
    try:  # pragma: no cover - network path
        import requests  # type: ignore

        sess = requests.Session()

        def _get(url: str, params: dict):
            resp = sess.get(url, params=params, timeout=15)
            resp.raise_for_status()
            return resp.json()
        return _get
    except Exception:  # pragma: no cover
        pass

    # 4) stdlib fallback.
    import json  # pragma: no cover - network path
    import urllib.parse
    import urllib.request

    def _get(url: str, params: dict):  # pragma: no cover
        qs = urllib.parse.urlencode(params)
        with urllib.request.urlopen(f"{url}?{qs}", timeout=15) as r:
            return json.loads(r.read().decode())

    return _get
