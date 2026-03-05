from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from trader.backtest.engine import BacktestConfig
from trader.experiments.runner import run_portfolio_validation


# Fixed configuration copied from guide/BASELINE_STATE.md.
FIXED_START = "2021-01-01"
FIXED_END = "2026-01-01"
FIXED_TIMEFRAME = "1h"
FIXED_DATA_SOURCE = "binance"
FIXED_TESTNET = False
FIXED_SYMBOLS = [
    "BTC/USDT",
    "ETH/USDT",
    "BNB/USDT",
    "SOL/USDT",
    "XRP/USDT",
    "ADA/USDT",
    "DOGE/USDT",
    "AVAX/USDT",
    "LINK/USDT",
    "TRX/USDT",
]
FIXED_SIGNAL_MODEL = "momentum"
FIXED_REBALANCE_BARS = 24
FIXED_LOOKBACK_MODE = "median_3"
FIXED_LOOKBACK_BARS = 24 * 7
FIXED_K = 4
FIXED_RANK_BUFFER = 2
FIXED_SHOCK_MODE = "downweight"
FIXED_SHOCK_FREEZE_MIN_FRACTION = 0.40
FIXED_SHOCK_COOLDOWN_BARS = 48
FIXED_ATR_SHOCK_THRESHOLD = 2.7
FIXED_GAP_SHOCK_THRESHOLD = 0.12
FIXED_SHOCK_WEIGHT_MULT_GAP = 0.15
FIXED_EXTREME_NO_TRADE = True
FIXED_EXTREME_VOL_PERCENTILE = 0.90
FIXED_EXTREME_NON_TREND_LOGIC = "or"
FIXED_EXTREME_REGIME_MODE = "delever"
FIXED_EXTREME_GROSS_MULT = 0.5
FIXED_TREND_SLOPE_THRESHOLD = 0.0015

# A/B lever (single lever policy)
ATR_MULT_A = 0.25
ATR_MULT_B = 0.30


def _hard_gate_ok(row: dict[str, float]) -> bool:
    return (
        float(row.get("liquidation_count", 1.0)) == 0.0
        and float(row.get("equity_zero_or_negative_count", 1.0)) == 0.0
        and float(row.get("fee_cost_total", 10_000_000.0)) <= 2000.0
    )


def _parse_selected_run_id(guide_text: str) -> str:
    m = re.search(r"selected run id[^:]*:\s*`([^`]+)`", guide_text, flags=re.IGNORECASE)
    if not m:
        raise RuntimeError("selected run id not found in guide/BASELINE_STATE.md")
    return m.group(1).strip()


def _coerce_tuple(raw: list[Any], expected_len: int) -> tuple[Any, ...]:
    values = tuple(raw)
    if len(values) != expected_len:
        raise ValueError(f"Expected length {expected_len}, got {len(values)}")
    return values


def _validate_fixed_config(baseline_cfg: dict[str, Any]) -> None:
    if str(baseline_cfg.get("data_source", "")).lower() != FIXED_DATA_SOURCE:
        raise RuntimeError("Baseline data_source mismatch.")
    if str(baseline_cfg.get("timeframe", "")).lower() != FIXED_TIMEFRAME:
        raise RuntimeError("Baseline timeframe mismatch.")
    if [str(x) for x in baseline_cfg.get("symbols", [])] != FIXED_SYMBOLS:
        raise RuntimeError("Baseline symbols mismatch.")
    start = str(baseline_cfg.get("start", ""))
    end = str(baseline_cfg.get("end", ""))
    if not start.startswith(FIXED_START) or not end.startswith(FIXED_END):
        raise RuntimeError("Baseline period mismatch.")

    selected = baseline_cfg.get("selected_params", {})
    safety = baseline_cfg.get("safety", {})
    regime = baseline_cfg.get("regime", {})
    if str(selected.get("signal_model", "")).lower() != FIXED_SIGNAL_MODEL:
        raise RuntimeError("Baseline signal model mismatch.")
    if int(selected.get("rebalance_bars", -1)) != FIXED_REBALANCE_BARS:
        raise RuntimeError("Baseline rebalance_bars mismatch.")
    if int(selected.get("k", -1)) != FIXED_K:
        raise RuntimeError("Baseline k mismatch.")
    if int(selected.get("rank_buffer", -1)) != FIXED_RANK_BUFFER:
        raise RuntimeError("Baseline rank_buffer mismatch.")
    if str(safety.get("lookback_score_mode", "")).lower() != FIXED_LOOKBACK_MODE:
        raise RuntimeError("Baseline lookback_score_mode mismatch.")
    if str(safety.get("shock_mode", "")).lower() != FIXED_SHOCK_MODE:
        raise RuntimeError("Baseline shock_mode mismatch.")
    if abs(float(safety.get("shock_freeze_min_fraction", -1.0)) - FIXED_SHOCK_FREEZE_MIN_FRACTION) > 1e-12:
        raise RuntimeError("Baseline shock_freeze_min_fraction mismatch.")
    if abs(float(regime.get("trend_slope_threshold", -1.0)) - FIXED_TREND_SLOPE_THRESHOLD) > 1e-12:
        raise RuntimeError("Baseline trend_slope_threshold mismatch.")


