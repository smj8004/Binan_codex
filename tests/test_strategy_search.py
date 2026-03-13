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
