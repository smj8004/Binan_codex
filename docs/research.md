# Research: Binance USDT-M Historical Data and Strategy Search

Date: 2026-03-11
Scope: Phase A read-only survey before implementation

## Goal

- Collect real Binance USDT-M Futures historical candles for:
  - `BTCUSDT`
  - `ETHUSDT`
  - `XRPUSDT`
  - `TRXUSDT`
  - `ADAUSDT`
  - `SOLUSDT`
- Store reusable local files for the last 1 year, `1h` first, extensible to `15m` and `4h`
- Reuse existing backtest and experiment patterns where they are already sound
- Keep live/testnet order code separate from historical research code

## Findings

| Topic | File path | Evidence | Reusable? | Notes |
|---|---|---|---|---|
| Existing historical spot-style downloader | `trader/data/historical.py` | Downloads `/api/v3/klines`, caches CSV, merges by timestamp, but uses spot endpoint naming and `api.binance.com` | Partial | Useful merge/cache ideas, not suitable as-is because this task requires USDT-M Futures historical candles only |
| Existing generic futures OHLCV fetch via ccxt | `trader/data/binance.py` | `BinanceDataClient` sets `defaultType=future` and has `fetch_ohlcv_range()` with pagination/dedup/sort | Yes | Good low-level fallback/reference; still better to add a dedicated historical module with explicit FAPI mainnet endpoint and file layout |
| Existing futures bulk downloader | `trader/data/futures_data.py` | Uses `https://fapi.binance.com`, `/fapi/v1/klines`, saves raw/clean data, resamples to `5m/15m/1h/4h` | Yes | Strongest reuse source for endpoint choice, pagination, validation, and interval constants; current output layout is broader than this task needs |
| Existing backtest engine | `trader/backtest/engine.py` | Expects DataFrame with `timestamp/open/high/low/close/volume`; supports long/short, fees, slippage, latency, limit/market behavior | Yes | This should be the core execution engine for strategy comparison instead of writing a new fill simulator |
| Existing performance summary | `trader/backtest/metrics.py` | Provides `total_return`, `max_drawdown`, `win_rate`, `profit_factor`, `sharpe_like`, `trades` | Yes | Useful base metrics, but this task still needs extra reporting such as CAGR, fee totals, avg trade return, OOS metrics, and symbol dispersion |
| Existing optimization/walk-forward pattern | `trader/optimize.py` | Contains parameter grid generation, constraints, export helpers, and rolling train/test window logic | Partial | Useful utilities and design reference; current optimizer is effectively hard-wired to `ema_cross`, so it is not sufficient for fair 3-strategy comparison as-is |
| Existing experiment suite with CSV/MD outputs | `trader/experiments/runner.py` | Writes `summary.csv/json`, `report.md`, walk-forward tables, and cost-stress outputs under `out/experiments/...` | Yes | Output conventions are worth mirroring for the new research workflow, but the new workflow should stay narrower and symbol-focused |
| Existing strategy building blocks: EMA | `trader/strategy/ema_cross.py` | Implements stateful EMA cross strategy with optional shorting and stop/take-profit exits | Yes | Direct reuse candidate |
| Existing strategy building blocks: Donchian | `trader/strategy/trend_family.py` | `TrendDonchianBreakout` already implements Donchian entry/exit logic | Yes | Direct reuse candidate |
| Existing strategy building blocks: RSI mean reversion | `trader/strategy/meanrev_family.py` | `MeanRevRSIStrategy` exists and is closer to mean-reversion than `trader/strategy/rsi.py` | Partial | Reusable conceptually, but a dedicated research wrapper may still be cleaner for explicit exit-threshold reporting |
| Existing result export pattern | `trader/optimize.py`, `trader/backtest_compare.py`, `trader/experiments/report.py` | Existing code exports CSV, parquet, JSON, and markdown reports | Yes | New workflow should export CSV + markdown first, consistent with repo usage |
| Existing cost model location | `trader/backtest/engine.py` | `_fee_rate()`, `_slippage_fraction()`, `_execution_price()` apply taker/maker fee and slippage per fill | Yes | This is the right place to inherit fee/slippage behavior; use `MARKET` orders and taker cost for conservative research runs |
| Existing live/testnet code path | `trader/cli.py`, `trader/runtime.py`, `trader/broker/live_binance.py`, `trader/broker/paper.py` | Runtime and broker code handle paper/live/testnet execution and order submission | No for modification | This task must not alter those paths; new research code should sit under `trader/research/` and `scripts/` only |

