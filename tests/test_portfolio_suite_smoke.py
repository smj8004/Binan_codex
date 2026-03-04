from pathlib import Path
import json

from trader.backtest.engine import BacktestConfig
from trader.experiments.runner import run_portfolio_validation


def test_portfolio_suite_smoke(tmp_path: Path) -> None:
    output = run_portfolio_validation(
        symbols=[
            "BTC/USDT",
            "ETH/USDT",
            "BNB/USDT",
            "SOL/USDT",
            "XRP/USDT",
            "ADA/USDT",
        ],
        timeframe="1h",
        start="2025-01-01",
        end="2025-03-01",
        base_config=BacktestConfig(symbol="BTC/USDT", timeframe="1h", persist_to_db=False, initial_equity=10_000.0),
        output_root=tmp_path,
        seed=123,
        data_source="synthetic",
        csv_path=None,
        testnet=True,
        signal_models=["momentum", "mean_reversion"],
        lookback_bars=[24, 48],
        rebalance_bars=[4],
        k_values=[1, 2],
        gross_values=[1.0],
        rank_buffers=[0],
        high_vol_percentiles=[0.65],
        gross_maps=["balanced"],
        off_grace_bars_list=[0],
        phased_entry_steps_list=[1],
        turnover_threshold=0.05,
        turnover_threshold_high_vol=0.10,
        turnover_threshold_low_vol=0.05,
        vol_lookback=48,
        fee_multipliers=[1.0],
        fixed_slippage_bps=[3.0],
        atr_slippage_mults=[0.05],
        slippage_mode="mixed",
        latency_bars=[0],
        order_models=["market"],
        limit_timeout_bars=1,
        limit_fill_probability=0.9,
        limit_unfilled_penalty_bps=2.0,
        walk_train_days=20,
        walk_test_days=10,
        walk_step_days=10,
        walk_top_pct=0.5,
        walk_max_candidates=8,
        walk_metric="sharpe_like",
        trend_ema_span=24,
        trend_slope_lookback=6,
        trend_slope_threshold=0.001,
        regime_atr_period=14,
        regime_vol_lookback=48,
        regime_vol_percentile=0.65,
        high_vol_gross_mult=0.5,
        debug_mode=True,
        max_cost_ratio_per_bar=0.10,
        dd_controller_enabled=True,
        dd_thresholds=(0.10, 0.20, 0.30, 0.40),
        dd_gross_mults=(1.0, 0.7, 0.5, 0.3, 0.0),
        dd_recover_thresholds=(0.08, 0.16, 0.24, 0.32),
        kill_cooldown_bars=24,
        disable_new_entry_when_dd=True,
        rolling_peak_window_bars=240,
        stage_down_confirm_bars=12,
        stage3_down_confirm_bars=24,
        reentry_ramp_steps=2,
        disable_new_entry_stage=3,
        dd_turnover_threshold_mult=1.5,
        dd_rebalance_mult=None,
        cap_mode="adaptive",
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
        trading_halt_bars=24,
        skip_trades_if_cost_exceeds_equity_ratio=0.02,
        transition_smoother_enabled=True,
        gross_step_up=0.10,
        gross_step_down=0.25,
        post_halt_cooldown_bars=24,
        post_halt_max_gross=0.15,
        liquidation_lookback_bars=120,
        liquidation_lookback_max_gross=0.15,
        enable_symbol_shock_filters=True,
        max_abs_weight_per_symbol=0.12,
        atr_shock_threshold=2.5,
        gap_shock_threshold=0.10,
        shock_cooldown_bars=24,
        shock_mode="downweight",
        shock_weight_mult_atr=0.25,
        shock_weight_mult_gap=0.10,
        shock_freeze_rebalance=True,
        shock_freeze_min_fraction=0.30,
        stop_on_anomaly=False,
    )

    assert output.run_dir.exists()
    assert (output.run_dir / "config.json").exists()
    assert (output.run_dir / "summary.csv").exists()
    assert (output.run_dir / "summary.json").exists()
    assert (output.run_dir / "portfolio_equity_curve.csv").exists()
    assert (output.run_dir / "dd_timeline.csv").exists()
    assert (output.run_dir / "gross_target_vs_applied.csv").exists()
    assert (output.run_dir / "excluded_symbols.csv").exists()
    assert (output.run_dir / "symbol_risk_caps.csv").exists()
    assert (output.run_dir / "portfolio_positions.csv").exists()
    assert (output.run_dir / "turnover.csv").exists()
    assert (output.run_dir / "cost_breakdown.csv").exists()
    assert (output.run_dir / "liquidation_events.csv").exists()
    assert (output.run_dir / "diagnostics.json").exists()
    assert (output.run_dir / "debug_dump.json").exists()
    assert (output.run_dir / "report.md").exists()
    assert (output.run_dir / "plots" / "portfolio_equity_curve.png").stat().st_size > 100
    summary = json.loads((output.run_dir / "summary.json").read_text(encoding="utf-8"))
    diagnostics = json.loads((output.run_dir / "diagnostics.json").read_text(encoding="utf-8"))
    dd_timeline = (output.run_dir / "dd_timeline.csv").read_text(encoding="utf-8")
    gross_rows = (output.run_dir / "gross_target_vs_applied.csv").read_text(encoding="utf-8").splitlines()
    cap_rows = (output.run_dir / "symbol_risk_caps.csv").read_text(encoding="utf-8").splitlines()
    turnover_rows = (output.run_dir / "turnover.csv").read_text(encoding="utf-8").splitlines()
    # 2 months, 1h bars, 4h rebalance -> attempts should be comfortably above 100.
    assert float(summary.get("rebalance_attempt_count", 0.0)) >= 100.0
    assert float(diagnostics.get("equity_zero_or_negative_count", 1)) == 0.0
    assert isinstance(diagnostics.get("dd_trigger_counts"), dict)
    assert "rolling_peak" in dd_timeline.splitlines()[0]
    assert "stage_transitions_down" in diagnostics
    assert isinstance(diagnostics.get("cap_histogram"), dict)
    assert float(diagnostics.get("count_cap_hits", -1)) >= 0.0
    assert "shocked_counts_by_reason" in diagnostics
    assert "fraction_of_time_any_shock_active" in diagnostics
    assert "rebalance_skipped_due_to_shock_count" in diagnostics
    excluded_counts = diagnostics.get("excluded_counts_by_reason", {})
    assert isinstance(excluded_counts, dict)
    assert float(sum(float(v) for v in excluded_counts.values())) <= 50.0
    header = gross_rows[0].split(",")
    idx_applied = header.index("applied_gross")
    if len(gross_rows) > 2:
        prev_val = None
        checks = 0
        for row in gross_rows[1:]:
            try:
                val = float(row.split(",")[idx_applied])
            except Exception:
                continue
            if prev_val is not None:
                diff = val - prev_val
                assert diff <= 0.100001
                assert diff >= -0.250001
                checks += 1
                if checks >= 10:
                    break
            prev_val = val
    cap_header = cap_rows[0].split(",")
    cap_idx = cap_header.index("capped_weight")
    cap_checks = 0
    for row in cap_rows[1:]:
        try:
            cval = abs(float(row.split(",")[cap_idx]))
        except Exception:
            continue
        assert cval <= 0.120001
        cap_checks += 1
        if cap_checks >= 10:
            break
    assert "rebalance_skipped_due_to_shock" in turnover_rows[0].split(",")
