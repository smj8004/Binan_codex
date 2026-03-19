from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from trader.backtest.engine import BacktestEngine
from trader.research.strategy_search import (
    BroadSweepConfig,
    _Accumulator,
    _BroadCandidate,
    _default_regime_spec,
    _json_dumps,
    _load_candles_for_interval,
    _run_broad_backtest,
)


MAJOR_SYMBOLS = {"BTCUSDT", "ETHUSDT", "BNBUSDT"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run fixed-pocket holdout validation for selected finalists.")
    parser.add_argument("--symbols", nargs="+", required=True, help="Symbols such as BTCUSDT ETHUSDT SOLUSDT")
    parser.add_argument("--data-root", default="data/futures_historical", help="Historical candle root directory")
    parser.add_argument(
        "--out-root",
        default="out/strategy_search_compare/final_showdown_donchian_vs_macd_holdout",
        help="Directory for holdout validation outputs",
    )
    parser.add_argument(
        "--mode",
        choices=("standard", "extended-macd-confirmation"),
        default="standard",
        help="Validation mode. 'standard' compares Donchian vs MACD on one trailing holdout. "
        "'extended-macd-confirmation' runs MACD across 60d/90d/120d holdouts.",
    )
    parser.add_argument("--holdout-days", type=int, default=120, help="Trailing days reserved for holdout validation")
    parser.add_argument("--train-days", type=int, default=180, help="Carry-through broad-sweep train window")
    parser.add_argument("--test-days", type=int, default=60, help="Carry-through broad-sweep test window")
    parser.add_argument("--step-days", type=int, default=60, help="Carry-through broad-sweep step window")
    parser.add_argument("--taker-fee-bps", type=float, default=5.0, help="Baseline taker fee in basis points")
    parser.add_argument("--slippage-bps", type=float, default=2.0, help="Baseline slippage in basis points")
    parser.add_argument("--initial-equity", type=float, default=10_000.0, help="Initial equity per symbol backtest")
    parser.add_argument("--fixed-notional-usdt", type=float, default=1_000.0, help="Fixed notional per entry")
    parser.add_argument("--min-trade-count", type=int, default=3, help="Minimum trade count hint")
    parser.add_argument("--jobs", type=int, default=1, help="Reserved for interface consistency")
    return parser


def _primary_candidate() -> tuple[str, str, _BroadCandidate]:
    regime = _default_regime_spec("donchian_breakout", "1h")
    return (
        "donchian_breakout",
        "1h",
        _BroadCandidate(
            strategy_family="donchian_breakout",
            strategy_name="donchian_breakout",
            params={"entry_period": 40, "exit_period": 5, "allow_short": False},
            regime_name=regime.name,
            regime_params=regime.params,
        ),
    )


def _control_candidate() -> tuple[str, str, _BroadCandidate]:
    regime = _default_regime_spec("macd", "4h")
    return (
        "macd",
        "4h",
        _BroadCandidate(
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
        ),
    )


def _config(
    *,
    args: argparse.Namespace,
    out_root: Path,
    taker_fee_bps: float,
    slippage_bps: float,
    families: tuple[str, ...],
) -> BroadSweepConfig:
    return BroadSweepConfig(
        intervals=("1h", "4h"),
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
        families=families,
        max_combos=None,
        time_budget_hours=6.0,
        jobs=max(1, int(args.jobs)),
        regime_mode="family-default",
    )


def _candidate_metrics(
    *,
    symbols: list[str],
    data_root: Path,
    candidate_name: str,
    interval: str,
    candidate: _BroadCandidate,
    config: BroadSweepConfig,
    holdout_days: int,
    scenario_name: str,
    holdout_label: str,
) -> tuple[dict[str, object], list[dict[str, object]], dict[str, str]]:
    engine = BacktestEngine()
    by_symbol_rows: list[dict[str, object]] = []
    holdout_start_text = ""
    holdout_end_text = ""

    for symbol in symbols:
        candles = _load_candles_for_interval(symbol=symbol, interval=interval, data_root=data_root)
        holdout_end = candles["timestamp"].max()
        holdout_start = holdout_end - pd.Timedelta(days=int(holdout_days))
        holdout_candles = candles[candles["timestamp"] >= holdout_start].reset_index(drop=True)
        holdout_start_text = holdout_candles["timestamp"].min().isoformat()
        holdout_end_text = holdout_candles["timestamp"].max().isoformat()

        result, coverage_ratio = _run_broad_backtest(
            candles=holdout_candles,
            symbol=symbol,
            interval=interval,
            candidate=candidate,
            config=config,
            engine=engine,
        )
        acc = _Accumulator(initial_equity=config.initial_equity, timeframe=interval)
        acc.add(result=result, start_ts=holdout_candles["timestamp"].min(), end_ts=holdout_candles["timestamp"].max())
        metrics = acc.metrics()
        by_symbol_rows.append(
            {
                "scenario": scenario_name,
                "holdout_label": holdout_label,
                "holdout_days": int(holdout_days),
                "strategy_family": candidate_name,
                "strategy_name": candidate.strategy_name,
                "interval": interval,
                "symbol": symbol,
                "params_json": _json_dumps(candidate.params),
                "regime_name": candidate.regime_name,
                "regime_params_json": _json_dumps(candidate.regime_params or {}),
                "holdout_start": holdout_start_text,
                "holdout_end": holdout_end_text,
                "holdout_total_return": metrics["total_return"],
                "holdout_cagr": metrics["cagr"],
                "holdout_max_drawdown": metrics["max_drawdown"],
                "holdout_sharpe": metrics["sharpe_like"],
                "trade_count": metrics["trade_count"],
                "win_rate": metrics["win_rate"],
                "fee_cost_total": metrics["fee_cost_total"],
                "avg_trade_return": metrics["avg_trade_return"],
                "gross_pnl_total": metrics["gross_pnl_total"],
                "net_pnl_total": metrics["net_pnl_total"],
                "fee_to_gross_ratio": metrics["fee_to_gross_ratio"],
                "oos_positive": bool(metrics["total_return"] > 0.0),
                "regime_coverage_ratio": coverage_ratio,
            }
        )

    by_symbol_df = pd.DataFrame(by_symbol_rows)
    major = by_symbol_df[by_symbol_df["symbol"].isin(MAJOR_SYMBOLS)]
    alt = by_symbol_df[~by_symbol_df["symbol"].isin(MAJOR_SYMBOLS)]
    row = {
        "scenario": scenario_name,
        "holdout_label": holdout_label,
        "holdout_days": int(holdout_days),
        "strategy_family": candidate_name,
        "strategy_name": candidate.strategy_name,
        "interval": interval,
        "params_json": _json_dumps(candidate.params),
        "regime_name": candidate.regime_name,
        "regime_params_json": _json_dumps(candidate.regime_params or {}),
        "holdout_start": holdout_start_text,
        "holdout_end": holdout_end_text,
        "symbol_count": int(len(by_symbol_df)),
        "holdout_total_return_mean": float(by_symbol_df["holdout_total_return"].mean()),
        "holdout_sharpe_mean": float(by_symbol_df["holdout_sharpe"].mean()),
        "holdout_max_drawdown_mean": float(by_symbol_df["holdout_max_drawdown"].mean()),
        "positive_symbols": int(by_symbol_df["oos_positive"].sum()),
        "symbol_return_std": float(by_symbol_df["holdout_total_return"].std(ddof=0)) if len(by_symbol_df) > 1 else 0.0,
        "trade_count_mean": float(by_symbol_df["trade_count"].mean()),
        "trade_count_total": float(by_symbol_df["trade_count"].sum()),
        "fee_cost_total": float(by_symbol_df["fee_cost_total"].sum()),
        "regime_coverage_ratio": float(by_symbol_df["regime_coverage_ratio"].mean()),
        "major_holdout_return_mean": float(major["holdout_total_return"].mean()) if not major.empty else 0.0,
        "alt_holdout_return_mean": float(alt["holdout_total_return"].mean()) if not alt.empty else 0.0,
    }
    return row, by_symbol_rows, {"holdout_start": holdout_start_text, "holdout_end": holdout_end_text}


def _scenario_rows(
    *,
    symbols: list[str],
    args: argparse.Namespace,
    scenario_name: str,
    config: BroadSweepConfig,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, str]]:
    candidates = [_primary_candidate(), _control_candidate()]
    summary_rows: list[dict[str, object]] = []
    by_symbol_rows: list[dict[str, object]] = []
    holdout_meta = {"holdout_start": "", "holdout_end": ""}

    for family_name, interval, candidate in candidates:
        row, symbol_rows, holdout_meta = _candidate_metrics(
            symbols=symbols,
            data_root=Path(args.data_root),
            candidate_name=family_name,
            interval=interval,
            candidate=candidate,
            config=config,
            holdout_days=int(args.holdout_days),
            scenario_name=scenario_name,
            holdout_label=f"{int(args.holdout_days)}d",
        )
        summary_rows.append(row)
        by_symbol_rows.extend(symbol_rows)
    return pd.DataFrame(summary_rows), pd.DataFrame(by_symbol_rows), holdout_meta