def _run_case(
    *,
    out_root: Path,
    baseline_cfg: dict[str, Any],
    initial_equity: float,
    scenario: str,
    shock_weight_mult_atr: float,
) -> dict[str, Any]:
    selected = baseline_cfg["selected_params"]
    safety = baseline_cfg["safety"]
    cost = baseline_cfg["cost"]
    walk = baseline_cfg["walk_forward"]
    regime = baseline_cfg["regime"]
    regime_thr = safety.get("regime_turnover_threshold_map", {})

    output = run_portfolio_validation(
        symbols=list(FIXED_SYMBOLS),
        timeframe=FIXED_TIMEFRAME,
        start=FIXED_START,
        end=FIXED_END,
        base_config=BacktestConfig(
            symbol="BTC/USDT",
            timeframe=FIXED_TIMEFRAME,
            persist_to_db=False,
            initial_equity=float(initial_equity),
        ),
        output_root=out_root,
        seed=int(baseline_cfg["seed"]),
        data_source=FIXED_DATA_SOURCE,
        csv_path=None,
        testnet=FIXED_TESTNET,
        signal_models=[str(FIXED_SIGNAL_MODEL)],
        lookback_bars=[int(FIXED_LOOKBACK_BARS)],
        rebalance_bars=[int(FIXED_REBALANCE_BARS)],
        k_values=[int(FIXED_K)],
        gross_values=[float(selected["gross_exposure"])],
        rank_buffers=[int(FIXED_RANK_BUFFER)],
        high_vol_percentiles=[float(selected["high_vol_percentile"])],
        gross_maps=[str(selected["gross_map"])],
        off_grace_bars_list=[int(selected["off_grace_bars"])],
        phased_entry_steps_list=[int(selected["phased_entry_steps"])],
        turnover_threshold=float(selected["turnover_threshold"]),
        turnover_threshold_high_vol=float(regime_thr.get("trend|high_vol", selected["turnover_threshold"])),
        turnover_threshold_low_vol=float(regime_thr.get("trend|low_vol", selected["turnover_threshold"])),
        vol_lookback=int(selected["vol_lookback"]),
        fee_multipliers=[float(x) for x in cost["fee_multipliers"]],
        fixed_slippage_bps=[float(x) for x in cost["fixed_slippage_bps"]],
        atr_slippage_mults=[float(x) for x in cost["atr_slippage_mults"]],
        slippage_mode=str(cost["slippage_mode"]),
        latency_bars=[int(x) for x in cost["latency_bars"]],
        order_models=[str(x) for x in cost["order_models"]],
        limit_timeout_bars=int(cost["limit_timeout_bars"]),
        limit_fill_probability=float(cost["limit_fill_probability"]),
        limit_unfilled_penalty_bps=float(cost["limit_unfilled_penalty_bps"]),
        walk_train_days=int(walk["train_days"]),
        walk_test_days=int(walk["test_days"]),
        walk_step_days=int(walk["step_days"]),
        walk_top_pct=float(walk["top_pct"]),
        walk_max_candidates=int(walk["max_candidates"]),
        walk_metric=str(walk["metric"]),
        trend_ema_span=int(regime["trend_ema_span"]),
        trend_slope_lookback=int(regime["trend_slope_lookback"]),
        trend_slope_threshold=float(FIXED_TREND_SLOPE_THRESHOLD),
        regime_atr_period=int(regime["atr_period"]),
        regime_vol_lookback=int(regime["vol_lookback"]),
        regime_vol_percentile=float(regime["vol_percentile"]),
        high_vol_gross_mult=float(regime["high_vol_gross_mult"]),
        debug_mode=bool(safety["debug_mode"]),
        max_cost_ratio_per_bar=float(safety["max_cost_ratio_per_bar"]),
        dd_controller_enabled=bool(safety["dd_controller_enabled"]),
        dd_thresholds=_coerce_tuple([float(x) for x in safety["dd_thresholds"]], 4),  # type: ignore[arg-type]
        dd_gross_mults=_coerce_tuple([float(x) for x in safety["dd_gross_mults"]], 5),  # type: ignore[arg-type]
        dd_recover_thresholds=_coerce_tuple([float(x) for x in safety["dd_recover_thresholds"]], 4),  # type: ignore[arg-type]
        kill_cooldown_bars=int(safety["kill_cooldown_bars"]),
        disable_new_entry_when_dd=bool(safety["disable_new_entry_when_dd"]),
        rolling_peak_window_bars=None if safety["rolling_peak_window_bars"] is None else int(safety["rolling_peak_window_bars"]),
        stage_down_confirm_bars=int(safety["stage_down_confirm_bars"]),
        stage3_down_confirm_bars=int(safety["stage3_down_confirm_bars"]),
        reentry_ramp_steps=int(safety["reentry_ramp_steps"]),
        disable_new_entry_stage=int(safety["disable_new_entry_stage"]),
        dd_turnover_threshold_mult=float(safety["dd_turnover_threshold_mult"]),
        dd_rebalance_mult=None if safety["dd_rebalance_mult"] is None else float(safety["dd_rebalance_mult"]),
        cap_mode=str(safety["cap_mode"]),
        base_cap=float(safety["base_cap"]),
        cap_min=float(safety["cap_min"]),
        cap_max=float(safety["cap_max"]),
        backlog_thresholds=_coerce_tuple([float(x) for x in safety["backlog_thresholds"]], 3),  # type: ignore[arg-type]
        cap_steps=_coerce_tuple([float(x) for x in safety["cap_steps"]], 4),  # type: ignore[arg-type]
        high_vol_cap_max=float(safety["high_vol_cap_max"]),
        max_turnover_notional_to_equity=(
            None
            if safety["max_turnover_notional_to_equity"] is None
            else float(safety["max_turnover_notional_to_equity"])
        ),
        drift_threshold=None if safety["drift_threshold"] is None else float(safety["drift_threshold"]),
        gross_decay_steps=int(safety["gross_decay_steps"]),
        max_notional_to_equity_mult=float(safety["max_notional_to_equity_mult"]),
        enable_liquidation=bool(safety["enable_liquidation"]),
        equity_floor_ratio=float(safety["equity_floor_ratio"]),
        trading_halt_bars=int(safety["trading_halt_bars"]),
        skip_trades_if_cost_exceeds_equity_ratio=float(safety["skip_trades_if_cost_exceeds_equity_ratio"]),
        transition_smoother_enabled=bool(safety["transition_smoother_enabled"]),
        gross_step_up=float(safety["gross_step_up"]),
        gross_step_down=float(safety["gross_step_down"]),
        post_halt_cooldown_bars=int(safety["post_halt_cooldown_bars"]),
        post_halt_max_gross=float(safety["post_halt_max_gross"]),
        liquidation_lookback_bars=int(safety["liquidation_lookback_bars"]),
        liquidation_lookback_max_gross=float(safety["liquidation_lookback_max_gross"]),
        enable_symbol_shock_filters=bool(safety["enable_symbol_shock_filters"]),
        max_abs_weight_per_symbol=float(safety["max_abs_weight_per_symbol"]),
        atr_shock_threshold=float(FIXED_ATR_SHOCK_THRESHOLD),
        gap_shock_threshold=float(FIXED_GAP_SHOCK_THRESHOLD),
        shock_cooldown_bars=int(FIXED_SHOCK_COOLDOWN_BARS),
        shock_mode=str(FIXED_SHOCK_MODE),
        shock_weight_mult_atr=float(shock_weight_mult_atr),
        shock_weight_mult_gap=float(FIXED_SHOCK_WEIGHT_MULT_GAP),
        shock_freeze_rebalance=bool(safety["shock_freeze_rebalance"]),
        shock_freeze_min_fraction=float(FIXED_SHOCK_FREEZE_MIN_FRACTION),
        lookback_score_mode=str(FIXED_LOOKBACK_MODE),
        extreme_no_trade=bool(FIXED_EXTREME_NO_TRADE),
        extreme_no_trade_vol_percentile=float(FIXED_EXTREME_VOL_PERCENTILE),
        extreme_no_trade_non_trend_logic=str(FIXED_EXTREME_NON_TREND_LOGIC),
        extreme_regime_mode=str(FIXED_EXTREME_REGIME_MODE),
        extreme_gross_mult=float(FIXED_EXTREME_GROSS_MULT),
        stop_on_anomaly=bool(safety["stop_on_anomaly"]),
    )

    run_cfg = json.loads((output.run_dir / "config.json").read_text(encoding="utf-8"))
    diagnostics = json.loads((output.run_dir / "diagnostics.json").read_text(encoding="utf-8"))

    if "testnet" not in run_cfg or bool(run_cfg.get("testnet")) is not False:
        raise RuntimeError(f"{scenario}: config.json must record testnet=false.")
    config_atr_mult = float(run_cfg.get("safety", {}).get("shock_weight_mult_atr", -1.0))
    diagnostics_atr_mult = float(diagnostics.get("shock_weight_mult_atr", -1.0))
    if abs(config_atr_mult - float(shock_weight_mult_atr)) > 1e-12:
        raise RuntimeError(f"{scenario}: config shock_weight_mult_atr mismatch ({config_atr_mult} != {shock_weight_mult_atr}).")
    if abs(diagnostics_atr_mult - float(shock_weight_mult_atr)) > 1e-12:
        raise RuntimeError(f"{scenario}: diagnostics shock_weight_mult_atr mismatch ({diagnostics_atr_mult} != {shock_weight_mult_atr}).")

    summary = json.loads((output.run_dir / "summary.json").read_text(encoding="utf-8"))
    turnover = pd.read_csv(output.run_dir / "turnover.csv")
    if {"rebalance_skipped_due_to_shock", "rebalance_skipped_due_to_extreme"}.issubset(set(turnover.columns)):
        skipped_ratio = float(
            (
                turnover["rebalance_skipped_due_to_shock"].astype(bool)
                | turnover["rebalance_skipped_due_to_extreme"].astype(bool)
            ).mean()
        )
    else:
        skipped_ratio = float(summary.get("rebalance_skipped_due_to_final_ratio", 0.0))
    shocked_counts = diagnostics.get("shocked_counts_by_reason", {}) or {}

    row = {
        "scenario": scenario,
        "run_id": output.run_id,
        "run_dir": str(output.run_dir.relative_to(REPO_ROOT)),
        "shock_weight_mult_atr": float(shock_weight_mult_atr),
        "net_pnl": float(summary.get("net_pnl", 0.0)),
        "max_drawdown": float(summary.get("portfolio_max_drawdown", 0.0)),
        "fee_cost_total": float(summary.get("fee_cost_total", 0.0)),
        "oos_positive_ratio": float(summary.get("oos_positive_ratio", 0.0)),
        "avg_turnover_ratio": float(summary.get("avg_turnover_ratio", 0.0)),
        "skipped_ratio": float(skipped_ratio),
        "avg_effective_gross": float(diagnostics.get("applied_gross_mean", 0.0)),
        "shock_skip_ratio": float(summary.get("rebalance_skipped_due_to_shock_ratio", 0.0)),
        "extreme_skip_ratio": float(summary.get("rebalance_skipped_due_to_extreme_ratio", 0.0)),
        "extreme_no_trade_ratio": float(diagnostics.get("extreme_no_trade_ratio", 0.0)),
        "atr_shock_count": float(shocked_counts.get("atr_shock", 0.0)),
        "gap_shock_count": float(shocked_counts.get("gap_shock", 0.0)),
        "liquidation_count": float(summary.get("liquidation_count", 0.0)),
        "equity_zero_or_negative_count": float(summary.get("equity_zero_or_negative_count", 0.0)),
        "config_testnet": bool(run_cfg.get("testnet")),
        "config_atr_mult": float(config_atr_mult),
        "diagnostics_atr_mult": float(diagnostics_atr_mult),
    }
    row["hard_gate_ok"] = _hard_gate_ok(row)
    return row


