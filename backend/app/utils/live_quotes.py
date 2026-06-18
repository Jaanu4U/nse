"""Shared helper for batch-fetching today's live OHLCV for a set of symbols.

Used by the daily picks scorecard and both strategy scorecards so the in-flight
(currently-trading) session can be graded against the running market instead of a
frozen database snapshot. Kept dependency-light and resilient: a network failure or a
missing symbol simply yields no quote for that name rather than raising.
"""
from __future__ import annotations

import logging
import time
from typing import Dict, List

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

# Tiny TTL cache so several panels polling at once (or one panel polling every 30s)
# don't hammer yfinance with one download per visitor per tick.
_CACHE: Dict[frozenset, Dict[str, Dict[str, float]]] = {}
_CACHE_AT: Dict[frozenset, float] = {}
_TTL_SECONDS = 20


def fetch_live_ohlcv(symbols: List[str], suffix: str = ".NS") -> Dict[str, Dict[str, float]]:
    """Return {SYMBOL: {open, high, low, close, volume}} for today's bar.

    ``suffix`` is appended to each symbol to form the yfinance ticker (``.NS`` for NSE).
    Results are cached for a few seconds keyed by the requested symbol set.
    """
    if not symbols:
        return {}
    key = frozenset(symbols)
    now = time.time()
    if key in _CACHE and (now - _CACHE_AT.get(key, 0)) < _TTL_SECONDS:
        return _CACHE[key]

    tickers = [f"{s}{suffix}" for s in symbols]
    try:
        df = yf.download(
            tickers, period="1d", interval="1d",
            group_by="ticker", progress=False, threads=True,
        )
    except Exception as e:  # pragma: no cover - network
        logger.warning("Live quote download failed: %s", e)
        return _CACHE.get(key, {})
    if df is None or df.empty:
        return _CACHE.get(key, {})

    out: Dict[str, Dict[str, float]] = {}
    for sym, tk in zip(symbols, tickers):
        try:
            sub = df if len(tickers) == 1 else (
                df[tk] if tk in df.columns.get_level_values(0) else None)
            if sub is None:
                continue
            sub = sub.dropna(how="all")
            if sub.empty:
                continue
            last = sub.iloc[-1]
            o, h, l, c = (float(last["Open"]), float(last["High"]),
                          float(last["Low"]), float(last["Close"]))
            if any(pd.isna(x) for x in (o, h, l, c)):
                continue
            v = int(last["Volume"]) if not pd.isna(last["Volume"]) else 0
            out[sym] = {"open": o, "high": h, "low": l, "close": c, "volume": v}
        except Exception:
            continue

    if out:
        _CACHE[key] = out
        _CACHE_AT[key] = now
    return out
