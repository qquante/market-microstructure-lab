#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RUNS = {
    "conservative": ROOT / "backtests",
    "spread20": ROOT / "backtests_spread20",
}
MAIN_CAP = "40"
MAIN_SCENARIO = "phase1_tier1"


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def f(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def i(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def r(value: Any, digits: int = 6) -> float:
    return round(f(value), digits)


def day_from_ms(ms: str) -> str:
    return datetime.fromtimestamp(i(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def split_strategy_name(strategy: str) -> tuple[str, str, str]:
    parts = strategy.split("__") if strategy else []
    if parts and parts[-1] in {"tx_on", "tx_off"}:
        exit_profile = parts[-1]
        parts = parts[:-1]
    else:
        exit_profile = "tx_on"
    if not parts:
        return "", "", exit_profile
    if len(parts) == 1:
        return parts[0], "base", exit_profile
    return parts[0], "__".join(parts[1:]), exit_profile


def compact_metric(row: dict[str, str]) -> dict[str, Any]:
    return {
        "cap": i(row.get("cap")),
        "scenario": row.get("scenario", ""),
        "group": row.get("group", ""),
        "trades": i(row.get("trades")),
        "net_pnl": r(row.get("net_pnl")),
        "gross_pnl": r(row.get("gross_pnl")),
        "costs": r(row.get("costs")),
        "win_rate": r(row.get("win_rate"), 2),
        "profit_factor": r(row.get("profit_factor"), 4),
        "max_drawdown": r(row.get("max_drawdown")),
        "max_losing_streak": i(row.get("max_losing_streak")),
        "avg_trade": r(row.get("avg_trade")),
        "avg_hold_minutes": r(row.get("avg_hold_minutes"), 2),
        "time_exits": i(row.get("time_exits")),
        "safety_exits": i(row.get("safety_exits")),
        "sample_confidence": row.get("sample_confidence", ""),
        "readiness": row.get("readiness", ""),
    }


def build_run(run_id: str, folder: Path) -> dict[str, Any]:
    coverage = read_csv(folder / "data_coverage.csv")
    best = read_csv(folder / "best_strategy_by_pair.csv")
    pair_strategy = read_csv(folder / "by_pair_strategy.csv")
    by_strategy = read_csv(folder / "by_strategy.csv")
    by_exit_profile = read_csv(folder / "by_exit_profile.csv")
    robustness = read_csv(folder / "robustness_by_pair_strategy.csv")
    tradeable = read_csv(folder / "tradeable_rank.csv")
    trades = read_csv(folder / "trades.csv")

    symbols = sorted({row.get("symbol", "") for row in coverage if row.get("symbol")})
    coverage_by_symbol = {
        row["symbol"]: {
            "first_5m": row.get("first_5m", ""),
            "last_5m": row.get("last_5m", ""),
            "candles_5m": i(row.get("candles_5m")),
            "candles_15m": i(row.get("candles_15m")),
            "coverage_days": r(row.get("coverage_days"), 3),
            "candidate_signals": i(row.get("candidate_signals")),
        }
        for row in coverage
        if row.get("symbol")
    }

    best_main = []
    for row in best:
        if row.get("cap") == MAIN_CAP and row.get("scenario") == MAIN_SCENARIO:
            base, profile, exit_profile = split_strategy_name(row.get("best_strategy", ""))
            best_main.append(
                {
                    "symbol": row.get("symbol", ""),
                    "strategy": row.get("best_strategy", ""),
                    "variant_base": base,
                    "variant_profile": profile,
                    "exit_profile": exit_profile,
                    "trades": i(row.get("trades")),
                    "net_pnl": r(row.get("net_pnl")),
                    "win_rate": r(row.get("win_rate"), 2),
                    "profit_factor": r(row.get("profit_factor"), 4),
                    "max_drawdown": r(row.get("max_drawdown")),
                    "avg_trade": r(row.get("avg_trade")),
                    "sample_confidence": row.get("sample_confidence", ""),
                }
            )
    best_main.sort(key=lambda x: x["net_pnl"], reverse=True)

    pair_strategy_main: dict[str, list[dict[str, Any]]] = defaultdict(list)
    scenario_matrix: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in pair_strategy:
        group = row.get("group", "")
        if "|" not in group:
            continue
        symbol, strategy = group.split("|", 1)
        metric = compact_metric(row)
        metric["symbol"] = symbol
        metric["strategy"] = strategy
        metric["variant_base"], metric["variant_profile"], metric["exit_profile"] = split_strategy_name(strategy)
        key = f"{symbol}|{strategy}"
        scenario_matrix[key][f"{row.get('cap')}|{row.get('scenario')}"] = metric
        if row.get("cap") == MAIN_CAP and row.get("scenario") == MAIN_SCENARIO:
            pair_strategy_main[symbol].append(metric)
    for rows in pair_strategy_main.values():
        rows.sort(key=lambda x: (x["net_pnl"], x["profit_factor"], x["trades"]), reverse=True)

    robustness_main: dict[str, dict[str, Any]] = {}
    for row in robustness:
        if row.get("cap") != MAIN_CAP or row.get("scenario") != MAIN_SCENARIO:
            continue
        key = row.get("pair_strategy", "")
        robustness_main[key] = {
            "days_active": i(row.get("days_active")),
            "positive_days": i(row.get("positive_days")),
            "negative_days": i(row.get("negative_days")),
            "positive_day_rate": r(row.get("positive_day_rate"), 2),
            "avg_daily_pnl": r(row.get("avg_daily_pnl")),
            "min_daily_pnl": r(row.get("min_daily_pnl")),
            "max_daily_pnl": r(row.get("max_daily_pnl")),
            "total_net_pnl": r(row.get("total_net_pnl")),
        }

    tradeable_main = []
    for row in tradeable:
        if row.get("cap") == MAIN_CAP and row.get("scenario") == MAIN_SCENARIO:
            base, profile, exit_profile = split_strategy_name(row.get("strategy", ""))
            tradeable_main.append(
                {
                    "symbol": row.get("symbol", ""),
                    "strategy": row.get("strategy", ""),
                    "variant_base": base,
                    "variant_profile": profile,
                    "exit_profile": exit_profile,
                    "trades": i(row.get("trades")),
                    "net_pnl": r(row.get("net_pnl")),
                    "win_rate": r(row.get("win_rate"), 2),
                    "profit_factor": r(row.get("profit_factor"), 4),
                    "max_drawdown": r(row.get("max_drawdown")),
                    "positive_day_rate": r(row.get("positive_day_rate"), 2),
                    "sample_confidence": row.get("sample_confidence", ""),
                    "readiness": row.get("readiness", ""),
                    "research_score": r(row.get("research_score")),
                }
            )
    tradeable_main.sort(key=lambda x: x["research_score"], reverse=True)

    strategy_totals = []
    for row in by_strategy:
        if row.get("cap") == MAIN_CAP and row.get("scenario") == MAIN_SCENARIO:
            metric = compact_metric(row)
            metric["strategy"] = row.get("group", "")
            metric["variant_base"], metric["variant_profile"], metric["exit_profile"] = split_strategy_name(metric["strategy"])
            strategy_totals.append(metric)
    strategy_totals.sort(key=lambda x: x["net_pnl"], reverse=True)

    exit_profile_totals = []
    for row in by_exit_profile:
        if row.get("cap") == MAIN_CAP and row.get("scenario") == MAIN_SCENARIO:
            exit_profile_totals.append(compact_metric(row))
    exit_profile_totals.sort(key=lambda x: x["net_pnl"], reverse=True)

    trade_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    daily_groups: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    exit_groups: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    reason_groups: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    side_groups: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    liquidity_groups: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in trades:
        if row.get("cap") != MAIN_CAP or row.get("scenario") != MAIN_SCENARIO:
            continue
        key = f"{row.get('symbol')}|{row.get('strategy')}"
        pnl = f(row.get("net_pnl"))
        day = day_from_ms(row.get("entry_time_ms", "0"))
        trade_groups[key].append(
            {
                "time": row.get("entry_time", ""),
                "exit_time": row.get("exit_time", ""),
                "side": row.get("side", ""),
                "exit_profile": row.get("exit_profile", "tx_on"),
                "liquidity": row.get("liquidity", ""),
                "entry": r(row.get("entry_price"), 8),
                "exit": r(row.get("exit_price"), 8),
                "hold": r(row.get("hold_minutes"), 2),
                "gross": r(row.get("gross_pnl")),
                "costs": r(row.get("costs")),
                "pnl": r(row.get("net_pnl")),
                "exit_reason": row.get("exit_reason", ""),
                "reason": row.get("reason", ""),
            }
        )
        daily_groups[key][day] += pnl
        exit_groups[key][row.get("exit_reason", "")] += 1
        reason_groups[key][row.get("reason", "")] += 1
        side_groups[key][row.get("side", "")] += 1
        liquidity_groups[key][row.get("liquidity", "")] += 1

    strategy_details: dict[str, dict[str, Any]] = {}
    for key, rows in trade_groups.items():
        rows.sort(key=lambda x: x["time"])
        equity = []
        total = 0.0
        for row in rows:
            total += row["pnl"]
            equity.append([row["time"], round(total, 6)])
        daily = [[day, round(pnl, 6)] for day, pnl in sorted(daily_groups[key].items())]
        strategy_details[key] = {
            "equity": equity,
            "daily": daily,
            "exits": dict(sorted(exit_groups[key].items(), key=lambda kv: kv[1], reverse=True)),
            "reasons": dict(sorted(reason_groups[key].items(), key=lambda kv: kv[1], reverse=True)[:8]),
            "sides": dict(side_groups[key]),
            "liquidity": dict(liquidity_groups[key]),
            "trades": rows,
        }

    return {
        "id": run_id,
        "folder": str(folder.name),
        "symbols": symbols,
        "coverage": coverage_by_symbol,
        "best": best_main,
        "pairStrategy": pair_strategy_main,
        "strategyTotals": strategy_totals,
        "exitProfileTotals": exit_profile_totals,
        "robustness": robustness_main,
        "tradeable": tradeable_main,
        "scenarioMatrix": scenario_matrix,
        "strategyDetails": strategy_details,
    }


def main() -> int:
    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "mainCap": int(MAIN_CAP),
        "mainScenario": MAIN_SCENARIO,
        "runs": {},
    }
    for run_id, folder in RUNS.items():
        if folder.exists():
            payload["runs"][run_id] = build_run(run_id, folder)
    out = Path(__file__).resolve().parent / "data.js"
    with out.open("w", encoding="utf-8") as f_out:
        f_out.write("window.BULK_DASHBOARD_DATA = ")
        json.dump(payload, f_out, ensure_ascii=False, separators=(",", ":"))
        f_out.write(";\n")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
