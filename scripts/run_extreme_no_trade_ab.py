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
    vals = tuple(raw)
    if len(vals) != expected_len:
        raise ValueError(f"Expected length {expected_len}, got {len(vals)}")
    return vals


def _run_case(
    *,
    out_root: Path,
    baseline_cfg: dict[str, Any],
    initial_equity: float,
    extreme_no_trade_enabled: bool,
    extreme_high_vol_percentile: float,
    extreme_non_trend_logic: str,
    extreme_regime_mode: str,
    extreme_gross_mult: float,
    k_override: int,
) -> dict[str, Any]:
    selected = baseline_cfg["selected_params"]
    safety = baseline_cfg["safety"]
    cost = baseline_cfg["cost"]
    walk = baseline_cfg["walk_forward"]
    regime = baseline_cfg["regime"]
    regime_thr = safety.get("regime_turnover_threshold_map", {})

    output = run_portfolio_validation(
        symbols=list(baseline_cfg["symbols"]),
        timeframe=str(baseline_cfg["timeframe"]),
        start="2021-01-01",
        end="2026-01-01",
        base_config=BacktestConfig(
            symbol="BTC/USDT",
            timeframe=str(baseline_cfg["timeframe"]),
            persist_to_db=False,
            initial_equity=float(initial_equity),
        ),
        output_root=out_root,
        seed=int(baseline_cfg["seed"]),
        data_source=str(baseline_cfg["data_source"]),
        csv_path=None,
        testnet=False,
        signal_models=[str(selected["signal_model"])],
        lookback_bars=[int(selected["lookback_bars"])],
        rebalance_bars=[int(selected["rebalance_bars"])],
        k_values=[int(k_override)],
        gross_values=[float(selected["gross_exposure"])],
        rank_buffers=[int(selected["rank_buffer"])],
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
        trend_slope_threshold=float(regime["trend_slope_threshold"]),
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
        atr_shock_threshold=float(safety["atr_shock_threshold"]),
        gap_shock_threshold=float(safety["gap_shock_threshold"]),
        shock_cooldown_bars=int(safety["shock_cooldown_bars"]),
        shock_mode=str(safety["shock_mode"]),
        shock_weight_mult_atr=float(safety["shock_weight_mult_atr"]),
        shock_weight_mult_gap=float(safety["shock_weight_mult_gap"]),
        shock_freeze_rebalance=bool(safety["shock_freeze_rebalance"]),
        shock_freeze_min_fraction=float(safety["shock_freeze_min_fraction"]),
        lookback_score_mode=str(safety["lookback_score_mode"]),
        extreme_no_trade=bool(extreme_no_trade_enabled),
        extreme_no_trade_vol_percentile=float(extreme_high_vol_percentile),
        extreme_no_trade_non_trend_logic=str(extreme_non_trend_logic),
        extreme_regime_mode=str(extreme_regime_mode),
        extreme_gross_mult=float(extreme_gross_mult),
        stop_on_anomaly=bool(safety["stop_on_anomaly"]),
    )

    summary = json.loads((output.run_dir / "summary.json").read_text(encoding="utf-8"))
    diagnostics = json.loads((output.run_dir / "diagnostics.json").read_text(encoding="utf-8"))
    turnover = pd.read_csv(output.run_dir / "turnover.csv")
    if {"rebalance_skipped_due_to_shock", "rebalance_skipped_due_to_extreme"}.issubset(set(turnover.columns)):
        final_skip_ratio = float(
            (
                turnover["rebalance_skipped_due_to_shock"].astype(bool)
                | turnover["rebalance_skipped_due_to_extreme"].astype(bool)
            ).mean()
        )
    else:
        final_skip_ratio = float(summary.get("rebalance_skipped_due_to_final_ratio", 0.0))
    turnover_notional_sum = 0.0
    if "turnover_notional" in turnover.columns:
        turnover_notional_sum = float(turnover["turnover_notional"].fillna(0.0).sum())
    return {
        "run_id": output.run_id,
        "run_dir": str(output.run_dir.relative_to(out_root.parents[1])),
        "net_pnl": float(summary.get("net_pnl", 0.0)),
        "max_drawdown": float(summary.get("portfolio_max_drawdown", 0.0)),
        "fee_cost_total": float(summary.get("fee_cost_total", 0.0)),
        "oos_positive_ratio": float(summary.get("oos_positive_ratio", 0.0)),
        "avg_turnover_ratio": float(summary.get("avg_turnover_ratio", 0.0)),
        "skipped_ratio": float(final_skip_ratio),
        "shock_skip_ratio": float(summary.get("rebalance_skipped_due_to_shock_ratio", 0.0)),
        "extreme_skip_ratio": float(summary.get("rebalance_skipped_due_to_extreme_ratio", 0.0)),
        "extreme_no_trade_ratio": float(diagnostics.get("extreme_no_trade_ratio", 0.0)),
        "avg_effective_gross": float(diagnostics.get("applied_gross_mean", 0.0)),
        "turnover_notional_sum": float(turnover_notional_sum),
        "extreme_high_vol_percentile": float(extreme_high_vol_percentile),
        "extreme_non_trend_logic": str(extreme_non_trend_logic),
        "trend_slope_threshold": float(regime["trend_slope_threshold"]),
        "k": int(k_override),
        "extreme_regime_mode": str(extreme_regime_mode),
        "extreme_gross_mult": float(extreme_gross_mult),
        "liquidation_count": float(summary.get("liquidation_count", 0.0)),
        "equity_zero_or_negative_count": float(summary.get("equity_zero_or_negative_count", 0.0)),
        "hard_gate_ok": _hard_gate_ok(summary),
    }


