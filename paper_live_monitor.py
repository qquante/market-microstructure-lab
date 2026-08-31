#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import time
import traceback
import urllib.parse
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bulk_market_data import normalize_ticker
from bulk_redis import publish_json
from bulk_micro_scalper_backtest import (
    PRIMARY_SYMBOLS,
    SCENARIOS,
    SECONDARY_SYMBOLS,
    Candidate,
    add_indicators,
    attach_regime,
    fetch_l2_metrics,
    http_get,
    iso_ms,
    make_candidates,
    normalize_candles,
    normalize_exchange_info,
    safe_float,
)


ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "paper_live"
DEFAULT_DASHBOARD_LIVE = ROOT / "dashboard" / "live_data.json"
SCENARIO_NAME = "phase1_tier1"


def now_ms() -> int:
    return int(time.time() * 1000)


def short_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def signal_id(candidate: Candidate) -> str:
    return f"{candidate.symbol}|{candidate.strategy}|{candidate.side}|{candidate.entry_time}|{candidate.reason}"


def trade_id_from_signal(sid: str) -> str:
    return f"tr_{short_hash(sid)}"


def direction(side: str) -> int:
    return 1 if side == "long" else -1


def executable_price(side: str, book: dict[str, float], action: str) -> float:
    if action == "entry":
        return safe_float(book["ask"] if side == "long" else book["bid"])
    return safe_float(book["bid"] if side == "long" else book["ask"])


def estimate_costs(notional: float, fill_liquidity: str = "taker") -> float:
    scenario = SCENARIOS[SCENARIO_NAME]
    fee_bps = scenario["taker_bps"] if fill_liquidity == "taker" else scenario["maker_bps"]
    fee_cost = notional * (fee_bps * 2 / 10000)
    slip_cost = notional * (0.05 * 2 / 10000)
    return fee_cost + slip_cost


def funding_pnl_for(side: str, notional: float, funding_rate: float, hold_minutes: float) -> float:
    return -direction(side) * notional * funding_rate * (hold_minutes / 60 / 8)