def _pick_winner(run_a: dict[str, Any], run_b: dict[str, Any]) -> tuple[str, str]:
    a_gate = bool(run_a["hard_gate_ok"])
    b_gate = bool(run_b["hard_gate_ok"])
    if not a_gate or not b_gate:
        return "FAIL", f"Hard gate failed (A={a_gate}, B={b_gate})."

    a_pnl = float(run_a["net_pnl"])
    b_pnl = float(run_b["net_pnl"])
    pnl_diff_ratio = abs(a_pnl - b_pnl) / max(abs(a_pnl), abs(b_pnl), 1e-9)
    if pnl_diff_ratio <= 0.05:
        a_mdd = float(run_a["max_drawdown"])
        b_mdd = float(run_b["max_drawdown"])
        if b_mdd > a_mdd:
            return "B", "net_pnl difference <=5%, B has less severe max_drawdown."
        if a_mdd > b_mdd:
            return "A", "net_pnl difference <=5%, A has less severe max_drawdown."
        a_fee = float(run_a["fee_cost_total"])
        b_fee = float(run_b["fee_cost_total"])
        if b_fee < a_fee:
            return "B", "net_pnl and max_drawdown are tied; B has lower fee_cost_total."
        if a_fee < b_fee:
            return "A", "net_pnl and max_drawdown are tied; A has lower fee_cost_total."
        return "TIE", "All tie-break metrics are tied."

    if b_pnl > a_pnl:
        return "B", "B has higher net_pnl."
    if a_pnl > b_pnl:
        return "A", "A has higher net_pnl."

    a_fee = float(run_a["fee_cost_total"])
    b_fee = float(run_b["fee_cost_total"])
    if b_fee < a_fee:
        return "B", "net_pnl tied; B has lower fee_cost_total."
    if a_fee < b_fee:
        return "A", "net_pnl tied; A has lower fee_cost_total."
    return "TIE", "net_pnl and fee_cost_total are tied."


