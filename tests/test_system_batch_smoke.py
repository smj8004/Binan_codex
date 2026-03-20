from __future__ import annotations

from pathlib import Path

from trader.backtest.engine import BacktestConfig
from trader.experiments.runner import SystemCandidate, run_system_batch


def test_system_batch_smoke(tmp_path: Path) -> None:
    tiny_candidates = [
        SystemCandidate(
            system_id="smoke",
            title="smoke",
            track="A",
            strategy_name="ema_cross",
            strategy_params={"fast_len": 8, "slow_len": 26, "risk_template": "balanced"},
            walk_grid_path="config/grids/ema_cross.yaml",
            notes="smoke",
        )
    ]
    out = run_system_batch(
        symbols=["BTC/USDT", "ETH/USDT"],
        timeframe="1h",
        start="2025-01-01",
        end="2025-04-01",
        base_config=BacktestConfig(symbol="BTC/USDT", timeframe="1h", persist_to_db=False),
        output_root=tmp_path,
        seed=7,
        data_source="synthetic",
        csv_path=None,
        testnet=True,
        walk_train_days=20,
        walk_test_days=10,
        walk_step_days=10,
        walk_top_pct=0.2,
        walk_max_candidates=20,
        candidates=tiny_candidates,
        fee_multipliers=[1.0],
        fixed_slippage_bps=[1.0],
        atr_slippage_mults=[0.02],
        latency_bars=[0],
    )
    assert out.batch_dir.exists()
    assert (out.batch_dir / "batch_summary.csv").exists()
    assert (out.batch_dir / "batch_promotion_summary.csv").exists()
    assert len(out.candidate_results) == 1
