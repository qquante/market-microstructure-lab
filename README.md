# Market Microstructure Lab

> **Local private review:** publication is paused. See [security review and remaining release conditions](docs/SECURITY_REVIEW.md). Local fixes have not been pushed.


A Python research workspace for order-book normalization, backtesting and paper-market monitoring.

![review](https://img.shields.io/badge/review-private-374151) ![primary](https://img.shields.io/badge/primary-Python-2563EB) [![LinkedIn](https://img.shields.io/badge/LinkedIn-Douglas%20Silva-0A66C2?logo=linkedin&logoColor=white)](https://www.linkedin.com/in/douglas-silva-data-analyst/)

[Overview](#overview) · [Architecture](#architecture) · [Setup](#setup) · [Validation](#validation) · [Roadmap](#roadmap) · [Review notes](docs/REVIEW.md)

## Overview

- Order-book normalization rejects invalid, empty and crossed inputs.
- Indicator/backtest helpers align closed timeframes and exit conditions.
- Paper monitoring and a local dashboard expose research state.

**Technology:** Python · SQLite · Redis RESP client · WebSocket · JavaScript/HTML dashboard

**Persistence and data:** SQLite persists paper state; Redis channels can publish updates. Large historical exports and downloaded Rust SDK repositories are excluded.

## Architecture

```text
Market observations → normalization/features → paper/backtest logic → SQLite and optional Redis → dashboard
```

| Location | Review focus |
| --- | --- |
| [`bulk_market_data.py`](bulk_market_data.py) | Source/configuration inspected for scope and structure |
| [`bulk_micro_scalper_backtest.py`](bulk_micro_scalper_backtest.py) | Source/configuration inspected for scope and structure |
| [`paper_live_monitor.py`](paper_live_monitor.py) | Source/configuration inspected for scope and structure |
| [`bulk_redis.py`](bulk_redis.py) | Source/configuration inspected for scope and structure |
| [`tests/test_bulk_ready.py`](tests/test_bulk_ready.py) | Source/configuration inspected for scope and structure |

Internal package and module names are retained where changing them would affect imports or compatibility. This descriptive repository name does not imply a completed package/API migration.

## Setup

Start with python -m unittest discover -s tests -v. Live feeds and dashboard commands require separately configured local data/services.

Start from an isolated environment. Use `.env.example` when present and inspect the configuration loader: an example file is not necessarily loaded automatically. Runtime databases, real account sessions and private credentials are not bundled. Live integration prerequisites are separate from local tests.

## Validation

Static parsing checked 9 Python files and 3 strict JSON files. Python syntax errors: 0; JSON errors: 0. The selected offline suite passed.

```console
python -m unittest discover -s tests -v
```

These results apply to the selected checks on the preparation copy. They do not establish deployment readiness, full coverage, live provider availability or measured performance. CI definitions, when present under `docs/ci-templates/`, are inactive review templates; no remote CI badge is claimed.

## Limits and current status

Paper trading and historical simulations are not live trading or profitability evidence. Downloaded Rust SDK code is not represented as original implementation in this repository.

This private snapshot is for code and portfolio review. Generated runtime material and unreviewed binary assets may be excluded. [Review notes](docs/REVIEW.md) record preparation scope, findings and outstanding validation.

## Roadmap

- [ ] Pin research dependencies and add a minimal fixture dataset.
- [ ] Document fees, fills and look-ahead assumptions in backtests.
- [ ] Review live-feed and dashboard authentication separately.

## History and attribution

No recoverable original Git history was verified for this snapshot. Import and preparation commits use their actual current dates; filesystem timestamps are not treated as development dates.

No open-source license is granted by this preparation. A license and attribution review is required before public redistribution.

## Maintainer and feedback

Douglas de Oliveira Silva · [GitHub](https://github.com/qquante) · [LinkedIn](https://www.linkedin.com/in/douglas-silva-data-analyst/)

For this private review, report reproducible issues with a minimal fixture and the exact check output. Keep account data, credentials and generated sensitive reports out of issues and commits. Existing contribution guidelines apply where present.

**Topics:** `python`, `sqlite`, `redis`, `market-data`, `backtesting`, `paper-trading`, `data-analysis`