def main() -> None:
    repo_root = REPO_ROOT
    out_root = repo_root / "out" / "experiments"
    guide_path = repo_root / "guide" / "BASELINE_STATE.md"
    guide_text = guide_path.read_text(encoding="utf-8")
    selected_run_id = _parse_selected_run_id(guide_text)
    selected_run_dir = out_root / selected_run_id
    if not selected_run_dir.exists():
        raise FileNotFoundError(f"Baseline run directory not found: {selected_run_dir}")

    baseline_cfg = json.loads((selected_run_dir / "config.json").read_text(encoding="utf-8"))
    _validate_fixed_config(baseline_cfg)
    bench = pd.read_csv(selected_run_dir / "benchmark_btc_buyhold.csv", nrows=1)
    if bench.empty:
        raise RuntimeError("Cannot infer initial equity from benchmark_btc_buyhold.csv.")
    initial_equity = float(bench.iloc[0]["btc_equity"])

    sweep_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    sweep_dir = out_root / f"shock_weight_mult_atr_ab_{sweep_id}"
    sweep_dir.mkdir(parents=True, exist_ok=True)

    run_a = _run_case(
        out_root=out_root,
        baseline_cfg=baseline_cfg,
        initial_equity=initial_equity,
        scenario="A_atr_mult_0.25",
        shock_weight_mult_atr=ATR_MULT_A,
    )
    run_b = _run_case(
        out_root=out_root,
        baseline_cfg=baseline_cfg,
        initial_equity=initial_equity,
        scenario="B_atr_mult_0.30",
        shock_weight_mult_atr=ATR_MULT_B,
    )

    comparison_cols = [
        "scenario",
        "run_id",
        "shock_weight_mult_atr",
        "net_pnl",
        "max_drawdown",
        "fee_cost_total",
        "oos_positive_ratio",
        "avg_turnover_ratio",
        "skipped_ratio",
        "avg_effective_gross",
        "shock_skip_ratio",
        "extreme_skip_ratio",
        "extreme_no_trade_ratio",
        "atr_shock_count",
        "gap_shock_count",
        "liquidation_count",
        "equity_zero_or_negative_count",
        "hard_gate_ok",
    ]
    comparison_df = pd.DataFrame([run_a, run_b])[comparison_cols]
    comparison_path = sweep_dir / "baseline_vs_variant.csv"
    comparison_df.to_csv(comparison_path, index=False)

    winner, reason = _pick_winner(run_a, run_b)
    report_lines = [
        f"# Shock Weight ATR Multiplier A/B Report ({sweep_id})",
        "",
        "## Scope",
        f"- guide selected baseline run id: `{selected_run_id}`",
        "- single lever only: `shock_weight_mult_atr`",
        f"- Run A: `shock_weight_mult_atr={ATR_MULT_A:.2f}` (baseline)",
        f"- Run B: `shock_weight_mult_atr={ATR_MULT_B:.2f}` (variant)",
        f"- fixed: `shock_cooldown_bars={FIXED_SHOCK_COOLDOWN_BARS}`, `atr_shock_threshold={FIXED_ATR_SHOCK_THRESHOLD}`, `gap_shock_threshold={FIXED_GAP_SHOCK_THRESHOLD:.2f}`, `shock_weight_mult_gap={FIXED_SHOCK_WEIGHT_MULT_GAP:.2f}` and all other baseline fixed values unchanged",
        "",
        "## Run IDs",
        f"- Run A: `{run_a['run_id']}`",
        f"- Run B: `{run_b['run_id']}`",
        "",
        "## Runtime Verification",
        f"- VERIFY A testnet={run_a['config_testnet']} config_atr_mult={run_a['config_atr_mult']:.2f} diagnostics_atr_mult={run_a['diagnostics_atr_mult']:.2f}",
        f"- VERIFY B testnet={run_b['config_testnet']} config_atr_mult={run_b['config_atr_mult']:.2f} diagnostics_atr_mult={run_b['diagnostics_atr_mult']:.2f}",
        "",
        "## Baseline vs Variant",
        "| scenario | run_id | shock_weight_mult_atr | net_pnl | max_drawdown | fee_cost_total | oos_positive_ratio | avg_turnover_ratio | skipped_ratio | avg_effective_gross | shock_skip_ratio | extreme_skip_ratio | extreme_no_trade_ratio | atr_shock_count | gap_shock_count | liquidation_count | eq0_count |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in comparison_df.to_dict(orient="records"):
        report_lines.append(
            f"| {row['scenario']} | {row['run_id']} | {float(row['shock_weight_mult_atr']):.2f} | "
            f"{float(row['net_pnl']):.4f} | {float(row['max_drawdown']):.6f} | {float(row['fee_cost_total']):.4f} | "
            f"{float(row['oos_positive_ratio']):.6f} | {float(row['avg_turnover_ratio']):.6f} | {float(row['skipped_ratio']):.6f} | "
            f"{float(row['avg_effective_gross']):.6f} | {float(row['shock_skip_ratio']):.6f} | {float(row['extreme_skip_ratio']):.6f} | "
            f"{float(row['extreme_no_trade_ratio']):.6f} | {float(row['atr_shock_count']):.0f} | {float(row['gap_shock_count']):.0f} | "
            f"{float(row['liquidation_count']):.0f} | {float(row['equity_zero_or_negative_count']):.0f} |"
        )
    report_lines.extend(
        [
            "",
            "## Hard Gate",
            f"- Run A: `{bool(run_a['hard_gate_ok'])}` (`liq={int(run_a['liquidation_count'])}`, `eq0={int(run_a['equity_zero_or_negative_count'])}`, `fee={float(run_a['fee_cost_total']):.4f}`)",
            f"- Run B: `{bool(run_b['hard_gate_ok'])}` (`liq={int(run_b['liquidation_count'])}`, `eq0={int(run_b['equity_zero_or_negative_count'])}`, `fee={float(run_b['fee_cost_total']):.4f}`)",
            "",
            "## Verdict",
            f"- winner: **{winner}**",
            f"- reason: {reason}",
        ]
    )
    (sweep_dir / "report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    print(f"VERIFY A testnet={run_a['config_testnet']} config_atr_mult={run_a['config_atr_mult']:.2f} diagnostics_atr_mult={run_a['diagnostics_atr_mult']:.2f}")
    print(f"VERIFY B testnet={run_b['config_testnet']} config_atr_mult={run_b['config_atr_mult']:.2f} diagnostics_atr_mult={run_b['diagnostics_atr_mult']:.2f}")
    print(str(sweep_dir))
    print(str(comparison_path))
    print(run_a["run_id"])
    print(run_b["run_id"])
    print(winner)


if __name__ == "__main__":
    main()
