from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from trader.backtest.engine import BacktestConfig
from trader.experiments.runner import run_portfolio_validation


def _hard_gate_ok(row: dict[str, float]) -> bool:
    return (
        float(row.get("liquidation_count", 1.0)) == 0.0
        and float(row.get("equity_zero_or_negative_count", 1.0)) == 0.0
        and float(row.get("fee_cost_total", 10_000_000.0)) <= 2000.0
    )


def _pick_recommendation(df: pd.DataFrame) -> pd.Series:
    passed = df[df["hard_gate_ok"] == True].copy()  # noqa: E712
    if passed.empty:
        return df.sort_values(["net_pnl", "max_drawdown", "avg_turnover_ratio"], ascending=[False, False, True]).iloc[0]

    max_pnl = float(passed["net_pnl"].max())
    pnl_similar = passed[passed["net_pnl"] >= (max_pnl * 0.95)].copy()
    if pnl_similar.empty:
        pnl_similar = passed.copy()
    pnl_similar = pnl_similar.sort_values(
        ["max_drawdown", "avg_turnover_ratio", "net_pnl"],
        ascending=[False, True, False],
    )
    return pnl_similar.iloc[0]


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    out_root = repo_root / "out" / "experiments"
    sweep_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    sweep_dir = out_root / f"rank_buffer_sweep_{sweep_id}"
    sweep_dir.mkdir(parents=True, exist_ok=True)

    symbols = [
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
    rank_buffers = [0, 1, 2]
    run_rows: list[dict[str, float | int | str | bool]] = []
    run_meta: list[dict[str, str | int | float | bool]] = []

    for rank_buffer in rank_buffers:
        output = run_portfolio_validation(
            symbols=symbols,
            timeframe="1h",
            start="2021-01-01",
            end="2026-01-01",
            base_config=BacktestConfig(symbol="BTC/USDT", timeframe="1h", persist_to_db=False, initial_equity=10_000.0),
            output_root=out_root,
            seed=44,
            data_source="binance",
            csv_path=None,
            testnet=False,
            signal_models=["momentum"],
            lookback_bars=[24 * 7],
            rebalance_bars=[24],
            k_values=[4],
            gross_values=[1.0],
            rank_buffers=[rank_buffer],
            high_vol_percentiles=[0.85],
            gross_maps=["off_range_highvol"],
            off_grace_bars_list=[24],
            phased_entry_steps_list=[2],
            turnover_threshold=0.08,
            turnover_threshold_high_vol=0.20,
            turnover_threshold_low_vol=0.08,
            vol_lookback=96,
            fee_multipliers=[1.0, 1.5, 2.0],
            fixed_slippage_bps=[2.0],
            atr_slippage_mults=[0.02],
            slippage_mode="mixed",
            latency_bars=[0, 1],
            order_models=["market", "limit"],
            limit_timeout_bars=2,
            limit_fill_probability=0.9,
            limit_unfilled_penalty_bps=3.0,
            walk_train_days=240,
            walk_test_days=60,
            walk_step_days=30,
            walk_top_pct=0.15,
            walk_max_candidates=120,
            walk_metric="sharpe_like",
            trend_ema_span=48,
            trend_slope_lookback=8,
            trend_slope_threshold=0.0015,
            regime_atr_period=14,
            regime_vol_lookback=120,
            regime_vol_percentile=0.65,
            high_vol_gross_mult=0.5,
            debug_mode=True,
            max_cost_ratio_per_bar=0.05,
            dd_controller_enabled=True,
            dd_thresholds=(0.10, 0.20, 0.30, 0.40),
            dd_gross_mults=(1.0, 0.70, 0.50, 0.30, 0.0),
            dd_recover_thresholds=(0.08, 0.16, 0.24, 0.32),
            kill_cooldown_bars=168,
            disable_new_entry_when_dd=True,
            rolling_peak_window_bars=720,
            stage_down_confirm_bars=48,
            stage3_down_confirm_bars=96,
            reentry_ramp_steps=3,
            disable_new_entry_stage=3,
            dd_turnover_threshold_mult=1.5,
            dd_rebalance_mult=None,
            cap_mode="fixed",
            base_cap=0.25,
            cap_min=0.20,
            cap_max=0.40,
            backlog_thresholds=(0.25, 0.50, 0.75),
            cap_steps=(0.25, 0.30, 0.35, 0.40),
            high_vol_cap_max=0.30,
            max_turnover_notional_to_equity=0.25,
            drift_threshold=0.35,
            gross_decay_steps=3,
            max_notional_to_equity_mult=3.0,
            enable_liquidation=True,
            equity_floor_ratio=0.01,
            trading_halt_bars=168,
            skip_trades_if_cost_exceeds_equity_ratio=0.02,
            transition_smoother_enabled=True,
            gross_step_up=0.10,
            gross_step_down=0.25,
            post_halt_cooldown_bars=168,
            post_halt_max_gross=0.15,
            liquidation_lookback_bars=720,
            liquidation_lookback_max_gross=0.15,
            enable_symbol_shock_filters=True,
            max_abs_weight_per_symbol=0.12,
            atr_shock_threshold=2.5,
            gap_shock_threshold=0.10,
            shock_cooldown_bars=72,
            shock_mode="downweight",
            shock_weight_mult_atr=0.25,
            shock_weight_mult_gap=0.10,
            shock_freeze_rebalance=True,
            shock_freeze_min_fraction=0.40,
            lookback_score_mode="median_3",
            stop_on_anomaly=False,
        )

        summary_path = output.run_dir / "summary.json"
        turnover_path = output.run_dir / "turnover.csv"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        turnover_df = pd.read_csv(turnover_path)
        turnover_notional_sum = float(turnover_df["turnover_notional"].fillna(0.0).sum()) if "turnover_notional" in turnover_df.columns else 0.0
        turnover_trade_count_sum = float(turnover_df["trades_this_bar"].fillna(0.0).sum()) if "trades_this_bar" in turnover_df.columns else float(summary.get("trade_count", 0.0))

        row: dict[str, float | int | str | bool] = {
            "run_label": f"rank_buffer_{rank_buffer}",
            "run_id": output.run_id,
            "run_dir": str(output.run_dir.relative_to(repo_root)),
            "rank_buffer": int(rank_buffer),
            "net_pnl": float(summary.get("net_pnl", 0.0)),
            "max_drawdown": float(summary.get("portfolio_max_drawdown", 0.0)),
            "oos_positive_ratio": float(summary.get("oos_positive_ratio", 0.0)),
            "fee_cost_total": float(summary.get("fee_cost_total", 0.0)),
            "liquidation_count": float(summary.get("liquidation_count", 0.0)),
            "equity_zero_or_negative_count": float(summary.get("equity_zero_or_negative_count", 0.0)),
            "avg_turnover_ratio": float(summary.get("avg_turnover_ratio", 0.0)),
            "skipped_ratio": float(summary.get("rebalance_skipped_due_to_shock_ratio", 0.0)),
            "turnover_notional_sum": turnover_notional_sum,
            "trade_count_sum": turnover_trade_count_sum,
        }
        row["hard_gate_ok"] = _hard_gate_ok({k: float(v) for k, v in row.items() if isinstance(v, (int, float))})
        run_rows.append(row)
        run_meta.append(
            {
                "rank_buffer": rank_buffer,
                "run_id": output.run_id,
                "run_dir": str(output.run_dir.relative_to(repo_root)),
                "selected_params": json.dumps(summary.get("selected_params", {}), ensure_ascii=False),
            }
        )

    comparison_df = pd.DataFrame(run_rows).sort_values("rank_buffer").reset_index(drop=True)
    recommendation = _pick_recommendation(comparison_df)
    comparison_path = sweep_dir / "rank_buffer_sweep_comparison.csv"
    comparison_df.to_csv(comparison_path, index=False)

    rec_label = str(recommendation["run_label"])
    rec_reason = (
        f"hard_gate_ok={bool(recommendation['hard_gate_ok'])}, "
        f"net_pnl={float(recommendation['net_pnl']):.2f}, "
        f"max_drawdown={float(recommendation['max_drawdown']):.6f}, "
        f"avg_turnover_ratio={float(recommendation['avg_turnover_ratio']):.6f}, "
        f"fee_cost_total={float(recommendation['fee_cost_total']):.2f}"
    )

    report_lines = [
        f"# Rank Buffer Sweep Report ({sweep_id})",
        "",
        "## Scope",
        "- fixed baseline: ensemble_median_7_14_28 + existing overlays",
        "- changed lever only: rank_buffer in {0, 1, 2}",
        "",
        "## Comparison",
        "```csv",
        comparison_df.to_csv(index=False).strip(),
        "```",
        "",
        "## Interpretation",
        "- as rank_buffer increases, turnover is expected to decrease because held symbols can persist inside the hysteresis band.",
        "- evaluate performance with hard-gate first (liq=0, eq0=0, fee<=2000), then net_pnl priority.",
        "",
        "## Recommendation",
        f"- selected: {rec_label}",
        f"- reason: {rec_reason}",
    ]
    (sweep_dir / "report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    (sweep_dir / "runs.json").write_text(json.dumps(run_meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(str(sweep_dir))
    print(str(comparison_path))
    print(rec_label)


if __name__ == "__main__":
    main()
