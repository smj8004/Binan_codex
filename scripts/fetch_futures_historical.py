from __future__ import annotations

import argparse

from trader.data.binance_futures_historical import BinanceFuturesHistoricalClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch Binance USDT-M Futures historical candles to local CSV files.")
    parser.add_argument("--symbols", nargs="+", required=True, help="Symbols such as BTCUSDT ETHUSDT SOLUSDT")
    parser.add_argument("--interval", default="1h", help="Binance interval, e.g. 15m, 1h, 4h")
    parser.add_argument("--days", type=int, default=365, help="Number of days to sync")
    parser.add_argument("--root-dir", default="data/futures_historical", help="Output root directory")
    parser.add_argument("--request-delay", type=float, default=0.20, help="Delay between HTTP requests in seconds")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    client = BinanceFuturesHistoricalClient(request_delay=args.request_delay)
    try:
        results = client.sync_symbols(
            symbols=[str(symbol).upper() for symbol in args.symbols],
            interval=args.interval,
            days=args.days,
            root_dir=args.root_dir,
        )
    finally:
        client.close()

    for result in results:
        print(
            f"{result.symbol} {result.interval} rows={result.rows} fetched_rows={result.fetched_rows} "
            f"path={result.path} range=({result.start},{result.end})"
        )


if __name__ == "__main__":
    main()