def _comparison_df(*, baseline_df: pd.DataFrame, stress_df: pd.DataFrame) -> pd.DataFrame:
    stress_lookup = {str(row["strategy_family"]): row for _, row in stress_df.iterrows()}
    rows: list[dict[str, object]] = []
    for _, row in baseline_df.iterrows():
        family = str(row["strategy_family"])
        stress_row = stress_lookup[family]
        rows.append(
            {
                "strategy_family": family,
                "interval": str(row["interval"]),
                "best_params_json": str(row["params_json"]),
                "baseline_holdout_total_return": float(row["holdout_total_return_mean"]),
                "baseline_holdout_sharpe": float(row["holdout_sharpe_mean"]),
                "baseline_holdout_max_drawdown": float(row["holdout_max_drawdown_mean"]),
                "baseline_positive_symbols": int(row["positive_symbols"]),
                "baseline_symbol_return_std": float(row["symbol_return_std"]),
                "baseline_trade_count_mean": float(row["trade_count_mean"]),
                "baseline_fee_cost_total": float(row["fee_cost_total"]),
                "baseline_regime_coverage_ratio": float(row["regime_coverage_ratio"]),
                "baseline_major_holdout_return_mean": float(row["major_holdout_return_mean"]),
                "baseline_alt_holdout_return_mean": float(row["alt_holdout_return_mean"]),
                "stress_holdout_total_return": float(stress_row["holdout_total_return_mean"]),
                "stress_holdout_sharpe": float(stress_row["holdout_sharpe_mean"]),
                "stress_holdout_max_drawdown": float(stress_row["holdout_max_drawdown_mean"]),
                "stress_positive_symbols": int(stress_row["positive_symbols"]),
                "stress_symbol_return_std": float(stress_row["symbol_return_std"]),
                "stress_trade_count_mean": float(stress_row["trade_count_mean"]),
                "stress_fee_cost_total": float(stress_row["fee_cost_total"]),
                "stress_regime_coverage_ratio": float(stress_row["regime_coverage_ratio"]),
                "stress_major_holdout_return_mean": float(stress_row["major_holdout_return_mean"]),
                "stress_alt_holdout_return_mean": float(stress_row["alt_holdout_return_mean"]),
            }
        )
    return pd.DataFrame(rows)


