from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


DEFAULT_SCENARIOS = ("baseline", "fee_1p5x", "fee_2x", "slip_2x", "slip_3x", "mixed_2x")
MAJOR_SYMBOLS = {"BTCUSDT", "ETHUSDT", "BNBUSDT"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare fee/slippage stress outputs for regime-conditioned broad sweeps.")
    parser.add_argument("--stress-root", required=True, help="Directory containing per-scenario broad-sweep outputs")
    parser.add_argument(
        "--scenarios",
        nargs="+",
        default=list(DEFAULT_SCENARIOS),
        help="Scenario subdirectories to compare",
    )
    parser.add_argument("--out-root", help="Directory to write comparison outputs (defaults to stress root)")
    return parser


def _load_run(root: Path) -> dict[str, pd.DataFrame]:
    return {
        "summary": pd.read_csv(root / "summary.csv"),
        "family": pd.read_csv(root / "strategy_family_summary.csv"),
        "by_symbol": pd.read_csv(root / "by_symbol.csv"),
    }


def _best_metrics(summary_df: pd.DataFrame) -> dict[str, float | int | str]:
    best = summary_df.iloc[0]
    return {
        "hard_gate_pass_count": int(summary_df["hard_gate_pass"].sum()),
        "best_family": str(best["strategy_family"]),
        "best_strategy": str(best["strategy_name"]),
        "best_interval": str(best["interval"]),
        "best_params_json": str(best["params_json"]),
        "best_regime_name": str(best.get("regime_name", "off")),
        "best_regime_params_json": str(best.get("regime_params_json", "{}")),
        "best_oos_total_return_mean": float(best["oos_total_return_mean"]),
        "best_oos_sharpe_mean": float(best["oos_sharpe_mean"]),
        "best_oos_max_drawdown_mean": float(best["oos_max_drawdown_mean"]),
        "best_positive_symbols": int(best["positive_symbols"]),
        "best_symbol_return_std": float(best["symbol_return_std"]),
        "best_trade_count_mean": float(best["trade_count_mean"]),
        "best_fee_cost_total": float(best["fee_cost_total"]),
        "best_regime_coverage_ratio": float(best.get("regime_coverage_ratio", 1.0)),
    }


def _candidate_mask(df: pd.DataFrame, candidate: dict[str, float | int | str]) -> pd.Series:
    return (
        (df["strategy_family"] == candidate["best_family"])
        & (df["strategy_name"] == candidate["best_strategy"])
        & (df["interval"] == candidate["best_interval"])
        & (df["params_json"] == candidate["best_params_json"])
        & (df.get("regime_name", "off") == candidate["best_regime_name"])
        & (df.get("regime_params_json", "{}") == candidate["best_regime_params_json"])
    )


def _overall_comparison(runs: dict[str, dict[str, pd.DataFrame]], scenarios: list[str]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for order, scenario in enumerate(scenarios, start=1):
        metrics = _best_metrics(runs[scenario]["summary"])
        rows.append({"scenario_order": order, "scenario": scenario, **metrics})
    return pd.DataFrame(rows)


def _family_comparison(runs: dict[str, dict[str, pd.DataFrame]], scenarios: list[str]) -> pd.DataFrame:
    baseline_family = runs["baseline"]["family"][
        [
            "strategy_family",
            "oos_total_return_mean",
            "oos_sharpe_mean",
            "oos_max_drawdown_mean",
            "positive_symbols",
            "trade_count_mean",
            "fee_cost_total",
            "regime_coverage_ratio",
            "hard_gate_pass",
        ]
    ].rename(
        columns={
            "oos_total_return_mean": "baseline_oos_total_return_mean",
            "oos_sharpe_mean": "baseline_oos_sharpe_mean",
            "oos_max_drawdown_mean": "baseline_oos_max_drawdown_mean",
            "positive_symbols": "baseline_positive_symbols",
            "trade_count_mean": "baseline_trade_count_mean",
            "fee_cost_total": "baseline_fee_cost_total",
            "regime_coverage_ratio": "baseline_regime_coverage_ratio",
            "hard_gate_pass": "baseline_hard_gate_pass",
        }
    )

    rows: list[pd.DataFrame] = []
    for order, scenario in enumerate(scenarios, start=1):
        family_df = runs[scenario]["family"].merge(baseline_family, on="strategy_family", how="left")
        family_df["scenario_order"] = order
        family_df["scenario"] = scenario
        family_df["delta_oos_total_return_mean"] = (
            family_df["oos_total_return_mean"] - family_df["baseline_oos_total_return_mean"]
        )
        family_df["delta_oos_sharpe_mean"] = family_df["oos_sharpe_mean"] - family_df["baseline_oos_sharpe_mean"]
        family_df["delta_oos_max_drawdown_mean"] = (
            family_df["oos_max_drawdown_mean"] - family_df["baseline_oos_max_drawdown_mean"]
        )
        family_df["delta_positive_symbols"] = family_df["positive_symbols"] - family_df["baseline_positive_symbols"]
        family_df["delta_trade_count_mean"] = family_df["trade_count_mean"] - family_df["baseline_trade_count_mean"]
        family_df["delta_fee_cost_total"] = family_df["fee_cost_total"] - family_df["baseline_fee_cost_total"]
        family_df["delta_regime_coverage_ratio"] = (
            family_df["regime_coverage_ratio"] - family_df["baseline_regime_coverage_ratio"]
        )
        rows.append(family_df)
    columns = [
        "scenario_order",
        "scenario",
        "strategy_family",
        "interval",
        "best_rank",
        "strategy_name",
        "params_json",
        "regime_name",
        "regime_params_json",
        "oos_total_return_mean",
        "oos_sharpe_mean",
        "oos_max_drawdown_mean",
        "trade_count_mean",
        "fee_cost_total",
        "positive_symbols",
        "symbol_return_std",
        "regime_coverage_ratio",
        "hard_gate_pass",
        "rank_score",
        "baseline_oos_total_return_mean",
        "baseline_oos_sharpe_mean",
        "baseline_oos_max_drawdown_mean",
        "baseline_positive_symbols",
        "baseline_trade_count_mean",
        "baseline_fee_cost_total",
        "baseline_regime_coverage_ratio",
        "baseline_hard_gate_pass",
        "delta_oos_total_return_mean",
        "delta_oos_sharpe_mean",
        "delta_oos_max_drawdown_mean",
        "delta_positive_symbols",
        "delta_trade_count_mean",
        "delta_fee_cost_total",
        "delta_regime_coverage_ratio",
    ]
    return pd.concat(rows, ignore_index=True)[columns].sort_values(
        ["scenario_order", "oos_total_return_mean", "oos_sharpe_mean", "positive_symbols"],
        ascending=[True, False, False, False],
    )


def _baseline_top_candidate_table(runs: dict[str, dict[str, pd.DataFrame]], scenarios: list[str]) -> pd.DataFrame:
    baseline_best = _best_metrics(runs["baseline"]["summary"])
    rows: list[dict[str, object]] = []
    for order, scenario in enumerate(scenarios, start=1):
        summary_df = runs[scenario]["summary"]
        match = summary_df.loc[_candidate_mask(summary_df, baseline_best)].copy()
        if match.empty:
            rows.append({"scenario_order": order, "scenario": scenario, "candidate_found": False})
            continue
        row = match.iloc[0]
        rows.append(
            {
                "scenario_order": order,
                "scenario": scenario,
                "candidate_found": True,
                "rank": int(row["rank"]),
                "hard_gate_pass": bool(row["hard_gate_pass"]),
                "oos_total_return_mean": float(row["oos_total_return_mean"]),
                "oos_sharpe_mean": float(row["oos_sharpe_mean"]),
                "oos_max_drawdown_mean": float(row["oos_max_drawdown_mean"]),
                "positive_symbols": int(row["positive_symbols"]),
                "trade_count_mean": float(row["trade_count_mean"]),
                "fee_cost_total": float(row["fee_cost_total"]),
                "regime_coverage_ratio": float(row.get("regime_coverage_ratio", 1.0)),
                "return_delta_vs_baseline": float(row["oos_total_return_mean"])
                - float(baseline_best["best_oos_total_return_mean"]),
                "sharpe_delta_vs_baseline": float(row["oos_sharpe_mean"])
                - float(baseline_best["best_oos_sharpe_mean"]),
            }
        )
    return pd.DataFrame(rows)


def _major_alt_sensitivity_table(runs: dict[str, dict[str, pd.DataFrame]], scenarios: list[str]) -> pd.DataFrame:
    baseline_best = _best_metrics(runs["baseline"]["summary"])
    rows: list[dict[str, object]] = []
    for order, scenario in enumerate(scenarios, start=1):
        by_symbol_df = runs[scenario]["by_symbol"]
        subset = by_symbol_df.loc[_candidate_mask(by_symbol_df, baseline_best)].copy()
        if subset.empty:
            continue
        subset["bucket"] = subset["symbol"].map(lambda symbol: "major" if str(symbol) in MAJOR_SYMBOLS else "alt")
        grouped = (
            subset.groupby("bucket", dropna=False)
            .agg(
                symbols=("symbol", "count"),
                oos_return_mean=("oos_total_return", "mean"),
                oos_sharpe_mean=("oos_sharpe", "mean"),
                positive_symbols=("oos_positive", "sum"),
                trade_count_mean=("trade_count", "mean"),
                fee_cost_total=("fee_cost_total", "sum"),
                regime_coverage_ratio=("regime_coverage_ratio", "mean"),
            )
            .reset_index()
        )
        grouped["scenario_order"] = order
        grouped["scenario"] = scenario
        rows.append(grouped)
    if not rows:
        return pd.DataFrame(
            columns=[
                "scenario_order",
                "scenario",
                "bucket",
                "symbols",
                "oos_return_mean",
                "oos_sharpe_mean",
                "positive_symbols",
                "trade_count_mean",
                "fee_cost_total",
                "regime_coverage_ratio",
            ]
        )
    return pd.concat(rows, ignore_index=True)[
        [
            "scenario_order",
            "scenario",
            "bucket",
            "symbols",
            "oos_return_mean",
            "oos_sharpe_mean",
            "positive_symbols",
            "trade_count_mean",
            "fee_cost_total",
            "regime_coverage_ratio",
        ]
    ].sort_values(["scenario_order", "bucket"])


def _resilience_table(family_df: pd.DataFrame) -> pd.DataFrame:
    stressed = family_df[family_df["scenario"] != "baseline"].copy()
    grouped = (
        stressed.groupby("strategy_family", dropna=False)
        .agg(
            mean_return_delta=("delta_oos_total_return_mean", "mean"),
            worst_return_delta=("delta_oos_total_return_mean", "min"),
            mean_sharpe_delta=("delta_oos_sharpe_mean", "mean"),
            mean_fee_delta=("delta_fee_cost_total", "mean"),
            positive_return_scenarios=("oos_total_return_mean", lambda values: int((pd.Series(values) > 0).sum())),
            hard_gate_survival_scenarios=("hard_gate_pass", "sum"),
        )
        .reset_index()
        .sort_values(
            ["positive_return_scenarios", "hard_gate_survival_scenarios", "mean_return_delta", "mean_sharpe_delta"],
            ascending=[False, False, False, False],
        )
    )
    return grouped


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


def _render_markdown(
    *,
    overall_df: pd.DataFrame,
    family_df: pd.DataFrame,
    baseline_top_df: pd.DataFrame,
    major_alt_df: pd.DataFrame,
    resilience_df: pd.DataFrame,
) -> str:
    baseline_row = overall_df.loc[overall_df["scenario"] == "baseline"].iloc[0]
    strongest_row = overall_df.sort_values(
        ["best_oos_total_return_mean", "best_oos_sharpe_mean", "hard_gate_pass_count"],
        ascending=[False, False, False],
    ).iloc[0]
    weakest_row = overall_df.sort_values(
        ["best_oos_total_return_mean", "best_oos_sharpe_mean", "hard_gate_pass_count"],
        ascending=[True, True, True],
    ).iloc[0]
    hard_gate_zero = overall_df.loc[overall_df["hard_gate_pass_count"] == 0, "scenario"].tolist()
    robust_families = resilience_df.head(5)["strategy_family"].tolist()

    lines = [
        "# Regime Fee/Slippage Stress Comparison",
        "",
        "## Key Answers",
        "",
        f"- baseline hard-gate pass count: `{int(baseline_row['hard_gate_pass_count'])}`",
        f"- strongest stress scenario by best OOS return: `{strongest_row['scenario']}` ({float(strongest_row['best_oos_total_return_mean']):.6f})",
        f"- weakest stress scenario by best OOS return: `{weakest_row['scenario']}` ({float(weakest_row['best_oos_total_return_mean']):.6f})",
        f"- scenarios with zero hard-gate winners: `{', '.join(hard_gate_zero) if hard_gate_zero else 'none'}`",
        f"- families with the best average cost resilience: `{', '.join(robust_families) if robust_families else 'none'}`",
        "",
        "## Overall Scenario Metrics",
        "",
    ]
    lines.extend(
        _markdown_table(
            overall_df,
            [
                "scenario",
                "hard_gate_pass_count",
                "best_oos_total_return_mean",
                "best_oos_sharpe_mean",
                "best_oos_max_drawdown_mean",
                "best_positive_symbols",
                "best_trade_count_mean",
                "best_fee_cost_total",
                "best_regime_coverage_ratio",
            ],
        )
    )
    lines.extend(["", "## Baseline Top Candidate Under Stress", ""])
    lines.extend(
        _markdown_table(
            baseline_top_df,
            [
                "scenario",
                "rank",
                "hard_gate_pass",
                "oos_total_return_mean",
                "oos_sharpe_mean",
                "positive_symbols",
                "trade_count_mean",
                "fee_cost_total",
                "regime_coverage_ratio",
                "return_delta_vs_baseline",
            ],
        )
    )
    lines.extend(["", "## Family Stress Comparison", ""])
    lines.extend(
        _markdown_table(
            family_df,
            [
                "scenario",
                "strategy_family",
                "oos_total_return_mean",
                "delta_oos_total_return_mean",
                "oos_sharpe_mean",
                "delta_oos_sharpe_mean",
                "positive_symbols",
                "delta_positive_symbols",
                "fee_cost_total",
                "delta_fee_cost_total",
            ],
        )
    )
    lines.extend(["", "## Major vs Alt Sensitivity (baseline top candidate)", ""])
    lines.extend(
        _markdown_table(
            major_alt_df,
            [
                "scenario",
                "bucket",
                "symbols",
                "oos_return_mean",
                "oos_sharpe_mean",
                "positive_symbols",
                "trade_count_mean",
                "fee_cost_total",
                "regime_coverage_ratio",
            ],
        )
    )
    lines.extend(["", "## Cost-Resilient Families", ""])
    lines.extend(
        _markdown_table(
            resilience_df.head(8),
            [
                "strategy_family",
                "positive_return_scenarios",
                "hard_gate_survival_scenarios",
                "mean_return_delta",
                "worst_return_delta",
                "mean_sharpe_delta",
                "mean_fee_delta",
            ],
        )
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    args = build_parser().parse_args()
    stress_root = Path(args.stress_root)
    out_root = Path(args.out_root) if args.out_root else stress_root
    out_root.mkdir(parents=True, exist_ok=True)
    scenarios = [str(scenario) for scenario in args.scenarios]

    missing = [scenario for scenario in scenarios if not (stress_root / scenario / "summary.csv").exists()]
    if missing:
        raise FileNotFoundError(f"Missing scenario outputs: {missing}")
    if "baseline" not in scenarios:
        raise ValueError("Scenarios must include baseline for stress comparison.")

    runs = {scenario: _load_run(stress_root / scenario) for scenario in scenarios}
    overall_df = _overall_comparison(runs, scenarios)
    family_df = _family_comparison(runs, scenarios)
    baseline_top_df = _baseline_top_candidate_table(runs, scenarios)
    major_alt_df = _major_alt_sensitivity_table(runs, scenarios)
    resilience_df = _resilience_table(family_df)

    overall_path = out_root / "overall_stress_comparison.csv"
    family_path = out_root / "family_stress_comparison.csv"
    markdown_path = out_root / "stress_comparison.md"

    overall_df.to_csv(overall_path, index=False)
    family_df.to_csv(family_path, index=False)
    markdown_path.write_text(
        _render_markdown(
            overall_df=overall_df,
            family_df=family_df,
            baseline_top_df=baseline_top_df,
            major_alt_df=major_alt_df,
            resilience_df=resilience_df,
        ),
        encoding="utf-8",
    )

    print(f"overall_csv={overall_path}")
    print(f"family_csv={family_path}")
    print(f"markdown={markdown_path}")


if __name__ == "__main__":
    main()
