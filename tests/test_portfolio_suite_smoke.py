from pathlib import Path

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
        turnover_threshold=0.05,
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
    )

    assert output.run_dir.exists()
    assert (output.run_dir / "config.json").exists()
    assert (output.run_dir / "summary.csv").exists()
    assert (output.run_dir / "summary.json").exists()
    assert (output.run_dir / "portfolio_equity_curve.csv").exists()
    assert (output.run_dir / "portfolio_positions.csv").exists()
    assert (output.run_dir / "turnover.csv").exists()
    assert (output.run_dir / "cost_breakdown.csv").exists()
    assert (output.run_dir / "report.md").exists()
    assert (output.run_dir / "plots" / "portfolio_equity_curve.png").stat().st_size > 100
