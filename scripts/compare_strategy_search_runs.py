from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


MAJOR_SYMBOLS = {"BTCUSDT", "ETHUSDT", "BNBUSDT"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare two broad-sweep output directories.")
    parser.add_argument("--baseline-root", required=True, help="Baseline broad-sweep output directory")
    parser.add_argument("--variant-root", required=True, help="Variant broad-sweep output directory")
    parser.add_argument("--out-root", required=True, help="Directory to write comparison outputs")
    parser.add_argument("--baseline-label", default="baseline", help="Label for baseline run")
    parser.add_argument("--variant-label", default="variant", help="Label for variant run")
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


def _overall_compare(
    *,
    baseline_summary: pd.DataFrame,
    variant_summary: pd.DataFrame,
    baseline_label: str,
    variant_label: str,
) -> pd.DataFrame:
    baseline = _best_metrics(baseline_summary)
    variant = _best_metrics(variant_summary)
    metric_names = [
        "hard_gate_pass_count",
        "best_oos_total_return_mean",
        "best_oos_sharpe_mean",
        "best_oos_max_drawdown_mean",
        "best_positive_symbols",
        "best_symbol_return_std",
        "best_trade_count_mean",
        "best_fee_cost_total",
        "best_regime_coverage_ratio",
    ]
    rows: list[dict[str, object]] = []
    for metric in metric_names:
        baseline_value = baseline[metric]
        variant_value = variant[metric]
        rows.append(
            {
                "metric": metric,
                baseline_label: baseline_value,
                variant_label: variant_value,
                "delta_variant_minus_baseline": float(variant_value) - float(baseline_value),
            }
        )
    return pd.DataFrame(rows)


def _family_compare(baseline_family: pd.DataFrame, variant_family: pd.DataFrame) -> pd.DataFrame:
    merged = baseline_family.merge(variant_family, on="strategy_family", suffixes=("_baseline", "_variant"))
    merged["delta_oos_total_return_mean"] = (
        merged["oos_total_return_mean_variant"] - merged["oos_total_return_mean_baseline"]
    )
    merged["delta_oos_sharpe_mean"] = merged["oos_sharpe_mean_variant"] - merged["oos_sharpe_mean_baseline"]
    merged["delta_oos_max_drawdown_mean"] = (
        merged["oos_max_drawdown_mean_variant"] - merged["oos_max_drawdown_mean_baseline"]
    )
    merged["delta_positive_symbols"] = merged["positive_symbols_variant"] - merged["positive_symbols_baseline"]
    merged["delta_symbol_return_std"] = (
        merged["symbol_return_std_variant"] - merged["symbol_return_std_baseline"]
    )
    merged["delta_trade_count_mean"] = merged["trade_count_mean_variant"] - merged["trade_count_mean_baseline"]
    merged["delta_fee_cost_total"] = merged["fee_cost_total_variant"] - merged["fee_cost_total_baseline"]
    if "regime_coverage_ratio_baseline" in merged.columns and "regime_coverage_ratio_variant" in merged.columns:
        merged["delta_regime_coverage_ratio"] = (
            merged["regime_coverage_ratio_variant"] - merged["regime_coverage_ratio_baseline"]
        )
    else:
        merged["delta_regime_coverage_ratio"] = 0.0
    return merged.sort_values(
        ["delta_oos_total_return_mean", "delta_oos_sharpe_mean", "delta_positive_symbols"],
        ascending=[False, False, False],
    ).reset_index(drop=True)


def _top_bucket_summary(summary_df: pd.DataFrame, by_symbol_df: pd.DataFrame) -> pd.DataFrame:
    top = _best_metrics(summary_df)
    subset = by_symbol_df[
        (by_symbol_df["strategy_family"] == top["best_family"])
        & (by_symbol_df["strategy_name"] == top["best_strategy"])
        & (by_symbol_df["interval"] == top["best_interval"])
        & (by_symbol_df["params_json"] == top["best_params_json"])
        & (by_symbol_df.get("regime_name", "off") == top["best_regime_name"])
        & (by_symbol_df.get("regime_params_json", "{}") == top["best_regime_params_json"])
    ].copy()
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
        )
        .reset_index()
    )
    return grouped.sort_values("bucket").reset_index(drop=True)


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
    baseline_bucket_df: pd.DataFrame,
    variant_bucket_df: pd.DataFrame,
    baseline_label: str,
    variant_label: str,
) -> str:
    baseline_hard_gate = int(overall_df.loc[overall_df["metric"] == "hard_gate_pass_count", baseline_label].iloc[0])
    variant_hard_gate = int(overall_df.loc[overall_df["metric"] == "hard_gate_pass_count", variant_label].iloc[0])
    best_return_delta = float(
        overall_df.loc[overall_df["metric"] == "best_oos_total_return_mean", "delta_variant_minus_baseline"].iloc[0]
    )
    best_positive_delta = int(
        overall_df.loc[overall_df["metric"] == "best_positive_symbols", "delta_variant_minus_baseline"].iloc[0]
    )
    improved_families = family_df[family_df["delta_oos_total_return_mean"] > 0]["strategy_family"].tolist()

    lines = [
        "# Universe Broad Sweep Comparison",
        "",
        f"- baseline: `{baseline_label}`",
        f"- variant: `{variant_label}`",
        "",
        "## Key Answers",
        "",
        f"- hard-gate winners: `{baseline_label}={baseline_hard_gate}`, `{variant_label}={variant_hard_gate}`",
        f"- best-candidate OOS return delta: `{best_return_delta:.6f}`",
        f"- best-candidate positive-symbol delta: `{best_positive_delta}`",
        f"- best-candidate regime coverage delta: `{float(overall_df.loc[overall_df['metric'] == 'best_regime_coverage_ratio', 'delta_variant_minus_baseline'].iloc[0]):.6f}`",
        f"- families with better best-candidate OOS return in the variant: `{', '.join(improved_families) if improved_families else 'none'}`",
        "",
        "## Overall Metrics",
        "",
    ]
    lines.extend(_markdown_table(overall_df, ["metric", baseline_label, variant_label, "delta_variant_minus_baseline"]))
    lines.extend(["", "## Family Deltas", ""])
    lines.extend(
        _markdown_table(
            family_df,
            [
                "strategy_family",
                "interval_baseline",
                "interval_variant",
                "oos_total_return_mean_baseline",
                "oos_total_return_mean_variant",
                "delta_oos_total_return_mean",
                "delta_oos_sharpe_mean",
                "delta_positive_symbols",
                "delta_trade_count_mean",
                "delta_fee_cost_total",
                "delta_regime_coverage_ratio",
            ],
        )
    )
    lines.extend(["", f"## Major vs Alt ({baseline_label} top candidate)", ""])
    lines.extend(
        _markdown_table(
            baseline_bucket_df,
            ["bucket", "symbols", "oos_return_mean", "oos_sharpe_mean", "positive_symbols", "trade_count_mean", "fee_cost_total"],
        )
    )
    lines.extend(["", f"## Major vs Alt ({variant_label} top candidate)", ""])
    lines.extend(
        _markdown_table(
            variant_bucket_df,
            ["bucket", "symbols", "oos_return_mean", "oos_sharpe_mean", "positive_symbols", "trade_count_mean", "fee_cost_total"],
        )
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    args = build_parser().parse_args()
    baseline_root = Path(args.baseline_root)
    variant_root = Path(args.variant_root)
    out_root = Path(args.out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    baseline = _load_run(baseline_root)
    variant = _load_run(variant_root)

    overall_df = _overall_compare(
        baseline_summary=baseline["summary"],
        variant_summary=variant["summary"],
        baseline_label=args.baseline_label,
        variant_label=args.variant_label,
    )
    family_df = _family_compare(baseline["family"], variant["family"])
    baseline_bucket_df = _top_bucket_summary(baseline["summary"], baseline["by_symbol"])
    variant_bucket_df = _top_bucket_summary(variant["summary"], variant["by_symbol"])

    overall_path = out_root / "overall_comparison.csv"
    family_path = out_root / "family_comparison.csv"
    markdown_path = out_root / "comparison.md"

    overall_df.to_csv(overall_path, index=False)
    family_df.to_csv(family_path, index=False)
    markdown_path.write_text(
        _render_markdown(
            overall_df=overall_df,
            family_df=family_df,
            baseline_bucket_df=baseline_bucket_df,
            variant_bucket_df=variant_bucket_df,
            baseline_label=args.baseline_label,
            variant_label=args.variant_label,
        ),
        encoding="utf-8",
    )

    print(f"overall_csv={overall_path}")
    print(f"family_csv={family_path}")
    print(f"markdown={markdown_path}")


if __name__ == "__main__":
    main()
