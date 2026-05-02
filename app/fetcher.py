"""yfinance wrapper. Returns a structured snapshot dict per ticker.

Each indicator is computed defensively — if any one fails, others still go through.
A short in-process cache keeps Yahoo from getting hammered when several watchlist
entries share a ticker, or when the dashboard refreshes during a tick.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import yfinance as yf

log = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 60  # snapshots refresh at most once a minute
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


@dataclass
class _Indicators:
    price: float | None = None
    prev_close: float | None = None
    day_change_pct: float | None = None
    ma20: float | None = None
    ma50: float | None = None
    ma200: float | None = None
    pct_from_ma20: float | None = None
    pct_from_ma50: float | None = None
    pct_from_ma200: float | None = None
    high_52w: float | None = None
    low_52w: float | None = None
    pct_from_52w_high: float | None = None
    pct_from_52w_low: float | None = None
    volume: int | None = None
    avg_volume_30d: float | None = None
    volume_ratio: float | None = None
    next_earnings_date: str | None = None
    last_earnings_date: str | None = None
    last_earnings_eps_actual: float | None = None
    last_earnings_eps_estimate: float | None = None
    last_earnings_surprise_pct: float | None = None
    rsi14: float | None = None
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        # JSON-safe: replace NaN with None
        for k, v in list(d.items()):
            if isinstance(v, float) and math.isnan(v):
                d[k] = None
        return d


def _safe(name: str, fn, errors: list[str]):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 — we want to keep going
        log.warning("indicator %s failed: %s", name, e)
        errors.append(f"{name}: {e!s}")
        return None


def _rsi(closes, period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0) for d in deltas]
    losses = [max(-d, 0) for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def _pct(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return round((a - b) / b * 100, 2)


def fetch_snapshot(ticker: str, *, use_cache: bool = True) -> dict[str, Any]:
    """Pull a structured snapshot of a ticker. Always returns a dict, never raises."""
    ticker = ticker.upper().strip()
    now = time.time()
    cached = _cache.get(ticker)
    if use_cache and cached and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]

    ind = _Indicators()

    try:
        t = yf.Ticker(ticker)
    except Exception as e:  # noqa: BLE001
        ind.errors.append(f"ticker init: {e!s}")
        return _finalize(ticker, ind)

    # 1y history is enough for MA200 and 52-week extremes.
    hist = _safe("history", lambda: t.history(period="1y", auto_adjust=False), ind.errors)
    if hist is not None and not hist.empty:
        closes = hist["Close"].dropna().tolist()
        volumes = hist["Volume"].dropna().tolist()
        if closes:
            ind.price = round(float(closes[-1]), 4)
            if len(closes) >= 2:
                ind.prev_close = round(float(closes[-2]), 4)
                ind.day_change_pct = _pct(ind.price, ind.prev_close)
            if len(closes) >= 20:
                ind.ma20 = round(sum(closes[-20:]) / 20, 4)
                ind.pct_from_ma20 = _pct(ind.price, ind.ma20)
            if len(closes) >= 50:
                ind.ma50 = round(sum(closes[-50:]) / 50, 4)
                ind.pct_from_ma50 = _pct(ind.price, ind.ma50)
            if len(closes) >= 200:
                ind.ma200 = round(sum(closes[-200:]) / 200, 4)
                ind.pct_from_ma200 = _pct(ind.price, ind.ma200)
            ind.high_52w = round(max(closes), 4)
            ind.low_52w = round(min(closes), 4)
            ind.pct_from_52w_high = _pct(ind.price, ind.high_52w)
            ind.pct_from_52w_low = _pct(ind.price, ind.low_52w)
            ind.rsi14 = _rsi(closes)
        if volumes:
            ind.volume = int(volumes[-1])
            if len(volumes) >= 30:
                avg = sum(volumes[-30:]) / 30
                ind.avg_volume_30d = round(avg, 0)
                if avg:
                    ind.volume_ratio = round(ind.volume / avg, 2)

    # Earnings: try .calendar first (next), then .earnings_dates (past + future EPS).
    cal = _safe("calendar", lambda: t.calendar, ind.errors)
    if isinstance(cal, dict) and "Earnings Date" in cal:
        ed = cal["Earnings Date"]
        if isinstance(ed, list) and ed:
            ind.next_earnings_date = str(ed[0])
        elif ed:
            ind.next_earnings_date = str(ed)

    edates = _safe("earnings_dates", lambda: t.earnings_dates, ind.errors)
    if edates is not None and not edates.empty:
        # Index is timezone-aware datetime. Find most recent past row with a non-null actual.
        df = edates.sort_index()
        now_ts = datetime.now(tz=df.index.tz) if df.index.tz else datetime.now()
        past = df[df.index <= now_ts]
        future = df[df.index > now_ts]
        if not past.empty:
            row = past.iloc[-1]
            ind.last_earnings_date = past.index[-1].isoformat()
            for col, attr in [
                ("EPS Estimate", "last_earnings_eps_estimate"),
                ("Reported EPS", "last_earnings_eps_actual"),
                ("Surprise(%)", "last_earnings_surprise_pct"),
            ]:
                if col in row.index:
                    val = row[col]
                    try:
                        if val is not None and not (isinstance(val, float) and math.isnan(val)):
                            setattr(ind, attr, round(float(val), 4))
                    except (TypeError, ValueError):
                        pass
        if ind.next_earnings_date is None and not future.empty:
            ind.next_earnings_date = future.index[0].isoformat()

    return _finalize(ticker, ind)


def _finalize(ticker: str, ind: _Indicators) -> dict[str, Any]:
    snap: dict[str, Any] = {
        "ticker": ticker,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        **ind.to_dict(),
    }
    _cache[ticker] = (time.time(), snap)
    return snap


def clear_cache() -> None:
    _cache.clear()