## Current Repo State Summary

### 1. Historical data collection/storage paths already present

- `trader/data/historical.py`
  - Spot-style downloader with CSV cache under `data/historical`
  - Good reference for merge/update behavior
  - Not acceptable as final implementation because it targets spot endpoints
- `trader/data/futures_data.py`
  - Futures mainnet downloader with raw/clean/meta layout under `data/futures`
  - Already fetches USDT-M Futures OHLCV and resamples to higher intervals
  - Broader than needed for this task, but endpoint and normalization logic are directly relevant
- `trader/data/binance.py`
  - Convenient programmatic fetcher for futures OHLCV ranges
  - Good fallback for quick reads, but this task needs deterministic local file storage and incremental sync

### 2. Binance Futures candle fetch code already reusable

- Best reusable logic source: `trader/data/futures_data.py`
  - Explicit FAPI mainnet URL
  - `/fapi/v1/klines`
  - request throttling and retry flow
  - dedup/sort/UTC normalization
- Secondary reusable source: `trader/data/binance.py`
  - Smaller surface area
  - good pagination pattern
- Conclusion:
  - create a new focused module `trader/data/binance_futures_historical.py`
  - reuse ideas and constants from existing futures code
  - keep output layout dedicated to this research task:
    - `data/futures_historical/BTCUSDT/1h.csv`
    - `data/futures_historical/ETHUSDT/1h.csv`
    - ...

### 3. Backtest engine existence and expected input format

- Core engine exists in `trader/backtest/engine.py`
- Required input columns:
  - `timestamp`
  - `open`
  - `high`
  - `low`
  - `close`
  - `volume`
- Important engine behaviors already available:
  - market/limit order handling
  - next-open or close execution source
  - slippage in bps or ATR mode
  - taker/maker fee model
  - trade list and equity curve output
- Conclusion:
  - reuse `BacktestEngine`
  - keep research runs on `MARKET` orders to make taker fee/slippage assumptions explicit and conservative

### 4. Strategy comparison and result save patterns already present

- `trader/optimize.py`
  - CSV/parquet export helpers
  - top-result ranking pattern
- `trader/experiments/runner.py`
  - structured output directory
  - `summary.csv`
  - `summary.json`
  - `report.md`
  - walk-forward tables
- `trader/backtest_compare.py`
  - leaderboard CSV
  - per-strategy trade/equity CSV
- Conclusion:
  - for this task, keep outputs minimal and explicit:
    - `out/strategy_search/summary.csv`
    - `out/strategy_search/by_symbol.csv`
    - `out/strategy_search/top_strategies.md`

### 5. Cost model handling in existing code

- `trader/backtest/engine.py`
  - `_fee_rate()` resolves maker/taker fee
  - `_slippage_fraction()` resolves fixed/ATR/mixed slippage
  - `_execution_price()` applies slippage on entry/exit
  - realized trade records include `fee_paid`
- Existing metrics do not summarize everything required for this task
  - no direct `fee_cost_total`
  - no direct `avg_trade_return`
  - no direct `OOS total_return`
- Conclusion:
  - reuse the engine for execution and fee/slippage behavior
  - compute additional research metrics in the new research module

### 6. Live/testnet vs historical backtest separation

- Live/testnet order flow is already isolated in:
  - `trader/runtime.py`
  - `trader/broker/live_binance.py`
  - `trader/broker/paper.py`
  - `trader/cli.py`