def _judge(baseline: dict[str, Any], variant: dict[str, Any]) -> tuple[str, str]:
    if not bool(baseline["hard_gate_ok"]) or not bool(variant["hard_gate_ok"]):
        return "FAIL", "Hard gate violation detected."
    b_pnl = float(baseline["net_pnl"])
    v_pnl = float(variant["net_pnl"])
    if v_pnl > b_pnl:
        return "SUCCESS", "Variant net_pnl improved over baseline."
    pnl_diff_ratio = abs(v_pnl - b_pnl) / max(abs(b_pnl), 1e-9)
    if pnl_diff_ratio <= 0.05 and float(variant["max_drawdown"]) > float(baseline["max_drawdown"]):
        return "PARTIAL", "Within 5% net_pnl band and max_drawdown improved."
    return "FAIL", "Both net_pnl and max_drawdown did not meet success criteria."


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    out_root = repo_root / "out" / "experiments"
    guide_path = repo_root / "guide" / "BASELINE_STATE.md"
    guide_text = guide_path.read_text(encoding="utf-8")
    selected_run_id = _parse_selected_run_id(guide_text)
    selected_run_dir = out_root / selected_run_id
    if not selected_run_dir.exists():
        raise FileNotFoundError(f"Baseline run directory not found: {selected_run_dir}")

    baseline_cfg = json.loads((selected_run_dir / "config.json").read_text(encoding="utf-8"))
    if str(baseline_cfg.get("data_source", "")).lower() != "binance":
        raise RuntimeError("Selected baseline data_source is not binance.")
    baseline_start = str(baseline_cfg.get("start", ""))
    baseline_end = str(baseline_cfg.get("end", ""))
    if not baseline_start.startswith("2021-01-01") or not baseline_end.startswith("2026-01-01"):
        raise RuntimeError("Selected baseline period does not match fixed configuration.")
    sel = baseline_cfg.get("selected_params", {})
    if int(sel.get("k", -1)) != 4 or int(sel.get("rank_buffer", -1)) != 2:
        raise RuntimeError("Selected baseline k/rank_buffer does not match fixed configuration.")
    safety_cfg = baseline_cfg.get("safety", {})
    regime_cfg = baseline_cfg.get("regime", {})
    if str(safety_cfg.get("lookback_score_mode", "")).lower() != "median_3":
        raise RuntimeError("Selected baseline lookback_score_mode is not median_3.")
    if abs(float(safety_cfg.get("shock_freeze_min_fraction", -1.0)) - 0.40) > 1e-12:
        raise RuntimeError("Selected baseline shock_freeze_min_fraction is not 0.40.")
    if abs(float(regime_cfg.get("trend_slope_threshold", -1.0)) - 0.0015) > 1e-12:
        raise RuntimeError("Selected baseline trend_slope_threshold is not 0.0015.")

    bench = pd.read_csv(selected_run_dir / "benchmark_btc_buyhold.csv", nrows=1)
    if bench.empty:
        raise RuntimeError("Cannot infer initial equity from benchmark_btc_buyhold.csv.")
    initial_equity = float(bench.iloc[0]["btc_equity"])

    sweep_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    sweep_dir = out_root / f"extreme_no_trade_ab_{sweep_id}"
    sweep_dir.mkdir(parents=True, exist_ok=True)

    run_a = _run_case(
        out_root=out_root,
        baseline_cfg=baseline_cfg,
        initial_equity=initial_equity,
        extreme_no_trade_enabled=True,
        extreme_high_vol_percentile=0.90,
        extreme_non_trend_logic="or",
        extreme_regime_mode="delever",
        extreme_gross_mult=0.5,
        k_override=4,
    )
    run_b = _run_case(
        out_root=out_root,
        baseline_cfg=baseline_cfg,
        initial_equity=initial_equity,
        extreme_no_trade_enabled=True,
        extreme_high_vol_percentile=0.90,
        extreme_non_trend_logic="or",
        extreme_regime_mode="delever",
        extreme_gross_mult=0.5,
        k_override=5,
    )
    run_a["scenario"] = "A_k4"
    run_b["scenario"] = "B_k5"
    comparison_df = pd.DataFrame([run_a, run_b])[
        [
            "scenario",
            "run_id",
            "k",
            "trend_slope_threshold",
            "extreme_high_vol_percentile",
            "extreme_non_trend_logic",
            "extreme_regime_mode",
            "extreme_gross_mult",
            "avg_effective_gross",
            "avg_turnover_ratio",
            "net_pnl",
            "max_drawdown",
            "fee_cost_total",
            "oos_positive_ratio",
            "skipped_ratio",
            "shock_skip_ratio",
            "extreme_skip_ratio",
            "extreme_no_trade_ratio",
            "turnover_notional_sum",
            "liquidation_count",
            "equity_zero_or_negative_count",
            "hard_gate_ok",
        ]
    ]
    verdict, reason = _judge(run_a, run_b)
    comparison_path = sweep_dir / "baseline_vs_variant.csv"
    comparison_df.to_csv(comparison_path, index=False)

    lines = [
        f"# Extreme Regime No-Trade A/B Report ({sweep_id})",
        "",
        "## Scope",
        f"- guide selected baseline run id: `{selected_run_id}`",
        "- fixed config loaded from guide and selected baseline run config",
        "- data_source: `binance` / `testnet=False` (mainnet historical)",
        "- single lever only: `k` (`4` vs `5`)",
        "- fixed: `lookback_score_mode=median_3(7/14/28)`, `rank_buffer=2`, `extreme_no_trade=ON`, `extreme_high_vol_percentile=0.90`, `extreme_non_trend_logic=OR`, `trend_slope_threshold=0.0015`, `extreme_regime_mode=delever`, `extreme_gross_mult=0.5`, `shock_freeze_min_fraction=0.40`",
        "",
        "## Run IDs",
        f"- Run A (`k=4`): `{run_a['run_id']}`",
        f"- Run B (`k=5`): `{run_b['run_id']}`",
        "",
        "## Baseline vs Variant",
        "| scenario | run_id | net_pnl | max_drawdown | fee_cost_total | oos_positive_ratio | avg_turnover_ratio | skipped_ratio | avg_effective_gross |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in comparison_df.to_dict(orient="records"):
        lines.append(
            f"| {row['scenario']} | {row['run_id']} | {float(row['net_pnl']):.4f} | {float(row['max_drawdown']):.6f} | {float(row['fee_cost_total']):.4f} | "
            f"{float(row['oos_positive_ratio']):.6f} | {float(row['avg_turnover_ratio']):.6f} | {float(row['skipped_ratio']):.6f} | {float(row['avg_effective_gross']):.6f} |"
        )
    lines.extend(
        [
            "",
            "## Hard Gate",
            f"- Run A hard_gate_ok: `{bool(run_a['hard_gate_ok'])}`",
            f"- Run B hard_gate_ok: `{bool(run_b['hard_gate_ok'])}`",
            "",
            "## Verdict",
            f"- verdict: **{verdict}**",
            f"- reason: {reason}",
        ]
    )
    if verdict == "FAIL":
        lines.append("- next action: keep `k=4` as finalized setting.")
    (sweep_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (sweep_dir / "runs.json").write_text(
        json.dumps(
            {
                "selected_baseline_run_id": selected_run_id,
                "run_a": run_a,
                "run_b": run_b,
                "verdict": verdict,
                "reason": reason,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(str(sweep_dir))
    print(str(comparison_path))
    print(run_a["run_id"])
    print(run_b["run_id"])
    print(verdict)


if __name__ == "__main__":
    main()
