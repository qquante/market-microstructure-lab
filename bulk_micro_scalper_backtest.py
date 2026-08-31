#!/usr/bin/env python3
"""
Read-only BULK micro-scalper backtester.

This script does not generate keys, request faucet funds, sign transactions, or
send orders. It only reads public BULK staging market data and simulates the
strategy described in estrategia_crypto_micro_scalper_2026.md.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from bulk_market_data import (
    confidence_label,
    is_valid_ohlcv,
    normalize_orderbook,
    normalize_ticker,
    safe_float as market_safe_float,
    safe_int,
)


BASE_URL = "https://staging-api.bulk.trade/api/v1"
PRIMARY_SYMBOLS = ["BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD", "XRP-USD", "DOGE-USD"]
SECONDARY_SYMBOLS = ["SUI-USD", "ZEC-USD", "FARTCOIN-USD"]
SCENARIOS = {
    "zero_fee_staging": {"maker_bps": 0.0, "taker_bps": 0.0, "slippage_mult": 1.0},
    "phase1_tier1": {"maker_bps": 0.0, "taker_bps": 3.5, "slippage_mult": 1.0},
    "phase2_tier1": {"maker_bps": 2.0, "taker_bps": 3.5, "slippage_mult": 1.0},
    "phase1_tier1_slip2x": {"maker_bps": 0.0, "taker_bps": 3.5, "slippage_mult": 2.0},
}
DEFAULT_DAILY_CAPS = [20, 30, 40]
DEFAULT_DAYS = 30
DEFAULT_NOTIONAL_USD = 400.0
OUTPUT_DIR = "backtests"
STRATEGY_VARIANTS = [
    ("s025_t020", 0.0025, 0.0020, 1.0),
    ("s030_t025", 0.0030, 0.0025, 1.0),
    ("s035_t030", 0.0035, 0.0030, 1.25),
    ("s040_t035", 0.0040, 0.0035, 1.5),
    ("s045_t040", 0.0045, 0.0040, 1.5),
    ("s050_t045", 0.0050, 0.0045, 2.0),
    ("s050_t100", 0.0050, 0.0100, 2.0),
    ("s060_t050", 0.0060, 0.0050, 2.0),
    ("s070_t060", 0.0070, 0.0060, 2.0),
    ("s080_t070", 0.0080, 0.0070, 2.0),
    ("s090_t080", 0.0090, 0.0080, 2.0),
    ("s100_t050", 0.0100, 0.0050, 2.0),
    ("s100_t090", 0.0100, 0.0090, 2.0),
    ("s100_t100", 0.0100, 0.0100, 2.0),
    ("s100_t200", 0.0100, 0.0200, 3.0),
    ("s125_t110", 0.0125, 0.0110, 3.0),
    ("s150_t125", 0.0150, 0.0125, 3.0),
    ("s175_t150", 0.0175, 0.0150, 3.0),
    ("s200_t100", 0.0200, 0.0100, 3.0),
    ("s200_t175", 0.0200, 0.0175, 3.0),
    ("s225_t200", 0.0225, 0.0200, 4.0),
    ("s250_t225", 0.0250, 0.0225, 4.0),
    ("s300_t200", 0.0300, 0.0200, 4.0),
    ("s300_t300", 0.0300, 0.0300, 4.0),
]
TIME_EXIT_ON = "tx_on"
TIME_EXIT_OFF = "tx_off"
EXIT_PROFILES = (TIME_EXIT_ON, TIME_EXIT_OFF)


@dataclass
class Candle:
    t: int
    T: int
    o: float
    h: float
    l: float
    c: float
    v: float
    n: int = 0
    ind: dict[str, float | None] = field(default_factory=dict)
    regime: dict[str, float | None] = field(default_factory=dict)


@dataclass
class Candidate:
    symbol: str
    strategy: str
    side: str
    entry_idx: int
    entry_time: int
    entry_price: float
    stop_pct: float
    tp_pct: float
    max_hold: int
    liquidity: str
    score: float
    reason: str
    exit_profile: str = TIME_EXIT_ON


@dataclass
class RawTrade:
    symbol: str
    strategy: str
    side: str
    liquidity: str
    entry_time: int
    exit_time: int
    entry_price: float
    exit_price: float
    exit_reason: str
    hold_minutes: float
    gross_pnl: float
    funding_pnl: float
    notional: float
    reason: str


def iso_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def day_key(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def hour_key(ms: int) -> int:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).hour


def http_get(path: str, params: dict[str, Any] | None = None, timeout: int = 30, retries: int = 3) -> Any:
    params = params or {}
    query = urllib.parse.urlencode(params)
    url = f"{BASE_URL}{path}"
    if query:
        url = f"{url}?{query}"
    req = urllib.request.Request(url, headers={"User-Agent": "bulk-readonly-backtester/1.0"})
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                payload = response.read().decode("utf-8")
            break
        except Exception as exc:
            last_exc = exc
            if attempt == retries - 1:
                raise
            time.sleep(0.8 * (attempt + 1))
    else:
        raise RuntimeError(f"request failed without exception: {last_exc}")
    data = json.loads(payload)
    if isinstance(data, str):
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return data
    return data


def safe_float(value: Any, default: float = 0.0) -> float:
    return market_safe_float(value, default)


def normalize_exchange_info(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        if "markets" in data and isinstance(data["markets"], dict):
            return list(data["markets"].values())
        if "value" in data and isinstance(data["value"], list):
            return data["value"]
    if isinstance(data, list):
        return data
    return []


def normalize_candles(data: Any, start_ms: int, end_ms: int) -> list[Candle]:
    rows = data
    if isinstance(rows, str):
        rows = json.loads(rows)
    candles: dict[int, Candle] = {}
    for row in rows or []:
        t = safe_int(row.get("t"))
        if t < start_ms or t > end_ms:
            continue
        if not is_valid_ohlcv(row.get("o"), row.get("h"), row.get("l"), row.get("c"), row.get("v")):
            continue
        candles[t] = Candle(
            t=t,
            T=safe_int(row.get("T"), t),
            o=safe_float(row.get("o")),
            h=safe_float(row.get("h")),
            l=safe_float(row.get("l")),
            c=safe_float(row.get("c")),
            v=safe_float(row.get("v")),
            n=safe_int(row.get("n")),
        )
    return [candles[t] for t in sorted(candles)]


def ema(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if not values or len(values) < period:
        return out
    alpha = 2 / (period + 1)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = values[i] * alpha + prev * (1 - alpha)
        out[i] = prev
    return out


def ema_optional(values: list[float | None], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    alpha = 2 / (period + 1)
    seed_values: list[float] = []
    prev: float | None = None
    for i, value in enumerate(values):
        if value is None:
            continue
        if prev is None:
            seed_values.append(value)
            if len(seed_values) == period:
                prev = sum(seed_values) / period
                out[i] = prev
            continue
        prev = value * alpha + prev * (1 - alpha)
        out[i] = prev
    return out


def sma(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    total = 0.0
    for i, value in enumerate(values):
        total += value
        if i >= period:
            total -= values[i - period]
        if i >= period - 1:
            out[i] = total / period
    return out


def rolling_extreme(values: list[float], period: int, mode: str) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    for i in range(period, len(values)):
        window = values[i - period : i]
        out[i] = max(window) if mode == "max" else min(window)
    return out


def roc(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    for i in range(period, len(values)):
        base = values[i - period]
        out[i] = ((values[i] - base) / base) if base else None
    return out


def rsi(values: list[float], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return out
    gains = [0.0] * len(values)
    losses = [0.0] * len(values)
    for i in range(1, len(values)):
        delta = values[i] - values[i - 1]
        gains[i] = max(delta, 0.0)
        losses[i] = max(-delta, 0.0)
    avg_gain = sum(gains[1 : period + 1]) / period
    avg_loss = sum(losses[1 : period + 1]) / period
    if avg_gain == 0 and avg_loss == 0:
        out[period] = 50.0
    else:
        out[period] = 100.0 if avg_loss == 0 else 100 - (100 / (1 + avg_gain / avg_loss))
    for i in range(period + 1, len(values)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_gain == 0 and avg_loss == 0:
            out[i] = 50.0
        else:
            out[i] = 100.0 if avg_loss == 0 else 100 - (100 / (1 + avg_gain / avg_loss))
    return out


def atr(candles: list[Candle], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(candles)
    trs: list[float] = []
    for i, candle in enumerate(candles):
        if i == 0:
            tr = candle.h - candle.l
        else:
            prev_close = candles[i - 1].c
            tr = max(candle.h - candle.l, abs(candle.h - prev_close), abs(candle.l - prev_close))
        trs.append(tr)
    if len(trs) < period:
        return out
    first = sum(trs[:period]) / period
    out[period - 1] = first
    prev = first
    for i in range(period, len(trs)):
        prev = (prev * (period - 1) + trs[i]) / period
        out[i] = prev
    return out


def bollinger(values: list[float], period: int = 20, mult: float = 2.0) -> tuple[list[float | None], list[float | None], list[float | None], list[float | None]]:
    mid = sma(values, period)
    upper: list[float | None] = [None] * len(values)
    lower: list[float | None] = [None] * len(values)
    width: list[float | None] = [None] * len(values)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1 : i + 1]
        stdev = statistics.pstdev(window)
        assert mid[i] is not None
        upper[i] = mid[i] + mult * stdev
        lower[i] = mid[i] - mult * stdev
        width[i] = ((upper[i] - lower[i]) / mid[i]) if mid[i] else None
    return mid, upper, lower, width


def add_indicators(candles: list[Candle]) -> None:
    closes = [c.c for c in candles]
    highs = [c.h for c in candles]
    lows = [c.l for c in candles]
    volumes = [c.v for c in candles]
    ranges = [max(c.h - c.l, 0.0) for c in candles]
    ema9 = ema(closes, 9)
    ema12 = ema(closes, 12)
    ema20 = ema(closes, 20)
    ema26 = ema(closes, 26)
    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200)
    rsi2 = rsi(closes, 2)
    rsi14 = rsi(closes, 14)
    atr14 = atr(candles, 14)
    atr_sma50 = sma([x or 0.0 for x in atr14], 50)
    vol_sma20 = sma(volumes, 20)
    range_sma20 = sma(ranges, 20)
    roc3 = roc(closes, 3)
    roc6 = roc(closes, 6)
    high20 = rolling_extreme(highs, 20, "max")
    low20 = rolling_extreme(lows, 20, "min")
    high14 = rolling_extreme(highs, 14, "max")
    low14 = rolling_extreme(lows, 14, "min")
    high12 = rolling_extreme(highs, 12, "max")
    low12 = rolling_extreme(lows, 12, "min")
    high8 = rolling_extreme(highs, 8, "max")
    low8 = rolling_extreme(lows, 8, "min")
    bb_mid, bb_upper, bb_lower, bb_width = bollinger(closes, 20, 2.0)
    bb_width_sma = sma([x or 0.0 for x in bb_width], 50)
    macd_line: list[float | None] = [None] * len(candles)
    for i in range(len(candles)):
        if ema12[i] is not None and ema26[i] is not None:
            macd_line[i] = ema12[i] - ema26[i]
    macd_signal = ema_optional(macd_line, 9)

    current_day = ""
    cum_pv = 0.0
    cum_v = 0.0
    for i, candle in enumerate(candles):
        d = day_key(candle.t)
        if d != current_day:
            current_day = d
            cum_pv = 0.0
            cum_v = 0.0
        typical = (candle.h + candle.l + candle.c) / 3
        if candle.v > 0:
            cum_pv += typical * candle.v
            cum_v += candle.v
        vwap = cum_pv / cum_v if cum_v > 0 else typical
        candle.ind.update(
            {
                "ema9": ema9[i],
                "ema12": ema12[i],
                "ema20": ema20[i],
                "ema26": ema26[i],
                "ema50": ema50[i],
                "ema200": ema200[i],
                "macd": macd_line[i],
                "macd_signal": macd_signal[i],
                "macd_hist": (macd_line[i] - macd_signal[i]) if macd_line[i] is not None and macd_signal[i] is not None else None,
                "rsi2": rsi2[i],
                "rsi14": rsi14[i],
                "atr14": atr14[i],
                "atr_sma50": atr_sma50[i],
                "vol_sma20": vol_sma20[i],
                "range_sma20": range_sma20[i],
                "roc3": roc3[i],
                "roc6": roc6[i],
                "donchian_high20": high20[i],
                "donchian_low20": low20[i],
                "donchian_high14": high14[i],
                "donchian_low14": low14[i],
                "donchian_high12": high12[i],
                "donchian_low12": low12[i],
                "donchian_high8": high8[i],
                "donchian_low8": low8[i],
                "bb_mid": bb_mid[i],
                "bb_upper": bb_upper[i],
                "bb_lower": bb_lower[i],
                "bb_width": bb_width[i],
                "bb_width_sma50": bb_width_sma[i],
                "keltner_upper": (ema20[i] + 1.5 * atr14[i]) if ema20[i] is not None and atr14[i] is not None else None,
                "keltner_lower": (ema20[i] - 1.5 * atr14[i]) if ema20[i] is not None and atr14[i] is not None else None,
                "stoch14": ((candle.c - low14[i]) / (high14[i] - low14[i]) * 100) if high14[i] and low14[i] and high14[i] != low14[i] else None,
                "vwap": vwap,
            }
        )


def attach_regime(five: list[Candle], fifteen: list[Candle]) -> None:
    add_indicators(fifteen)
    j = 0
    for candle in five:
        while j + 1 < len(fifteen) and fifteen[j + 1].T <= candle.T:
            j += 1
        if fifteen and fifteen[j].T <= candle.T:
            candle.regime = fifteen[j].ind.copy()
        else:
            candle.regime = {}


def fetch_l2_metrics(symbol: str) -> dict[str, float]:
    try:
        book = http_get("/l2book", {"type": "l2book", "coin": symbol, "nlevels": 20}, timeout=15)
        parsed = normalize_orderbook(book, symbol)
        if parsed is None:
            raise ValueError("empty or invalid book")
        return parsed[1]
    except Exception:
        return {"bid": 0.0, "ask": 0.0, "mid": 0.0, "spread_bps": math.inf, "bid_depth": 0.0, "ask_depth": 0.0}


def classify_regime(candle: Candle) -> str:
    close = candle.c
    ema20 = candle.regime.get("ema20")
    ema50 = candle.regime.get("ema50")
    ema200 = candle.regime.get("ema200")
    if ema200 and ema20 and ema50:
        if close > ema200 and ema20 > ema50:
            return "bull"
        if close < ema200 and ema20 < ema50:
            return "bear"
    if ema20 and ema50:
        if ema20 > ema50:
            return "bull_weak"
        if ema20 < ema50:
            return "bear_weak"
    return "lateral"


def make_candidates(
    symbol: str,
    candles: list[Candle],
    ticker: dict[str, Any],
    premium_z: float,
    spread_bps: float,
    max_spread_bps: float,
    future_buffer: int = 16,
    live_entry: bool = False,
    exit_profiles: tuple[str, ...] = (TIME_EXIT_ON,),
) -> list[Candidate]:
    candidates: list[Candidate] = []
    ticker = normalize_ticker(ticker, symbol)
    premium_z = safe_float(premium_z)
    funding_rate = safe_float(ticker.get("fundingRate"))
    has_funding_premium = bool(ticker.get("hasFundingPremium"))
    oi = safe_float(ticker.get("openInterest"))
    oi_score = min(math.log10(max(oi, 1.0)), 6.0) / 6.0

    end = len(candles) if live_entry else len(candles) - future_buffer
    for i in range(210, end):
        c = candles[i]
        prev1 = candles[i - 1]
        prev2 = candles[i - 2]
        prev3 = candles[i - 3]
        nxt = candles[i + 1] if i + 1 < len(candles) else c
        r = classify_regime(c)
        ind = c.ind
        vwap = ind.get("vwap")
        rsi2v = ind.get("rsi2")
        rsi14 = ind.get("rsi14")
        atr14 = ind.get("atr14")
        vol_sma20 = ind.get("vol_sma20")
        ema9 = ind.get("ema9")
        ema20 = ind.get("ema20")
        ema50 = ind.get("ema50")
        ema200 = ind.get("ema200")
        macd_hist = ind.get("macd_hist")
        prev_macd_hist = prev1.ind.get("macd_hist")
        roc3v = ind.get("roc3")
        roc6v = ind.get("roc6")
        stoch14 = ind.get("stoch14")
        high20 = ind.get("donchian_high20")
        low20 = ind.get("donchian_low20")
        high14 = ind.get("donchian_high14")
        low14 = ind.get("donchian_low14")
        high12 = ind.get("donchian_high12")
        low12 = ind.get("donchian_low12")
        high8 = ind.get("donchian_high8")
        low8 = ind.get("donchian_low8")
        bb_upper = ind.get("bb_upper")
        bb_lower = ind.get("bb_lower")
        bb_width = ind.get("bb_width")
        bb_width_sma50 = ind.get("bb_width_sma50")
        keltner_upper = ind.get("keltner_upper")
        keltner_lower = ind.get("keltner_lower")
        atr_sma50 = ind.get("atr_sma50")
        range_sma20 = ind.get("range_sma20")
        if not all(x is not None for x in [vwap, rsi14, atr14, vol_sma20, ema9, ema20, ema50]):
            continue
        assert vwap is not None and rsi14 is not None and atr14 is not None and vol_sma20 is not None
        atr_pct = atr14 / c.c if c.c else 0.0
        if atr_pct <= 0 or spread_bps > max_spread_bps:
            continue

        def add(
            strategy: str,
            side: str,
            stop_pct: float,
            tp_pct: float,
            max_hold: int,
            liquidity: str,
            score: float,
            reason: str,
        ) -> None:
            del stop_pct, tp_pct
            for variant_name, variant_stop, variant_tp, hold_mult in STRATEGY_VARIANTS:
                for exit_profile in exit_profiles:
                    candidates.append(
                        Candidate(
                            symbol,
                            f"{strategy}__{variant_name}__{exit_profile}",
                            side,
                            min(i + 1, len(candles) - 1),
                            c.T if live_entry else nxt.t,
                            nxt.o,
                            variant_stop,
                            variant_tp,
                            max(1, int(round(max_hold * hold_mult))),
                            liquidity,
                            score,
                            f"{reason}__{variant_name}__{exit_profile}",
                            exit_profile,
                        )
                    )

        dist_vwap_atr = abs(c.c - vwap) / atr14 if atr14 else 0.0
        if c.c < vwap and 25 <= rsi14 <= 45 and dist_vwap_atr >= 0.25 and r not in {"bear"}:
            add("vwap_reversion", "long", 0.0016, 0.0012, 4, "maker", dist_vwap_atr + oi_score, "below_vwap_rsi_reversion")
        if c.c > vwap and 55 <= rsi14 <= 75 and dist_vwap_atr >= 0.25 and r not in {"bull"}:
            add("vwap_reversion", "short", 0.0016, 0.0012, 4, "maker", dist_vwap_atr + oi_score, "above_vwap_rsi_reversion")

        z = (c.c - vwap) / atr14 if atr14 else 0.0
        if z <= -0.55 and 22 <= rsi14 <= 52 and r not in {"bear"}:
            add("vwap_zscore_reversion", "long", 0.0018, 0.0014, 5, "maker", abs(z) + oi_score, "vwap_zscore_negative_snapback")
        if z >= 0.55 and 48 <= rsi14 <= 78 and r not in {"bull"}:
            add("vwap_zscore_reversion", "short", 0.0018, 0.0014, 5, "maker", abs(z) + oi_score, "vwap_zscore_positive_snapback")

        if has_funding_premium and oi > 0 and vwap:
            if premium_z > 2.0 and funding_rate > 0 and c.c < vwap and rsi14 < 52:
                add("premium_funding_reversal", "short", 0.0025, 0.0030, 10, "maker", premium_z + oi_score, "crowded_long_premium_funding_reversal")
            if premium_z < -2.0 and funding_rate < 0 and c.c > vwap and rsi14 > 48:
                add("premium_funding_reversal", "long", 0.0025, 0.0030, 10, "maker", abs(premium_z) + oi_score, "crowded_short_premium_funding_reversal")

        if rsi2v is not None and c.c < vwap and dist_vwap_atr >= 0.15 and rsi2v <= 8 and r not in {"bear"}:
            add("rsi2_vwap_snapback", "long", 0.0018, 0.0015, 5, "maker", (100 - rsi2v) / 100 + dist_vwap_atr, "rsi2_oversold_below_vwap")
        if rsi2v is not None and c.c > vwap and dist_vwap_atr >= 0.15 and rsi2v >= 92 and r not in {"bull"}:
            add("rsi2_vwap_snapback", "short", 0.0018, 0.0015, 5, "maker", rsi2v / 100 + dist_vwap_atr, "rsi2_overbought_above_vwap")

        if stoch14 is not None:
            if c.c < vwap and stoch14 <= 12 and rsi14 <= 44 and r not in {"bear"}:
                add("stoch_vwap_reversion", "long", 0.0017, 0.0014, 5, "maker", (100 - stoch14) / 100 + dist_vwap_atr, "stoch_oversold_below_vwap")
            if c.c > vwap and stoch14 >= 88 and rsi14 >= 56 and r not in {"bull"}:
                add("stoch_vwap_reversion", "short", 0.0017, 0.0014, 5, "maker", stoch14 / 100 + dist_vwap_atr, "stoch_overbought_above_vwap")

        minute = datetime.fromtimestamp(c.t / 1000, tz=timezone.utc).minute
        if minute in {0, 15, 30, 45} and c.v >= (vol_sma20 or 0.0) * 0.6:
            prev_bull = prev1.c > prev1.o and prev2.c > prev2.o
            prev_bear = prev1.c < prev1.o and prev2.c < prev2.o
            if prev_bull and c.c > vwap and ema9 and ema20 and ema9 > ema20:
                add("candle_turn_momentum", "long", 0.0010, 0.0015, 3, "taker", 1.0 + oi_score, "15m_turn_two_green_above_vwap")
            if prev_bear and c.c < vwap and ema9 and ema20 and ema9 < ema20:
                add("candle_turn_momentum", "short", 0.0010, 0.0015, 3, "taker", 1.0 + oi_score, "15m_turn_two_red_below_vwap")

        if bb_upper and bb_lower and bb_width and bb_width_sma50 and vol_sma20 and c.v > vol_sma20 * 1.5:
            compressed = bb_width < bb_width_sma50 * 0.9
            if compressed and c.c > bb_upper and r in {"bull", "bull_weak"}:
                add("bollinger_atr_breakout", "long", 0.0020, 0.0035, 12, "taker", 2.0 + oi_score, "bb_upper_breakout_volume")
            if compressed and c.c < bb_lower and r in {"bear", "bear_weak"}:
                add("bollinger_atr_breakout", "short", 0.0020, 0.0035, 12, "taker", 2.0 + oi_score, "bb_lower_breakout_volume")

        if bb_upper and bb_lower:
            if c.l < bb_lower and c.c > bb_lower and rsi14 <= 45 and r not in {"bear"}:
                add("bollinger_mean_reversion", "long", 0.0020, 0.0022, 8, "maker", 1.2 + oi_score, "bb_lower_reclaim")
            if c.h > bb_upper and c.c < bb_upper and rsi14 >= 55 and r not in {"bull"}:
                add("bollinger_mean_reversion", "short", 0.0020, 0.0022, 8, "maker", 1.2 + oi_score, "bb_upper_reject")

        if keltner_upper and keltner_lower:
            if c.l < keltner_lower and c.c > keltner_lower and rsi14 <= 43 and r not in {"bear"}:
                add("keltner_reversion", "long", 0.0019, 0.0020, 7, "maker", 1.25 + dist_vwap_atr, "keltner_lower_reclaim")
            if c.h > keltner_upper and c.c < keltner_upper and rsi14 >= 57 and r not in {"bull"}:
                add("keltner_reversion", "short", 0.0019, 0.0020, 7, "maker", 1.25 + dist_vwap_atr, "keltner_upper_reject")
            if c.c > keltner_upper and ema9 and ema20 and ema9 > ema20 and c.v >= vol_sma20 * 1.2 and r in {"bull", "bull_weak"}:
                add("keltner_breakout", "long", 0.0020, 0.0030, 10, "taker", 1.6 + oi_score, "keltner_upper_trend_break")
            if c.c < keltner_lower and ema9 and ema20 and ema9 < ema20 and c.v >= vol_sma20 * 1.2 and r in {"bear", "bear_weak"}:
                add("keltner_breakout", "short", 0.0020, 0.0030, 10, "taker", 1.6 + oi_score, "keltner_lower_trend_break")

        if ema9 and ema20 and ema50 and c.v >= vol_sma20 * 0.5:
            if r in {"bull", "bull_weak"} and ema9 > ema20 > ema50 and c.l <= ema20 and c.c > ema9 and 42 <= rsi14 <= 70:
                add("ema_pullback_momentum", "long", 0.0017, 0.0024, 8, "maker", 1.4 + oi_score, "bull_ema20_pullback_reclaim")
            if r in {"bear", "bear_weak"} and ema9 < ema20 < ema50 and c.h >= ema20 and c.c < ema9 and 30 <= rsi14 <= 58:
                add("ema_pullback_momentum", "short", 0.0017, 0.0024, 8, "maker", 1.4 + oi_score, "bear_ema20_pullback_reject")

        if ema20 and ema50 and ema200 and vol_sma20 and c.v >= vol_sma20 * 0.55:
            if c.c > ema200 and ema20 > ema50 and c.l <= ema20 and 45 <= rsi14 <= 62:
                add("rsi50_trend_pullback", "long", 0.0016, 0.0021, 7, "maker", 1.2 + oi_score, "bull_trend_rsi50_pullback")
            if c.c < ema200 and ema20 < ema50 and c.h >= ema20 and 38 <= rsi14 <= 55:
                add("rsi50_trend_pullback", "short", 0.0016, 0.0021, 7, "maker", 1.2 + oi_score, "bear_trend_rsi50_pullback")

        prev_ema9 = prev1.ind.get("ema9")
        prev_ema20 = prev1.ind.get("ema20")
        if prev_ema9 and prev_ema20 and ema9 and ema20 and c.v >= vol_sma20 * 0.7:
            if prev_ema9 <= prev_ema20 and ema9 > ema20 and c.c > vwap and r not in {"bear"}:
                add("ema_cross_momentum", "long", 0.0014, 0.0020, 6, "taker", 1.1 + oi_score, "ema9_cross_up_vwap_confirm")
            if prev_ema9 >= prev_ema20 and ema9 < ema20 and c.c < vwap and r not in {"bull"}:
                add("ema_cross_momentum", "short", 0.0014, 0.0020, 6, "taker", 1.1 + oi_score, "ema9_cross_down_vwap_confirm")

        if macd_hist is not None and prev_macd_hist is not None and ema20 and c.v >= vol_sma20 * 0.65:
            if prev_macd_hist <= 0 < macd_hist and c.c > vwap and c.c > ema20 and r not in {"bear"}:
                add("macd_vwap_continuation", "long", 0.0015, 0.0022, 7, "taker", 1.15 + abs(macd_hist / c.c) * 1000 + oi_score, "macd_hist_flip_up_above_vwap")
            if prev_macd_hist >= 0 > macd_hist and c.c < vwap and c.c < ema20 and r not in {"bull"}:
                add("macd_vwap_continuation", "short", 0.0015, 0.0022, 7, "taker", 1.15 + abs(macd_hist / c.c) * 1000 + oi_score, "macd_hist_flip_down_below_vwap")

        if high20 and low20 and c.v >= vol_sma20 * 1.15:
            if c.c > high20 and r in {"bull", "bull_weak"}:
                add("donchian_breakout", "long", 0.0020, 0.0030, 10, "taker", 1.8 + oi_score, "donchian20_high_breakout")
            if c.c < low20 and r in {"bear", "bear_weak"}:
                add("donchian_breakout", "short", 0.0020, 0.0030, 10, "taker", 1.8 + oi_score, "donchian20_low_breakout")

            if c.h > high20 and c.c < high20 and rsi14 >= 62:
                add("donchian_fade", "short", 0.0018, 0.0020, 7, "maker", 1.3 + oi_score, "failed_donchian_high_break")
            if c.l < low20 and c.c > low20 and rsi14 <= 38:
                add("donchian_fade", "long", 0.0018, 0.0020, 7, "maker", 1.3 + oi_score, "failed_donchian_low_break")

        if high14 and low14:
            if c.h > high14 and c.c < high14 and c.c < c.o and rsi14 >= 55:
                add("liquidity_sweep_reversal", "short", 0.0018, 0.0022, 7, "maker", 1.45 + oi_score, "sweep_prior_high_close_back_inside")
            if c.l < low14 and c.c > low14 and c.c > c.o and rsi14 <= 45:
                add("liquidity_sweep_reversal", "long", 0.0018, 0.0022, 7, "maker", 1.45 + oi_score, "sweep_prior_low_close_back_inside")

        if high12 and low12:
            range_width = (high12 - low12) / c.c if c.c else 0.0
            if 0.0015 <= range_width <= 0.012:
                if c.c <= low12 + (high12 - low12) * 0.25 and rsi14 <= 42:
                    add("range_mid_reversion", "long", 0.0018, 0.0020, 8, "maker", 1.0 + range_width * 100, "range_lower_quartile_reversion")
                if c.c >= high12 - (high12 - low12) * 0.25 and rsi14 >= 58:
                    add("range_mid_reversion", "short", 0.0018, 0.0020, 8, "maker", 1.0 + range_width * 100, "range_upper_quartile_reversion")

        if high8 and low8 and range_sma20 and atr_sma50:
            small_range = (high8 - low8) <= max(range_sma20 * 1.15, atr14 * 1.25)
            vol_expanding = atr14 >= atr_sma50 * 0.85 if atr_sma50 else False
            if small_range and vol_expanding and c.c > high8 and c.v >= vol_sma20 * 1.05 and r not in {"bear"}:
                add("micro_range_breakout", "long", 0.0015, 0.0024, 6, "taker", 1.25 + oi_score, "micro_range_high_break")
            if small_range and vol_expanding and c.c < low8 and c.v >= vol_sma20 * 1.05 and r not in {"bull"}:
                add("micro_range_breakout", "short", 0.0015, 0.0024, 6, "taker", 1.25 + oi_score, "micro_range_low_break")

        if roc3v is not None and roc6v is not None:
            if roc6v < -0.004 and roc3v > -0.001 and c.c > c.o and rsi14 <= 42 and r not in {"bear"}:
                add("roc_exhaustion_reversal", "long", 0.0020, 0.0024, 8, "maker", 1.1 + abs(roc6v) * 100, "six_bar_selloff_exhaustion")
            if roc6v > 0.004 and roc3v < 0.001 and c.c < c.o and rsi14 >= 58 and r not in {"bull"}:
                add("roc_exhaustion_reversal", "short", 0.0020, 0.0024, 8, "maker", 1.1 + abs(roc6v) * 100, "six_bar_rally_exhaustion")

        body = abs(c.c - c.o)
        candle_range = max(c.h - c.l, 1e-12)
        lower_wick = min(c.o, c.c) - c.l
        upper_wick = c.h - max(c.o, c.c)
        if c.v >= vol_sma20 * 1.8 and body / candle_range <= 0.55:
            if lower_wick / candle_range >= 0.45 and c.c > c.o and rsi14 <= 48:
                add("volume_wick_reversal", "long", 0.0020, 0.0023, 8, "maker", 1.6 + lower_wick / candle_range, "high_volume_lower_wick_reversal")
            if upper_wick / candle_range >= 0.45 and c.c < c.o and rsi14 >= 52:
                add("volume_wick_reversal", "short", 0.0020, 0.0023, 8, "maker", 1.6 + upper_wick / candle_range, "high_volume_upper_wick_reversal")

        prev3_down = prev3.c < prev3.o and prev2.c < prev2.o and prev1.c < prev1.o
        prev3_up = prev3.c > prev3.o and prev2.c > prev2.o and prev1.c > prev1.o
        if prev3_down and c.c > c.o and c.c > prev1.o and rsi14 <= 46 and c.c < vwap:
            add("three_bar_exhaustion", "long", 0.0018, 0.0021, 6, "maker", 1.2 + oi_score, "three_red_then_reversal_close")
        if prev3_up and c.c < c.o and c.c < prev1.o and rsi14 >= 54 and c.c > vwap:
            add("three_bar_exhaustion", "short", 0.0018, 0.0021, 6, "maker", 1.2 + oi_score, "three_green_then_reversal_close")

    return candidates


def simulated_exit(candidate: Candidate, candles: list[Candle]) -> tuple[float, int, str]:
    direction = 1 if candidate.side == "long" else -1
    entry = candidate.entry_price
    stop = entry * (1 - candidate.stop_pct) if direction == 1 else entry * (1 + candidate.stop_pct)
    tp = entry * (1 + candidate.tp_pct) if direction == 1 else entry * (1 - candidate.tp_pct)
    if candidate.exit_profile == TIME_EXIT_OFF:
        last_idx = len(candles) - 1
        exit_reason = "end_of_sample"
    else:
        last_idx = min(candidate.entry_idx + candidate.max_hold, len(candles) - 1)
        exit_reason = "time_exit"
    exit_price = candles[last_idx].c
    exit_time = candles[last_idx].T
    for j in range(candidate.entry_idx, last_idx + 1):
        c = candles[j]
        if direction == 1:
            stop_hit = c.l <= stop
            tp_hit = c.h >= tp
        else:
            stop_hit = c.h >= stop
            tp_hit = c.l <= tp
        if stop_hit and tp_hit:
            exit_price = stop
            exit_time = c.T
            exit_reason = "stop_conservative_same_candle"
            break
        if stop_hit:
            exit_price = stop
            exit_time = c.T
            exit_reason = "stop"
            break
        if tp_hit:
            exit_price = tp
            exit_time = c.T
            exit_reason = "take_profit"
            break
    return exit_price, exit_time, exit_reason


def simulate_trade(candidate: Candidate, candles: list[Candle], notional: float, funding_rate: float) -> RawTrade:
    direction = 1 if candidate.side == "long" else -1
    entry = candidate.entry_price
    exit_price, exit_time, exit_reason = simulated_exit(candidate, candles)
    gross = direction * ((exit_price - entry) / entry) * notional
    hold_minutes = max((exit_time - candidate.entry_time) / 60000, 0.0)
    funding_pnl = (-direction) * notional * funding_rate * (hold_minutes / 60 / 8)
    return RawTrade(
        symbol=candidate.symbol,
        strategy=candidate.strategy,
        side=candidate.side,
        liquidity=candidate.liquidity,
        entry_time=candidate.entry_time,
        exit_time=exit_time,
        entry_price=entry,
        exit_price=exit_price,
        exit_reason=exit_reason,
        hold_minutes=hold_minutes,
        gross_pnl=gross,
        funding_pnl=funding_pnl,
        notional=notional,
        reason=candidate.reason,
    )


def candidate_close_time(candidate: Candidate, candles_by_symbol: dict[str, list[Candle]] | None = None) -> int:
    if candles_by_symbol and candidate.symbol in candles_by_symbol:
        return simulated_exit(candidate, candles_by_symbol[candidate.symbol])[1]
    return candidate.entry_time + candidate.max_hold * 5 * 60 * 1000


def select_candidates(candidates: list[Candidate], daily_cap: int, candles_by_symbol: dict[str, list[Candle]] | None = None) -> list[Candidate]:
    by_day_count: dict[str, int] = defaultdict(int)
    last_exit_by_symbol: dict[str, int] = defaultdict(int)
    selected: list[Candidate] = []
    for c in sorted(candidates, key=lambda x: (x.entry_time, -x.score)):
        d = day_key(c.entry_time)
        if by_day_count[d] >= daily_cap:
            continue
        if c.entry_time < last_exit_by_symbol[c.symbol]:
            continue
        by_day_count[d] += 1
        last_exit_by_symbol[c.symbol] = candidate_close_time(c, candles_by_symbol)
        selected.append(c)
    return selected


def select_candidates_isolated(candidates: list[Candidate], daily_cap: int, candles_by_symbol: dict[str, list[Candle]] | None = None) -> list[Candidate]:
    selected: list[Candidate] = []
    grouped: dict[tuple[str, str], list[Candidate]] = defaultdict(list)
    for candidate in candidates:
        grouped[(candidate.symbol, candidate.strategy)].append(candidate)

    for key_candidates in grouped.values():
        by_day_count: dict[str, int] = defaultdict(int)
        last_exit = 0
        for candidate in sorted(key_candidates, key=lambda x: (x.entry_time, -x.score)):
            d = day_key(candidate.entry_time)
            if by_day_count[d] >= daily_cap:
                continue
            if candidate.entry_time < last_exit:
                continue
            by_day_count[d] += 1
            last_exit = candidate_close_time(candidate, candles_by_symbol)
            selected.append(candidate)

    return sorted(selected, key=lambda x: (x.entry_time, x.symbol, x.strategy, -x.score))


def net_for_scenario(trade: RawTrade, scenario: dict[str, float], spread_bps: float) -> tuple[float, float]:
    fee_bps = scenario["maker_bps"] if trade.liquidity == "maker" else scenario["taker_bps"]
    fee_cost = trade.notional * (fee_bps * 2 / 10000)
    if trade.liquidity == "maker":
        slip_bps = max(0.05, spread_bps * 0.15) * scenario["slippage_mult"]
    else:
        slip_bps = (max(0.25, spread_bps * 0.5) + 0.5) * scenario["slippage_mult"]
    slip_cost = trade.notional * (slip_bps * 2 / 10000)
    total_cost = fee_cost + slip_cost
    return trade.gross_pnl + trade.funding_pnl - total_cost, total_cost


def max_drawdown(pnls: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    return abs(max_dd)


def summarize(rows: list[dict[str, Any]], group_key: str | None = None) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if group_key is None:
        groups["all"] = rows
    else:
        for row in rows:
            groups[str(row[group_key])].append(row)
    summaries: list[dict[str, Any]] = []
    for key, items in sorted(groups.items()):
        pnls = [safe_float(x["net_pnl"]) for x in sorted(items, key=lambda y: y["entry_time_ms"])]
        wins = [x for x in items if safe_float(x["net_pnl"]) > 0]
        losses = [x for x in items if safe_float(x["net_pnl"]) < 0]
        gross_profit = sum(safe_float(x["net_pnl"]) for x in wins)
        gross_loss = abs(sum(safe_float(x["net_pnl"]) for x in losses))
        losing_streak = 0
        max_losing_streak = 0
        for pnl in pnls:
            if pnl < 0:
                losing_streak += 1
                max_losing_streak = max(max_losing_streak, losing_streak)
            else:
                losing_streak = 0
        summaries.append(
            {
                "group": key,
                "trades": len(items),
                "net_pnl": round(sum(pnls), 6),
                "gross_pnl": round(sum(safe_float(x["gross_pnl"]) for x in items), 6),
                "costs": round(sum(safe_float(x["costs"]) for x in items), 6),
                "funding_pnl": round(sum(safe_float(x["funding_pnl"]) for x in items), 6),
                "win_rate": round((len(wins) / len(items) * 100) if items else 0.0, 2),
                "profit_factor": round((gross_profit / gross_loss) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0), 4),
                "max_drawdown": round(max_drawdown(pnls), 6),
                "max_losing_streak": max_losing_streak,
                "avg_trade": round((sum(pnls) / len(items)) if items else 0.0, 6),
                "avg_hold_minutes": round((sum(safe_float(x.get("hold_minutes")) for x in items) / len(items)) if items else 0.0, 2),
                "time_exits": sum(1 for x in items if str(x.get("exit_reason")) == "time_exit"),
                "safety_exits": sum(1 for x in items if str(x.get("exit_reason")) == "safety_exit"),
                "sample_confidence": confidence_label(len(items), len({day_key(int(x["entry_time_ms"])) for x in items})),
            }
        )
    return summaries


def write_csv(path: str, rows: list[dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not rows:
        with open(path, "w", newline="", encoding="utf-8") as f:
            f.write("")
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def markdown_table(rows: list[dict[str, Any]], columns: list[str], limit: int | None = None) -> str:
    rows = rows[:limit] if limit else rows
    if not rows:
        return "_Sem dados._"
    out = ["|" + "|".join(columns) + "|", "|" + "|".join(["---"] * len(columns)) + "|"]
    for row in rows:
        out.append("|" + "|".join(str(row.get(col, "")) for col in columns) + "|")
    return "\n".join(out)


def run_backtest(args: argparse.Namespace) -> int:
    output_dir = args.output_dir
    os.makedirs(output_dir, exist_ok=True)
    warnings: list[str] = []
    daily_caps = sorted(set(args.daily_caps))
    principal_cap = max(daily_caps)
    symbols = PRIMARY_SYMBOLS + (SECONDARY_SYMBOLS if args.include_secondary else [])
    end_ms = int(time.time() * 1000)

    print("Fetching exchange info and stats...")
    try:
        stats = http_get("/stats", {"period": "1d"})
        if isinstance(stats, dict) and stats.get("timestamp"):
            end_ms = int(stats["timestamp"])
    except Exception as exc:
        stats = {}
        warnings.append(f"/stats failed: {exc}")
    start_ms = end_ms - args.days * 24 * 60 * 60 * 1000

    try:
        exchange_info = normalize_exchange_info(http_get("/exchangeInfo"))
    except Exception as exc:
        exchange_info = []
        warnings.append(f"/exchangeInfo failed: {exc}")
    market_by_symbol = {m.get("symbol"): m for m in exchange_info}
    unsupported = [s for s in symbols if s not in market_by_symbol]
    if unsupported:
        warnings.append(f"Symbols missing from exchangeInfo: {', '.join(unsupported)}")
    symbols = [s for s in symbols if s in market_by_symbol]

    tickers: dict[str, dict[str, Any]] = {}
    funding_rates: dict[str, float] = {}
    spreads: dict[str, float] = {}
    candles_5m: dict[str, list[Candle]] = {}
    all_candidates: list[Candidate] = []
    coverage_rows: list[dict[str, Any]] = []
    candidate_counts: dict[str, int] = defaultdict(int)

    premiums: dict[str, float] = {}
    for symbol in symbols:
        try:
            ticker = normalize_ticker(http_get(f"/ticker/{urllib.parse.quote(symbol)}"), symbol)
        except Exception as exc:
            warnings.append(f"ticker failed for {symbol}: {exc}")
            continue
        tickers[symbol] = ticker
        funding_rates[symbol] = safe_float(ticker.get("fundingRate"))
        premiums[symbol] = safe_float(ticker.get("premium"))
    premium_values = list(premiums.values())
    premium_mean = statistics.mean(premium_values) if premium_values else 0.0
    premium_stdev = statistics.pstdev(premium_values) if len(premium_values) > 1 else 1.0

    for symbol in symbols:
        if symbol not in tickers:
            continue
        print(f"Fetching {symbol} candles/book...")
        try:
            k5 = http_get("/klines", {"symbol": symbol, "interval": "5m", "startTime": start_ms, "endTime": end_ms})
            k15 = http_get("/klines", {"symbol": symbol, "interval": "15m", "startTime": start_ms, "endTime": end_ms})
            five = normalize_candles(k5, start_ms, end_ms)
            fifteen = normalize_candles(k15, start_ms, end_ms)
            if len(five) < 250 or len(fifteen) < 250:
                warnings.append(f"Insufficient candles for {symbol}: 5m={len(five)} 15m={len(fifteen)}")
                continue
            coverage_days = (five[-1].T - five[0].t) / (24 * 60 * 60 * 1000)
            coverage_rows.append(
                {
                    "symbol": symbol,
                    "first_5m": iso_ms(five[0].t),
                    "last_5m": iso_ms(five[-1].T),
                    "candles_5m": len(five),
                    "candles_15m": len(fifteen),
                    "coverage_days": round(coverage_days, 3),
                }
            )
            if coverage_days < args.days * 0.8:
                warnings.append(
                    f"{symbol} coverage is {coverage_days:.2f} days, below requested {args.days}; "
                    "BULK staging /klines appears to expose only cached/available history."
                )
            add_indicators(five)
            attach_regime(five, fifteen)
            candles_5m[symbol] = five
        except Exception as exc:
            warnings.append(f"candles failed for {symbol}: {exc}")
            continue
        l2 = fetch_l2_metrics(symbol)
        if l2["bid"] <= 0 or l2["ask"] <= 0:
            warnings.append(f"Invalid L2 book for {symbol}; symbol skipped for signal generation.")
            continue
        spreads[symbol] = l2["spread_bps"]
        premium_z = (premiums.get(symbol, 0.0) - premium_mean) / (premium_stdev or 1.0)
        symbol_candidates = make_candidates(symbol, five, tickers[symbol], premium_z, spreads[symbol], args.max_spread_bps, exit_profiles=EXIT_PROFILES)
        candidate_counts[symbol] = len(symbol_candidates)
        if not symbol_candidates:
            warnings.append(f"No candidate signals generated for {symbol}.")
        all_candidates.extend(symbol_candidates)

    if not all_candidates:
        warnings.append("No candidate trades generated.")

    trade_rows: list[dict[str, Any]] = []
    for cap in daily_caps:
        if args.selection_mode == "isolated":
            selected = select_candidates_isolated(all_candidates, cap, candles_5m)
        else:
            selected = select_candidates(all_candidates, cap, candles_5m)
        for candidate in selected:
            raw = simulate_trade(candidate, candles_5m[candidate.symbol], args.notional, funding_rates.get(candidate.symbol, 0.0))
            for scenario_name, scenario in SCENARIOS.items():
                net, costs = net_for_scenario(raw, scenario, spreads.get(raw.symbol, 2.0))
                trade_rows.append(
                    {
                        "cap": cap,
                        "scenario": scenario_name,
                        "symbol": raw.symbol,
                        "strategy": raw.strategy,
                        "exit_profile": candidate.exit_profile,
                        "side": raw.side,
                        "liquidity": raw.liquidity,
                        "entry_time": iso_ms(raw.entry_time),
                        "exit_time": iso_ms(raw.exit_time),
                        "entry_time_ms": raw.entry_time,
                        "exit_time_ms": raw.exit_time,
                        "entry_price": round(raw.entry_price, 8),
                        "exit_price": round(raw.exit_price, 8),
                        "exit_reason": raw.exit_reason,
                        "hold_minutes": round(raw.hold_minutes, 2),
                        "notional": raw.notional,
                        "gross_pnl": round(raw.gross_pnl, 6),
                        "funding_pnl": round(raw.funding_pnl, 6),
                        "costs": round(costs, 6),
                        "net_pnl": round(net, 6),
                        "spread_bps_snapshot": round(spreads.get(raw.symbol, 2.0), 4),
                        "reason": raw.reason,
                    }
                )

    write_csv(os.path.join(output_dir, "trades.csv"), trade_rows)
    for row in coverage_rows:
        row["candidate_signals"] = candidate_counts.get(str(row["symbol"]), 0)
    write_csv(os.path.join(output_dir, "data_coverage.csv"), coverage_rows)

    daily_rows: list[dict[str, Any]] = []
    for (cap, scenario, day), items in group_rows(trade_rows, ["cap", "scenario", "day"]).items():
        del day  # day is included in key tuple for clarity below.
        summaries = summarize(items)
        row = summaries[0] if summaries else {}
        daily_rows.append({"cap": cap, "scenario": scenario, "day": group_day(items), **{k: v for k, v in row.items() if k != "group"}})
    daily_rows.sort(key=lambda r: (int(r["cap"]), str(r["scenario"]), str(r["day"])))
    write_csv(os.path.join(output_dir, "daily_summary.csv"), daily_rows)

    pair_rows = grouped_summary_csv(trade_rows, "symbol")
    strategy_rows = grouped_summary_csv(trade_rows, "strategy")
    exit_profile_rows = grouped_summary_csv(trade_rows, "exit_profile")
    pair_strategy_rows = grouped_summary_csv(add_pair_strategy_key(trade_rows), "pair_strategy")
    robustness_rows = robustness_by_pair_strategy(trade_rows)
    best_strategy_rows = best_strategy_by_pair(pair_strategy_rows)
    tradeable_rows = tradeable_rank(pair_strategy_rows, robustness_rows, args.min_trades)
    write_csv(os.path.join(output_dir, "by_pair.csv"), pair_rows)
    write_csv(os.path.join(output_dir, "by_strategy.csv"), strategy_rows)
    write_csv(os.path.join(output_dir, "by_exit_profile.csv"), exit_profile_rows)
    write_csv(os.path.join(output_dir, "by_pair_strategy.csv"), pair_strategy_rows)
    write_csv(os.path.join(output_dir, "robustness_by_pair_strategy.csv"), robustness_rows)
    write_csv(os.path.join(output_dir, "best_strategy_by_pair.csv"), best_strategy_rows)
    write_csv(os.path.join(output_dir, "tradeable_rank.csv"), tradeable_rows)

    report_rows = summarize([r for r in trade_rows if int(r["cap"]) == principal_cap and r["scenario"] == "phase1_tier1"])
    report_main = report_rows[0] if report_rows else {}
    cap_compare = summarize([r for r in trade_rows if r["scenario"] == "phase1_tier1"], "cap")
    scenario_compare = summarize([r for r in trade_rows if int(r["cap"]) == principal_cap], "scenario")
    pair_main = [r for r in pair_rows if int(r["cap"]) == principal_cap and r["scenario"] == "phase1_tier1"]
    strat_main = [r for r in strategy_rows if int(r["cap"]) == principal_cap and r["scenario"] == "phase1_tier1"]
    exit_profile_main = [r for r in exit_profile_rows if int(r["cap"]) == principal_cap and r["scenario"] == "phase1_tier1"]
    pair_strategy_main = [r for r in pair_strategy_rows if int(r["cap"]) == principal_cap and r["scenario"] == "phase1_tier1"]
    best_strategy_main = [r for r in best_strategy_rows if int(r["cap"]) == principal_cap and r["scenario"] == "phase1_tier1"]
    tradeable_main = [r for r in tradeable_rows if int(r["cap"]) == principal_cap and r["scenario"] == "phase1_tier1"]
    pair_main.sort(key=lambda r: safe_float(r["net_pnl"]), reverse=True)
    strat_main.sort(key=lambda r: safe_float(r["net_pnl"]), reverse=True)
    exit_profile_main.sort(key=lambda r: safe_float(r["net_pnl"]), reverse=True)
    pair_strategy_main.sort(key=lambda r: safe_float(r["net_pnl"]), reverse=True)
    best_strategy_main.sort(key=lambda r: safe_float(r["net_pnl"]), reverse=True)
    tradeable_main.sort(key=lambda r: safe_float(r["research_score"]), reverse=True)
    slip2 = summarize([r for r in trade_rows if int(r["cap"]) == principal_cap and r["scenario"] == "phase1_tier1_slip2x"])
    slip2_pf = safe_float(slip2[0].get("profit_factor")) if slip2 else 0.0
    effective_days = min((safe_float(r["coverage_days"]) for r in coverage_rows), default=0.0)
    active_symbols = sorted({r["symbol"] for r in coverage_rows})

    report = [
        "# BULK Micro Scalper Backtest Report",
        "",
        f"- Periodo solicitado: `{iso_ms(start_ms)}` ate `{iso_ms(end_ms)}`",
        f"- Dias solicitados: `{args.days}`",
        f"- Cobertura efetiva minima: `{effective_days:.2f}` dias",
        f"- Simbolo(s) com dados: `{', '.join(active_symbols)}`",
        f"- Notional por trade: `US${args.notional:.2f}`",
        f"- Caps testados: `{', '.join(str(x) for x in daily_caps)}` trades/dia",
        f"- Modo de selecao: `{args.selection_mode}`",
        f"- Filtro maximo de spread: `{args.max_spread_bps:.2f}` bps",
        "- Ranking tradeable: `mostra todas as estrategias com confianca de amostra`",
        f"- Principal: cap `{principal_cap}` + scenario `phase1_tier1`",
        "- Resultado aprovado: `NAO APLICAVEL - BULK ready mede consistencia de dados, nao aprovacao final de lucro`",
        "",
        "## Cobertura De Dados",
        "",
        markdown_table(coverage_rows, ["symbol", "first_5m", "last_5m", "candles_5m", "candles_15m", "coverage_days", "candidate_signals"]),
        "",
        "## Resultado Principal",
        "",
        markdown_table(report_rows, ["group", "trades", "sample_confidence", "net_pnl", "gross_pnl", "costs", "funding_pnl", "win_rate", "profit_factor", "max_drawdown", "max_losing_streak", "avg_trade", "avg_hold_minutes", "time_exits", "safety_exits"]),
        "",
        "## Comparacao Por Cap Diario",
        "",
        markdown_table(cap_compare, ["group", "trades", "sample_confidence", "net_pnl", "win_rate", "profit_factor", "max_drawdown", "max_losing_streak", "avg_trade"]),
        "",
        "## Comparacao Por Cenario De Custo",
        "",
        markdown_table(scenario_compare, ["group", "trades", "net_pnl", "costs", "win_rate", "profit_factor", "max_drawdown", "avg_trade"]),
        "",
        "## Ranking Por Par",
        "",
        markdown_table(pair_main, ["group", "trades", "sample_confidence", "net_pnl", "win_rate", "profit_factor", "max_drawdown", "avg_trade"]),
        "",
        "## Ranking Por Motor",
        "",
        markdown_table(strat_main, ["group", "trades", "sample_confidence", "net_pnl", "win_rate", "profit_factor", "max_drawdown", "avg_trade", "avg_hold_minutes", "time_exits", "safety_exits"]),
        "",
        "## Comparacao Time Exit",
        "",
        markdown_table(exit_profile_main, ["group", "trades", "sample_confidence", "net_pnl", "win_rate", "profit_factor", "max_drawdown", "avg_trade", "avg_hold_minutes", "time_exits", "safety_exits"]),
        "",
        "## Melhor Estrategia Por Token",
        "",
        markdown_table(best_strategy_main, ["symbol", "best_strategy", "trades", "net_pnl", "win_rate", "profit_factor", "max_drawdown", "avg_trade"]),
        "",
        "## Ranking Tradeable",
        "",
        markdown_table(tradeable_main, ["symbol", "strategy", "trades", "sample_confidence", "readiness", "net_pnl", "win_rate", "profit_factor", "max_drawdown", "positive_day_rate", "research_score"], limit=30),
        "",
        "## Ranking Token + Estrategia",
        "",
        markdown_table(pair_strategy_main, ["group", "trades", "sample_confidence", "net_pnl", "win_rate", "profit_factor", "max_drawdown", "avg_trade"], limit=20),
        "",
        "## Boas Praticas Aplicadas",
        "",
        "- Sinal usa candle fechado e entrada simulada no proximo candle.",
        "- Stop/take profit no mesmo candle usa saida conservadora pelo stop.",
        "- Resultado principal inclui custo e slippage estimados, mesmo com staging mostrando fee zerada.",
        "- Ranking por token e estrategia fica isolado para evitar misturar setups bons e ruins.",
        "- Ranking tradeable mostra tudo e separa confianca da amostra de resultado financeiro.",
        "",
        "## BULK Ready",
        "",
        f"- Dados publicos capturados: `{'OK' if coverage_rows else 'SEM DADOS'}`",
        f"- Cobertura curta da BULK detectada: `{'SIM' if effective_days < args.days * 0.8 else 'NAO'}`",
        f"- Funding aplicado ao PnL: `{'SIM' if any(abs(v) > 0 for v in funding_rates.values()) else 'SIM, mas ticker atual veio zerado'}`",
        f"- Slippage 2x PF observado: `{slip2_pf:.4f}`",
        "",
        "## Observacoes Importantes",
        "",
        "- Este backtest e read-only e nao envia ordens.",
        "- No modo `isolated`, cada token + estrategia recebe seu proprio cap diario; os totais gerais sao matriz de pesquisa, nao carteira executavel simultanea.",
        "- O endpoint `/feeState` em staging pode mostrar fees zeradas; por isso o resultado principal usa custo simulado da documentacao.",
        "- Funding/premium entra quando o ticker expoe `fundingRate`, `markPrice`, `oraclePrice` e `openInterest`.",
        "- `exchangeInfo` em staging pode listar apenas GTC/IOC; ALO/post-only precisa ser validado antes de trading real.",
        "- Candles com volume zero sao mantidos, mas os indicadores de volume filtram varias entradas nesses trechos.",
    ]
    if warnings:
        report.extend(["", "## Warnings", ""])
        report.extend(f"- {w}" for w in warnings)
    with open(os.path.join(output_dir, "bulk_micro_scalper_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(report) + "\n")

    print(f"Wrote {len(trade_rows)} scenario trade rows.")
    print(f"Report: {os.path.join(output_dir, 'bulk_micro_scalper_report.md')}")
    return 0


def group_day(items: list[dict[str, Any]]) -> str:
    if not items:
        return ""
    return day_key(int(items[0]["entry_time_ms"]))


def group_rows(rows: list[dict[str, Any]], keys: list[str]) -> dict[tuple[Any, ...], list[dict[str, Any]]]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        values = []
        for key in keys:
            if key == "day":
                values.append(day_key(int(row["entry_time_ms"])))
            else:
                values.append(row[key])
        grouped[tuple(values)].append(row)
    return grouped


def grouped_summary_csv(rows: list[dict[str, Any]], group_key: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for (cap, scenario), cap_rows in group_rows(rows, ["cap", "scenario"]).items():
        for row in summarize(cap_rows, group_key):
            out.append({"cap": cap, "scenario": scenario, **row})
    out.sort(key=lambda r: (int(r["cap"]), str(r["scenario"]), str(r["group"])))
    return out


def add_pair_strategy_key(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keyed: list[dict[str, Any]] = []
    for row in rows:
        copy = dict(row)
        copy["pair_strategy"] = f"{row['symbol']}|{row['strategy']}"
        keyed.append(copy)
    return keyed


def best_strategy_by_pair(pair_strategy_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in pair_strategy_rows:
        pair_strategy = str(row["group"])
        symbol = pair_strategy.split("|", 1)[0]
        grouped[(int(row["cap"]), str(row["scenario"]), symbol)].append(row)

    best_rows: list[dict[str, Any]] = []
    for (cap, scenario, symbol), rows in grouped.items():
        rows.sort(key=lambda r: (safe_float(r["net_pnl"]), safe_float(r["profit_factor"]), safe_float(r["trades"])), reverse=True)
        best = rows[0]
        best_strategy = str(best["group"]).split("|", 1)[1] if "|" in str(best["group"]) else str(best["group"])
        best_rows.append(
            {
                "cap": cap,
                "scenario": scenario,
                "symbol": symbol,
                "best_strategy": best_strategy,
                "trades": best["trades"],
                "net_pnl": best["net_pnl"],
                "gross_pnl": best["gross_pnl"],
                "costs": best["costs"],
                "funding_pnl": best["funding_pnl"],
                "win_rate": best["win_rate"],
                "profit_factor": best["profit_factor"],
                "max_drawdown": best["max_drawdown"],
                "max_losing_streak": best["max_losing_streak"],
                "avg_trade": best["avg_trade"],
                "sample_confidence": best.get("sample_confidence", ""),
            }
        )
    best_rows.sort(key=lambda r: (int(r["cap"]), str(r["scenario"]), safe_float(r["net_pnl"])), reverse=True)
    return best_rows


def robustness_by_pair_strategy(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    daily: dict[tuple[int, str, str, str], float] = defaultdict(float)
    trades: dict[tuple[int, str, str], int] = defaultdict(int)
    for row in rows:
        cap = int(row["cap"])
        scenario = str(row["scenario"])
        pair_strategy = f"{row['symbol']}|{row['strategy']}"
        day = day_key(int(row["entry_time_ms"]))
        daily[(cap, scenario, pair_strategy, day)] += safe_float(row["net_pnl"])
        trades[(cap, scenario, pair_strategy)] += 1

    grouped: dict[tuple[int, str, str], list[float]] = defaultdict(list)
    for (cap, scenario, pair_strategy, _day), pnl in daily.items():
        grouped[(cap, scenario, pair_strategy)].append(pnl)

    out: list[dict[str, Any]] = []
    for (cap, scenario, pair_strategy), pnls in grouped.items():
        positive_days = sum(1 for x in pnls if x > 0)
        negative_days = sum(1 for x in pnls if x < 0)
        symbol, strategy = pair_strategy.split("|", 1)
        out.append(
            {
                "cap": cap,
                "scenario": scenario,
                "symbol": symbol,
                "strategy": strategy,
                "pair_strategy": pair_strategy,
                "trades": trades[(cap, scenario, pair_strategy)],
                "days_active": len(pnls),
                "positive_days": positive_days,
                "negative_days": negative_days,
                "positive_day_rate": round((positive_days / len(pnls) * 100) if pnls else 0.0, 2),
                "avg_daily_pnl": round((sum(pnls) / len(pnls)) if pnls else 0.0, 6),
                "min_daily_pnl": round(min(pnls), 6) if pnls else 0.0,
                "max_daily_pnl": round(max(pnls), 6) if pnls else 0.0,
                "total_net_pnl": round(sum(pnls), 6),
            }
        )
    out.sort(key=lambda r: (int(r["cap"]), str(r["scenario"]), safe_float(r["total_net_pnl"])), reverse=True)
    return out


def tradeable_rank(pair_strategy_rows: list[dict[str, Any]], robustness_rows: list[dict[str, Any]], min_trades: int) -> list[dict[str, Any]]:
    del min_trades
    robust_by_key = {
        (int(row["cap"]), str(row["scenario"]), str(row["pair_strategy"])): row
        for row in robustness_rows
    }
    out: list[dict[str, Any]] = []
    for row in pair_strategy_rows:
        trades = int(safe_float(row["trades"]))
        pf = safe_float(row["profit_factor"])
        key = (int(row["cap"]), str(row["scenario"]), str(row["group"]))
        robust = robust_by_key.get(key, {})
        sample_confidence = confidence_label(trades, safe_float(robust.get("days_active")))
        readiness = "observacao"
        if sample_confidence != "baixa" and pf >= 1.15:
            readiness = "paper_candidate"
        if sample_confidence == "alta" and pf >= 1.15:
            readiness = "ready_for_forward_test"
        score = (
            safe_float(row["net_pnl"])
            + pf
            + safe_float(robust.get("positive_day_rate")) / 25
            - safe_float(row["max_drawdown"]) / 10
            - safe_float(row["max_losing_streak"]) / 5
        )
        symbol, strategy = str(row["group"]).split("|", 1)
        out.append(
            {
                "cap": row["cap"],
                "scenario": row["scenario"],
                "symbol": symbol,
                "strategy": strategy,
                "trades": row["trades"],
                "net_pnl": row["net_pnl"],
                "win_rate": row["win_rate"],
                "profit_factor": row["profit_factor"],
                "max_drawdown": row["max_drawdown"],
                "max_losing_streak": row["max_losing_streak"],
                "positive_day_rate": robust.get("positive_day_rate", 0.0),
                "avg_daily_pnl": robust.get("avg_daily_pnl", 0.0),
                "sample_confidence": sample_confidence,
                "readiness": readiness,
                "research_score": round(score, 6),
            }
        )
    out.sort(key=lambda r: (int(r["cap"]), str(r["scenario"]), safe_float(r["research_score"])), reverse=True)
    return out


def parse_daily_caps(value: str) -> list[int]:
    caps = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        cap = int(part)
        if cap <= 0:
            raise argparse.ArgumentTypeError("daily caps must be positive integers")
        caps.append(cap)
    if not caps:
        raise argparse.ArgumentTypeError("provide at least one daily cap")
    return caps


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only BULK 5m/15m scalper backtest.")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--notional", type=float, default=DEFAULT_NOTIONAL_USD)
    parser.add_argument("--capital", type=float, default=1000.0)
    parser.add_argument("--include-secondary", action="store_true")
    parser.add_argument("--daily-caps", type=parse_daily_caps, default=DEFAULT_DAILY_CAPS, help="Comma-separated daily trade caps, e.g. 20,30,40")
    parser.add_argument("--selection-mode", choices=["isolated", "global"], default="isolated", help="isolated compares each token+strategy independently; global simulates one shared daily cap")
    parser.add_argument("--max-spread-bps", type=float, default=12.0, help="Skip symbols when the current L2 spread snapshot is wider than this.")
    parser.add_argument("--min-trades", type=int, default=20, help="Minimum trades for the tradeable ranking.")
    parser.add_argument("--output-dir", default=OUTPUT_DIR)
    return parser.parse_args()


if __name__ == "__main__":
    try:
        raise SystemExit(run_backtest(parse_args()))
    except urllib.error.URLError as exc:
        print(f"Network error: {exc}", file=sys.stderr)
        raise SystemExit(2)
