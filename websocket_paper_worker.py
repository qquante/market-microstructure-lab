#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import socket
import sqlite3
import ssl
import struct
import time
import traceback
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from bulk_market_data import is_valid_ohlcv, normalize_orderbook, normalize_ticker_message, safe_int
from bulk_micro_scalper_backtest import (
    EXIT_PROFILES,
    PRIMARY_SYMBOLS,
    SCENARIOS,
    SECONDARY_SYMBOLS,
    STRATEGY_VARIANTS,
    TIME_EXIT_OFF,
    TIME_EXIT_ON,
    Candle,
    add_indicators,
    attach_regime,
    iso_ms,
    make_candidates,
    safe_float,
)


ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "ws_live"
DEFAULT_DASHBOARD_JSON = ROOT / "dashboard" / "ws_data.json"
WS_URL = "wss://staging-ws.bulk.trade"
REST_API_URL = "https://staging-api.bulk.trade/api/v1"
SCENARIO_NAME = "phase1_tier1"
DEFAULT_ACCOUNT_EQUITY = 100.0

PORTFOLIO = {
    "BNB-USD": {
        "rsi50_trend_pullback__s035_t030",
        "macd_vwap_continuation__s175_t150",
        "vwap_zscore_reversion__s100_t100",
    },
    "BTC-USD": {
        "keltner_breakout__s080_t070",
        "donchian_breakout__s060_t050",
        "candle_turn_momentum__s050_t100",
    },
    "DOGE-USD": {
        "keltner_breakout__s050_t100",
        "donchian_breakout__s050_t100",
    },
    "ETH-USD": {
        "rsi50_trend_pullback__s050_t100",
        "three_bar_exhaustion__s050_t100",
    },
    "FARTCOIN-USD": {
        "stoch_vwap_reversion__s100_t100",
        "bollinger_mean_reversion__s040_t035",
        "vwap_reversion__s100_t100",
    },
    "SUI-USD": {
        "vwap_reversion__s100_t100",
        "stoch_vwap_reversion__s100_t100",
        "keltner_reversion__s100_t100",
        "candle_turn_momentum__s100_t100",
    },
    "XRP-USD": {
        "keltner_breakout__s100_t090",
        "candle_turn_momentum__s100_t100",
        "donchian_breakout__s070_t060",
        "keltner_reversion__s100_t100",
        "rsi50_trend_pullback__s100_t100",
    },
    "ZEC-USD": {
        "donchian_fade__s050_t100",
        "volume_wick_reversal__s100_t100",
    },
}

ALL_STRATEGY_BASES = [
    "vwap_reversion",
    "vwap_zscore_reversion",
    "rsi2_vwap_snapback",
    "stoch_vwap_reversion",
    "candle_turn_momentum",
    "bollinger_atr_breakout",
    "bollinger_mean_reversion",
    "keltner_reversion",
    "keltner_breakout",
    "ema_pullback_momentum",
    "rsi50_trend_pullback",
    "ema_cross_momentum",
    "macd_vwap_continuation",
    "donchian_breakout",
    "donchian_fade",
    "liquidity_sweep_reversal",
    "range_mid_reversion",
    "micro_range_breakout",
    "roc_exhaustion_reversal",
    "volume_wick_reversal",
    "three_bar_exhaustion",
    "premium_funding_reversal",
]


def now_ms() -> int:
    return int(time.time() * 1000)


def account_id(symbol: str, strategy: str) -> str:
    return f"{symbol}|{strategy}"


