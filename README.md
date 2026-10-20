# Market Microstructure Lab

![Language: Python](https://img.shields.io/badge/Language-Python-3776AB?style=flat-square&logo=python) ![Mode: Paper research](https://img.shields.io/badge/Mode-Paper%20research-555555?style=flat-square) [![License: MIT](https://img.shields.io/badge/License-MIT-555555?style=flat-square)](LICENSE)

A Python research workspace for order-book normalization, closed-timeframe features, backtests and persisted paper-market state.

Its useful engineering boundary is consistency: the same observations need to mean the same thing in a historical calculation, a WebSocket worker and a dashboard. The repository includes regressions for those boundaries instead of relying only on a headline backtest result.

## Research invariants covered by the tests

- Non-finite numeric input is rejected or replaced by an explicit fallback.
- Empty or crossed order books do not become valid snapshots.
- Higher-timeframe context uses closed candles, avoiding access to an unfinished future window.
- `tx_on` and `tx_off` profiles preserve different time-exit behavior.
- Paper positions and strategy replicas retain their exit profile in SQLite.

These cases are visible in [test_bulk_ready.py](tests/test_bulk_ready.py).

## Try the normalizer offline

```console
python -c "from bulk_market_data import normalize_orderbook; print(normalize_orderbook({'levels': [[], []]}, 'BTC-USD'))"
python -m unittest discover -s tests -v
```

The first command returns `None` for the synthetic empty book. It uses no feed or trading account.

## Research and paper surfaces

| Surface | Implementation |
| --- | --- |
| Market-data normalization | [bulk_market_data.py](bulk_market_data.py) |
| Indicators, alignment and simulated exits | [bulk_micro_scalper_backtest.py](bulk_micro_scalper_backtest.py) |
| Polling-based paper positions | [paper_live_monitor.py](paper_live_monitor.py) |
| WebSocket paper worker | [websocket_paper_worker.py](websocket_paper_worker.py) |
| Dashboard data generation | [dashboard/build_dashboard_data.py](dashboard/build_dashboard_data.py) |

Core research modules use Python's standard library; feeds, datasets and optional Redis connectivity are separate operational inputs. A paper account is simulated state. Results from these paths do not establish live fill quality or profitability, and excluded third-party SDK downloads are not represented as original implementation.

**Technology:** Python · SQLite · Redis RESP client · WebSocket · JavaScript/HTML dashboard

Project-authored source: [MIT](LICENSE). Third-party components retain their own notices and terms.
