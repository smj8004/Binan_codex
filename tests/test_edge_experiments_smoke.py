from __future__ import annotations

from pathlib import Path

from trader.backtest.engine import BacktestConfig
from trader.experiments.runner import run_edge_validation


def test_edge_experiments_smoke(tmp_path: Path) -> None:
    grid_path = tmp_path / "ema_grid.yaml"
    grid_path.write_text(
        "fast_len: [8, 12]\n"
        "slow_len: [21, 34]\n"
        "stop_loss_pct: [0.0]\n"
        "take_profit_pct: [0.0]\n",
        encoding="utf-8",
    )

    output = run_edge_validation(
        symbol="BTC/USDT",
        timeframe="1h",
        start="2025-01-01",
        end="2025-02-01",
        strategy_name="ema_cross",
        strategy_params={"fast_len": 12, "slow_len": 26, "stop_loss_pct": 0.0, "take_profit_pct": 0.0},
        base_config=BacktestConfig(
            symbol="BTC/USDT",
            timeframe="1h",
            initial_equity=10_000.0,
            persist_to_db=False,
        ),
        output_root=tmp_path,
        seed=123,
        data_source="synthetic",
        csv_path=None,
        testnet=True,
        suite="all",
        fee_multipliers=[1.0, 2.0],
        fixed_slippage_bps=[1.0, 5.0],
        atr_slippage_mults=[0.02],
        slippage_mode="mixed",
        latency_bars=[0, 1],
        order_models=["market", "limit"],
        limit_timeout_bars=1,
        limit_fill_probability=0.9,
        limit_unfilled_penalty_bps=2.0,
        walk_train_days=8,
        walk_test_days=4,
        walk_step_days=4,
        walk_top_pct=0.5,
        walk_max_candidates=4,
        walk_metric="sharpe_like",
        walk_grid_path=str(grid_path),
        trend_ema_span=24,
        trend_slope_lookback=6,
        trend_slope_threshold=0.001,
        regime_atr_period=14,
        regime_vol_lookback=80,
        regime_vol_percentile=0.65,
    )

    assert output.run_dir.exists()
    assert (output.run_dir / "config.json").exists()
    assert (output.run_dir / "summary.csv").exists()
    assert (output.run_dir / "report.md").exists()
    assert (output.run_dir / "cost_stress.csv").exists()
    assert (output.run_dir / "walk_forward_windows.csv").exists()
    assert (output.run_dir / "regime_table.csv").exists()
    assert (output.run_dir / "plots" / "cost_net_pnl_line.png").stat().st_size > 100
    assert (output.run_dir / "plots" / "walk_forward_oos_hist.png").stat().st_size > 100
    assert (output.run_dir / "plots" / "regime_net_pnl_bar.png").stat().st_size > 100
