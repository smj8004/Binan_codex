from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from trader.backtest.engine import BacktestConfig
from trader.experiments.runner import run_system_batch
from trader.research.promotion import sort_promotion_records, write_promotion_markdown
from trader.research.strategy_search import (
    BroadSweepConfig,
    _BroadCandidate,
    _default_regime_spec,
    build_broad_candidate_promotion_record,
    run_broad_candidate_holdout,
    run_broad_sweep_candidates,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the unified research promotion ladder for Track A/B/C and the incumbent MACD benchmark.")
    parser.add_argument("--symbols", nargs="+", required=True, help="Symbols such as BTCUSDT ETHUSDT SOLUSDT")
    parser.add_argument("--data-root", default="data/futures_historical", help="Historical candle root directory")
    parser.add_argument("--out-root", default="out/strategy_search_compare/unified_candidate_ladder", help="Output directory")
    parser.add_argument("--timeframe", default="4h", help="Primary timeframe for Track A/B/C batch evaluation")
    parser.add_argument("--benchmark-interval", default="4h", help="Benchmark interval for incumbent MACD")
    parser.add_argument("--train-days", type=int, default=180)
    parser.add_argument("--test-days", type=int, default=60)
    parser.add_argument("--step-days", type=int, default=60)
    parser.add_argument("--holdout-days", type=int, default=120)
    parser.add_argument("--initial-equity", type=float, default=10_000.0)
    parser.add_argument("--fixed-notional-usdt", type=float, default=1_000.0)
    parser.add_argument("--taker-fee-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    return parser


def _incumbent_candidate(interval: str) -> _BroadCandidate:
    regime = _default_regime_spec("macd", interval)
    return _BroadCandidate(
        strategy_family="macd",
        strategy_name="macd_momentum",
        params={
            "fast_period": 12,
            "slow_period": 26,
            "signal_period": 9,
            "use_histogram": False,
            "histogram_threshold": 0.0,
            "adx_filter": False,
            "adx_window": 14,
            "adx_threshold": 20.0,
            "allow_short": True,
        },
        regime_name=regime.name,
        regime_params=regime.params,
    )


def main() -> None:
    args = build_parser().parse_args()
    data_root = Path(args.data_root)
    out_root = Path(args.out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    batch = run_system_batch(
        symbols=[str(symbol).upper() for symbol in args.symbols],
        timeframe=args.timeframe,
        start="2021-01-01",
        end="2026-03-01",
        base_config=BacktestConfig(
            symbol=str(args.symbols[0]).upper(),
            timeframe=args.timeframe,
            initial_equity=args.initial_equity,
            fixed_notional_usdt=args.fixed_notional_usdt,
            taker_fee_bps=args.taker_fee_bps,
            slippage_bps=args.slippage_bps,
            persist_to_db=False,
        ),
        output_root=out_root / "tracks",
        seed=42,
        data_source="csv",
        csv_path=str(data_root),
        testnet=False,
        walk_train_days=args.train_days,
        walk_test_days=args.test_days,
        walk_step_days=args.step_days,
        holdout_days=args.holdout_days,
        promotion_only=True,
    )
    track_df = pd.read_csv(batch.batch_dir / "batch_promotion_summary.csv")

    benchmark_candidate = _incumbent_candidate(args.benchmark_interval)
    baseline_cfg = BroadSweepConfig(
        intervals=(args.benchmark_interval,),
        data_root=data_root,
        out_root=out_root / "benchmark" / "baseline",
        initial_equity=args.initial_equity,
        fixed_notional_usdt=args.fixed_notional_usdt,
        taker_fee_bps=args.taker_fee_bps,
        slippage_bps=args.slippage_bps,
        train_days=args.train_days,
        test_days=args.test_days,
        step_days=args.step_days,
        min_trade_count=3,
        families=("macd",),
        max_combos=1,
        jobs=1,
        regime_mode="family-default",
    )
    stress_cfg = BroadSweepConfig(
        intervals=(args.benchmark_interval,),
        data_root=data_root,
        out_root=out_root / "benchmark" / "mixed_2x",
        initial_equity=args.initial_equity,
        fixed_notional_usdt=args.fixed_notional_usdt,
        taker_fee_bps=args.taker_fee_bps * 2.0,
        slippage_bps=args.slippage_bps * 2.0,
        train_days=args.train_days,
        test_days=args.test_days,
        step_days=args.step_days,
        min_trade_count=3,
        families=("macd",),
        max_combos=1,
        jobs=1,
        regime_mode="family-default",
    )

    baseline_result = run_broad_sweep_candidates(
        symbols=[str(symbol).upper() for symbol in args.symbols],
        config=baseline_cfg,
        candidates=[benchmark_candidate],
        raw_combo_count=1,
    )
    stress_result = run_broad_sweep_candidates(
        symbols=[str(symbol).upper() for symbol in args.symbols],
        config=stress_cfg,
        candidates=[benchmark_candidate],
        raw_combo_count=1,
    )
    holdout_result = run_broad_candidate_holdout(
        symbols=[str(symbol).upper() for symbol in args.symbols],
        interval=args.benchmark_interval,
        candidate=benchmark_candidate,
        config=baseline_cfg,
        holdout_days=args.holdout_days,
    )
    holdout_stress_result = run_broad_candidate_holdout(
        symbols=[str(symbol).upper() for symbol in args.symbols],
        interval=args.benchmark_interval,
        candidate=benchmark_candidate,
        config=stress_cfg,
        holdout_days=args.holdout_days,
    )
    holdout_result.summary_df.to_csv(out_root / "benchmark" / "holdout_summary.csv", index=False)
    holdout_result.by_symbol_df.to_csv(out_root / "benchmark" / "holdout_by_symbol.csv", index=False)
    holdout_stress_result.summary_df.to_csv(out_root / "benchmark" / "holdout_mixed_2x_summary.csv", index=False)
    holdout_stress_result.by_symbol_df.to_csv(out_root / "benchmark" / "holdout_mixed_2x_by_symbol.csv", index=False)

    benchmark_row = build_broad_candidate_promotion_record(
        baseline_summary_row=baseline_result.summary_df.iloc[0],
        stress_summary_row=stress_result.summary_df.iloc[0],
        holdout_summary_row=holdout_result.summary_df.iloc[0],
        holdout_stress_summary_row=holdout_stress_result.summary_df.iloc[0],
        candidate_id="incumbent_macd_regime_gated",
        title="Incumbent Regime-Gated MACD",
        track="Benchmark",
    )
    benchmark_df = pd.DataFrame([benchmark_row])
    benchmark_df.to_csv(out_root / "benchmark" / "benchmark_promotion_summary.csv", index=False)

    combined_df = sort_promotion_records(pd.concat([track_df, benchmark_df], ignore_index=True, sort=False))
    combined_path = out_root / "shortlist_summary.csv"
    combined_df.to_csv(combined_path, index=False)
    markdown_path = out_root / "shortlist.md"
    write_promotion_markdown(path=markdown_path, df=combined_df, heading="Unified Candidate Ladder")

    print(f"track_batch_dir={batch.batch_dir}")
    print(f"benchmark_summary_csv={out_root / 'benchmark' / 'benchmark_promotion_summary.csv'}")
    print(f"shortlist_summary_csv={combined_path}")
    print(f"shortlist_markdown={markdown_path}")


if __name__ == "__main__":
    main()