def pnl_for(side: str, entry_price: float, exit_price: float, notional: float) -> float:
    return direction(side) * ((exit_price - entry_price) / entry_price) * notional


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS runtime_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS market_ticks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            time_ms INTEGER NOT NULL,
            time TEXT NOT NULL,
            symbol TEXT NOT NULL,
            bid REAL NOT NULL,
            ask REAL NOT NULL,
            mid REAL NOT NULL,
            spread_bps REAL NOT NULL,
            bid_depth REAL NOT NULL,
            ask_depth REAL NOT NULL,
            last_price REAL,
            mark_price REAL,
            oracle_price REAL,
            funding_rate REAL,
            open_interest REAL,
            premium REAL
        );
        CREATE INDEX IF NOT EXISTS idx_market_ticks_symbol_time ON market_ticks(symbol, time_ms);

        CREATE TABLE IF NOT EXISTS signals (
            signal_id TEXT PRIMARY KEY,
            time_ms INTEGER NOT NULL,
            time TEXT NOT NULL,
            symbol TEXT NOT NULL,
            strategy TEXT NOT NULL,
            side TEXT NOT NULL,
            status TEXT NOT NULL,
            reason TEXT NOT NULL,
            candidate_liquidity TEXT NOT NULL,
            score REAL NOT NULL,
            bid REAL NOT NULL,
            ask REAL NOT NULL,
            mid REAL NOT NULL,
            spread_bps REAL NOT NULL,
            blocked_reason TEXT,
            created_at_ms INTEGER NOT NULL,
            trade_id TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_signals_symbol_strategy ON signals(symbol, strategy, time_ms);

        CREATE TABLE IF NOT EXISTS trades (
            trade_id TEXT PRIMARY KEY,
            signal_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            strategy TEXT NOT NULL,
            side TEXT NOT NULL,
            status TEXT NOT NULL,
            entry_time_ms INTEGER NOT NULL,
            entry_time TEXT NOT NULL,
            entry_price REAL NOT NULL,
            stop_price REAL NOT NULL,
            tp_price REAL NOT NULL,
            max_hold_minutes REAL NOT NULL,
            notional REAL NOT NULL,
            spread_bps_entry REAL NOT NULL,
            reason TEXT NOT NULL,
            candidate_liquidity TEXT NOT NULL,
            exit_time_ms INTEGER,
            exit_time TEXT,
            exit_price REAL,
            exit_reason TEXT,
            gross_pnl REAL DEFAULT 0,
            costs REAL DEFAULT 0,
            net_pnl REAL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_trades_symbol_strategy ON trades(symbol, strategy, status);

        CREATE TABLE IF NOT EXISTS trade_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id TEXT,
            signal_id TEXT,
            event_time_ms INTEGER NOT NULL,
            event_time TEXT NOT NULL,
            symbol TEXT,
            strategy TEXT,
            event_type TEXT NOT NULL,
            side TEXT,
            bid REAL,
            ask REAL,
            mark_price REAL,
            spread_bps REAL,
            gross_pnl REAL DEFAULT 0,
            costs REAL DEFAULT 0,
            unrealized_pnl REAL DEFAULT 0,
            realized_pnl REAL DEFAULT 0,
            reason TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_trade_events_trade_time ON trade_events(trade_id, event_time_ms);

        CREATE TABLE IF NOT EXISTS closed_trades (
            trade_id TEXT PRIMARY KEY,
            signal_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            strategy TEXT NOT NULL,
            side TEXT NOT NULL,
            entry_time_ms INTEGER NOT NULL,
            entry_time TEXT NOT NULL,
            exit_time_ms INTEGER NOT NULL,
            exit_time TEXT NOT NULL,
            entry_price REAL NOT NULL,
            exit_price REAL NOT NULL,
            stop_price REAL,
            tp_price REAL,
            exit_reason TEXT NOT NULL,
            hold_minutes REAL NOT NULL,
            notional REAL NOT NULL,
            gross_pnl REAL NOT NULL,
            costs REAL NOT NULL,
            net_pnl REAL NOT NULL,
            spread_bps_entry REAL NOT NULL,
            spread_bps_exit REAL NOT NULL,
            reason TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_closed_symbol_strategy ON closed_trades(symbol, strategy, exit_time_ms);

        CREATE TABLE IF NOT EXISTS strategy_summary (
            group_key TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            strategy TEXT NOT NULL,
            variant_base TEXT,
            variant_profile TEXT,
            closed_trades INTEGER NOT NULL,
            open_positions INTEGER NOT NULL,
            closed_net_pnl REAL NOT NULL,
            open_net_pnl REAL NOT NULL,
            target_net_pnl REAL NOT NULL DEFAULT 0,
            stop_net_pnl REAL NOT NULL DEFAULT 0,
            total_net_pnl REAL NOT NULL,
            wins INTEGER NOT NULL,
            losses INTEGER NOT NULL,
            win_rate REAL NOT NULL,
            profit_factor REAL NOT NULL,
            avg_spread_bps REAL NOT NULL,
            updated_at_ms INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    ensure_column(conn, "closed_trades", "stop_price", "REAL")
    ensure_column(conn, "closed_trades", "tp_price", "REAL")
    ensure_column(conn, "market_ticks", "oracle_price", "REAL")
    ensure_column(conn, "market_ticks", "funding_rate", "REAL")
    ensure_column(conn, "market_ticks", "open_interest", "REAL")
    ensure_column(conn, "market_ticks", "premium", "REAL")
    ensure_column(conn, "trades", "funding_rate_entry", "REAL DEFAULT 0")
    ensure_column(conn, "trades", "premium_entry", "REAL DEFAULT 0")
    ensure_column(conn, "closed_trades", "funding_pnl", "REAL DEFAULT 0")
    ensure_column(conn, "closed_trades", "funding_rate_entry", "REAL DEFAULT 0")
    ensure_column(conn, "closed_trades", "premium_entry", "REAL DEFAULT 0")
    ensure_column(conn, "strategy_summary", "variant_base", "TEXT")
    ensure_column(conn, "strategy_summary", "variant_profile", "TEXT")
    ensure_column(conn, "strategy_summary", "target_net_pnl", "REAL NOT NULL DEFAULT 0")
    ensure_column(conn, "strategy_summary", "stop_net_pnl", "REAL NOT NULL DEFAULT 0")
    if not get_state(conn, "started_at"):
        set_state(conn, "started_at", iso_ms(now_ms()))
    conn.commit()


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def get_state(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM runtime_state WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row else default


def set_state(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO runtime_state(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


def log_error(output_dir: Path, message: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "errors.log").open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now(timezone.utc).isoformat()} {message}\n")


def insert_event(
    conn: sqlite3.Connection,
    event_type: str,
    ts_ms: int,
    *,
    trade_id: str | None = None,
    signal_id_value: str | None = None,
    symbol: str | None = None,
    strategy: str | None = None,
    side: str | None = None,
    book: dict[str, float] | None = None,
    mark_price: float | None = None,
    gross_pnl: float = 0.0,
    costs: float = 0.0,
    unrealized_pnl: float = 0.0,
    realized_pnl: float = 0.0,
    reason: str = "",
) -> None:
    book = book or {}
    conn.execute(
        """
        INSERT INTO trade_events(
            trade_id, signal_id, event_time_ms, event_time, symbol, strategy, event_type, side,
            bid, ask, mark_price, spread_bps, gross_pnl, costs, unrealized_pnl, realized_pnl, reason
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            trade_id,
            signal_id_value,
            ts_ms,
            iso_ms(ts_ms),
            symbol,
            strategy,
            event_type,
            side,
            safe_float(book.get("bid")),
            safe_float(book.get("ask")),
            mark_price,
            safe_float(book.get("spread_bps")),
            gross_pnl,
            costs,
            unrealized_pnl,
            realized_pnl,
            reason,
        ),
    )


def insert_market_tick(conn: sqlite3.Connection, ts_ms: int, symbol: str, ticker: dict[str, Any], book: dict[str, float]) -> None:
    ticker = normalize_ticker(ticker, symbol)
    conn.execute(
        """
        INSERT INTO market_ticks(
            time_ms, time, symbol, bid, ask, mid, spread_bps, bid_depth, ask_depth,
            last_price, mark_price, oracle_price, funding_rate, open_interest, premium
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ts_ms,
            iso_ms(ts_ms),
            symbol,
            book["bid"],
            book["ask"],
            book["mid"],
            book["spread_bps"],
            book["bid_depth"],
            book["ask_depth"],
            safe_float(ticker.get("lastPrice")),
            safe_float(ticker.get("markPrice")),
            safe_float(ticker.get("oraclePrice")),
            safe_float(ticker.get("fundingRate")),
            safe_float(ticker.get("openInterest")),
            safe_float(ticker.get("premium")),
        ),
    )


def fetch_market(symbol: str, start_ms: int, end_ms: int) -> tuple[dict[str, Any], dict[str, float], list[Any], list[Any]]:
    ticker = normalize_ticker(http_get(f"/ticker/{urllib.parse.quote(symbol)}", timeout=15), symbol)
    book = fetch_l2_metrics(symbol)
    k5 = http_get("/klines", {"symbol": symbol, "interval": "5m", "startTime": start_ms, "endTime": end_ms}, timeout=30)
    k15 = http_get("/klines", {"symbol": symbol, "interval": "15m", "startTime": start_ms, "endTime": end_ms}, timeout=30)
    five = [c for c in normalize_candles(k5, start_ms, end_ms) if c.T <= end_ms]
    fifteen = [c for c in normalize_candles(k15, start_ms, end_ms) if c.T <= end_ms]
    return ticker, book, five, fifteen


def build_candidates(symbol: str, ticker: dict[str, Any], book: dict[str, float], five: list[Any], fifteen: list[Any], args: argparse.Namespace) -> list[Candidate]:
    if len(five) < 250 or len(fifteen) < 250:
        return []
    add_indicators(five)
    attach_regime(five, fifteen)
    candidates = make_candidates(symbol, five, ticker, 0.0, book["spread_bps"], args.signal_spread_bps, future_buffer=1, live_entry=True)
    latest_close = five[-1].T
    return [candidate for candidate in candidates if candidate.entry_time == latest_close]


def has_open_trade(conn: sqlite3.Connection, symbol: str, strategy: str) -> bool:
    row = conn.execute("SELECT 1 FROM trades WHERE symbol = ? AND strategy = ? AND status = 'open' LIMIT 1", (symbol, strategy)).fetchone()
    return row is not None


def global_daily_count(conn: sqlite3.Connection) -> int:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = conn.execute("SELECT COUNT(*) c FROM trades WHERE entry_time LIKE ?", (f"{day}%",)).fetchone()
    return int(row["c"] if row else 0)


def realized_today(conn: sqlite3.Connection) -> float:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = conn.execute("SELECT COALESCE(SUM(net_pnl), 0) pnl FROM closed_trades WHERE exit_time LIKE ?", (f"{day}%",)).fetchone()
    return safe_float(row["pnl"] if row else 0)


def record_signal(conn: sqlite3.Connection, candidate: Candidate, book: dict[str, float], status: str, blocked_reason: str, trade_id: str | None) -> None:
    sid = signal_id(candidate)
    conn.execute(
        """
        INSERT OR IGNORE INTO signals(
            signal_id, time_ms, time, symbol, strategy, side, status, reason, candidate_liquidity,
            score, bid, ask, mid, spread_bps, blocked_reason, created_at_ms, trade_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            sid,
            candidate.entry_time,
            iso_ms(candidate.entry_time),
            candidate.symbol,
            candidate.strategy,
            candidate.side,
            status,
            candidate.reason,
            candidate.liquidity,
            candidate.score,
            book["bid"],
            book["ask"],
            book["mid"],
            book["spread_bps"],
            blocked_reason,
            now_ms(),
            trade_id,
        ),
    )
    insert_event(
        conn,
        "signal_blocked" if blocked_reason else "signal_detected",
        candidate.entry_time,
        trade_id=trade_id,
        signal_id_value=sid,
        symbol=candidate.symbol,
        strategy=candidate.strategy,
        side=candidate.side,
        book=book,
        reason=blocked_reason or candidate.reason,
    )


def open_trade(conn: sqlite3.Connection, candidate: Candidate, book: dict[str, float], ticker: dict[str, Any], args: argparse.Namespace, ts_ms: int) -> str | None:
    sid = signal_id(candidate)
    tid = trade_id_from_signal(sid)
    if conn.execute("SELECT 1 FROM trades WHERE trade_id = ?", (tid,)).fetchone():
        return None
    entry_price = executable_price(candidate.side, book, "entry")
    if entry_price <= 0:
        return None
    side_dir = direction(candidate.side)
    stop_price = entry_price * (1 - candidate.stop_pct) if side_dir == 1 else entry_price * (1 + candidate.stop_pct)
    tp_price = entry_price * (1 + candidate.tp_pct) if side_dir == 1 else entry_price * (1 - candidate.tp_pct)
    conn.execute(
        """
        INSERT INTO trades(
            trade_id, signal_id, symbol, strategy, side, status, entry_time_ms, entry_time,
            entry_price, stop_price, tp_price, max_hold_minutes, notional, spread_bps_entry,
            reason, candidate_liquidity, funding_rate_entry, premium_entry
        )
        VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            tid,
            sid,
            candidate.symbol,
            candidate.strategy,
            candidate.side,
            ts_ms,
            iso_ms(ts_ms),
            entry_price,
            stop_price,
            tp_price,
            candidate.max_hold * 5,
            args.notional,
            book["spread_bps"],
            candidate.reason,
            candidate.liquidity,
            safe_float(ticker.get("fundingRate")),
            safe_float(ticker.get("premium")),
        ),
    )
    conn.execute("UPDATE signals SET status = 'opened', trade_id = ? WHERE signal_id = ?", (tid, sid))
    costs = estimate_costs(args.notional, "taker")
    insert_event(
        conn,
        "trade_opened",
        ts_ms,
        trade_id=tid,
        signal_id_value=sid,
        symbol=candidate.symbol,
        strategy=candidate.strategy,
        side=candidate.side,
        book=book,
        mark_price=entry_price,
        costs=costs,
        reason=candidate.reason,
    )
    return tid


def close_reason(trade: sqlite3.Row, mark_price: float, ts_ms: int) -> str | None:
    if trade["side"] == "long":
        if mark_price <= trade["stop_price"]:
            return "stop_hit"
        if mark_price >= trade["tp_price"]:
            return "take_profit_hit"
    else:
        if mark_price >= trade["stop_price"]:
            return "stop_hit"
        if mark_price <= trade["tp_price"]:
            return "take_profit_hit"
    if (ts_ms - trade["entry_time_ms"]) / 60000 >= safe_float(trade["max_hold_minutes"]):
        return "time_exit"
    return None


def close_price_for_reason(trade: sqlite3.Row, mark_price: float, reason: str) -> float:
    if reason == "stop_hit":
        return safe_float(trade["stop_price"])
    if reason == "take_profit_hit":
        return safe_float(trade["tp_price"])
    return mark_price


def split_strategy_variant(strategy: str) -> tuple[str, str]:
    if "__" not in strategy:
        return strategy, "base"
    base, profile = strategy.split("__", 1)
    return base, profile


def distance_bps(side: str, mark_price: float, target_price: float, target_type: str) -> float:
    if mark_price <= 0 or target_price <= 0:
        return 0.0
    if side == "long":
        raw = (target_price - mark_price) if target_type == "take" else (mark_price - target_price)
    else:
        raw = (mark_price - target_price) if target_type == "take" else (target_price - mark_price)
    return raw / mark_price * 10000


def configured_pct(side: str, entry_price: float, target_price: float, target_type: str) -> float:
    if entry_price <= 0 or target_price <= 0:
        return 0.0
    if side == "long":
        raw = (target_price - entry_price) if target_type == "take" else (entry_price - target_price)
    else:
        raw = (entry_price - target_price) if target_type == "take" else (target_price - entry_price)
    return raw / entry_price


def enrich_open_row(row: dict[str, Any]) -> dict[str, Any]:
    variant_base, variant_profile = split_strategy_variant(str(row.get("strategy", "")))
    row["variant_base"] = variant_base
    row["variant_profile"] = variant_profile
    mark_price = safe_float(row.get("mark_price")) or safe_float(row.get("entry_price"))
    notional = safe_float(row.get("notional"))
    entry_price = safe_float(row.get("entry_price"))
    tp_price = safe_float(row.get("tp_price"))
    stop_price = safe_float(row.get("stop_price"))
    side = str(row.get("side"))
    costs = estimate_costs(notional, "taker")
    target_net = pnl_for(side, entry_price, tp_price, notional) - costs
    stop_net = pnl_for(side, entry_price, stop_price, notional) - costs
    row["target_net_pnl"] = target_net
    row["stop_net_pnl"] = stop_net
    row["tp_pct"] = configured_pct(side, entry_price, tp_price, "take")
    row["stop_pct"] = configured_pct(side, entry_price, stop_price, "stop")
    row["distance_to_take_bps"] = distance_bps(side, mark_price, tp_price, "take")
    row["distance_to_stop_bps"] = distance_bps(side, mark_price, stop_price, "stop")
    row["risk_reward_now"] = abs(target_net) / abs(stop_net) if abs(stop_net) > 0 else 0.0
    return row


def enrich_closed_row(row: dict[str, Any]) -> dict[str, Any]:
    variant_base, variant_profile = split_strategy_variant(str(row.get("strategy", "")))
    row["variant_base"] = variant_base
    row["variant_profile"] = variant_profile
    entry_price = safe_float(row.get("entry_price"))
    row["tp_pct"] = configured_pct(str(row.get("side")), entry_price, safe_float(row.get("tp_price")), "take")
    row["stop_pct"] = configured_pct(str(row.get("side")), entry_price, safe_float(row.get("stop_price")), "stop")
    return row


def mark_and_maybe_close(conn: sqlite3.Connection, trade: sqlite3.Row, book: dict[str, float], ts_ms: int) -> None:
    mark_price = executable_price(trade["side"], book, "exit")
    gross = pnl_for(trade["side"], trade["entry_price"], mark_price, trade["notional"])
    hold_minutes = max((ts_ms - trade["entry_time_ms"]) / 60000, 0.0)
    funding_pnl = funding_pnl_for(trade["side"], trade["notional"], safe_float(trade["funding_rate_entry"]), hold_minutes)
    costs = estimate_costs(trade["notional"], "taker")
    net = gross + funding_pnl - costs
    reason = close_reason(trade, mark_price, ts_ms)
    insert_event(
        conn,
        "trade_marked",
        ts_ms,
        trade_id=trade["trade_id"],
        signal_id_value=trade["signal_id"],
        symbol=trade["symbol"],
        strategy=trade["strategy"],
        side=trade["side"],
        book=book,
        mark_price=mark_price,
        gross_pnl=gross,
        costs=costs,
        unrealized_pnl=net,
        reason="mark_to_market_bid_ask",
    )
    if not reason:
        return

    exit_price = close_price_for_reason(trade, mark_price, reason)
    realized_gross = pnl_for(trade["side"], trade["entry_price"], exit_price, trade["notional"])
    realized_funding = funding_pnl_for(trade["side"], trade["notional"], safe_float(trade["funding_rate_entry"]), hold_minutes)
    realized_net = realized_gross + realized_funding - costs
    insert_event(
        conn,
        reason,
        ts_ms,
        trade_id=trade["trade_id"],
        signal_id_value=trade["signal_id"],
        symbol=trade["symbol"],
        strategy=trade["strategy"],
        side=trade["side"],
        book=book,
        mark_price=exit_price,
        gross_pnl=realized_gross,
        costs=costs,
        realized_pnl=realized_net,
        reason=reason,
    )
    conn.execute(
        """
        UPDATE trades
        SET status = 'closed', exit_time_ms = ?, exit_time = ?, exit_price = ?, exit_reason = ?,
            gross_pnl = ?, costs = ?, net_pnl = ?
        WHERE trade_id = ?
        """,
        (ts_ms, iso_ms(ts_ms), exit_price, reason, realized_gross, costs, realized_net, trade["trade_id"]),
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO closed_trades(
            trade_id, signal_id, symbol, strategy, side, entry_time_ms, entry_time, exit_time_ms, exit_time,
            entry_price, exit_price, stop_price, tp_price, exit_reason, hold_minutes, notional, gross_pnl, costs, net_pnl,
            spread_bps_entry, spread_bps_exit, reason, funding_pnl, funding_rate_entry, premium_entry
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            trade["trade_id"],
            trade["signal_id"],
            trade["symbol"],
            trade["strategy"],
            trade["side"],
            trade["entry_time_ms"],
            trade["entry_time"],
            ts_ms,
            iso_ms(ts_ms),
            trade["entry_price"],
            exit_price,
            trade["stop_price"],
            trade["tp_price"],
            reason,
            hold_minutes,
            trade["notional"],
            realized_gross,
            costs,
            realized_net,
            trade["spread_bps_entry"],
            book["spread_bps"],
            trade["reason"],
            realized_funding,
            safe_float(trade["funding_rate_entry"]),
            safe_float(trade["premium_entry"]),
        ),
    )
    insert_event(
        conn,
        "trade_closed",
        ts_ms,
        trade_id=trade["trade_id"],
        signal_id_value=trade["signal_id"],
        symbol=trade["symbol"],
        strategy=trade["strategy"],
        side=trade["side"],
        book=book,
        mark_price=exit_price,
        gross_pnl=realized_gross,
        costs=costs,
        realized_pnl=realized_net,
        reason=reason,
    )


def recompute_summary(conn: sqlite3.Connection, ts_ms: int) -> None:
    conn.execute("DELETE FROM strategy_summary")
    groups = set()
    for row in conn.execute("SELECT DISTINCT symbol, strategy FROM trades"):
        groups.add((row["symbol"], row["strategy"]))
    for row in conn.execute("SELECT DISTINCT symbol, strategy FROM signals"):
        groups.add((row["symbol"], row["strategy"]))
    for symbol, strategy in groups:
        closed = conn.execute("SELECT * FROM closed_trades WHERE symbol = ? AND strategy = ?", (symbol, strategy)).fetchall()
        open_rows = latest_open_rows(conn, symbol=symbol, strategy=strategy)
        closed_pnls = [safe_float(row["net_pnl"]) for row in closed]
        open_pnls = [safe_float(row["net_unrealized_pnl"]) for row in open_rows]
        target_pnls = [safe_float(row["target_net_pnl"]) for row in open_rows]
        stop_pnls = [safe_float(row["stop_net_pnl"]) for row in open_rows]
        wins = [pnl for pnl in closed_pnls if pnl > 0]
        losses = [pnl for pnl in closed_pnls if pnl < 0]
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        spreads = [safe_float(row["spread_bps_entry"]) for row in closed] + [safe_float(row["spread_bps_now"]) for row in open_rows]
        variant_base, variant_profile = split_strategy_variant(strategy)
        conn.execute(
            """
            INSERT INTO strategy_summary(
                group_key, symbol, strategy, variant_base, variant_profile, closed_trades, open_positions,
                closed_net_pnl, open_net_pnl, target_net_pnl, stop_net_pnl, total_net_pnl,
                wins, losses, win_rate, profit_factor, avg_spread_bps, updated_at_ms, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"{symbol}|{strategy}",
                symbol,
                strategy,
                variant_base,
                variant_profile,
                len(closed_pnls),
                len(open_pnls),
                sum(closed_pnls),
                sum(open_pnls),
                sum(target_pnls),
                sum(stop_pnls),
                sum(closed_pnls) + sum(open_pnls),
                len(wins),
                len(losses),
                (len(wins) / len(closed_pnls) * 100) if closed_pnls else 0.0,
                (gross_profit / gross_loss) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0),
                (sum(spreads) / len(spreads)) if spreads else 0.0,
                ts_ms,
                iso_ms(ts_ms),
            ),
        )


def latest_open_rows(conn: sqlite3.Connection, symbol: str | None = None, strategy: str | None = None) -> list[dict[str, Any]]:
    clauses = ["t.status = 'open'"]
    params: list[Any] = []
    if symbol:
        clauses.append("t.symbol = ?")
        params.append(symbol)
    if strategy:
        clauses.append("t.strategy = ?")
        params.append(strategy)
    rows = conn.execute(
        f"""
        SELECT t.*, e.mark_price, e.spread_bps AS spread_bps_now, e.gross_pnl AS gross_unrealized_pnl,
               e.costs AS estimated_costs, e.unrealized_pnl AS net_unrealized_pnl, e.event_time AS last_mark_time
        FROM trades t
        LEFT JOIN trade_events e ON e.event_id = (
            SELECT event_id FROM trade_events
            WHERE trade_id = t.trade_id AND event_type = 'trade_marked'
            ORDER BY event_time_ms DESC LIMIT 1
        )
        WHERE {" AND ".join(clauses)}
        ORDER BY t.entry_time_ms DESC
        """,
        params,
    ).fetchall()
    return [enrich_open_row(dict(row)) for row in rows]


def rows(conn: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(query, params).fetchall()]


def write_csv(path: Path, rows_: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = sorted({key for row in rows_ for key in row})
    with path.open("w", encoding="utf-8", newline="") as f:
        if not keys:
            f.write("")
            return
        writer = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows_)


def export_csvs(conn: sqlite3.Connection, output_dir: Path) -> None:
    open_rows = latest_open_rows(conn)
    closed_rows = [enrich_closed_row(row) for row in rows(conn, "SELECT * FROM closed_trades ORDER BY exit_time_ms")]
    signal_rows = rows(conn, "SELECT * FROM signals ORDER BY time_ms")
    tick_rows = rows(conn, "SELECT * FROM market_ticks ORDER BY time_ms")
    event_rows = rows(conn, "SELECT * FROM trade_events ORDER BY event_time_ms, event_id")
    summary_rows = rows(conn, "SELECT * FROM strategy_summary ORDER BY total_net_pnl DESC")
    write_csv(output_dir / "open_positions.csv", open_rows)
    write_csv(output_dir / "closed_trades.csv", closed_rows)
    write_csv(output_dir / "signals.csv", signal_rows)
    write_csv(output_dir / "book_snapshots.csv", tick_rows)
    write_csv(output_dir / "trade_events.csv", event_rows)
    write_csv(output_dir / "live_summary.csv", summary_rows)


def build_variant_matrix(open_rows: list[dict[str, Any]], closed_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}

    def group(symbol: str, variant_base: str, variant_profile: str) -> dict[str, Any]:
        key = (symbol, variant_base, variant_profile)
        if key not in grouped:
            grouped[key] = {
                "symbol": symbol,
                "variant_base": variant_base,
                "variant_profile": variant_profile,
                "group_key": f"{symbol}|{variant_base}|{variant_profile}",
                "open_positions": 0,
                "closed_trades": 0,
                "closed_net_pnl": 0.0,
                "open_net_pnl": 0.0,
                "target_net_pnl": 0.0,
                "stop_net_pnl": 0.0,
                "tp_pct": 0.0,
                "stop_pct": 0.0,
                "wins": 0,
                "losses": 0,
                "gross_profit": 0.0,
                "gross_loss": 0.0,
            }
        return grouped[key]

    for row in open_rows:
        item = group(str(row.get("symbol", "")), str(row.get("variant_base", "")), str(row.get("variant_profile", "")))
        item["open_positions"] += 1
        item["open_net_pnl"] += safe_float(row.get("net_unrealized_pnl"))
        item["target_net_pnl"] += safe_float(row.get("target_net_pnl"))
        item["stop_net_pnl"] += safe_float(row.get("stop_net_pnl"))
        item["tp_pct"] = item["tp_pct"] or safe_float(row.get("tp_pct"))
        item["stop_pct"] = item["stop_pct"] or safe_float(row.get("stop_pct"))

    for row in closed_rows:
        pnl = safe_float(row.get("net_pnl"))
        item = group(str(row.get("symbol", "")), str(row.get("variant_base", "")), str(row.get("variant_profile", "")))
        item["tp_pct"] = item["tp_pct"] or safe_float(row.get("tp_pct"))
        item["stop_pct"] = item["stop_pct"] or safe_float(row.get("stop_pct"))
        item["closed_trades"] += 1
        item["closed_net_pnl"] += pnl
        if pnl > 0:
            item["wins"] += 1
            item["gross_profit"] += pnl
        elif pnl < 0:
            item["losses"] += 1
            item["gross_loss"] += abs(pnl)

    result = []
    for item in grouped.values():
        closed_count = int(item["closed_trades"])
        gross_loss = safe_float(item["gross_loss"])
        gross_profit = safe_float(item["gross_profit"])
        item["total_net_pnl"] = safe_float(item["closed_net_pnl"]) + safe_float(item["open_net_pnl"])
        item["win_rate"] = (safe_float(item["wins"]) / closed_count * 100) if closed_count else 0.0
        item["profit_factor"] = (gross_profit / gross_loss) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)
        item.pop("gross_profit", None)
        item.pop("gross_loss", None)
        result.append(item)
    return sorted(result, key=lambda row: (str(row["symbol"]), str(row["variant_base"]), -safe_float(row["closed_net_pnl"]), -safe_float(row["target_net_pnl"])))


def build_dashboard_payload(conn: sqlite3.Connection) -> dict[str, Any]:
    generated_ms = now_ms()
    summary = rows(conn, "SELECT * FROM strategy_summary ORDER BY total_net_pnl DESC")
    open_rows = latest_open_rows(conn)
    closed = [enrich_closed_row(row) for row in rows(conn, "SELECT * FROM closed_trades ORDER BY exit_time_ms DESC LIMIT 500")]
    signals = rows(conn, "SELECT * FROM signals ORDER BY time_ms DESC LIMIT 500")
    latest_books = {
        row["symbol"]: row
        for row in rows(
            conn,
            """
            SELECT * FROM market_ticks mt
            WHERE id = (SELECT id FROM market_ticks WHERE symbol = mt.symbol ORDER BY time_ms DESC, id DESC LIMIT 1)
            ORDER BY symbol
            """,
        )
    }
    book_history = defaultdict(list)
    for row in rows(conn, "SELECT * FROM market_ticks ORDER BY time_ms DESC LIMIT 2160"):
        if len(book_history[row["symbol"]]) < 240:
            book_history[row["symbol"]].append(row)
    book_history = {symbol: list(reversed(items)) for symbol, items in book_history.items()}

    equity_by_symbol: dict[str, list[list[Any]]] = defaultdict(list)
    equity_by_strategy: dict[str, list[list[Any]]] = defaultdict(list)
    running_symbol: dict[str, float] = defaultdict(float)
    running_strategy: dict[str, float] = defaultdict(float)
    for row in rows(conn, "SELECT * FROM closed_trades ORDER BY exit_time_ms, trade_id"):
        running_symbol[row["symbol"]] += safe_float(row["net_pnl"])
        running_strategy[f"{row['symbol']}|{row['strategy']}"] += safe_float(row["net_pnl"])
        equity_by_symbol[row["symbol"]].append([row["exit_time"], round(running_symbol[row["symbol"]], 6)])
        equity_by_strategy[f"{row['symbol']}|{row['strategy']}"].append([row["exit_time"], round(running_strategy[f"{row['symbol']}|{row['strategy']}"], 6)])

    trade_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows(conn, "SELECT * FROM trade_events ORDER BY event_time_ms DESC, event_id DESC LIMIT 3000"):
        if row.get("trade_id"):
            trade_events[row["trade_id"]].append(row)
    trade_events = {tid: list(reversed(items)) for tid, items in trade_events.items()}

    trades = [enrich_closed_row(row) for row in rows(conn, "SELECT * FROM trades ORDER BY entry_time_ms DESC LIMIT 1000")]
    last_cycle_at = get_state(conn, "last_cycle_at", "")
    last_cycle_ms = safe_float(get_state(conn, "last_cycle_ms", "0"))
    age_seconds = max((generated_ms - last_cycle_ms) / 1000, 0) if last_cycle_ms else 999999
    if age_seconds < 90:
        health = "LIVE"
    elif age_seconds < 300:
        health = "STALE"
    else:
        health = "OFFLINE"
    return {
        "generatedAt": iso_ms(generated_ms),
        "startedAt": get_state(conn, "started_at", ""),
        "lastCycleAt": last_cycle_at,
        "lastCycleAgeSeconds": round(age_seconds, 1),
        "cycles": int(safe_float(get_state(conn, "cycles", "0"))),
        "status": get_state(conn, "status", "unknown"),
        "health": health,
        "lastError": get_state(conn, "last_error", ""),
        "open": open_rows,
        "closed": closed,
        "signals": signals,
        "summary": summary,
        "books": latest_books,
        "bookHistory": book_history,
        "equityBySymbol": equity_by_symbol,
        "equityByStrategy": equity_by_strategy,
        "variantMatrix": build_variant_matrix(open_rows, closed),
        "trades": trades,
        "tradeEvents": trade_events,
        "symbols": sorted(latest_books),
    }


def write_dashboard_json(conn: sqlite3.Connection, path: Path) -> None:
    payload = build_dashboard_payload(conn)
    try:
        publish_json(payload)
    except Exception as exc:
        set_state(conn, "last_redis_error", str(exc))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    tmp.replace(path)


def run_cycle(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    output_dir = Path(args.output_dir)
    ts_ms = now_ms()
    start_ms = ts_ms - args.history_days * 24 * 60 * 60 * 1000
    books_by_symbol: dict[str, dict[str, float]] = {}
    for symbol in args.symbols:
        try:
            ticker, book, five, fifteen = fetch_market(symbol, start_ms, ts_ms)
            if book["bid"] <= 0 or book["ask"] <= 0:
                raise RuntimeError("empty or invalid L2 book")
            books_by_symbol[symbol] = book
            insert_market_tick(conn, ts_ms, symbol, ticker, book)
            candidates = build_candidates(symbol, ticker, book, five, fifteen, args)
            for candidate in candidates:
                sid = signal_id(candidate)
                if conn.execute("SELECT 1 FROM signals WHERE signal_id = ?", (sid,)).fetchone():
                    continue
                blocked_reason = ""
                status = "detected"
                tid = trade_id_from_signal(sid)
                if book["spread_bps"] > args.max_spread_bps:
                    blocked_reason = "blocked_by_spread"
                    status = "blocked_by_spread"
                    tid = None
                elif has_open_trade(conn, candidate.symbol, candidate.strategy):
                    blocked_reason = "already_open"
                    status = "blocked_already_open"
                    tid = None
                elif args.global_daily_limit > 0 and global_daily_count(conn) >= args.global_daily_limit:
                    blocked_reason = "global_daily_limit"
                    status = "blocked_global_limit"
                    tid = None
                elif realized_today(conn) <= args.daily_loss_limit:
                    blocked_reason = "daily_loss_limit"
                    status = "blocked_daily_loss"
                    tid = None
                record_signal(conn, candidate, book, status, blocked_reason, tid)
                if not blocked_reason:
                    open_trade(conn, candidate, book, ticker, args, now_ms())
        except Exception as exc:
            message = f"{symbol}: {exc}"
            set_state(conn, "last_error", message)
            log_error(output_dir, f"{message}\n{traceback.format_exc()}")
            insert_event(conn, "api_error", ts_ms, symbol=symbol, reason=message)

    mark_ts_ms = now_ms()
    open_trades = conn.execute("SELECT * FROM trades WHERE status = 'open' ORDER BY entry_time_ms").fetchall()
    for trade in open_trades:
        try:
            book = books_by_symbol.get(trade["symbol"]) or fetch_l2_metrics(trade["symbol"])
            if book["bid"] <= 0 or book["ask"] <= 0:
                raise RuntimeError("empty or invalid L2 book while marking")
            mark_and_maybe_close(conn, trade, book, mark_ts_ms)
        except Exception as exc:
            message = f"{trade['symbol']} mark/close {trade['trade_id']}: {exc}"
            set_state(conn, "last_error", message)
            log_error(output_dir, f"{message}\n{traceback.format_exc()}")
            insert_event(conn, "api_error", mark_ts_ms, trade_id=trade["trade_id"], symbol=trade["symbol"], strategy=trade["strategy"], reason=message)

    cycle_end_ms = now_ms()
    recompute_summary(conn, cycle_end_ms)
    cycles = int(safe_float(get_state(conn, "cycles", "0"))) + 1
    set_state(conn, "cycles", cycles)
    set_state(conn, "last_cycle_ms", cycle_end_ms)
    set_state(conn, "last_cycle_at", iso_ms(cycle_end_ms))
    set_state(conn, "status", "running")
    conn.commit()
    export_csvs(conn, output_dir)
    write_dashboard_json(conn, Path(args.dashboard_live_json))


def discover_symbols(include_secondary: bool) -> list[str]:
    requested = PRIMARY_SYMBOLS + (SECONDARY_SYMBOLS if include_secondary else [])
    try:
        markets = normalize_exchange_info(http_get("/exchangeInfo", timeout=20))
        available = {market.get("symbol") for market in markets}
        return [symbol for symbol in requested if symbol in available]
    except Exception:
        return requested


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only BULK paper-live monitor backed by SQLite.")
    parser.add_argument("--include-secondary", action="store_true", default=True)
    parser.add_argument("--symbols", default="")
    parser.add_argument("--interval-seconds", type=int, default=60)
    parser.add_argument("--duration-hours", type=float, default=24.0)
    parser.add_argument("--history-days", type=int, default=30)
    parser.add_argument("--notional", type=float, default=400.0)
    parser.add_argument("--signal-spread-bps", type=float, default=1000.0)
    parser.add_argument("--max-spread-bps", type=float, default=12.0)
    parser.add_argument("--global-daily-limit", type=int, default=40)
    parser.add_argument("--daily-loss-limit", type=float, default=-25.0)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--dashboard-live-json", default=str(DEFAULT_DASHBOARD_LIVE))
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.symbols:
        args.symbols = [symbol.strip() for symbol in args.symbols.split(",") if symbol.strip()]
    else:
        args.symbols = discover_symbols(args.include_secondary)
    return args


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    conn = connect(output_dir / "paper_live.sqlite")
    init_db(conn)
    deadline = None if args.duration_hours <= 0 else time.time() + args.duration_hours * 60 * 60
    while True:
        run_cycle(conn, args)
        open_count = conn.execute("SELECT COUNT(*) AS c FROM trades WHERE status = 'open'").fetchone()["c"]
        print(f"{datetime.now(timezone.utc).isoformat()} cycle={get_state(conn, 'cycles')} open={open_count}", flush=True)
        if args.once or (deadline is not None and time.time() >= deadline):
            set_state(conn, "status", "once_complete" if args.once else "stopped")
            conn.commit()
            write_dashboard_json(conn, Path(args.dashboard_live_json))
            return 0
        time.sleep(max(args.interval_seconds, 1))


if __name__ == "__main__":
    raise SystemExit(main())
