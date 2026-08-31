from __future__ import annotations

import math
import sqlite3
import tempfile
import time
import unittest
from types import SimpleNamespace

from bulk_market_data import normalize_orderbook, safe_float
from bulk_micro_scalper_backtest import Candle, Candidate, TIME_EXIT_OFF, TIME_EXIT_ON, add_indicators, attach_regime, rsi, simulate_trade
from paper_live_monitor import close_reason, init_db, mark_and_maybe_close
from websocket_paper_worker import EXIT_PROFILES, PORTFOLIO, Worker, init_db as init_ws_db, parse_book, parse_ticker, split_strategy


def candle(t: int, close: float = 100.0) -> Candle:
    return Candle(t=t, T=t + 300_000, o=close, h=close + 1, l=close - 1, c=close, v=10)


class BulkReadyTests(unittest.TestCase):
    def test_safe_float_rejects_nan_and_inf(self) -> None:
        self.assertEqual(safe_float(math.nan, 7.0), 7.0)
        self.assertEqual(safe_float(math.inf, 7.0), 7.0)

    def test_rsi_flat_series_is_neutral(self) -> None:
        values = [100.0] * 20
        self.assertEqual(rsi(values, 14)[14], 50.0)
        self.assertEqual(rsi(values, 14)[19], 50.0)

    def test_macd_signal_starts_after_valid_macd_values(self) -> None:
        candles = [candle(i * 300_000, 100 + i) for i in range(40)]
        add_indicators(candles)
        self.assertIsNone(candles[32].ind["macd_signal"])
        self.assertIsNotNone(candles[33].ind["macd_signal"])

    def test_regime_uses_only_closed_15m_candles(self) -> None:
        five = [candle(300_000), candle(600_000), candle(900_000)]
        fifteen = [
            Candle(t=0, T=900_000, o=100, h=101, l=99, c=100, v=10, ind={"marker": 1.0}),
            Candle(t=900_000, T=1_800_000, o=101, h=102, l=100, c=101, v=10, ind={"marker": 2.0}),
        ]
        attach_regime(five, fifteen)
        self.assertEqual(five[0].regime, {})
        self.assertEqual(five[1].regime.get("marker"), 1.0)
        self.assertEqual(five[2].regime.get("marker"), 1.0)

    def test_orderbook_normalization_rejects_empty_or_crossed_books(self) -> None:
        self.assertIsNone(normalize_orderbook({"levels": [[], []]}, "BTC-USD"))
        self.assertIsNone(normalize_orderbook({"levels": [[[101, 1]], [[100, 1]]]}, "BTC-USD"))
        parsed = normalize_orderbook({"levels": [[[100, 2]], [[101, 3]]]}, "BTC-USD")
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed[0], "BTC-USD")
        self.assertEqual(parsed[1]["bid"], 100.0)

    def test_websocket_parsers_keep_payload_symbol_without_topic(self) -> None:
        book = parse_book({"type": "l2Snapshot", "data": {"book": {"symbol": "BTC-USD", "levels": [[[100, 1]], [[101, 1]]]}}})
        self.assertIsNotNone(book)
        assert book is not None
        self.assertEqual(book[0], "BTC-USD")
        ticker = parse_ticker({"type": "ticker", "data": {"ticker": {"symbol": "ETH-USD", "markPrice": 100, "oraclePrice": 99, "fundingRate": 0.001, "openInterest": 10}}})
        self.assertIsNotNone(ticker)
        assert ticker is not None
        self.assertEqual(ticker[0], "ETH-USD")
        self.assertAlmostEqual(ticker[1]["premium"], (100 - 99) / 99)

    def test_backtest_time_exit_profiles_diverge(self) -> None:
        candles = [candle(i * 300_000, 100.0) for i in range(6)]
        base = dict(
            symbol="BTC-USD",
            strategy="vwap_reversion__s025_t020",
            side="long",
            entry_idx=0,
            entry_time=candles[0].t,
            entry_price=100.0,
            stop_pct=0.5,
            tp_pct=0.5,
            max_hold=1,
            liquidity="maker",
            score=1.0,
            reason="test",
        )
        tx_on = simulate_trade(Candidate(**base, exit_profile=TIME_EXIT_ON), candles, 100.0, 0.0)
        tx_off_base = {**base, "strategy": "vwap_reversion__s025_t020__tx_off"}
        tx_off = simulate_trade(Candidate(**tx_off_base, exit_profile=TIME_EXIT_OFF), candles, 100.0, 0.0)
        self.assertEqual(tx_on.exit_reason, "time_exit")
        self.assertEqual(tx_on.exit_time, candles[1].T)
        self.assertEqual(tx_off.exit_reason, "end_of_sample")
        self.assertEqual(tx_off.exit_time, candles[-1].T)

    def test_websocket_strategy_split_reads_exit_profile(self) -> None:
        self.assertEqual(split_strategy("rsi50_trend_pullback__s030_t025__tx_off"), ("rsi50_trend_pullback", "s030_t025", "tx_off"))
        self.assertEqual(split_strategy("rsi50_trend_pullback__s030_t025"), ("rsi50_trend_pullback", "s030_t025", "tx_on"))

    def test_websocket_init_creates_time_exit_replicas_for_curated(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        init_ws_db(conn, ["BTC-USD"], 100.0, "curated")
        expected = len(PORTFOLIO["BTC-USD"]) * len(EXIT_PROFILES)
        rows = conn.execute("SELECT strategy, exit_profile FROM ws_accounts WHERE symbol='BTC-USD'").fetchall()
        self.assertEqual(len(rows), expected)
        self.assertEqual({row["exit_profile"] for row in rows}, set(EXIT_PROFILES))

    def test_websocket_open_trade_inserts_row_with_exit_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = SimpleNamespace(
                output_dir=tmp,
                symbols=["BTC-USD"],
                account_equity=100.0,
                strategy_mode="curated",
                notional=100.0,
                book_store_ms=0,
            )
            worker = Worker(args)
            candidate = Candidate(
                symbol="BTC-USD",
                strategy="candle_turn_momentum__s025_t020__tx_off",
                side="long",
                entry_idx=0,
                entry_time=1000,
                entry_price=100.0,
                stop_pct=0.01,
                tp_pct=0.02,
                max_hold=5,
                liquidity="taker",
                score=1.0,
                reason="test__tx_off",
                exit_profile=TIME_EXIT_OFF,
            )
            worker.open_trade(candidate, "sig1", "trade1", "BTC-USD|candle_turn_momentum__s025_t020__tx_off", {"bid": 100.0, "ask": 101.0, "mid": 100.5, "spread_bps": 1.0})
            row = worker.conn.execute("SELECT * FROM ws_trades WHERE trade_id='trade1'").fetchone()
            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(row["exit_profile"], "tx_off")
            self.assertEqual(row["status"], "open")
            worker.conn.close()

    def test_websocket_worker_bootstraps_candles_from_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = SimpleNamespace(
                output_dir=tmp,
                symbols=["BTC-USD"],
                account_equity=100.0,
                strategy_mode="curated",
                notional=100.0,
                book_store_ms=0,
            )
            first = Worker(args)
            first.store_candles(
                "BTC-USD",
                "5m",
                [{"t": 1000, "T": 301000, "o": 100, "h": 101, "l": 99, "c": 100, "v": 10, "n": 1}],
                400000,
            )
            first.store_ticker(
                "BTC-USD",
                {"markPrice": 101.0, "oraclePrice": 100.0, "fundingRate": 0.001, "openInterest": 10.0, "premium": 0.01},
                400000,
            )
            first.store_book("BTC-USD", {"bid": 100.0, "ask": 101.0, "mid": 100.5, "spread_bps": 1.0, "bid_depth": 10.0, "ask_depth": 10.0}, int(time.time() * 1000))
            first.conn.commit()
            first.conn.close()
            second = Worker(args)
            self.assertIn(1000, second.candles[("BTC-USD", "5m")])
            self.assertIn("BTC-USD", second.tickers)
            self.assertIn("BTC-USD", second.books)
            second.conn.close()

    def test_rest_paper_close_reason_includes_time_exit(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT 'long' AS side, 95.0 AS stop_price, 105.0 AS tp_price,
                   1000 AS entry_time_ms, 5.0 AS max_hold_minutes
            """
        ).fetchone()
        self.assertEqual(close_reason(row, 100.0, 1000 + 5 * 60_000), "time_exit")

    def test_rest_paper_time_exit_persists_closed_trade_with_funding(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        init_db(conn)
        conn.execute(
            """
            INSERT INTO trades(
                trade_id, signal_id, symbol, strategy, side, status, entry_time_ms, entry_time,
                entry_price, stop_price, tp_price, max_hold_minutes, notional, spread_bps_entry,
                reason, candidate_liquidity, funding_rate_entry, premium_entry
            )
            VALUES('t1', 's1', 'BTC-USD', 'vwap_reversion__s025_t020', 'long', 'open',
                   1000, '1970-01-01T00:00:01+00:00', 100.0, 95.0, 105.0, 5.0,
                   100.0, 1.0, 'test', 'maker', 0.008, 0.001)
            """
        )
        trade = conn.execute("SELECT * FROM trades WHERE trade_id='t1'").fetchone()
        mark_and_maybe_close(conn, trade, {"bid": 100.0, "ask": 101.0, "mid": 100.5, "spread_bps": 1.0}, 1000 + 5 * 60_000)
        closed = conn.execute("SELECT * FROM closed_trades WHERE trade_id='t1'").fetchone()
        self.assertIsNotNone(closed)
        assert closed is not None
        self.assertEqual(closed["exit_reason"], "time_exit")
        self.assertNotEqual(closed["funding_pnl"], 0.0)


if __name__ == "__main__":
    unittest.main()
