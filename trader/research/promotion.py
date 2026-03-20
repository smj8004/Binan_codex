from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class PromotionThresholds:
    min_trade_count: float = 3.0
    min_walk_forward_positive_ratio: float = 0.50
    min_walk_forward_sharpe: float = 0.0
    min_stress_total_return: float = 0.0
    min_positive_symbol_ratio: float = 0.40
    min_holdout_total_return: float = 0.0
    max_symbol_return_std: float = 0.05


def required_positive_symbols(symbol_count: int, *, ratio: float) -> int:
    count = max(int(symbol_count), 0)
    if count <= 1:
        return count
    return max(2, int(math.ceil(count * max(float(ratio), 0.0))))


def _bool(value: Any) -> bool:
    return bool(value)


def build_promotion_record(
    *,
    source_stack: str,
    candidate_id: str,
    title: str,
    track: str,
    strategy_name: str,
    timeframe: str,
    symbol_count: int,
    trade_count_mean: float,
    walk_forward_positive_ratio: float,
    walk_forward_sharpe: float,
    stress_total_return_mean: float,
    positive_symbols: int,
    symbol_return_std: float,
    holdout_total_return_mean: float,
    holdout_stress_total_return_mean: float,
    holdout_positive_symbols: int,
    runtime_supported: bool,
    thresholds: PromotionThresholds | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = thresholds or PromotionThresholds()
    symbol_count_int = max(int(symbol_count), 0)
    min_positive_symbols = required_positive_symbols(symbol_count_int, ratio=cfg.min_positive_symbol_ratio)

    stage_candidate_generation = symbol_count_int > 0 and float(trade_count_mean) >= float(cfg.min_trade_count)
    stage_walk_forward = stage_candidate_generation and float(walk_forward_positive_ratio) >= float(cfg.min_walk_forward_positive_ratio) and float(walk_forward_sharpe) > float(cfg.min_walk_forward_sharpe)
    stage_stress = stage_walk_forward and float(stress_total_return_mean) > float(cfg.min_stress_total_return)
    stage_breadth = stage_stress and int(positive_symbols) >= min_positive_symbols and float(symbol_return_std) <= float(cfg.max_symbol_return_std)
    stage_holdout = stage_breadth and float(holdout_total_return_mean) > float(cfg.min_holdout_total_return) and float(holdout_stress_total_return_mean) > float(cfg.min_holdout_total_return) and int(holdout_positive_symbols) >= min_positive_symbols
    stage_execution_eligible = stage_holdout and bool(runtime_supported)

    if stage_execution_eligible:
        decision = "promote_to_execution_validation"
        rejection_reason = ""
        stage_reached = "execution_validation_eligible"
    elif stage_holdout and not runtime_supported:
        decision = "alpha_survivor_runtime_blocked"
        rejection_reason = "candidate survives alpha gates but lacks runtime support"
        stage_reached = "holdout"
    elif not stage_candidate_generation:
        decision = "reject_candidate_generation"
        rejection_reason = "insufficient coverage or trade count"
        stage_reached = "candidate_generation"
    elif not stage_walk_forward:
        decision = "reject_walk_forward"
        rejection_reason = "walk-forward robustness below threshold"
        stage_reached = "walk_forward"
    elif not stage_stress:
        decision = "reject_stress"
        rejection_reason = "cost/slippage stress did not survive"
        stage_reached = "stress"
    elif not stage_breadth:
        decision = "reject_breadth"
        rejection_reason = "symbol breadth or dispersion failed"
        stage_reached = "breadth"
    else:
        decision = "reject_holdout"
        rejection_reason = "holdout or stressed holdout did not survive"
        stage_reached = "holdout"

    row = {
        "source_stack": source_stack,
        "candidate_id": candidate_id,
        "title": title,
        "track": track,
        "strategy_name": strategy_name,
        "timeframe": timeframe,
        "symbol_count": symbol_count_int,
        "trade_count_mean": float(trade_count_mean),
        "walk_forward_positive_ratio": float(walk_forward_positive_ratio),
        "walk_forward_sharpe": float(walk_forward_sharpe),
        "stress_total_return_mean": float(stress_total_return_mean),
        "positive_symbols": int(positive_symbols),
        "min_positive_symbols_required": int(min_positive_symbols),
        "symbol_return_std": float(symbol_return_std),
        "holdout_total_return_mean": float(holdout_total_return_mean),
        "holdout_stress_total_return_mean": float(holdout_stress_total_return_mean),
        "holdout_positive_symbols": int(holdout_positive_symbols),
        "stage_candidate_generation_pass": _bool(stage_candidate_generation),
        "stage_walk_forward_pass": _bool(stage_walk_forward),
        "stage_stress_pass": _bool(stage_stress),
        "stage_breadth_pass": _bool(stage_breadth),
        "stage_holdout_pass": _bool(stage_holdout),
        "stage_execution_eligible": _bool(stage_execution_eligible),
        "runtime_supported": _bool(runtime_supported),
        "stage_reached": stage_reached,
        "decision": decision,
        "rejection_reason": rejection_reason,
        "passed_stage_count": int(sum(bool(x) for x in [stage_candidate_generation, stage_walk_forward, stage_stress, stage_breadth, stage_holdout, stage_execution_eligible])),
    }
    if extra:
        row.update(extra)
    return row


def sort_promotion_records(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    ordered = df.copy()
    if "rank" in ordered.columns:
        ordered = ordered.drop(columns=["rank"])
    ordered = ordered.sort_values(
        by=[
            "stage_execution_eligible",
            "stage_holdout_pass",
            "stage_breadth_pass",
            "stage_stress_pass",
            "passed_stage_count",
            "holdout_stress_total_return_mean",
            "holdout_total_return_mean",
            "stress_total_return_mean",
            "walk_forward_positive_ratio",
        ],
        ascending=[False, False, False, False, False, False, False, False, False],
    ).reset_index(drop=True)
    ordered.insert(0, "rank", range(1, len(ordered) + 1))
    return ordered


def write_promotion_markdown(*, path: Path, df: pd.DataFrame, heading: str) -> None:
    lines = [f"# {heading}", ""]
    if df.empty:
        lines.append("_No records generated._")
        path.write_text("\n".join(lines), encoding="utf-8")
        return
    lines.extend(
        [
            "| rank | candidate | track | source | decision | stage_reached | holdout | holdout_stress | stress | breadth |",
            "|---:|---|---|---|---|---|---:|---:|---:|---:|",
        ]
    )
    for _, row in df.iterrows():
        lines.append(
            f"| {int(row['rank'])} | {row['candidate_id']} | {row['track']} | {row['source_stack']} | {row['decision']} | "
            f"{row['stage_reached']} | {float(row['holdout_total_return_mean']):.4f} | "
            f"{float(row['holdout_stress_total_return_mean']):.4f} | {float(row['stress_total_return_mean']):.4f} | "
            f"{int(row['positive_symbols'])}/{int(row['symbol_count'])} |"
        )
    path.write_text("\n".join(lines), encoding="utf-8")
