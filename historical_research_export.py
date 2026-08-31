#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DEFAULT_INPUT = ROOT / "backtests"
DEFAULT_OUTPUT = ROOT / "dashboard" / "historical_data.json"


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def base_strategy(strategy: str) -> str:
    return strategy.split("__", 1)[0]


def enrich_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    strategy = str(out.get("strategy") or out.get("best_strategy") or out.get("group") or "")
    if "|" in strategy:
        strategy = strategy.split("|", 1)[1]
    out["variant_base"] = base_strategy(strategy)
    out["variant_profile"] = strategy.split("__", 1)[1] if "__" in strategy else "base"
    for key in [
        "cap",
        "trades",
        "closed_trades",
        "max_losing_streak",
    ]:
        if key in out:
            out[key] = safe_int(out[key])
    for key in [
        "net_pnl",
        "gross_pnl",
        "costs",
        "funding_pnl",
        "win_rate",
        "profit_factor",
        "max_drawdown",
        "avg_trade",
        "positive_day_rate",
        "research_score",
        "hold_minutes",
        "entry_price",
        "exit_price",
        "notional",
        "spread_bps_snapshot",
    ]:
        if key in out:
            out[key] = safe_float(out[key])
    return out


def main_filter(rows: list[dict[str, str]], cap: int, scenario: str) -> list[dict[str, Any]]:
    return [
        enrich_row(row)
        for row in rows
        if safe_int(row.get("cap")) == cap and str(row.get("scenario")) == scenario
    ]


def cumulative_equity(rows: list[dict[str, Any]], key: str) -> dict[str, list[list[Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row[key])].append(row)
    curves: dict[str, list[list[Any]]] = {}
    for name, items in grouped.items():
        pnl = 0.0
        curve: list[list[Any]] = []
        for row in sorted(items, key=lambda x: safe_int(x.get("exit_time_ms"))):
            pnl += safe_float(row.get("net_pnl"))
            curve.append([row.get("exit_time"), round(pnl, 6)])
        curves[name] = curve
    return curves


def split_pair_strategy(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        copy = dict(row)
        group = str(copy.get("group", ""))
        if "|" in group:
            copy["symbol"], copy["strategy"] = group.split("|", 1)
        copy["variant_base"] = base_strategy(str(copy.get("strategy", "")))
        copy["variant_profile"] = str(copy.get("strategy", "")).split("__", 1)[1] if "__" in str(copy.get("strategy", "")) else "base"
        out.append(enrich_row(copy))
    return out


def build_payload(input_dir: Path, cap: int, scenario: str) -> dict[str, Any]:
    coverage = [enrich_row(row) for row in read_csv(input_dir / "data_coverage.csv")]
    best = main_filter(read_csv(input_dir / "best_strategy_by_pair.csv"), cap, scenario)
    by_pair = main_filter(read_csv(input_dir / "by_pair.csv"), cap, scenario)
    by_strategy = main_filter(read_csv(input_dir / "by_strategy.csv"), cap, scenario)
    pair_strategy = split_pair_strategy(main_filter(read_csv(input_dir / "by_pair_strategy.csv"), cap, scenario))
    tradeable = main_filter(read_csv(input_dir / "tradeable_rank.csv"), cap, scenario)
    daily = main_filter(read_csv(input_dir / "daily_summary.csv"), cap, scenario)
    trades = main_filter(read_csv(input_dir / "trades.csv"), cap, scenario)
    trades.sort(key=lambda row: safe_int(row.get("entry_time_ms")))

    by_symbol_strategy: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in trades:
        by_symbol_strategy[f"{row.get('symbol')}|{row.get('strategy')}"].append(row)

    return {
        "generatedAt": now_iso(),
        "sourceDir": str(input_dir),
        "cap": cap,
        "scenario": scenario,
        "coverage": coverage,
        "best": sorted(best, key=lambda row: safe_float(row.get("net_pnl")), reverse=True),
        "byPair": sorted(by_pair, key=lambda row: safe_float(row.get("net_pnl")), reverse=True),
        "byStrategy": sorted(by_strategy, key=lambda row: safe_float(row.get("net_pnl")), reverse=True),
        "pairStrategy": sorted(pair_strategy, key=lambda row: safe_float(row.get("net_pnl")), reverse=True),
        "tradeable": sorted(tradeable, key=lambda row: safe_float(row.get("research_score")), reverse=True),
        "daily": daily,
        "trades": trades,
        "equityByPairStrategy": cumulative_equity(
            [{**row, "pair_strategy": f"{row.get('symbol')}|{row.get('strategy')}"} for row in trades],
            "pair_strategy",
        ),
        "equityBySymbol": cumulative_equity(trades, "symbol"),
        "tradeCounts": {
            key: len(value)
            for key, value in by_symbol_strategy.items()
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export historical backtest CSVs for the dashboard.")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cap", type=int, default=40)
    parser.add_argument("--scenario", default="phase1_tier1")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = build_payload(args.input_dir, args.cap, args.scenario)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {args.output_json} with {len(payload['trades'])} trades.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