- Historical experiments/backtests are already separate in:
  - `trader/backtest/*`
  - `trader/optimize.py`
  - `trader/experiments/*`
- Conclusion:
  - do not modify runtime or broker live/testnet paths
  - add new historical-only code under:
    - `trader/data/binance_futures_historical.py`
    - `trader/research/strategy_search.py`
    - `scripts/fetch_futures_historical.py`
    - `scripts/run_strategy_search.py`

## Recommended Reuse Decisions

1. Reuse `trader/backtest/engine.py` as the common execution/cost engine.
2. Reuse strategy implementations where they are already close to the requirement:
   - `trader/strategy/ema_cross.py`
   - `trader/strategy/trend_family.py` (`TrendDonchianBreakout`)
3. Reuse the output/reporting style from `trader/experiments/runner.py`, but produce lighter CSV/MD artifacts.
4. Add a new dedicated futures historical downloader instead of extending live/testnet or broad futures data code.
5. Keep the new research workflow file-based and reproducible from `scripts/`.

## Gaps That Must Be Implemented

- Dedicated mainnet-only historical futures candle sync to `data/futures_historical/...`
- Fair parameter search across at least 3 strategy families in one framework
- Walk-forward or rolling OOS aggregation that produces clear OOS ranking metrics
- Symbol-level and aggregate comparison CSVs
- Markdown summary of top strategies ranked by OOS performance
- Tests for fetch normalization, save/reload ordering, and strategy-search smoke execution

## 2026-03-12 Broad Sweep Discovery Extension

### Why broad sweep now

- single-lever experiments narrowed the search, but no branch passed the OOS hard gate
- a broader historical-data-first sweep is now more valuable than continuing one-at-a-time tweaks on already weak families
- the objective is not to prove one favorite strategy; it is to discover which common indicator families remain least-bad after fees, slippage, and rolling OOS

### Historical-data-first rationale

- live/testnet execution quality and strategy edge are different problems
- using only saved Binance USDT-M Futures candles keeps the evaluation reproducible and comparable across families
- OOS-first ranking is still mandatory even when the sweep is aggressive

### Broad sweep families included

- `ema_cross`
- `donchian_breakout`
- `supertrend`
- `price_adx_breakout`
- `rsi_mean_reversion`
- `bollinger`
- `macd`
- `stoch_rsi`

### Raw matrix size

| family | raw combos |
|---|---:|
| ema_cross | 50 |
| donchian_breakout | 12 |
| supertrend | 12 |
| price_adx_breakout | 30 |
| rsi_mean_reversion | 54 |
| bollinger | 36 |
| macd | 16 |
| stoch_rsi | 24 |
| total | 234 |

### Budgeted execution shape

- default broad sweep applies a fair round-robin cap of `96` combos total when `--max-combos` is not set
- with 8 families selected, that yields `12` combos per family in the default run
- this keeps the run large enough for discovery while staying safely inside the 6-hour budget

### Result snapshot

- executed command:
  - `uv run --active python scripts/run_strategy_search.py --symbols BTCUSDT ETHUSDT XRPUSDT TRXUSDT ADAUSDT SOLUSDT --intervals 1h 4h --mode broad-sweep --time-budget-hours 6`
- actual run artifacts:
  - `out/strategy_search_matrix/summary.csv`
  - `out/strategy_search_matrix/by_symbol.csv`
  - `out/strategy_search_matrix/window_results.csv`
  - `out/strategy_search_matrix/top_strategies.md`
  - `out/strategy_search_matrix/strategy_family_summary.csv`
- actual broad sweep outcome:
  - top candidate: `donchian_breakout @ 4h`
  - `oos_total_return_mean=-0.0035`
  - `oos_sharpe_mean=-0.5540`
  - hard-gate pass count: `0`
- interpretation:
  - the sweep found relatively better candidates, but still no hard-gate winner on this 1-year / 6-symbol / fee-inclusive setup
