from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from trader.research.strategy_search import (
    BroadSweepConfig,
    _BroadCandidate,
    _default_regime_spec,
    run_broad_sweep_candidates,
)


MAJOR_SYMBOLS = {"BTCUSDT", "ETHUSDT", "BNBUSDT"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a narrow final showdown between Donchian and MACD pockets.")
    parser.add_argument("--symbols", nargs="+", required=True, help="Symbols such as BTCUSDT ETHUSDT SOLUSDT")
    parser.add_argument("--intervals", nargs="+", default=["1h", "4h"], help="One or more candle intervals")
    parser.add_argument("--data-root", default="data/futures_historical", help="Historical candle root directory")
    parser.add_argument(
        "--out-root",
        default="out/strategy_search_compare/final_showdown_donchian_vs_macd",
        help="Directory for showdown outputs",
    )
    parser.add_argument("--train-days", type=int, default=180, help="Walk-forward train window in days")
    parser.add_argument("--test-days", type=int, default=60, help="Walk-forward test window in days")
    parser.add_argument("--step-days", type=int, default=60, help="Walk-forward step in days")
    parser.add_argument("--taker-fee-bps", type=float, default=5.0, help="Taker fee in basis points")
    parser.add_argument("--slippage-bps", type=float, default=2.0, help="Fixed slippage in basis points")
    parser.add_argument("--initial-equity", type=float, default=10_000.0, help="Initial equity per symbol backtest")
    parser.add_argument("--fixed-notional-usdt", type=float, default=1_000.0, help="Fixed notional per entry")
    parser.add_argument("--min-trade-count", type=int, default=3, help="Minimum trades for hard-gate preference")
    parser.add_argument("--jobs", type=int, default=1, help="Process worker count")
    parser.add_argument("--stress-taker-fee-multiplier", type=float, default=2.0, help="Taker fee multiplier for stress rerun")
    parser.add_argument("--stress-slippage-multiplier", type=float, default=2.0, help="Slippage multiplier for stress rerun")
    return parser


def _candidate_key(row: pd.Series | dict[str, object]) -> tuple[str, str, str, str, str]:
    return (
        str(row["strategy_family"]),
        str(row["strategy_name"]),
        str(row["interval"]),
        str(row["params_json"]),
        str(row["regime_name"]),
    )


def _build_candidates() -> list[_BroadCandidate]:
    candidates: list[_BroadCandidate] = []
    donchian_regime = _default_regime_spec("donchian_breakout", "1h")
    for entry_period, exit_period in [(30, 5), (30, 10), (40, 5), (40, 10)]:
        candidates.append(
            _BroadCandidate(
                strategy_family="donchian_breakout",
                strategy_name="donchian_breakout",
                params={
                    "entry_period": entry_period,
                    "exit_period": exit_period,
                    "allow_short": False,
                },
                regime_name=donchian_regime.name,
                regime_params=donchian_regime.params,
            )
        )

    macd_regime = _default_regime_spec("macd", "4h")
    for fast_period, slow_period, signal_period in [(8, 21, 5), (10, 30, 7), (12, 26, 9), (16, 32, 9)]:
        candidates.append(
            _BroadCandidate(
                strategy_family="macd",
                strategy_name="macd_momentum",
                params={
                    "fast_period": fast_period,
                    "slow_period": slow_period,
                    "signal_period": signal_period,
                    "use_histogram": False,
                    "histogram_threshold": 0.0,
                    "adx_filter": False,
                    "adx_window": 14,
                    "adx_threshold": 20.0,
                    "allow_short": True,
                },
                regime_name=macd_regime.name,
                regime_params=macd_regime.params,
            )
        )
    return candidates


def _base_config(args: argparse.Namespace, *, out_root: Path, taker_fee_bps: float, slippage_bps: float) -> BroadSweepConfig:
    return BroadSweepConfig(
        intervals=tuple(args.intervals),
        data_root=Path(args.data_root),
        out_root=out_root,
        initial_equity=args.initial_equity,
        fixed_notional_usdt=args.fixed_notional_usdt,
        taker_fee_bps=taker_fee_bps,
        slippage_bps=slippage_bps,
        train_days=args.train_days,
        test_days=args.test_days,
        step_days=args.step_days,
        min_trade_count=args.min_trade_count,
        families=("donchian_breakout", "macd"),
        max_combos=None,
        time_budget_hours=6.0,
        jobs=args.jobs,
        regime_mode="family-default",
    )


def _best_bucket_means(summary_df: pd.DataFrame, by_symbol_df: pd.DataFrame, strategy_family: str) -> tuple[float, float]:
    subset = summary_df[summary_df["strategy_family"] == strategy_family]
    if subset.empty:
        return 0.0, 0.0
    best = subset.iloc[0]
    key = _candidate_key(best)
    rows = by_symbol_df[
        by_symbol_df.apply(
            lambda row: _candidate_key(row) == key,
            axis=1,
        )
    ].copy()
    if rows.empty:
        return 0.0, 0.0
    major = rows[rows["symbol"].isin(MAJOR_SYMBOLS)]
    alt = rows[~rows["symbol"].isin(MAJOR_SYMBOLS)]
    major_mean = float(major["oos_total_return"].mean()) if not major.empty else 0.0
    alt_mean = float(alt["oos_total_return"].mean()) if not alt.empty else 0.0
    return major_mean, alt_mean


def _build_family_comparison(
    *,
    baseline_summary: pd.DataFrame,
    baseline_by_symbol: pd.DataFrame,
    stress_summary: pd.DataFrame,
    family_order: list[str],
) -> pd.DataFrame:
    stress_lookup = {
        _candidate_key(row): row
        for _, row in stress_summary.iterrows()
    }
    rows: list[dict[str, object]] = []
    for family in family_order:
        baseline_family = baseline_summary[baseline_summary["strategy_family"] == family].copy().reset_index(drop=True)
        if baseline_family.empty:
            continue
        stress_family = stress_summary[stress_summary["strategy_family"] == family].copy().reset_index(drop=True)
        best = baseline_family.iloc[0]
        best_key = _candidate_key(best)
        stress_best = stress_lookup.get(best_key)

        pass_rows = baseline_family[baseline_family["hard_gate_pass"]]
        surviving = 0
        for _, row in pass_rows.iterrows():
            stress_row = stress_lookup.get(_candidate_key(row))
            if stress_row is not None and bool(stress_row["hard_gate_pass"]):
                surviving += 1

        major_mean, alt_mean = _best_bucket_means(baseline_family, baseline_by_symbol, family)
        rows.append(
            {
                "strategy_family": family,
                "candidate_count": int(len(baseline_family)),
                "hard_gate_pass_count": int(baseline_family["hard_gate_pass"].sum()),
                "neighborhood_pass_rate": float(baseline_family["hard_gate_pass"].mean()),
                "neighborhood_median_return": float(baseline_family["oos_total_return_mean"].median()),
                "neighborhood_median_sharpe": float(baseline_family["oos_sharpe_mean"].median()),
                "best_rank": int(best["rank"]),
                "best_interval": str(best["interval"]),
                "best_params_json": str(best["params_json"]),
                "best_oos_total_return_mean": float(best["oos_total_return_mean"]),
                "best_oos_sharpe_mean": float(best["oos_sharpe_mean"]),
                "best_oos_max_drawdown_mean": float(best["oos_max_drawdown_mean"]),
                "best_positive_symbols": int(best["positive_symbols"]),
                "best_symbol_return_std": float(best["symbol_return_std"]),
                "best_trade_count_mean": float(best["trade_count_mean"]),
                "best_fee_cost_total": float(best["fee_cost_total"]),
                "best_regime_coverage_ratio": float(best["regime_coverage_ratio"]),
                "best_major_oos_return_mean": major_mean,
                "best_alt_oos_return_mean": alt_mean,
                "max_single_return_in_neighborhood": float(baseline_family["oos_total_return_mean"].max()),
                "max_single_sharpe_in_neighborhood": float(baseline_family["oos_sharpe_mean"].max()),
                "stress_best_candidate_survives": bool(stress_best is not None and bool(stress_best["hard_gate_pass"])),
                "stress_best_candidate_return": float(stress_best["oos_total_return_mean"]) if stress_best is not None else 0.0,
                "stress_best_candidate_sharpe": float(stress_best["oos_sharpe_mean"]) if stress_best is not None else 0.0,
                "stress_neighborhood_pass_count": int(stress_family["hard_gate_pass"].sum()) if not stress_family.empty else 0,
                "stress_neighborhood_pass_rate": float(stress_family["hard_gate_pass"].mean()) if not stress_family.empty else 0.0,
                "stress_survival_count": surviving,
                "stress_survival_rate": float(surviving / len(pass_rows)) if len(pass_rows) else 0.0,
            }
        )
    return pd.DataFrame(rows)


def _build_winner_markdown(family_df: pd.DataFrame) -> tuple[str, list[str]]:
    donch = family_df[family_df["strategy_family"] == "donchian_breakout"].iloc[0]
    macd = family_df[family_df["strategy_family"] == "macd"].iloc[0]
    donch_points = 0
    macd_points = 0

    if float(donch["best_oos_sharpe_mean"]) >= float(macd["best_oos_sharpe_mean"]):
        donch_points += 1
    else:
        macd_points += 1
    if float(donch["best_positive_symbols"]) >= float(macd["best_positive_symbols"]):
        donch_points += 1
    else:
        macd_points += 1
    if float(donch["stress_survival_rate"]) >= float(macd["stress_survival_rate"]):
        donch_points += 1
    else:
        macd_points += 1
    if float(donch["best_symbol_return_std"]) <= float(macd["best_symbol_return_std"]):
        donch_points += 1
    else:
        macd_points += 1

    winner = "donchian_breakout" if donch_points >= macd_points else "macd"
    reasons = [
        f"`{winner}` won the stricter reproducibility vote with the better combination of sharpe, breadth, and stress survival.",
        f"`donchian_breakout` best candidate: return `{float(donch['best_oos_total_return_mean']):.4f}`, sharpe `{float(donch['best_oos_sharpe_mean']):.4f}`, pass rate `{float(donch['neighborhood_pass_rate']):.2%}`.",
        f"`macd` best candidate: return `{float(macd['best_oos_total_return_mean']):.4f}`, sharpe `{float(macd['best_oos_sharpe_mean']):.4f}`, pass rate `{float(macd['neighborhood_pass_rate']):.2%}`.",
    ]
    return winner, reasons


def _markdown_table(df: pd.DataFrame, columns: list[str]) -> list[str]:
    if df.empty:
        return ["_No rows available._"]
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for _, row in df[columns].iterrows():
        values: list[str] = []
        for col in columns:
            value = row[col]
            if isinstance(value, float):
                values.append(f"{value:.6f}")
            else:
                values.append(str(value))
        lines.append("| " + " | ".join(values) + " |")
    return lines


def _build_markdown(*, family_df: pd.DataFrame, winner: str, reasons: list[str]) -> str:
    lines = [
        "# Donchian vs MACD Final Showdown",
        "",
        f"- winner: `{winner}`",
        "",
        "## Decision Reasons",
        "",
    ]
    for reason in reasons:
        lines.append(f"- {reason}")
    lines.extend(["", "## Family Comparison", ""])
    lines.extend(
        _markdown_table(
            family_df,
            [
                "strategy_family",
                "candidate_count",
                "hard_gate_pass_count",
                "neighborhood_pass_rate",
                "neighborhood_median_return",
                "best_oos_total_return_mean",
                "best_oos_sharpe_mean",
                "best_positive_symbols",
                "best_symbol_return_std",
                "best_trade_count_mean",
                "best_fee_cost_total",
                "best_regime_coverage_ratio",
                "stress_survival_rate",
            ],
        )
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    args = build_parser().parse_args()
    out_root = Path(args.out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    symbols = [str(symbol).upper() for symbol in args.symbols]
    candidates = _build_candidates()

    baseline_config = _base_config(
        args,
        out_root=out_root / "baseline",
        taker_fee_bps=float(args.taker_fee_bps),
        slippage_bps=float(args.slippage_bps),
    )
    baseline_result = run_broad_sweep_candidates(
        symbols=symbols,
        config=baseline_config,
        candidates=candidates,
        raw_combo_count=len(candidates),
    )

    stress_config = _base_config(
        args,
        out_root=out_root / "mixed_2x",
        taker_fee_bps=float(args.taker_fee_bps) * float(args.stress_taker_fee_multiplier),
        slippage_bps=float(args.slippage_bps) * float(args.stress_slippage_multiplier),
    )
    stress_result = run_broad_sweep_candidates(
        symbols=symbols,
        config=stress_config,
        candidates=candidates,
        raw_combo_count=len(candidates),
    )

    family_order = ["donchian_breakout", "macd"]
    family_df = _build_family_comparison(
        baseline_summary=baseline_result.summary_df,
        baseline_by_symbol=baseline_result.by_symbol_df,
        stress_summary=stress_result.summary_df,
        family_order=family_order,
    )
    winner, reasons = _build_winner_markdown(family_df)

    family_csv = out_root / "showdown_family_comparison.csv"
    markdown_path = out_root / "showdown.md"
    family_df.to_csv(family_csv, index=False)
    markdown_path.write_text(_build_markdown(family_df=family_df, winner=winner, reasons=reasons), encoding="utf-8")

    print(f"baseline_summary_csv={baseline_result.summary_path}")
    print(f"stress_summary_csv={stress_result.summary_path}")
    print(f"showdown_family_csv={family_csv}")
    print(f"showdown_markdown={markdown_path}")
    print(f"winner={winner}")


if __name__ == "__main__":
    main()
