from __future__ import annotations

import math
from typing import Any


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        parsed = int(float(value))
    except (TypeError, ValueError):
        return default
    return parsed


def is_finite_positive(value: Any) -> bool:
    return safe_float(value) > 0


def is_valid_ohlcv(o: Any, h: Any, l: Any, c: Any, v: Any) -> bool:
    open_ = safe_float(o)
    high = safe_float(h)
    low = safe_float(l)
    close = safe_float(c)
    volume = safe_float(v)
    if min(open_, high, low, close) <= 0 or volume < 0:
        return False
    return high >= max(open_, close) and low <= min(open_, close) and high >= low


def _topic_symbol(message: dict[str, Any]) -> str:
    topic = str(message.get("topic") or "")
    parts = topic.split(".")
    return parts[1] if len(parts) > 1 else ""


def normalize_ticker(data: Any, fallback_symbol: str = "") -> dict[str, Any]:
    if isinstance(data, str):
        return {"symbol": fallback_symbol} if fallback_symbol else {}
    raw = data or {}
    if isinstance(raw, dict) and "data" in raw:
        raw = raw.get("data") or {}
    if isinstance(raw, dict):
        ticker = raw.get("ticker") or raw
    else:
        ticker = {}
    symbol = str(ticker.get("symbol") or fallback_symbol or "")
    mark = safe_float(ticker.get("markPrice") or ticker.get("mark_price"))
    oracle = safe_float(ticker.get("oraclePrice") or ticker.get("indexPrice") or ticker.get("index_price") or ticker.get("oracle_price"))
    funding = safe_float(ticker.get("fundingRate") or ticker.get("funding_rate"))
    open_interest = safe_float(ticker.get("openInterest") or ticker.get("open_interest"))
    last = safe_float(ticker.get("lastPrice") or ticker.get("last_price") or ticker.get("price"))
    premium = ((mark - oracle) / oracle) if mark > 0 and oracle > 0 else 0.0
    return {
        **dict(ticker),
        "symbol": symbol,
        "lastPrice": last,
        "markPrice": mark,
        "oraclePrice": oracle,
        "fundingRate": funding,
        "openInterest": open_interest,
        "premium": premium,
        "hasFundingPremium": mark > 0 and oracle > 0 and ("fundingRate" in ticker or "funding_rate" in ticker),
    }


def normalize_ticker_message(message: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    ticker = normalize_ticker(message, _topic_symbol(message))
    symbol = str(ticker.get("symbol") or "")
    return (symbol, ticker) if symbol else None


def _level_px_sz(level: Any) -> tuple[float, float]:
    if isinstance(level, dict):
        return safe_float(level.get("px") or level.get("price")), safe_float(level.get("sz") or level.get("size"))
    if isinstance(level, (list, tuple)) and len(level) >= 2:
        return safe_float(level[0]), safe_float(level[1])
    return 0.0, 0.0


def normalize_orderbook(data: Any, fallback_symbol: str = "", depth_levels: int = 5) -> tuple[str, dict[str, float]] | None:
    if not isinstance(data, dict):
        return None
    raw = data.get("data") or data
    book = raw.get("book") if isinstance(raw, dict) else None
    book = book or raw
    if not isinstance(book, dict):
        return None
    symbol = str(book.get("symbol") or fallback_symbol or _topic_symbol(data) or "")
    levels = book.get("levels") or [book.get("bids", []), book.get("asks", [])]
    if not isinstance(levels, (list, tuple)) or len(levels) < 2:
        return None
    bids = levels[0] or []
    asks = levels[1] or []
    if not bids or not asks:
        return None
    bid, _ = _level_px_sz(bids[0])
    ask, _ = _level_px_sz(asks[0])
    if bid <= 0 or ask <= 0 or ask < bid:
        return None
    bid_depth = sum(_level_px_sz(level)[1] for level in bids[:depth_levels])
    ask_depth = sum(_level_px_sz(level)[1] for level in asks[:depth_levels])
    mid = (bid + ask) / 2
    spread_bps = ((ask - bid) / mid) * 10000 if mid > 0 else math.inf
    return symbol, {
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "spread_bps": max(spread_bps, 0.0),
        "bid_depth": bid_depth,
        "ask_depth": ask_depth,
        "source_ts": str(book.get("timestamp") or ""),
    }


def confidence_label(trades: int, coverage_days: float) -> str:
    if coverage_days < 7 or trades < 30:
        return "baixa"
    if coverage_days < 30 or trades < 100:
        return "media"
    return "alta"