def _render_markdown(*, comparison_df: pd.DataFrame, holdout_start: str, holdout_end: str) -> str:
    winner = "donchian_keep"
    if not comparison_df.empty:
        donch = comparison_df[comparison_df["strategy_family"] == "donchian_breakout"].iloc[0]
        macd = comparison_df[comparison_df["strategy_family"] == "macd"].iloc[0]
        if (
            float(macd["baseline_holdout_total_return"]) > float(donch["baseline_holdout_total_return"])
            and float(macd["stress_holdout_total_return"]) > float(donch["stress_holdout_total_return"])
        ):
            winner = "macd_promote"
        elif float(donch["baseline_holdout_total_return"]) <= 0.0 and float(macd["baseline_holdout_total_return"]) <= 0.0:
            winner = "revisit_required"

    lines = [
        "# Holdout Validation",
        "",
        f"- holdout_start: `{holdout_start}`",
        f"- holdout_end: `{holdout_end}`",
        f"- decision_hint: `{winner}`",
        "",
        "## Holdout Comparison",
        "",
        "| family | baseline_return | baseline_sharpe | baseline_mdd | baseline_pos_symbols | baseline_std | baseline_trades | baseline_fee | baseline_cov | stress_return | stress_sharpe | stress_mdd | stress_pos_symbols | stress_std | stress_trades | stress_fee | stress_cov |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for _, row in comparison_df.iterrows():
        lines.append(
            f"| {row['strategy_family']} | {float(row['baseline_holdout_total_return']):.4f} | {float(row['baseline_holdout_sharpe']):.4f} | {float(row['baseline_holdout_max_drawdown']):.4f} | "
            f"{int(row['baseline_positive_symbols'])} | {float(row['baseline_symbol_return_std']):.4f} | {float(row['baseline_trade_count_mean']):.2f} | {float(row['baseline_fee_cost_total']):.4f} | {float(row['baseline_regime_coverage_ratio']):.4f} | "
            f"{float(row['stress_holdout_total_return']):.4f} | {float(row['stress_holdout_sharpe']):.4f} | {float(row['stress_holdout_max_drawdown']):.4f} | {int(row['stress_positive_symbols'])} | "
            f"{float(row['stress_symbol_return_std']):.4f} | {float(row['stress_trade_count_mean']):.2f} | {float(row['stress_fee_cost_total']):.4f} | {float(row['stress_regime_coverage_ratio']):.4f} |"
        )
    return "\n".join(lines) + "\n"


def _extended_window_days() -> tuple[int, ...]:
    return (60, 90, 120)


def _extended_window_results(
    *,
    symbols: list[str],
    args: argparse.Namespace,
    scenario_name: str,
    config: BroadSweepConfig,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    family_name, interval, candidate = _control_candidate()
    window_rows: list[dict[str, object]] = []
    by_symbol_rows: list[dict[str, object]] = []
    for holdout_days in _extended_window_days():
        row, symbol_rows, _ = _candidate_metrics(
            symbols=symbols,
            data_root=Path(args.data_root),
            candidate_name=family_name,
            interval=interval,
            candidate=candidate,
            config=config,
            holdout_days=holdout_days,
            scenario_name=scenario_name,
            holdout_label=f"{holdout_days}d",
        )
        window_rows.append(row)
        by_symbol_rows.extend(symbol_rows)
    return pd.DataFrame(window_rows), pd.DataFrame(by_symbol_rows)


def _extended_summary(window_df: pd.DataFrame, scenario_name: str) -> pd.DataFrame:
    total_holdouts = int(len(window_df))
    positive_holdouts = int((window_df["holdout_total_return_mean"] > 0.0).sum())
    return pd.DataFrame(
        [
            {
                "scenario": scenario_name,
                "strategy_family": "macd",
                "interval": str(window_df["interval"].iloc[0]),
                "params_json": str(window_df["params_json"].iloc[0]),
                "regime_name": str(window_df["regime_name"].iloc[0]),
                "regime_params_json": str(window_df["regime_params_json"].iloc[0]),
                "holdout_count": total_holdouts,
                "holdout_success_count": positive_holdouts,
                "positive_holdouts_ratio": float(positive_holdouts / total_holdouts) if total_holdouts else 0.0,
                "median_return_across_holdouts": float(window_df["holdout_total_return_mean"].median()),
                "median_sharpe_across_holdouts": float(window_df["holdout_sharpe_mean"].median()),
                "median_max_drawdown_across_holdouts": float(window_df["holdout_max_drawdown_mean"].median()),
                "median_positive_symbols_across_holdouts": float(window_df["positive_symbols"].median()),
                "median_symbol_return_std_across_holdouts": float(window_df["symbol_return_std"].median()),
                "median_trade_count_mean_across_holdouts": float(window_df["trade_count_mean"].median()),
                "median_fee_cost_total_across_holdouts": float(window_df["fee_cost_total"].median()),
                "median_regime_coverage_ratio_across_holdouts": float(window_df["regime_coverage_ratio"].median()),
                "majors_mean_return_across_holdouts": float(window_df["major_holdout_return_mean"].mean()),
                "alts_mean_return_across_holdouts": float(window_df["alt_holdout_return_mean"].mean()),
            }
        ]
    )


def _extended_comparison_df(*, baseline_window_df: pd.DataFrame, stress_window_df: pd.DataFrame) -> pd.DataFrame:
    stress_lookup = {str(row["holdout_label"]): row for _, row in stress_window_df.iterrows()}
    rows: list[dict[str, object]] = []
    for _, row in baseline_window_df.iterrows():
        holdout_label = str(row["holdout_label"])
        stress_row = stress_lookup[holdout_label]
        rows.append(
            {
                "holdout_label": holdout_label,
                "holdout_days": int(row["holdout_days"]),
                "holdout_start": str(row["holdout_start"]),
                "holdout_end": str(row["holdout_end"]),
                "baseline_total_return": float(row["holdout_total_return_mean"]),
                "baseline_sharpe": float(row["holdout_sharpe_mean"]),
                "baseline_max_drawdown": float(row["holdout_max_drawdown_mean"]),
                "baseline_positive_symbols": int(row["positive_symbols"]),
                "baseline_symbol_return_std": float(row["symbol_return_std"]),
                "baseline_trade_count_mean": float(row["trade_count_mean"]),
                "baseline_fee_cost_total": float(row["fee_cost_total"]),
                "baseline_regime_coverage_ratio": float(row["regime_coverage_ratio"]),
                "baseline_major_return_mean": float(row["major_holdout_return_mean"]),
                "baseline_alt_return_mean": float(row["alt_holdout_return_mean"]),
                "mixed_2x_total_return": float(stress_row["holdout_total_return_mean"]),
                "mixed_2x_sharpe": float(stress_row["holdout_sharpe_mean"]),
                "mixed_2x_max_drawdown": float(stress_row["holdout_max_drawdown_mean"]),
                "mixed_2x_positive_symbols": int(stress_row["positive_symbols"]),
                "mixed_2x_symbol_return_std": float(stress_row["symbol_return_std"]),
                "mixed_2x_trade_count_mean": float(stress_row["trade_count_mean"]),
                "mixed_2x_fee_cost_total": float(stress_row["fee_cost_total"]),
                "mixed_2x_regime_coverage_ratio": float(stress_row["regime_coverage_ratio"]),
                "mixed_2x_major_return_mean": float(stress_row["major_holdout_return_mean"]),
                "mixed_2x_alt_return_mean": float(stress_row["alt_holdout_return_mean"]),
            }
        )
    return pd.DataFrame(rows)


def _extended_markdown(
    *,
    comparison_df: pd.DataFrame,
    baseline_summary_df: pd.DataFrame,
    stress_summary_df: pd.DataFrame,
) -> str:
    baseline_positive = int((comparison_df["baseline_total_return"] > 0.0).sum())
    stress_positive = int((comparison_df["mixed_2x_total_return"] > 0.0).sum())
    total_holdouts = int(len(comparison_df))
    decision = "macd_keep_paper_candidate"
    if baseline_positive < 2 or stress_positive < 2:
        decision = "revisit_required"

    baseline_row = baseline_summary_df.iloc[0]
    stress_row = stress_summary_df.iloc[0]
    lines = [
        "# MACD Extended Holdout Validation",
        "",
        f"- decision_hint: `{decision}`",
        f"- baseline_positive_holdouts: `{baseline_positive}/{total_holdouts}`",
        f"- mixed_2x_positive_holdouts: `{stress_positive}/{total_holdouts}`",
        f"- baseline_median_return_across_holdouts: `{float(baseline_row['median_return_across_holdouts']):.4f}`",
        f"- mixed_2x_median_return_across_holdouts: `{float(stress_row['median_return_across_holdouts']):.4f}`",
        "",
        "## Holdout Windows",
        "",
        "| holdout | baseline_return | baseline_sharpe | baseline_mdd | baseline_pos_symbols | baseline_cov | mixed_2x_return | mixed_2x_sharpe | mixed_2x_mdd | mixed_2x_pos_symbols | mixed_2x_cov |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for _, row in comparison_df.iterrows():
        lines.append(
            f"| {row['holdout_label']} | {float(row['baseline_total_return']):.4f} | {float(row['baseline_sharpe']):.4f} | "
            f"{float(row['baseline_max_drawdown']):.4f} | {int(row['baseline_positive_symbols'])} | {float(row['baseline_regime_coverage_ratio']):.4f} | "
            f"{float(row['mixed_2x_total_return']):.4f} | {float(row['mixed_2x_sharpe']):.4f} | {float(row['mixed_2x_max_drawdown']):.4f} | "
            f"{int(row['mixed_2x_positive_symbols'])} | {float(row['mixed_2x_regime_coverage_ratio']):.4f} |"
        )
    return "\n".join(lines) + "\n"


def _run_standard(args: argparse.Namespace, symbols: list[str], out_root: Path) -> None:
    baseline_config = _config(
        args=args,
        out_root=out_root / "baseline",
        taker_fee_bps=float(args.taker_fee_bps),
        slippage_bps=float(args.slippage_bps),
        families=("donchian_breakout", "macd"),
    )
    baseline_summary, baseline_by_symbol, holdout_meta = _scenario_rows(
        symbols=symbols,
        args=args,
        scenario_name="baseline",
        config=baseline_config,
    )

    stress_config = _config(
        args=args,
        out_root=out_root / "mixed_2x",
        taker_fee_bps=float(args.taker_fee_bps) * 2.0,
        slippage_bps=float(args.slippage_bps) * 2.0,
        families=("donchian_breakout", "macd"),
    )
    stress_summary, stress_by_symbol, _ = _scenario_rows(
        symbols=symbols,
        args=args,
        scenario_name="mixed_2x",
        config=stress_config,
    )

    (out_root / "baseline").mkdir(parents=True, exist_ok=True)
    (out_root / "mixed_2x").mkdir(parents=True, exist_ok=True)
    baseline_summary.to_csv(out_root / "baseline" / "summary.csv", index=False)
    baseline_by_symbol.to_csv(out_root / "baseline" / "by_symbol.csv", index=False)
    stress_summary.to_csv(out_root / "mixed_2x" / "summary.csv", index=False)
    stress_by_symbol.to_csv(out_root / "mixed_2x" / "by_symbol.csv", index=False)

    comparison_df = _comparison_df(baseline_df=baseline_summary, stress_df=stress_summary)
    comparison_path = out_root / "holdout_comparison.csv"
    markdown_path = out_root / "holdout_validation.md"
    comparison_df.to_csv(comparison_path, index=False)
    markdown_path.write_text(
        _render_markdown(
            comparison_df=comparison_df,
            holdout_start=holdout_meta["holdout_start"],
            holdout_end=holdout_meta["holdout_end"],
        ),
        encoding="utf-8",
    )

    print(f"baseline_summary_csv={out_root / 'baseline' / 'summary.csv'}")
    print(f"stress_summary_csv={out_root / 'mixed_2x' / 'summary.csv'}")
    print(f"holdout_comparison_csv={comparison_path}")
    print(f"holdout_markdown={markdown_path}")


def _run_extended_macd_confirmation(args: argparse.Namespace, symbols: list[str], out_root: Path) -> None:
    baseline_config = _config(
        args=args,
        out_root=out_root,
        taker_fee_bps=float(args.taker_fee_bps),
        slippage_bps=float(args.slippage_bps),
        families=("macd",),
    )
    stress_config = _config(
        args=args,
        out_root=out_root,
        taker_fee_bps=float(args.taker_fee_bps) * 2.0,
        slippage_bps=float(args.slippage_bps) * 2.0,
        families=("macd",),
    )

    baseline_window_df, baseline_by_symbol_df = _extended_window_results(
        symbols=symbols,
        args=args,
        scenario_name="baseline",
        config=baseline_config,
    )
    stress_window_df, stress_by_symbol_df = _extended_window_results(
        symbols=symbols,
        args=args,
        scenario_name="mixed_2x",
        config=stress_config,
    )

    window_results_df = pd.concat([baseline_window_df, stress_window_df], ignore_index=True)
    by_symbol_df = pd.concat([baseline_by_symbol_df, stress_by_symbol_df], ignore_index=True)
    baseline_summary_df = _extended_summary(baseline_window_df, "baseline")
    stress_summary_df = _extended_summary(stress_window_df, "mixed_2x")
    comparison_df = _extended_comparison_df(baseline_window_df=baseline_window_df, stress_window_df=stress_window_df)

    baseline_summary_path = out_root / "baseline_summary.csv"
    stress_summary_path = out_root / "mixed_2x_summary.csv"
    window_results_path = out_root / "holdout_window_results.csv"
    by_symbol_path = out_root / "holdout_by_symbol.csv"
    comparison_path = out_root / "holdout_comparison.csv"
    markdown_path = out_root / "macd_extended_holdout_validation.md"

    baseline_summary_df.to_csv(baseline_summary_path, index=False)
    stress_summary_df.to_csv(stress_summary_path, index=False)
    window_results_df.to_csv(window_results_path, index=False)
    by_symbol_df.to_csv(by_symbol_path, index=False)
    comparison_df.to_csv(comparison_path, index=False)
    markdown_path.write_text(
        _extended_markdown(
            comparison_df=comparison_df,
            baseline_summary_df=baseline_summary_df,
            stress_summary_df=stress_summary_df,
        ),
        encoding="utf-8",
    )

    print(f"baseline_summary_csv={baseline_summary_path}")
    print(f"stress_summary_csv={stress_summary_path}")
    print(f"holdout_window_results_csv={window_results_path}")
    print(f"holdout_comparison_csv={comparison_path}")
    print(f"holdout_markdown={markdown_path}")


def main() -> None:
    args = build_parser().parse_args()
    out_root = Path(args.out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    symbols = [str(symbol).upper() for symbol in args.symbols]

    if args.mode == "extended-macd-confirmation":
        _run_extended_macd_confirmation(args, symbols, out_root)
        return
    _run_standard(args, symbols, out_root)


if __name__ == "__main__":
    main()
