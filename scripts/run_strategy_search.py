from __future__ import annotations

import argparse
from pathlib import Path

from trader.research.strategy_search import (
    BroadSweepConfig,
    SUPPORTED_FAMILIES,
    SUPPORTED_STRATEGIES,
    StrategySearchConfig,
    run_broad_sweep,
    run_strategy_search,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run walk-forward strategy research on saved Binance futures historical candles.")
    parser.add_argument("--symbols", nargs="+", required=True, help="Symbols such as BTCUSDT ETHUSDT SOLUSDT")
    parser.add_argument("--mode", choices=["legacy", "broad-sweep"], default="legacy", help="Research execution mode")
    parser.add_argument("--interval", default="1h", help="Single candle interval for legacy mode")
    parser.add_argument("--intervals", nargs="+", help="One or more candle intervals for broad-sweep mode")
    parser.add_argument("--data-root", default="data/futures_historical", help="Historical candle root directory")
    parser.add_argument("--out-root", help="Directory for search outputs")
    parser.add_argument("--train-days", type=int, default=180, help="Walk-forward train window in days")
    parser.add_argument("--test-days", type=int, default=60, help="Walk-forward test window in days")
    parser.add_argument("--step-days", type=int, default=60, help="Walk-forward step in days")
    parser.add_argument("--taker-fee-bps", type=float, default=5.0, help="Taker fee in basis points")
    parser.add_argument("--slippage-bps", type=float, default=2.0, help="Fixed slippage in basis points")
    parser.add_argument("--taker-fee-multiplier", type=float, default=1.0, help="Optional multiplier applied to taker fee bps")
    parser.add_argument("--slippage-multiplier", type=float, default=1.0, help="Optional multiplier applied to slippage bps")
    parser.add_argument("--initial-equity", type=float, default=10_000.0, help="Initial equity per symbol backtest")
    parser.add_argument("--fixed-notional-usdt", type=float, default=1_000.0, help="Fixed notional per entry")
    parser.add_argument("--min-trade-count", type=int, default=3, help="Minimum trades for train candidate preference")
    parser.add_argument(
        "--strategies",
        nargs="+",
        choices=list(SUPPORTED_STRATEGIES),
        help="Optional subset of legacy strategies to execute",
    )
    parser.add_argument(
        "--families",
        nargs="+",
        choices=list(SUPPORTED_FAMILIES),
        help="Optional subset of broad-sweep families to execute",
    )
    parser.add_argument("--time-budget-hours", type=float, default=6.0, help="Broad-sweep wall-clock budget target")
    parser.add_argument("--max-combos", type=int, help="Optional hard cap on broad-sweep parameter combinations")
    parser.add_argument("--jobs", type=int, help="Process worker count for broad sweep")
    parser.add_argument(
        "--regime-mode",
        choices=["off", "family-default"],
        default="off",
        help="Optional regime gating mode for broad-sweep candidates",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    symbols = [str(symbol).upper() for symbol in args.symbols]
    data_root = Path(args.data_root)
    effective_taker_fee_bps = float(args.taker_fee_bps) * float(args.taker_fee_multiplier)
    effective_slippage_bps = float(args.slippage_bps) * float(args.slippage_multiplier)

    if args.mode == "broad-sweep":
        intervals = tuple(args.intervals) if args.intervals else ("1h", "4h")
        config = BroadSweepConfig(
            intervals=intervals,
            data_root=data_root,
            out_root=Path(args.out_root) if args.out_root else Path("out/strategy_search_matrix"),
            initial_equity=args.initial_equity,
            fixed_notional_usdt=args.fixed_notional_usdt,
            taker_fee_bps=effective_taker_fee_bps,
            slippage_bps=effective_slippage_bps,
            train_days=args.train_days,
            test_days=args.test_days,
            step_days=args.step_days,
            min_trade_count=args.min_trade_count,
            families=tuple(args.families) if args.families else None,
            max_combos=args.max_combos if args.max_combos is not None else BroadSweepConfig().max_combos,
            time_budget_hours=args.time_budget_hours,
            jobs=args.jobs if args.jobs is not None else BroadSweepConfig().jobs,
            regime_mode=args.regime_mode,
        )
        result = run_broad_sweep(symbols=symbols, config=config)
        print("\n=== BROAD SWEEP RESULTS ===")
        print(f"summary_csv={result.summary_path}")
        print(f"by_symbol_csv={result.by_symbol_path}")
        print(f"window_results_csv={result.window_results_path}")
        print(f"strategy_family_summary_csv={result.family_summary_path}")
        print(f"top_strategies_md={result.markdown_path}")

        if not result.summary_df.empty:
            best = result.summary_df.iloc[0]
            print(
                f"\ntop_strategy={best['strategy_family']}/{best['strategy_name']} "
                f"interval={best['interval']} "
                f"oos_total_return_mean={float(best['oos_total_return_mean']):.4f} "
                f"oos_sharpe_mean={float(best['oos_sharpe_mean']):.4f}"
            )

            # Hard gate summary
            hard_gate_pass_count = int(result.summary_df["hard_gate_pass"].sum())
            total_candidates = len(result.summary_df)
            print(f"\nhard_gate_pass_count={hard_gate_pass_count}/{total_candidates}")

            if hard_gate_pass_count == 0:
                print("\n[!] NO HARD-GATE WINNERS FOUND")
                print("Next recommended actions:")
                print("  1. Try different timeframes (15m, 2h, 8h, 1d)")
                print("  2. Expand symbol universe (test 15-20 symbols)")
                print("  3. Add regime-conditional filters to top families")
                print("  4. Consider portfolio cross-sectional approach")
            else:
                print(f"\n[+] {hard_gate_pass_count} strategies passed hard gate")
                print("Review top_strategies.md for operational validation candidates")
        return

    config = StrategySearchConfig(
        interval=args.interval,
        data_root=data_root,
        out_root=Path(args.out_root) if args.out_root else Path("out/strategy_search"),
        initial_equity=args.initial_equity,
        fixed_notional_usdt=args.fixed_notional_usdt,
        taker_fee_bps=effective_taker_fee_bps,
        slippage_bps=effective_slippage_bps,
        train_days=args.train_days,
        test_days=args.test_days,
        step_days=args.step_days,
        min_trade_count=args.min_trade_count,
        strategies=tuple(args.strategies) if args.strategies else None,
    )
    result = run_strategy_search(symbols=symbols, config=config)
    print("\n=== STRATEGY SEARCH RESULTS ===")
    print(f"summary_csv={result.summary_path}")
    print(f"by_symbol_csv={result.by_symbol_path}")
    print(f"top_strategies_md={result.markdown_path}")

    if not result.summary_df.empty:
        best = result.summary_df.iloc[0]
        print(
            f"\ntop_strategy={best['strategy']} "
            f"oos_total_return_mean={float(best['oos_total_return_mean']):.4f} "
            f"oos_sharpe_mean={float(best['oos_sharpe_mean']):.4f}"
        )

        # Hard gate summary
        hard_gate_pass_count = int(result.summary_df["hard_gate_pass"].sum())
        total_candidates = len(result.summary_df)
        print(f"\nhard_gate_pass_count={hard_gate_pass_count}/{total_candidates}")

        if hard_gate_pass_count == 0:
            print("\n[!] NO HARD-GATE WINNERS IN THIS SEARCH")
            print("Consider running broad-sweep mode to explore more families/intervals:")
            print("  uv run --active python scripts/run_strategy_search.py \\")
            print("    --symbols BTCUSDT ETHUSDT ... \\")
            print("    --intervals 1h 4h \\")
            print("    --mode broad-sweep")
        else:
            print(f"\n[+] {hard_gate_pass_count} strategies passed hard gate")
            print("Review top_strategies.md for operational validation candidates")


if __name__ == "__main__":
    main()
