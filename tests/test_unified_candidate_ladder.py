from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd


def _write_sample_symbol(root: Path, symbol: str, *, interval: str, bars: int) -> None:
    if interval == "4h":
        freq = "4h"
        close_offset = pd.Timedelta(hours=3, minutes=59)
    elif interval == "1h":
        freq = "1h"
        close_offset = pd.Timedelta(minutes=59)
    else:
        raise ValueError(f"Unsupported interval: {interval}")

    idx = pd.date_range("2025-01-01T00:00:00Z", periods=bars, freq=freq, tz="UTC")
    base = np.linspace(100.0, 150.0, bars)
    wave = 6.0 * np.sin(np.linspace(0, 16 * np.pi, bars))
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
            "funding_rate": np.full(bars, 0.0001),
        }
    ).to_csv(path, index=False)


def test_run_unified_candidate_ladder_script_smoke(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    out_root = tmp_path / "out"
    for symbol in ["BTCUSDT", "ETHUSDT"]:
        _write_sample_symbol(data_root, symbol, interval="4h", bars=6 * 140)

    cmd = [
        sys.executable,
        "scripts/run_unified_candidate_ladder.py",
        "--symbols",
        "BTCUSDT",
        "ETHUSDT",
        "--data-root",
        str(data_root),
        "--out-root",
        str(out_root),
        "--timeframe",
        "4h",
        "--benchmark-interval",
        "4h",
        "--train-days",
        "40",
        "--test-days",
        "20",
        "--step-days",
        "20",
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

    assert "shortlist_summary_csv=" in completed.stdout
    shortlist_path = out_root / "shortlist_summary.csv"
    assert shortlist_path.exists()
    shortlist_df = pd.read_csv(shortlist_path)
    assert "candidate_id" in shortlist_df.columns
    assert "decision" in shortlist_df.columns
    assert "incumbent_macd_regime_gated" in set(shortlist_df["candidate_id"])
    assert (out_root / "tracks").exists()
    assert (out_root / "benchmark" / "benchmark_promotion_summary.csv").exists()
    assert (out_root / "shortlist.md").exists()
