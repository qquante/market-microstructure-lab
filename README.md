# Market Microstructure Lab

A Python research workspace for order-book normalization, backtesting and paper-market monitoring.

## Features

- Order-book normalization rejects invalid, empty and crossed inputs.
- Indicator/backtest helpers align closed timeframes and exit conditions.
- Paper monitoring and a local dashboard expose research state.

## Stack

Python · SQLite · Redis RESP client · WebSocket · JavaScript/HTML dashboard

SQLite persists paper state; Redis channels can publish updates. Large historical exports and downloaded Rust SDK repositories are excluded.

## Getting started

Start with python -m unittest discover -s tests -v. Live feeds and dashboard commands require separately configured local data/services.

## How it works

```text
Market observations → normalization/features → paper/backtest logic → SQLite and optional Redis → dashboard
```

## Current limitations

Paper trading and historical simulations are not live trading or profitability evidence. Downloaded Rust SDK code is not represented as original implementation in this repository.

## Tests

```console
python -m unittest discover -s tests -v
```

These checks use offline fixtures or controlled backends.

## License

No open-source license has been granted. Redistribution terms remain to be defined.

## Offline validation

The Python modules use the standard library. Run the fixture suite without starting live feeds:

```console
python -m unittest discover -s tests -v
```