def short_id(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def ws_signal_id(symbol: str, strategy: str, side: str, candle_time: int, reason: str) -> str:
    return f"{symbol}|{strategy}|{side}|{candle_time}|{reason}"


def ws_trade_id(signal_id: str) -> str:
    return f"wstr_{short_id(signal_id)}"


def canonical_exit_signal_id(signal_id: str) -> str:
    return signal_id.replace("__tx_on", "").replace("__tx_off", "")


def direction(side: str) -> int:
    return 1 if side == "long" else -1


def split_strategy(strategy: str) -> tuple[str, str, str]:
    parts = strategy.split("__")
    if not parts:
        return strategy, "base", TIME_EXIT_ON
    if parts[-1] in EXIT_PROFILES:
        exit_profile = parts[-1]
        parts = parts[:-1]
    else:
        exit_profile = TIME_EXIT_ON
    if len(parts) == 1:
        return parts[0], "base", exit_profile
    return parts[0], "__".join(parts[1:]), exit_profile


def strategy_exit_variants(strategy: str) -> list[str]:
    if strategy.split("__")[-1] in EXIT_PROFILES:
        return [strategy]
    return [f"{strategy}__{exit_profile}" for exit_profile in EXIT_PROFILES]


def executable_price(side: str, book: dict[str, float], action: str) -> float:
    if action == "entry":
        return safe_float(book["ask"] if side == "long" else book["bid"])
    return safe_float(book["bid"] if side == "long" else book["ask"])


def pnl_for(side: str, entry_price: float, exit_price: float, notional: float) -> float:
    if entry_price <= 0:
        return 0.0
    return direction(side) * ((exit_price - entry_price) / entry_price) * notional


def taker_round_trip_cost(notional: float) -> float:
    scenario = SCENARIOS[SCENARIO_NAME]
    fee_bps = scenario["taker_bps"] * 2
    slip_bps = 0.05 * 2
    return notional * ((fee_bps + slip_bps) / 10000)


def funding_pnl_for(side: str, notional: float, funding_rate: float, hold_minutes: float) -> float:
    return -direction(side) * notional * funding_rate * (hold_minutes / 60 / 8)


def connect_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db(conn: sqlite3.Connection, symbols: list[str], account_equity: float, strategy_mode: str) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS ws_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ws_books (
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
            source_ts TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_ws_books_symbol_time ON ws_books(symbol, time_ms);

        CREATE TABLE IF NOT EXISTS ws_tickers (
            symbol TEXT PRIMARY KEY,
            time_ms INTEGER NOT NULL,
            time TEXT NOT NULL,
            mark_price REAL DEFAULT 0,
            oracle_price REAL DEFAULT 0,
            funding_rate REAL DEFAULT 0,
            open_interest REAL DEFAULT 0,
            premium REAL DEFAULT 0,
            raw_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS ws_public_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            time_ms INTEGER NOT NULL,
            time TEXT NOT NULL,
            symbol TEXT NOT NULL,
            price REAL NOT NULL,
            size REAL NOT NULL,
            side TEXT,
            raw_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ws_public_trades_symbol_time ON ws_public_trades(symbol, time_ms);

        CREATE TABLE IF NOT EXISTS ws_candles (
            symbol TEXT NOT NULL,
            interval TEXT NOT NULL,
            t INTEGER NOT NULL,
            close_ms INTEGER NOT NULL,
            o REAL NOT NULL,
            h REAL NOT NULL,
            l REAL NOT NULL,
            c REAL NOT NULL,
            v REAL NOT NULL,
            n INTEGER NOT NULL,
            updated_at_ms INTEGER NOT NULL,
            PRIMARY KEY(symbol, interval, t)
        );
        CREATE INDEX IF NOT EXISTS idx_ws_candles_symbol_interval_t ON ws_candles(symbol, interval, t);

        CREATE TABLE IF NOT EXISTS ws_accounts (
            account_id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            strategy TEXT NOT NULL,
            variant_base TEXT NOT NULL,
            variant_profile TEXT NOT NULL,
            exit_profile TEXT NOT NULL DEFAULT 'tx_on',
            start_equity REAL NOT NULL,
            status TEXT NOT NULL,
            created_at_ms INTEGER NOT NULL,
            max_daily_trades INTEGER NOT NULL,
            daily_loss_limit REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS ws_signals (
            signal_id TEXT PRIMARY KEY,
            time_ms INTEGER NOT NULL,
            time TEXT NOT NULL,
            symbol TEXT NOT NULL,
            strategy TEXT NOT NULL,
            exit_profile TEXT NOT NULL DEFAULT 'tx_on',
            side TEXT NOT NULL,
            status TEXT NOT NULL,
            reason TEXT NOT NULL,
            spread_bps REAL NOT NULL,
            account_id TEXT,
            trade_id TEXT,
            blocked_reason TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_ws_signals_symbol_time ON ws_signals(symbol, time_ms);

        CREATE TABLE IF NOT EXISTS ws_trades (
            trade_id TEXT PRIMARY KEY,
            signal_id TEXT NOT NULL,
            account_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            strategy TEXT NOT NULL,
            exit_profile TEXT NOT NULL DEFAULT 'tx_on',
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
            funding_rate_entry REAL DEFAULT 0,
            premium_entry REAL DEFAULT 0,
            exit_time_ms INTEGER,
            exit_time TEXT,
            exit_price REAL,
            exit_reason TEXT,
            gross_pnl REAL DEFAULT 0,
            costs REAL DEFAULT 0,
            net_pnl REAL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_ws_trades_account_status ON ws_trades(account_id, status);
        CREATE INDEX IF NOT EXISTS idx_ws_trades_symbol_strategy ON ws_trades(symbol, strategy, status);

        CREATE TABLE IF NOT EXISTS ws_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_time_ms INTEGER NOT NULL,
            event_time TEXT NOT NULL,
            event_type TEXT NOT NULL,
            trade_id TEXT,
            account_id TEXT,
            symbol TEXT,
            strategy TEXT,
            bid REAL,
            ask REAL,
            mark_price REAL,
            spread_bps REAL,
            unrealized_pnl REAL DEFAULT 0,
            realized_pnl REAL DEFAULT 0,
            reason TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_ws_events_trade_time ON ws_events(trade_id, event_time_ms);
        """
    )
    ensure_column(conn, "ws_trades", "funding_rate_entry", "REAL DEFAULT 0")
    ensure_column(conn, "ws_trades", "premium_entry", "REAL DEFAULT 0")
    ensure_column(conn, "ws_trades", "exit_profile", "TEXT DEFAULT 'tx_on'")
    ensure_column(conn, "ws_accounts", "exit_profile", "TEXT DEFAULT 'tx_on'")
    ensure_column(conn, "ws_signals", "exit_profile", "TEXT DEFAULT 'tx_on'")
    if not get_state(conn, "started_at"):
        set_state(conn, "started_at", iso_ms(now_ms()))
    if strategy_mode == "curated":
        for symbol in symbols:
            for strategy in sorted(PORTFOLIO.get(symbol, set())):
                for profiled_strategy in strategy_exit_variants(strategy):
                    ensure_account_row(conn, symbol, profiled_strategy, account_equity)
    elif strategy_mode == "all":
        for symbol in symbols:
            for base in ALL_STRATEGY_BASES:
                for variant_name, *_ in STRATEGY_VARIANTS:
                    for exit_profile in EXIT_PROFILES:
                        ensure_account_row(conn, symbol, f"{base}__{variant_name}__{exit_profile}", account_equity)
    conn.commit()


def ensure_account_row(conn: sqlite3.Connection, symbol: str, strategy: str, account_equity: float) -> None:
    base, profile, exit_profile = split_strategy(strategy)
    conn.execute(
        """
        INSERT OR IGNORE INTO ws_accounts(
            account_id, symbol, strategy, variant_base, variant_profile, exit_profile, start_equity,
            status, created_at_ms, max_daily_trades, daily_loss_limit
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, 'active', ?, 3, -5.0)
        """,
        (account_id(symbol, strategy), symbol, strategy, base, profile, exit_profile, account_equity, now_ms()),
    )


def get_state(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM ws_state WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row else default


def set_state(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO ws_state(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def log_error(output_dir: Path, message: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "errors.log").open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now(timezone.utc).isoformat()} {message}\n")


class MinimalWebSocket:
    def __init__(self, url: str, timeout: float = 10.0):
        self.url = url
        self.timeout = timeout
        self.sock: ssl.SSLSocket | socket.socket | None = None

    def __enter__(self) -> "MinimalWebSocket":
        parsed = urlparse(self.url)
        host = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        raw = socket.create_connection((host, port), timeout=self.timeout)
        if parsed.scheme == "wss":
            raw = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
        raw.settimeout(self.timeout)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "User-Agent: bulk-ws-paper-worker/1.0\r\n"
            "\r\n"
        )
        raw.sendall(request.encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = raw.recv(4096)
            if not chunk:
                raise ConnectionError("websocket handshake closed")
            response += chunk
        header = response.decode("latin1", errors="replace")
        if " 101 " not in header.split("\r\n", 1)[0]:
            raise ConnectionError(f"websocket handshake failed: {header.splitlines()[0] if header else 'empty'}")
        self.sock = raw
        return self

    def __exit__(self, *_exc: object) -> None:
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None

    def _read_exact(self, n: int) -> bytes:
        assert self.sock is not None
        out = b""
        while len(out) < n:
            chunk = self.sock.recv(n - len(out))
            if not chunk:
                raise ConnectionError("websocket closed")
            out += chunk
        return out

    def send_text(self, payload: dict[str, Any]) -> None:
        self._send_frame(0x1, json.dumps(payload, separators=(",", ":")).encode("utf-8"))

    def send_pong(self, payload: bytes) -> None:
        self._send_frame(0xA, payload)

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        assert self.sock is not None
        mask_key = os.urandom(4)
        length = len(payload)
        head = bytearray([0x80 | opcode])
        if length < 126:
            head.append(0x80 | length)
        elif length < 65536:
            head.append(0x80 | 126)
            head.extend(struct.pack("!H", length))
        else:
            head.append(0x80 | 127)
            head.extend(struct.pack("!Q", length))
        masked = bytes(payload[i] ^ mask_key[i % 4] for i in range(length))
        self.sock.sendall(bytes(head) + mask_key + masked)

    def recv_json(self) -> dict[str, Any] | None:
        while True:
            first, second = self._read_exact(2)
            opcode = first & 0x0F
            masked = bool(second & 0x80)
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read_exact(8))[0]
            mask = self._read_exact(4) if masked else b""
            payload = self._read_exact(length)
            if masked:
                payload = bytes(payload[i] ^ mask[i % 4] for i in range(length))
            if opcode == 0x8:
                raise ConnectionError("websocket close frame")
            if opcode == 0x9:
                self.send_pong(payload)
                continue
            if opcode == 0xA:
                continue
            if opcode == 0x1:
                return json.loads(payload.decode("utf-8"))
            if opcode == 0x2:
                return None


def parse_book(message: dict[str, Any]) -> tuple[str, dict[str, float]] | None:
    return normalize_orderbook(message)


def parse_ticker(message: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    return normalize_ticker_message(message)


def candle_rows(message: dict[str, Any]) -> tuple[str, str, list[dict[str, Any]]]:
    topic = str(message.get("topic") or "")
    parts = topic.split(".")
    symbol = parts[1] if len(parts) > 1 else ""
    interval = parts[2] if len(parts) > 2 else ""
    data = message.get("data") or {}
    rows_ = data.get("candles") or data.get("candle") or []
    if isinstance(rows_, dict):
        rows_ = [rows_]
    return symbol, interval, rows_


def parse_public_trades(message: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    topic = str(message.get("topic") or "")
    symbol = topic.split(".")[1] if "." in topic else ""
    data = message.get("data") or {}
    trades = data.get("trades") or data.get("trade") or data
    if isinstance(trades, dict):
        trades = [trades]
    return symbol, trades if isinstance(trades, list) else []


class Worker:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.output_dir = Path(args.output_dir)
        self.conn = connect_db(self.output_dir / "paper_ws.sqlite")
        self.symbols = args.symbols
        init_db(self.conn, self.symbols, args.account_equity, args.strategy_mode)
        self.books: dict[str, dict[str, float]] = {}
        self.tickers: dict[str, dict[str, Any]] = {}
        self.premium_history: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=120))
        self.candles: dict[tuple[str, str], dict[int, Candle]] = defaultdict(dict)
        self.last_book_write: dict[str, int] = defaultdict(int)
        self.last_signal_candle: dict[str, int] = defaultdict(int)
        self.last_candle_message_ms = 0
        self.last_rest_candle_sync_ms = 0
        self.last_dashboard_ms = 0
        self.last_commit_ms = 0
        self.load_recent_candles()
        self.load_latest_market_state()

    def subscriptions(self) -> list[dict[str, Any]]:
        subs: list[dict[str, Any]] = []
        for symbol in self.symbols:
            subs.append({"type": "ticker", "symbol": symbol})
            subs.append({"type": "trades", "symbol": symbol})
            subs.append({"type": "l2Snapshot", "symbol": symbol, "nlevels": 10})
            subs.append({"type": "candle", "symbol": symbol, "interval": "5m"})
            subs.append({"type": "candle", "symbol": symbol, "interval": "15m"})
        return subs

    def load_recent_candles(self, limit_per_interval: int = 1500) -> None:
        for symbol in self.symbols:
            for interval in ("5m", "15m"):
                rows = self.conn.execute(
                    """
                    SELECT t, close_ms, o, h, l, c, v, n
                    FROM ws_candles
                    WHERE symbol = ? AND interval = ?
                    ORDER BY t DESC
                    LIMIT ?
                    """,
                    (symbol, interval, limit_per_interval),
                ).fetchall()
                bucket = self.candles[(symbol, interval)]
                for row in reversed(rows):
                    bucket[int(row["t"])] = Candle(
                        t=int(row["t"]),
                        T=int(row["close_ms"]),
                        o=safe_float(row["o"]),
                        h=safe_float(row["h"]),
                        l=safe_float(row["l"]),
                        c=safe_float(row["c"]),
                        v=safe_float(row["v"]),
                        n=safe_int(row["n"]),
                    )

    def load_latest_market_state(self, max_book_age_seconds: float = 900.0) -> None:
        cutoff_ms = now_ms() - int(max_book_age_seconds * 1000)
        for row in self.conn.execute("SELECT * FROM ws_tickers"):
            ticker = dict(row)
            symbol = str(ticker.get("symbol") or "")
            if not symbol:
                continue
            normalized = {
                "symbol": symbol,
                "markPrice": safe_float(ticker.get("mark_price")),
                "oraclePrice": safe_float(ticker.get("oracle_price")),
                "fundingRate": safe_float(ticker.get("funding_rate")),
                "openInterest": safe_float(ticker.get("open_interest")),
                "premium": safe_float(ticker.get("premium")),
                "hasFundingPremium": True,
            }
            self.tickers[symbol] = normalized
            self.premium_history[symbol].append(safe_float(normalized.get("premium")))
        for symbol in self.symbols:
            row = self.conn.execute(
                """
                SELECT *
                FROM ws_books
                WHERE symbol = ?
                ORDER BY time_ms DESC, id DESC
                LIMIT 1
                """,
                (symbol,),
            ).fetchone()
            if not row or int(row["time_ms"]) < cutoff_ms:
                continue
            self.books[symbol] = {
                "bid": safe_float(row["bid"]),
                "ask": safe_float(row["ask"]),
                "mid": safe_float(row["mid"]),
                "spread_bps": safe_float(row["spread_bps"], math.inf),
                "bid_depth": safe_float(row["bid_depth"]),
                "ask_depth": safe_float(row["ask_depth"]),
                "source_ts": str(row["source_ts"] or row["time_ms"]),
            }

    def fetch_rest_candles(self, symbol: str, interval: str) -> list[dict[str, Any]]:
        params = urlencode({"symbol": symbol, "interval": interval, "endTime": now_ms()})
        url = f"{self.args.rest_api_url.rstrip('/')}/klines?{params}"
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "BULK-Strategy-Lab/1.0",
            },
        )
        with urlopen(request, timeout=self.args.rest_timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload if isinstance(payload, list) else []

    def sync_rest_candles(self, force: bool = False) -> None:
        current_ms = now_ms()
        interval_ms = int(self.args.rest_candle_sync_seconds * 1000)
        if not force and current_ms - self.last_rest_candle_sync_ms < interval_ms:
            return
        self.last_rest_candle_sync_ms = current_ms
        stored_any = False
        for symbol in self.symbols:
            for interval in ("5m", "15m"):
                try:
                    rows_ = self.fetch_rest_candles(symbol, interval)
                except Exception as exc:
                    set_state(self.conn, "last_rest_candle_error", f"{symbol} {interval}: {exc}")
                    continue
                before = len(self.candles[(symbol, interval)])
                self.store_candles(symbol, interval, rows_, current_ms)
                stored_any = stored_any or len(self.candles[(symbol, interval)]) >= before
            self.evaluate_symbol(symbol)
        if stored_any:
            set_state(self.conn, "last_rest_candle_sync_at", iso_ms(current_ms))
            set_state(self.conn, "last_rest_candle_error", "")

    def evaluate_all_symbols(self) -> None:
        for symbol in self.symbols:
            self.evaluate_symbol(symbol)

    def run(self) -> None:
        set_state(self.conn, "status", "starting")
        self.conn.commit()
        deadline = None if self.args.duration_seconds <= 0 else time.time() + self.args.duration_seconds
        while True:
            try:
                self.consume(deadline)
                if deadline is not None and time.time() >= deadline:
                    break
            except Exception as exc:
                set_state(self.conn, "status", "reconnecting")
                set_state(self.conn, "last_error", str(exc))
                log_error(self.output_dir, f"{exc}\n{traceback.format_exc()}")
                self.conn.commit()
                if deadline is not None and time.time() >= deadline:
                    break
                time.sleep(self.args.reconnect_seconds)
        set_state(self.conn, "status", "stopped")
        self.conn.commit()
        self.write_dashboard(force=True)

    def consume(self, deadline: float | None) -> None:
        with MinimalWebSocket(self.args.ws_url, timeout=15) as ws:
            ws.send_text({"method": "subscribe", "subscription": self.subscriptions()})
            set_state(self.conn, "status", "running")
            set_state(self.conn, "last_error", "")
            set_state(self.conn, "last_connect_at", iso_ms(now_ms()))
            connect_ms = now_ms()
            self.last_candle_message_ms = 0
            self.sync_rest_candles(force=True)
            self.evaluate_all_symbols()
            self.conn.commit()
            while True:
                if deadline is not None and time.time() >= deadline:
                    return
                message = ws.recv_json()
                if not message:
                    continue
                self.handle_message(message)
                current_ms = now_ms()
                self.sync_rest_candles()
                if current_ms - self.last_commit_ms >= 1000:
                    self.conn.commit()
                    self.last_commit_ms = current_ms
                if current_ms - self.last_dashboard_ms >= self.args.dashboard_interval_seconds * 1000:
                    self.write_dashboard()
                    self.last_dashboard_ms = current_ms
                stale_after_ms = int(self.args.candle_stale_seconds * 1000)
                grace_ms = max(stale_after_ms, 60_000)
                if current_ms - connect_ms > grace_ms:
                    last_candle_ms = self.last_candle_message_ms or connect_ms
                    if current_ms - last_candle_ms > stale_after_ms:
                        raise ConnectionError(
                            f"candle stream stale for {(current_ms - last_candle_ms) / 1000:.1f}s; reconnecting"
                        )

    def handle_message(self, message: dict[str, Any]) -> None:
        msg_type = str(message.get("type") or "")
        ts_ms = now_ms()
        set_state(self.conn, "last_message_at", iso_ms(ts_ms))
        set_state(self.conn, "messages", int(safe_float(get_state(self.conn, "messages", "0"))) + 1)
        if msg_type == "ticker":
            parsed = parse_ticker(message)
            if parsed:
                self.tickers[parsed[0]] = parsed[1]
                if parsed[1].get("hasFundingPremium"):
                    self.premium_history[parsed[0]].append(safe_float(parsed[1].get("premium")))
                self.store_ticker(parsed[0], parsed[1], ts_ms)
        elif msg_type == "l2Snapshot":
            parsed_book = parse_book(message)
            if parsed_book:
                symbol, book = parsed_book
                self.books[symbol] = book
                self.store_book(symbol, book, ts_ms)
                self.mark_open(symbol, book, ts_ms)
                self.evaluate_symbol(symbol)
        elif msg_type == "trades":
            symbol, trades = parse_public_trades(message)
            self.store_public_trades(symbol, trades, ts_ms)
        elif msg_type == "candle":
            symbol, interval, rows_ = candle_rows(message)
            self.store_candles(symbol, interval, rows_, ts_ms)
            if interval == "5m" and symbol:
                self.evaluate_symbol(symbol)

    def store_book(self, symbol: str, book: dict[str, float], ts_ms: int) -> None:
        if ts_ms - self.last_book_write[symbol] < self.args.book_store_ms:
            return
        self.last_book_write[symbol] = ts_ms
        self.conn.execute(
            """
            INSERT INTO ws_books(time_ms, time, symbol, bid, ask, mid, spread_bps, bid_depth, ask_depth, source_ts)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                str(book.get("source_ts", "")),
            ),
        )

    def store_ticker(self, symbol: str, ticker: dict[str, Any], ts_ms: int) -> None:
        if not symbol:
            return
        self.conn.execute(
            """
            INSERT INTO ws_tickers(symbol, time_ms, time, mark_price, oracle_price, funding_rate, open_interest, premium, raw_json)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                time_ms=excluded.time_ms, time=excluded.time, mark_price=excluded.mark_price,
                oracle_price=excluded.oracle_price, funding_rate=excluded.funding_rate,
                open_interest=excluded.open_interest, premium=excluded.premium, raw_json=excluded.raw_json
            """,
            (
                symbol,
                ts_ms,
                iso_ms(ts_ms),
                safe_float(ticker.get("markPrice")),
                safe_float(ticker.get("oraclePrice")),
                safe_float(ticker.get("fundingRate")),
                safe_float(ticker.get("openInterest")),
                safe_float(ticker.get("premium")),
                json.dumps(ticker, separators=(",", ":")),
            ),
        )

    def store_public_trades(self, symbol: str, trades: list[dict[str, Any]], fallback_ms: int) -> None:
        if not symbol:
            return
        for trade in trades:
            px = safe_float(trade.get("px") or trade.get("price") or trade.get("p"))
            sz = safe_float(trade.get("sz") or trade.get("size") or trade.get("s"))
            t_ms = int(safe_float(trade.get("t") or trade.get("time") or trade.get("timestamp"), fallback_ms))
            if t_ms > 10**15:
                t_ms //= 1_000_000
            side = str(trade.get("side") or trade.get("S") or "")
            if px <= 0:
                continue
            self.conn.execute(
                """
                INSERT INTO ws_public_trades(time_ms, time, symbol, price, size, side, raw_json)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (t_ms, iso_ms(t_ms), symbol, px, sz, side, json.dumps(trade, separators=(",", ":"))),
            )

    def store_candles(self, symbol: str, interval: str, rows_: list[dict[str, Any]], ts_ms: int) -> None:
        if not symbol or interval not in {"5m", "15m"}:
            return
        bucket = self.candles[(symbol, interval)]
        stored = False
        for row in rows_:
            if not is_valid_ohlcv(row.get("o"), row.get("h"), row.get("l"), row.get("c"), row.get("v")):
                continue
            candle = Candle(
                t=safe_int(row.get("t")),
                T=safe_int(row.get("T"), safe_int(row.get("t"))),
                o=safe_float(row.get("o")),
                h=safe_float(row.get("h")),
                l=safe_float(row.get("l")),
                c=safe_float(row.get("c")),
                v=safe_float(row.get("v")),
                n=safe_int(row.get("n")),
            )
            if candle.t <= 0 or candle.c <= 0:
                continue
            bucket[candle.t] = candle
            stored = True
            self.conn.execute(
                """
                INSERT INTO ws_candles(symbol, interval, t, close_ms, o, h, l, c, v, n, updated_at_ms)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, interval, t) DO UPDATE SET
                    close_ms=excluded.close_ms, o=excluded.o, h=excluded.h, l=excluded.l, c=excluded.c,
                    v=excluded.v, n=excluded.n, updated_at_ms=excluded.updated_at_ms
                """,
                (symbol, interval, candle.t, candle.T, candle.o, candle.h, candle.l, candle.c, candle.v, candle.n, ts_ms),
            )
        while len(bucket) > 1500:
            oldest = min(bucket)
            del bucket[oldest]
        if stored:
            set_state(self.conn, f"last_{interval}_candle_at", iso_ms(ts_ms))
            if interval == "5m":
                self.last_candle_message_ms = ts_ms

    def evaluate_symbol(self, symbol: str) -> None:
        if symbol not in self.books:
            return
        if self.args.strategy_mode == "curated" and symbol not in PORTFOLIO:
            return
        book = self.books[symbol]
        five = sorted(self.candles[(symbol, "5m")].values(), key=lambda c: c.t)
        fifteen = sorted(self.candles[(symbol, "15m")].values(), key=lambda c: c.t)
        closed_five = [c for c in five if c.T <= now_ms()]
        closed_fifteen = [c for c in fifteen if c.T <= closed_five[-1].T] if closed_five else []
        if len(closed_five) < 230 or len(closed_fifteen) < 80:
            return
        latest = closed_five[-1]
        if latest.t <= self.last_signal_candle[symbol]:
            return
        self.last_signal_candle[symbol] = latest.t
        add_indicators(closed_five)
        attach_regime(closed_five, closed_fifteen)
        ticker = self.tickers.get(symbol, {})
        candidates = make_candidates(
            symbol,
            closed_five,
            ticker,
            self.premium_z(symbol),
            book["spread_bps"],
            self.args.signal_spread_bps,
            future_buffer=1,
            live_entry=True,
            exit_profiles=EXIT_PROFILES,
        )
        allowed = {item for strategy in PORTFOLIO.get(symbol, set()) for item in strategy_exit_variants(strategy)}
        for candidate in candidates:
            if candidate.entry_time != latest.T:
                continue
            if self.args.strategy_mode == "curated" and candidate.strategy not in allowed:
                continue
            self.record_candidate(candidate, book)

    def premium_z(self, symbol: str) -> float:
        values = list(self.premium_history.get(symbol, []))
        if len(values) < 20:
            return 0.0
        stdev = 0.0
        mean = sum(values) / len(values)
        if len(values) > 1:
            stdev = (sum((x - mean) ** 2 for x in values) / len(values)) ** 0.5
        return (values[-1] - mean) / (stdev or 1.0)

    def record_candidate(self, candidate: Any, book: dict[str, float]) -> None:
        sid = ws_signal_id(candidate.symbol, candidate.strategy, candidate.side, candidate.entry_time, candidate.reason)
        if self.conn.execute("SELECT 1 FROM ws_signals WHERE signal_id = ?", (sid,)).fetchone():
            return
        account = account_id(candidate.symbol, candidate.strategy)
        self.ensure_account(candidate.symbol, candidate.strategy)
        blocked_reason = ""
        status = "opened"
        tid = ws_trade_id(sid)
        if book["spread_bps"] > self.args.max_spread_bps:
            status = "blocked_by_spread"
            blocked_reason = "spread"
            tid = None
        elif self.has_open(account):
            status = "blocked_already_open"
            blocked_reason = "already_open"
            tid = None
        elif self.daily_trade_count(account) >= self.args.account_daily_limit:
            status = "blocked_daily_limit"
            blocked_reason = "daily_limit"
            tid = None
        elif self.account_realized_today(account) <= self.args.daily_loss_limit:
            status = "blocked_daily_loss"
            blocked_reason = "daily_loss"
            tid = None
        elif self.args.global_daily_limit > 0 and self.global_daily_count() >= self.args.global_daily_limit and not self.has_exit_profile_sibling_today(sid):
            status = "blocked_global_limit"
            blocked_reason = "global_limit"
            tid = None
        if not blocked_reason:
            try:
                self.open_trade(candidate, sid, tid or ws_trade_id(sid), account, book)
            except Exception as exc:
                status = "open_failed"
                blocked_reason = "open_error"
                tid = None
                log_error(Path(self.args.output_dir), f"open_trade failed for {sid}: {exc}\n{traceback.format_exc()}")
        self.conn.execute(
            """
            INSERT INTO ws_signals(signal_id, time_ms, time, symbol, strategy, exit_profile, side, status, reason, spread_bps, account_id, trade_id, blocked_reason)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sid,
                candidate.entry_time,
                iso_ms(candidate.entry_time),
                candidate.symbol,
                candidate.strategy,
                candidate.exit_profile,
                candidate.side,
                status,
                candidate.reason,
                book["spread_bps"],
                account,
                tid,
                blocked_reason,
            ),
        )

    def ensure_account(self, symbol: str, strategy: str) -> None:
        ensure_account_row(self.conn, symbol, strategy, self.args.account_equity)

    def has_open(self, account: str) -> bool:
        return bool(self.conn.execute("SELECT 1 FROM ws_trades WHERE account_id = ? AND status = 'open' LIMIT 1", (account,)).fetchone())

    def daily_trade_count(self, account: str) -> int:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        row = self.conn.execute("SELECT COUNT(*) c FROM ws_trades WHERE account_id = ? AND entry_time LIKE ?", (account, f"{day}%")).fetchone()
        return int(row["c"] if row else 0)

    def global_daily_count(self) -> int:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        rows = self.conn.execute("SELECT symbol, strategy, entry_time_ms FROM ws_trades WHERE entry_time LIKE ?", (f"{day}%",)).fetchall()
        unique_signals = set()
        for row in rows:
            base, profile, _exit_profile = split_strategy(str(row["strategy"]))
            unique_signals.add((str(row["symbol"]), base, profile, int(row["entry_time_ms"])))
        return len(unique_signals)

    def has_exit_profile_sibling_today(self, signal_id: str) -> bool:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        canonical = canonical_exit_signal_id(signal_id)
        rows = self.conn.execute("SELECT signal_id FROM ws_trades WHERE entry_time LIKE ?", (f"{day}%",)).fetchall()
        for row in rows:
            if canonical_exit_signal_id(str(row["signal_id"])) == canonical:
                return True
        return False

    def account_realized_today(self, account: str) -> float:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        row = self.conn.execute(
            "SELECT COALESCE(SUM(net_pnl), 0) pnl FROM ws_trades WHERE account_id = ? AND status = 'closed' AND exit_time LIKE ?",
            (account, f"{day}%"),
        ).fetchone()
        return safe_float(row["pnl"] if row else 0)

    def open_trade(self, candidate: Any, sid: str, tid: str, account: str, book: dict[str, float]) -> None:
        entry = executable_price(candidate.side, book, "entry")
        stop = entry * (1 - candidate.stop_pct) if candidate.side == "long" else entry * (1 + candidate.stop_pct)
        take = entry * (1 + candidate.tp_pct) if candidate.side == "long" else entry * (1 - candidate.tp_pct)
        ts = now_ms()
        notional = self.args.notional
        ticker = self.tickers.get(candidate.symbol, {})
        self.conn.execute(
            """
            INSERT INTO ws_trades(
                trade_id, signal_id, account_id, symbol, strategy, exit_profile, side, status, entry_time_ms, entry_time,
                entry_price, stop_price, tp_price, max_hold_minutes, notional, spread_bps_entry,
                funding_rate_entry, premium_entry
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tid,
                sid,
                account,
                candidate.symbol,
                candidate.strategy,
                candidate.exit_profile,
                candidate.side,
                ts,
                iso_ms(ts),
                entry,
                stop,
                take,
                candidate.max_hold,
                notional,
                book["spread_bps"],
                safe_float(ticker.get("fundingRate")),
                safe_float(ticker.get("premium")),
            ),
        )
        self.insert_event("trade_opened", ts, tid, account, candidate.symbol, candidate.strategy, book, 0.0, 0.0, candidate.reason)

    def insert_event(
        self,
        event_type: str,
        ts_ms: int,
        trade_id: str | None,
        account: str | None,
        symbol: str | None,
        strategy: str | None,
        book: dict[str, float] | None,
        unrealized: float,
        realized: float,
        reason: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO ws_events(event_time_ms, event_time, event_type, trade_id, account_id, symbol, strategy, bid, ask, mark_price, spread_bps, unrealized_pnl, realized_pnl, reason)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ts_ms,
                iso_ms(ts_ms),
                event_type,
                trade_id,
                account,
                symbol,
                strategy,
                safe_float(book.get("bid")) if book else None,
                safe_float(book.get("ask")) if book else None,
                safe_float(book.get("mid") if book else 0),
                safe_float(book.get("spread_bps")) if book else None,
                unrealized,
                realized,
                reason,
            ),
        )

    def mark_open(self, symbol: str, book: dict[str, float], ts_ms: int) -> None:
        open_rows = self.conn.execute("SELECT * FROM ws_trades WHERE symbol = ? AND status = 'open'", (symbol,)).fetchall()
        for trade in open_rows:
            mark = executable_price(trade["side"], book, "exit")
            gross = pnl_for(trade["side"], trade["entry_price"], mark, trade["notional"])
            costs = taker_round_trip_cost(trade["notional"])
            hold_minutes = max((ts_ms - trade["entry_time_ms"]) / 60000, 0.0)
            funding_pnl = funding_pnl_for(trade["side"], trade["notional"], safe_float(trade["funding_rate_entry"]), hold_minutes)
            net = gross + funding_pnl - costs
            self.insert_event("trade_marked", ts_ms, trade["trade_id"], trade["account_id"], symbol, trade["strategy"], book, net, 0.0, "book_mark")
            exit_reason = ""
            if trade["side"] == "long" and mark <= trade["stop_price"]:
                exit_reason = "stop_hit"
            elif trade["side"] == "short" and mark >= trade["stop_price"]:
                exit_reason = "stop_hit"
            elif trade["side"] == "long" and mark >= trade["tp_price"]:
                exit_reason = "take_profit_hit"
            elif trade["side"] == "short" and mark <= trade["tp_price"]:
                exit_reason = "take_profit_hit"
            elif trade["exit_profile"] == TIME_EXIT_ON and (ts_ms - trade["entry_time_ms"]) / 60000 >= safe_float(trade["max_hold_minutes"]):
                exit_reason = "time_exit"
            elif trade["exit_profile"] == TIME_EXIT_OFF and self.args.stale_position_hours > 0 and hold_minutes >= self.args.stale_position_hours * 60:
                exit_reason = "safety_exit"
            if exit_reason:
                exit_price = mark
                if exit_reason == "stop_hit":
                    exit_price = safe_float(trade["stop_price"])
                elif exit_reason == "take_profit_hit":
                    exit_price = safe_float(trade["tp_price"])
                realized_gross = pnl_for(trade["side"], trade["entry_price"], exit_price, trade["notional"])
                realized_funding = funding_pnl_for(trade["side"], trade["notional"], safe_float(trade["funding_rate_entry"]), hold_minutes)
                realized_net = realized_gross + realized_funding - costs
                self.close_trade(trade, book, ts_ms, exit_price, realized_gross, costs, realized_net, exit_reason)

    def close_trade(self, trade: sqlite3.Row, book: dict[str, float], ts_ms: int, exit_price: float, gross: float, costs: float, net: float, reason: str) -> None:
        self.conn.execute(
            """
            UPDATE ws_trades
            SET status='closed', exit_time_ms=?, exit_time=?, exit_price=?, exit_reason=?, gross_pnl=?, costs=?, net_pnl=?
            WHERE trade_id=?
            """,
            (ts_ms, iso_ms(ts_ms), exit_price, reason, gross, costs, net, trade["trade_id"]),
        )
        self.insert_event(reason, ts_ms, trade["trade_id"], trade["account_id"], trade["symbol"], trade["strategy"], book, 0.0, net, reason)
        self.insert_event("trade_closed", ts_ms, trade["trade_id"], trade["account_id"], trade["symbol"], trade["strategy"], book, 0.0, net, reason)

    def write_dashboard(self, force: bool = False) -> None:
        payload = build_payload(self.conn)
        path = Path(self.args.dashboard_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        tmp.replace(path)
        if force:
            self.conn.commit()


def latest_open_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT t.*, e.bid, e.ask, e.mark_price, e.spread_bps AS spread_bps_now, e.unrealized_pnl AS open_pnl
        FROM ws_trades t
        LEFT JOIN ws_events e ON e.event_id = (
            SELECT event_id FROM ws_events
            WHERE trade_id = t.trade_id AND event_type='trade_marked'
            ORDER BY event_time_ms DESC, event_id DESC LIMIT 1
        )
        WHERE t.status='open'
        ORDER BY t.entry_time_ms DESC
        """
    ).fetchall()
    out = [dict(row) for row in rows]
    ts = now_ms()
    for row in out:
        row["hold_minutes"] = round(max((ts - safe_float(row.get("entry_time_ms"))) / 60000, 0.0), 2)
    return out


def build_payload(conn: sqlite3.Connection) -> dict[str, Any]:
    generated_ms = now_ms()
    accounts = [dict(r) for r in conn.execute("SELECT * FROM ws_accounts ORDER BY symbol, strategy")]
    open_rows = latest_open_rows(conn)
    closed_rows = [dict(r) for r in conn.execute("SELECT * FROM ws_trades WHERE status='closed' ORDER BY exit_time_ms DESC LIMIT 300")]
    for row in closed_rows:
        row["hold_minutes"] = round(max((safe_float(row.get("exit_time_ms")) - safe_float(row.get("entry_time_ms"))) / 60000, 0.0), 2)
    signals = [dict(r) for r in conn.execute("SELECT * FROM ws_signals ORDER BY time_ms DESC LIMIT 300")]
    latest_books = [
        dict(r)
        for r in conn.execute(
            """
            SELECT * FROM ws_books b
            WHERE id = (SELECT id FROM ws_books WHERE symbol=b.symbol ORDER BY time_ms DESC, id DESC LIMIT 1)
            ORDER BY symbol
            """
        )
    ]
    account_rows = []
    for account in accounts:
        acc_id = account["account_id"]
        closed = conn.execute("SELECT COALESCE(SUM(net_pnl),0) pnl, COUNT(*) n, SUM(CASE WHEN net_pnl>0 THEN 1 ELSE 0 END) wins FROM ws_trades WHERE account_id=? AND status='closed'", (acc_id,)).fetchone()
        closed_meta = conn.execute(
            """
            SELECT
                COALESCE(AVG((exit_time_ms - entry_time_ms) / 60000.0), 0) avg_hold_minutes,
                SUM(CASE WHEN exit_reason='time_exit' THEN 1 ELSE 0 END) time_exits,
                SUM(CASE WHEN exit_reason='safety_exit' THEN 1 ELSE 0 END) safety_exits,
                SUM(CASE WHEN exit_reason IN ('stop_hit', 'stop') THEN 1 ELSE 0 END) stop_exits,
                SUM(CASE WHEN exit_reason IN ('take_profit_hit', 'take_profit') THEN 1 ELSE 0 END) take_exits
            FROM ws_trades
            WHERE account_id=? AND status='closed'
            """,
            (acc_id,),
        ).fetchone()
        open_pnl = sum(safe_float(row.get("open_pnl")) for row in open_rows if row.get("account_id") == acc_id)
        gross_win = conn.execute("SELECT COALESCE(SUM(net_pnl),0) v FROM ws_trades WHERE account_id=? AND status='closed' AND net_pnl>0", (acc_id,)).fetchone()["v"]
        gross_loss = conn.execute("SELECT COALESCE(-SUM(net_pnl),0) v FROM ws_trades WHERE account_id=? AND status='closed' AND net_pnl<0", (acc_id,)).fetchone()["v"]
        realized = safe_float(closed["pnl"])
        closed_n = int(closed["n"] or 0)
        wins = int(closed["wins"] or 0)
        pf = safe_float(gross_win) / safe_float(gross_loss) if safe_float(gross_loss) else (999 if safe_float(gross_win) else 0)
        account_rows.append({
            **account,
            "closed_trades": closed_n,
            "open_positions": sum(1 for row in open_rows if row.get("account_id") == acc_id),
            "realized_pnl": realized,
            "open_pnl": open_pnl,
            "equity": safe_float(account["start_equity"]) + realized + open_pnl,
            "win_rate": (wins / closed_n * 100) if closed_n else 0,
            "profit_factor": pf,
            "avg_hold_minutes": safe_float(closed_meta["avg_hold_minutes"] if closed_meta else 0),
            "time_exits": int(closed_meta["time_exits"] or 0) if closed_meta else 0,
            "safety_exits": int(closed_meta["safety_exits"] or 0) if closed_meta else 0,
            "stop_exits": int(closed_meta["stop_exits"] or 0) if closed_meta else 0,
            "take_exits": int(closed_meta["take_exits"] or 0) if closed_meta else 0,
        })
    last_message_ms = 0
    last_message_at = get_state(conn, "last_message_at", "")
    try:
        if last_message_at:
            last_message_ms = int(datetime.fromisoformat(last_message_at).timestamp() * 1000)
    except ValueError:
        last_message_ms = 0
    age = (generated_ms - last_message_ms) / 1000 if last_message_ms else 999999
    return {
        "generatedAt": iso_ms(generated_ms),
        "startedAt": get_state(conn, "started_at", ""),
        "lastMessageAt": last_message_at,
        "lastMessageAgeSeconds": round(max(age, 0), 1),
        "health": "LIVE" if age < 10 else ("STALE" if age < 60 else "OFFLINE"),
        "status": get_state(conn, "status", "unknown"),
        "messages": int(safe_float(get_state(conn, "messages", "0"))),
        "lastError": get_state(conn, "last_error", ""),
        "last5mCandleAt": get_state(conn, "last_5m_candle_at", ""),
        "last15mCandleAt": get_state(conn, "last_15m_candle_at", ""),
        "lastRestCandleSyncAt": get_state(conn, "last_rest_candle_sync_at", ""),
        "lastRestCandleError": get_state(conn, "last_rest_candle_error", ""),
        "accounts": account_rows,
        "open": open_rows,
        "closed": closed_rows,
        "signals": signals,
        "books": latest_books,
        "tickers": [dict(r) for r in conn.execute("SELECT * FROM ws_tickers ORDER BY symbol")],
        "publicTrades": [dict(r) for r in conn.execute("SELECT * FROM ws_public_trades ORDER BY time_ms DESC LIMIT 200")],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="BULK read-only WebSocket paper worker.")
    parser.add_argument("--ws-url", default=WS_URL)
    parser.add_argument("--rest-api-url", default=REST_API_URL)
    parser.add_argument("--symbols", default="")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--dashboard-json", default=str(DEFAULT_DASHBOARD_JSON))
    parser.add_argument("--account-equity", type=float, default=DEFAULT_ACCOUNT_EQUITY)
    parser.add_argument("--strategy-mode", choices=["curated", "all"], default="curated")
    parser.add_argument("--notional", type=float, default=100.0)
    parser.add_argument("--max-spread-bps", type=float, default=12.0)
    parser.add_argument("--signal-spread-bps", type=float, default=1000.0)
    parser.add_argument("--account-daily-limit", type=int, default=3)
    parser.add_argument("--global-daily-limit", type=int, default=40)
    parser.add_argument("--daily-loss-limit", type=float, default=-5.0)
    parser.add_argument("--stale-position-hours", type=float, default=24.0)
    parser.add_argument("--candle-stale-seconds", type=float, default=480.0)
    parser.add_argument("--rest-candle-sync-seconds", type=float, default=60.0)
    parser.add_argument("--rest-timeout-seconds", type=float, default=4.0)
    parser.add_argument("--book-store-ms", type=int, default=1000)
    parser.add_argument("--dashboard-interval-seconds", type=int, default=5)
    parser.add_argument("--duration-seconds", type=float, default=0.0)
    parser.add_argument("--reconnect-seconds", type=float, default=3.0)
    args = parser.parse_args()
    if args.symbols:
        args.symbols = [x.strip() for x in args.symbols.split(",") if x.strip()]
    else:
        args.symbols = PRIMARY_SYMBOLS + SECONDARY_SYMBOLS
    return args


def main() -> int:
    args = parse_args()
    worker = Worker(args)
    worker.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
