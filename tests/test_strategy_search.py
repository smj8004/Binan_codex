from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from trader.research.strategy_search import BroadSweepConfig, StrategySearchConfig, calculate_adx, run_broad_sweep, run_strategy_search


def _write_sample_symbol(root: Path, symbol: str, *, interval: str = "1h", bars: int = 24 * 35) -> None:
    if interval == "1h":
        freq = "1h"
        close_offset = pd.Timedelta(minutes=59)
    elif interval == "4h":
        freq = "4h"
        close_offset = pd.Timedelta(hours=3, minutes=59)
    else:
        raise ValueError(f"Unsupported interval for test fixture: {interval}")

    idx = pd.date_range("2025-01-01T00:00:00Z", periods=bars, freq=freq, tz="UTC")
    base = np.linspace(100.0, 130.0, bars)
    wave = 8.0 * np.sin(np.linspace(0, 18 * np.pi, bars))
    close = base + wave
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + 1.5
    low = np.minimum(open_, close) - 1.5
    volume = 1000 + 50 * np.cos(np.linspace(0, 8 * np.pi, bars))

    path = root / symbol / f"{interval}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "timestamp": idx,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "close_time": idx + close_offset,
            "quote_volume": volume * close,
            "trades": np.full(bars, 100),
            "taker_buy_base": volume * 0.45,
            "taker_buy_quote": volume * close * 0.45,
        }
    ).to_csv(path, index=False)


def test_calculate_adx_smoke() -> None:
    bars = 80
    idx = pd.date_range("2025-01-01T00:00:00Z", periods=bars, freq="1h", tz="UTC")
    close = np.linspace(100.0, 160.0, bars)
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + 1.0
    low = np.minimum(open_, close) - 1.0
    candles = pd.DataFrame(
        {
            "timestamp": idx,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.full(bars, 1000.0),
        }
    )

    adx = calculate_adx(candles, window=14)

    assert len(adx) == bars
    assert float(adx.iloc[-1]) > 0.0
    assert float(adx.max()) <= 100.0


