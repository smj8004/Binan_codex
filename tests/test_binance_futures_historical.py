from __future__ import annotations

from pathlib import Path

import pandas as pd

from trader.data.binance_futures_historical import BinanceFuturesHistoricalClient


def test_fetch_range_normalizes_futures_klines() -> None:
    client = BinanceFuturesHistoricalClient()

    base_ms = int(pd.Timestamp("2025-01-01T00:00:00Z").timestamp() * 1000)
    rows = [
        [base_ms, "100", "110", "95", "105", "10", base_ms + 3_599_999, "1000", 20, "4", "400", "0"],
        [base_ms + 3_600_000, "105", "115", "100", "112", "12", base_ms + 7_199_999, "1200", 25, "5", "500", "0"],
        [base_ms + 7_200_000, "112", "120", "111", "118", "14", base_ms + 10_799_999, "1400", 30, "6", "600", "0"],
    ]

    def fake_request_klines(*, symbol: str, interval: str, start_ms: int, end_ms: int, limit: int) -> list[list[str]]:
        assert symbol == "BTCUSDT"
        assert interval == "1h"
        assert limit == 1500
        return [row for row in rows if int(row[0]) >= start_ms]

    client._request_klines = fake_request_klines  # type: ignore[method-assign]
    df = client.fetch_range(
        symbol="BTCUSDT",
        interval="1h",
        start="2025-01-01T00:00:00Z",
        end="2025-01-01T03:00:00Z",
    )

    assert list(df.columns) == [
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "close_time",
        "quote_volume",
        "trades",
        "taker_buy_base",
        "taker_buy_quote",
    ]
    assert len(df) == 3
    assert "UTC" in str(df["timestamp"].dtype)
    assert float(df.iloc[0]["open"]) == 100.0
    assert int(df.iloc[-1]["trades"]) == 30


def test_load_file_sorts_and_deduplicates_saved_rows(tmp_path: Path) -> None:
    path = tmp_path / "BTCUSDT" / "1h.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "timestamp": "2025-01-01T01:00:00Z",
                "open": 105,
                "high": 115,
                "low": 100,
                "close": 112,
                "volume": 12,
                "close_time": "2025-01-01T01:59:59Z",
                "quote_volume": 1200,
                "trades": 25,
                "taker_buy_base": 5,
                "taker_buy_quote": 500,
            },
            {
                "timestamp": "2025-01-01T00:00:00Z",
                "open": 100,
                "high": 110,
                "low": 95,
                "close": 105,
                "volume": 10,
                "close_time": "2025-01-01T00:59:59Z",
                "quote_volume": 1000,
                "trades": 20,
                "taker_buy_base": 4,
                "taker_buy_quote": 400,
            },
            {
                "timestamp": "2025-01-01T01:00:00Z",
                "open": 105,
                "high": 115,
                "low": 100,
                "close": 112,
                "volume": 12,
                "close_time": "2025-01-01T01:59:59Z",
                "quote_volume": 1200,
                "trades": 25,
                "taker_buy_base": 5,
                "taker_buy_quote": 500,
            },
        ]
    ).to_csv(path, index=False)

    client = BinanceFuturesHistoricalClient()
    loaded = client.load_file(path)

    assert len(loaded) == 2
    assert loaded["timestamp"].tolist() == [
        pd.Timestamp("2025-01-01T00:00:00Z"),
        pd.Timestamp("2025-01-01T01:00:00Z"),
    ]