def test_strategy_search_module_smoke(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    out_root = tmp_path / "out"
    _write_sample_symbol(data_root, "BTCUSDT")

    result = run_strategy_search(
        symbols=["BTCUSDT"],
        config=StrategySearchConfig(
            interval="1h",
            data_root=data_root,
            out_root=out_root,
            train_days=10,
            test_days=5,
            step_days=10,
            min_trade_count=1,
        ),
    )

    assert result.summary_path.exists()
    assert result.by_symbol_path.exists()
    assert result.markdown_path.exists()
    assert not result.summary_df.empty
    assert not result.by_symbol_df.empty
    assert "donchian_breakout_adx" in set(result.summary_df["strategy"])
    assert "donchian_breakout_adx" in set(result.by_symbol_df["strategy"])


def test_run_strategy_search_script_generates_outputs(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    out_root = tmp_path / "out"
    _write_sample_symbol(data_root, "BTCUSDT")
    _write_sample_symbol(data_root, "ETHUSDT")

    cmd = [
        sys.executable,
        "scripts/run_strategy_search.py",
        "--symbols",
        "BTCUSDT",
        "ETHUSDT",
        "--interval",
        "1h",
        "--data-root",
        str(data_root),
        "--out-root",
        str(out_root),
        "--train-days",
        "10",
        "--test-days",
        "5",
        "--step-days",
        "10",
        "--min-trade-count",
        "1",
        "--strategies",
        "donchian_breakout",
        "donchian_breakout_adx",
    ]
    completed = subprocess.run(
        cmd,
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=True,
    )

    assert "summary_csv=" in completed.stdout
    assert (out_root / "summary.csv").exists()
    assert (out_root / "by_symbol.csv").exists()
    assert (out_root / "top_strategies.md").exists()
    summary_df = pd.read_csv(out_root / "summary.csv")
    assert set(summary_df["strategy"]) == {"donchian_breakout", "donchian_breakout_adx"}


def test_run_strategy_search_script_works_with_4h_data(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    out_root = tmp_path / "out_4h"
    _write_sample_symbol(data_root, "BTCUSDT", interval="4h", bars=24 * 35)

    cmd = [
        sys.executable,
        "scripts/run_strategy_search.py",
        "--symbols",
        "BTCUSDT",
        "--interval",
        "4h",
        "--data-root",
        str(data_root),
        "--out-root",
        str(out_root),
        "--train-days",
        "40",
        "--test-days",
        "20",
        "--step-days",
        "20",
        "--min-trade-count",
        "1",
        "--strategies",
        "donchian_breakout_adx",
    ]
    completed = subprocess.run(
        cmd,
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=True,
    )

    assert "summary_csv=" in completed.stdout
    summary_df = pd.read_csv(out_root / "summary.csv")
    assert list(summary_df["strategy"]) == ["donchian_breakout_adx"]
    assert list(summary_df["interval"]) == ["4h"]


def test_broad_sweep_module_smoke(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    out_root = tmp_path / "matrix"
    for symbol in ["BTCUSDT", "ETHUSDT"]:
        _write_sample_symbol(data_root, symbol, interval="1h", bars=24 * 80)
        _write_sample_symbol(data_root, symbol, interval="4h", bars=6 * 80)

    result = run_broad_sweep(
        symbols=["BTCUSDT", "ETHUSDT"],
        config=BroadSweepConfig(
            intervals=("1h", "4h"),
            data_root=data_root,
            out_root=out_root,
            train_days=40,
            test_days=20,
            step_days=20,
            min_trade_count=1,
            time_budget_hours=6.0,
            max_combos=8,
            jobs=1,
        ),
    )

    assert result.summary_path.exists()
    assert result.by_symbol_path.exists()
    assert result.window_results_path.exists()
    assert result.family_summary_path.exists()
    assert result.markdown_path.exists()
    families = set(result.summary_df["strategy_family"])
    assert families == {"ema_cross", "donchian_breakout", "supertrend", "price_adx_breakout", "rsi_mean_reversion", "bollinger", "macd", "stoch_rsi"}
    family_summary_df = pd.read_csv(result.family_summary_path)
    assert len(family_summary_df) == 8
    assert set(family_summary_df["strategy_family"]) == families


def test_broad_sweep_module_smoke_with_family_default_regime(tmp_path: Path) -> None:
    data_root = tmp_path / "data_regime"
    out_root = tmp_path / "matrix_regime"
    for symbol in ["BTCUSDT", "ETHUSDT"]:
        _write_sample_symbol(data_root, symbol, interval="1h", bars=24 * 80)
        _write_sample_symbol(data_root, symbol, interval="4h", bars=6 * 80)

    result = run_broad_sweep(
        symbols=["BTCUSDT", "ETHUSDT"],
        config=BroadSweepConfig(
            intervals=("1h", "4h"),
            data_root=data_root,
            out_root=out_root,
            train_days=40,
            test_days=20,
            step_days=20,
            min_trade_count=1,
            time_budget_hours=6.0,
            max_combos=8,
            jobs=1,
            regime_mode="family-default",
        ),
    )

    assert not result.summary_df.empty
    assert "regime_name" in result.summary_df.columns
    assert "regime_params_json" in result.summary_df.columns
    assert "regime_coverage_ratio" in result.summary_df.columns
    assert set(result.summary_df["regime_name"]) != {"off"}
    assert result.summary_df["regime_coverage_ratio"].between(0.0, 1.0).all()
    assert result.summary_df["regime_params_json"].str.contains("min_coverage_ratio").all()
    by_symbol_df = pd.read_csv(result.by_symbol_path)
    assert "regime_name" in by_symbol_df.columns
    assert "regime_params_json" in by_symbol_df.columns
    assert "regime_coverage_ratio" in by_symbol_df.columns


def test_run_strategy_search_script_broad_sweep_generates_outputs(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    out_root = tmp_path / "matrix_cli"
    for symbol in ["BTCUSDT", "ETHUSDT"]:
        _write_sample_symbol(data_root, symbol, interval="1h", bars=24 * 80)
        _write_sample_symbol(data_root, symbol, interval="4h", bars=6 * 80)

    cmd = [
        sys.executable,
        "scripts/run_strategy_search.py",
        "--symbols",
        "BTCUSDT",
        "ETHUSDT",
        "--mode",
        "broad-sweep",
        "--intervals",
        "1h",
        "4h",
        "--data-root",
        str(data_root),
        "--out-root",
        str(out_root),
        "--train-days",
        "40",
        "--test-days",
        "20",
        "--step-days",
        "20",
        "--min-trade-count",
        "1",
        "--max-combos",
        "8",
        "--jobs",
        "1",
    ]
    completed = subprocess.run(
        cmd,
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=True,
    )

    assert "strategy_family_summary_csv=" in completed.stdout
    assert (out_root / "summary.csv").exists()
    assert (out_root / "by_symbol.csv").exists()
    assert (out_root / "window_results.csv").exists()
    assert (out_root / "strategy_family_summary.csv").exists()
    assert (out_root / "top_strategies.md").exists()
    summary_df = pd.read_csv(out_root / "summary.csv")
    assert set(summary_df["strategy_family"]) == {"ema_cross", "donchian_breakout", "supertrend", "price_adx_breakout", "rsi_mean_reversion", "bollinger", "macd", "stoch_rsi"}
    family_summary_df = pd.read_csv(out_root / "strategy_family_summary.csv")
    assert len(family_summary_df) == 8
    assert set(family_summary_df["strategy_family"]) == set(summary_df["strategy_family"])


def test_run_strategy_search_script_broad_sweep_supports_regime_mode(tmp_path: Path) -> None:
    data_root = tmp_path / "data_cli_regime"
    out_root = tmp_path / "matrix_cli_regime"
    for symbol in ["BTCUSDT", "ETHUSDT"]:
        _write_sample_symbol(data_root, symbol, interval="1h", bars=24 * 80)
        _write_sample_symbol(data_root, symbol, interval="4h", bars=6 * 80)

    cmd = [
        sys.executable,
        "scripts/run_strategy_search.py",
        "--symbols",
        "BTCUSDT",
        "ETHUSDT",
        "--mode",
        "broad-sweep",
        "--intervals",
        "1h",
        "4h",
        "--regime-mode",
        "family-default",
        "--data-root",
        str(data_root),
        "--out-root",
        str(out_root),
        "--train-days",
        "40",
        "--test-days",
        "20",
        "--step-days",
        "20",
        "--min-trade-count",
        "1",
        "--max-combos",
        "8",
        "--jobs",
        "1",
    ]
    completed = subprocess.run(
        cmd,
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=True,
    )

    assert "strategy_family_summary_csv=" in completed.stdout
    summary_df = pd.read_csv(out_root / "summary.csv")
    assert "regime_name" in summary_df.columns
    assert set(summary_df["regime_name"]) != {"off"}


def test_run_strategy_search_script_broad_sweep_supports_cost_multipliers(tmp_path: Path) -> None:
    data_root = tmp_path / "data_cli_cost"
    out_root = tmp_path / "matrix_cli_cost"
    for symbol in ["BTCUSDT", "ETHUSDT"]:
        _write_sample_symbol(data_root, symbol, interval="1h", bars=24 * 80)
        _write_sample_symbol(data_root, symbol, interval="4h", bars=6 * 80)

    cmd = [
        sys.executable,
        "scripts/run_strategy_search.py",
        "--symbols",
        "BTCUSDT",
        "ETHUSDT",
        "--mode",
        "broad-sweep",
        "--intervals",
        "1h",
        "4h",
        "--regime-mode",
        "family-default",
        "--taker-fee-multiplier",
        "2.0",
        "--slippage-multiplier",
        "2.0",
        "--data-root",
        str(data_root),
        "--out-root",
        str(out_root),
        "--train-days",
        "40",
        "--test-days",
        "20",
        "--step-days",
        "20",
        "--min-trade-count",
        "1",
        "--max-combos",
        "8",
        "--jobs",
        "1",
    ]
    subprocess.run(
        cmd,
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=True,
    )

    markdown = (out_root / "top_strategies.md").read_text(encoding="utf-8")
    assert "- taker_fee_bps: `10.0`" in markdown
    assert "- slippage_bps: `4.0`" in markdown


def test_compare_regime_stress_runs_script_smoke(tmp_path: Path) -> None:
    stress_root = tmp_path / "stress"
    scenarios = ["baseline", "fee_1p5x", "fee_2x", "slip_2x", "slip_3x", "mixed_2x"]

    base_summary = pd.DataFrame(
        [
            {
                "rank": 1,
                "strategy_family": "donchian_breakout",
                "strategy_name": "donchian_breakout",
                "interval": "1h",
                "params_json": '{"allow_short":false}',
                "regime_name": "trend_high_adx_not_low_vol",
                "regime_params_json": '{"adx_window":14}',
                "oos_total_return_mean": 0.01,
                "oos_sharpe_mean": 0.4,
                "oos_max_drawdown_mean": -0.02,
                "positive_symbols": 9,
                "symbol_return_std": 0.01,
                "trade_count_mean": 20.0,
                "fee_cost_total": 200.0,
                "regime_coverage_ratio": 0.4,
                "hard_gate_pass": True,
            },
            {
                "rank": 2,
                "strategy_family": "macd",
                "strategy_name": "macd_momentum",
                "interval": "4h",
                "params_json": '{"fast_period":12}',
                "regime_name": "trend_high_adx_not_low_vol",
                "regime_params_json": '{"adx_window":14}',
                "oos_total_return_mean": 0.005,
                "oos_sharpe_mean": 0.3,
                "oos_max_drawdown_mean": -0.03,
                "positive_symbols": 7,
                "symbol_return_std": 0.012,
                "trade_count_mean": 12.0,
                "fee_cost_total": 150.0,
                "regime_coverage_ratio": 0.45,
                "hard_gate_pass": True,
            },
        ]
    )
    base_family = pd.DataFrame(
        [
            {
                "strategy_family": "donchian_breakout",
                "interval": "1h",
                "best_rank": 1,
                "strategy_name": "donchian_breakout",
                "params_json": '{"allow_short":false}',
                "regime_name": "trend_high_adx_not_low_vol",
                "regime_params_json": '{"adx_window":14}',
                "oos_total_return_mean": 0.01,
                "oos_sharpe_mean": 0.4,
                "oos_max_drawdown_mean": -0.02,
                "trade_count_mean": 20.0,
                "fee_cost_total": 200.0,
                "positive_symbols": 9,
                "symbol_return_std": 0.01,
                "regime_coverage_ratio": 0.4,
                "hard_gate_pass": True,
                "rank_score": 10.0,
            },
            {
                "strategy_family": "macd",
                "interval": "4h",
                "best_rank": 2,
                "strategy_name": "macd_momentum",
                "params_json": '{"fast_period":12}',
                "regime_name": "trend_high_adx_not_low_vol",
                "regime_params_json": '{"adx_window":14}',
                "oos_total_return_mean": 0.005,
                "oos_sharpe_mean": 0.3,
                "oos_max_drawdown_mean": -0.03,
                "trade_count_mean": 12.0,
                "fee_cost_total": 150.0,
                "positive_symbols": 7,
                "symbol_return_std": 0.012,
                "regime_coverage_ratio": 0.45,
                "hard_gate_pass": True,
                "rank_score": 9.0,
            },
        ]
    )
    base_by_symbol = pd.DataFrame(
        [
            {
                "strategy_family": "donchian_breakout",
                "strategy_name": "donchian_breakout",
                "interval": "1h",
                "symbol": "BTCUSDT",
                "params_json": '{"allow_short":false}',
                "regime_name": "trend_high_adx_not_low_vol",
                "regime_params_json": '{"adx_window":14}',
                "oos_total_return": 0.015,
                "oos_sharpe": 0.5,
                "trade_count": 22,
                "fee_cost_total": 40.0,
                "oos_positive": True,
                "regime_coverage_ratio": 0.42,
            },
            {
                "strategy_family": "donchian_breakout",
                "strategy_name": "donchian_breakout",
                "interval": "1h",
                "symbol": "ETHUSDT",
                "params_json": '{"allow_short":false}',
                "regime_name": "trend_high_adx_not_low_vol",
                "regime_params_json": '{"adx_window":14}',
                "oos_total_return": 0.011,
                "oos_sharpe": 0.45,
                "trade_count": 21,
                "fee_cost_total": 38.0,
                "oos_positive": True,
                "regime_coverage_ratio": 0.40,
            },
            {
                "strategy_family": "donchian_breakout",
                "strategy_name": "donchian_breakout",
                "interval": "1h",
                "symbol": "BNBUSDT",
                "params_json": '{"allow_short":false}',
                "regime_name": "trend_high_adx_not_low_vol",
                "regime_params_json": '{"adx_window":14}',
                "oos_total_return": 0.010,
                "oos_sharpe": 0.43,
                "trade_count": 20,
                "fee_cost_total": 36.0,
                "oos_positive": True,
                "regime_coverage_ratio": 0.41,
            },
            {
                "strategy_family": "donchian_breakout",
                "strategy_name": "donchian_breakout",
                "interval": "1h",
                "symbol": "SOLUSDT",
                "params_json": '{"allow_short":false}',
                "regime_name": "trend_high_adx_not_low_vol",
                "regime_params_json": '{"adx_window":14}',
                "oos_total_return": 0.008,
                "oos_sharpe": 0.35,
                "trade_count": 18,
                "fee_cost_total": 34.0,
                "oos_positive": True,
                "regime_coverage_ratio": 0.39,
            },
        ]
    )

    for index, scenario in enumerate(scenarios):
        scenario_root = stress_root / scenario
        scenario_root.mkdir(parents=True, exist_ok=True)
        scale = 1.0 - 0.1 * index
        summary_df = base_summary.copy()
        family_df = base_family.copy()
        by_symbol_df = base_by_symbol.copy()
        summary_df["oos_total_return_mean"] *= scale
        summary_df["oos_sharpe_mean"] *= scale
        summary_df["fee_cost_total"] *= 1.0 + 0.2 * index
        summary_df["hard_gate_pass"] = summary_df["oos_total_return_mean"] > 0.002
        family_df["oos_total_return_mean"] *= scale
        family_df["oos_sharpe_mean"] *= scale
        family_df["fee_cost_total"] *= 1.0 + 0.2 * index
        family_df["hard_gate_pass"] = family_df["oos_total_return_mean"] > 0.002
        by_symbol_df["oos_total_return"] *= scale
        by_symbol_df["oos_sharpe"] *= scale
        by_symbol_df["fee_cost_total"] *= 1.0 + 0.2 * index
        by_symbol_df["oos_positive"] = by_symbol_df["oos_total_return"] > 0
        summary_df.to_csv(scenario_root / "summary.csv", index=False)
        family_df.to_csv(scenario_root / "strategy_family_summary.csv", index=False)
        by_symbol_df.to_csv(scenario_root / "by_symbol.csv", index=False)

    cmd = [
        sys.executable,
        "scripts/compare_regime_stress_runs.py",
        "--stress-root",
        str(stress_root),
    ]
    completed = subprocess.run(
        cmd,
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=True,
    )

    assert "overall_csv=" in completed.stdout
    assert (stress_root / "overall_stress_comparison.csv").exists()
    assert (stress_root / "family_stress_comparison.csv").exists()
    assert (stress_root / "stress_comparison.md").exists()


def test_run_final_showdown_script_smoke(tmp_path: Path) -> None:
    data_root = tmp_path / "data_showdown"
    out_root = tmp_path / "out_showdown"
    for symbol in ["BTCUSDT", "ETHUSDT"]:
        _write_sample_symbol(data_root, symbol, interval="1h", bars=24 * 80)
        _write_sample_symbol(data_root, symbol, interval="4h", bars=6 * 80)

    cmd = [
        sys.executable,
        "scripts/run_final_showdown.py",
        "--symbols",
        "BTCUSDT",
        "ETHUSDT",
        "--intervals",
        "1h",
        "4h",
        "--data-root",
        str(data_root),
        "--out-root",
        str(out_root),
        "--train-days",
        "40",
        "--test-days",
        "20",
        "--step-days",
        "20",
        "--jobs",
        "1",
    ]
    completed = subprocess.run(
        cmd,
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=True,
    )

    assert "winner=" in completed.stdout
    assert (out_root / "baseline" / "summary.csv").exists()
    assert (out_root / "mixed_2x" / "summary.csv").exists()
    assert (out_root / "showdown_family_comparison.csv").exists()
    assert (out_root / "showdown.md").exists()


def test_run_holdout_validation_script_smoke(tmp_path: Path) -> None:
    data_root = tmp_path / "data_holdout"
    out_root = tmp_path / "out_holdout"
    for symbol in ["BTCUSDT", "ETHUSDT"]:
        _write_sample_symbol(data_root, symbol, interval="1h", bars=24 * 80)
        _write_sample_symbol(data_root, symbol, interval="4h", bars=6 * 80)

    cmd = [
        sys.executable,
        "scripts/run_holdout_validation.py",
        "--symbols",
        "BTCUSDT",
        "ETHUSDT",
        "--data-root",
        str(data_root),
        "--out-root",
        str(out_root),
        "--holdout-days",
        "20",
    ]
    completed = subprocess.run(
        cmd,
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=True,
    )

    assert "holdout_comparison_csv=" in completed.stdout
    assert (out_root / "baseline" / "summary.csv").exists()
    assert (out_root / "mixed_2x" / "summary.csv").exists()
    assert (out_root / "holdout_comparison.csv").exists()
    assert (out_root / "holdout_validation.md").exists()
    markdown = (out_root / "holdout_validation.md").read_text(encoding="utf-8")
    assert "decision_hint: `" in markdown


def test_run_holdout_validation_script_extended_macd_confirmation_smoke(tmp_path: Path) -> None:
    data_root = tmp_path / "data_holdout_extended"
    out_root = tmp_path / "out_holdout_extended"
    for symbol in ["BTCUSDT", "ETHUSDT"]:
        _write_sample_symbol(data_root, symbol, interval="1h", bars=24 * 120)
        _write_sample_symbol(data_root, symbol, interval="4h", bars=6 * 120)

    cmd = [
        sys.executable,
        "scripts/run_holdout_validation.py",
        "--symbols",
        "BTCUSDT",
        "ETHUSDT",
        "--mode",
        "extended-macd-confirmation",
        "--data-root",
        str(data_root),
        "--out-root",
        str(out_root),
    ]
    completed = subprocess.run(
        cmd,
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=True,
    )

    assert "holdout_window_results_csv=" in completed.stdout
    assert (out_root / "baseline_summary.csv").exists()
    assert (out_root / "mixed_2x_summary.csv").exists()
    assert (out_root / "holdout_window_results.csv").exists()
    assert (out_root / "holdout_comparison.csv").exists()
    assert (out_root / "macd_extended_holdout_validation.md").exists()
