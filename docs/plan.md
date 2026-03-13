# Plan: Binance Futures Historical Research Pipeline

Date: 2026-03-11
Status: Active

## 1. Data pipeline

- Source:
  - Binance USDT-M Futures public mainnet endpoint
  - primary endpoint: `/fapi/v1/klines`
- Scope:
  - last 365 days
  - symbols:
    - `BTCUSDT`
    - `ETHUSDT`
    - `XRPUSDT`
    - `TRXUSDT`
    - `ADAUSDT`
    - `SOLUSDT`
  - default interval: `1h`
  - design must remain extensible to `15m` and `4h`
- Implementation:
  - add `trader/data/binance_futures_historical.py`
  - add `scripts/fetch_futures_historical.py`
- Required behavior:
  - paginate until full requested range is covered
  - respect rate limits with conservative delay/retry
  - save local files
  - deduplicate rows by timestamp
  - merge with existing files on rerun
  - avoid unnecessary re-download when cached data already covers the requested window
- File structure:
  - `data/futures_historical/BTCUSDT/1h.csv`
  - `data/futures_historical/ETHUSDT/1h.csv`
  - `data/futures_historical/XRPUSDT/1h.csv`
  - `data/futures_historical/TRXUSDT/1h.csv`
  - `data/futures_historical/ADAUSDT/1h.csv`
  - `data/futures_historical/SOLUSDT/1h.csv`

## 2. Strategy search framework

- Add:
  - `trader/research/strategy_search.py`
  - `scripts/run_strategy_search.py`
- Compare at least these 3 strategy families in the same framework:
  - `EMA cross trend-following`
  - `Donchian breakout`
  - `RSI mean-reversion`
- Strategy stance definition:
  - EMA cross:
    - compare `long/flat` and `long/short`
  - Donchian breakout:
    - compare `long/flat` and `long/short`
  - RSI mean-reversion:
    - compare `long/flat` and `long/short`
- Parameter sweep ranges:
  - EMA cross:
    - `fast_len`: `[8, 21]`
    - `slow_len`: `[55, 89]`
    - `allow_short`: `[False, True]`
  - Donchian breakout:
    - `entry_period`: `[20, 55]`
    - `exit_period`: `[10, 20]`
    - `allow_short`: `[False, True]`
  - RSI mean-reversion:
    - `rsi_period`: `[7, 14]`
    - `lower`: `[20, 30]`
    - `upper`: `[70]`
    - `exit_threshold`: `[50]`
    - `allow_short`: `[False, True]`
- Common execution assumptions:
  - same backtest engine for all strategies
  - same cost model for all strategies
  - fixed leverage, not swept in this task
  - no live/testnet order path involved

## 3. Evaluation framework

- Must include:
  - train/test split
  - rolling walk-forward OOS
  - symbol-by-symbol comparison
  - aggregate comparison across all six symbols
- Default walk-forward structure:
  - `train_days=180`
  - `test_days=60`
  - `step_days=60`
- Per-window process:
  1. evaluate parameter grid on train window
  2. select best train configuration inside that strategy family
  3. evaluate selected configuration on the next OOS window
  4. roll forward
- Required metrics per strategy/symbol aggregate:
  - `total_return`
  - `cagr`
  - `max_drawdown`
  - `sharpe_like`
  - `trade_count`
  - `win_rate`
  - `fee_cost_total`
  - `avg_trade_return`
  - `oos_total_return`
  - `oos_sharpe`
  - `symbol_consistency_count`
  - `symbol_return_std`
- Ranking rule:
  - sort primarily by OOS performance
  - use hard-gate flags as filter/context, not as a hidden override
- Hard gates for “top candidate” label:
  - `oos_total_return > 0`
  - drawdown not excessive
  - trade count not trivially small
  - fees do not consume the whole gross edge
  - consistent positive behavior in at least 3 of 6 symbols

## 4. Deliverables

- Data collection script:
  - `scripts/fetch_futures_historical.py`
- Strategy search script:
  - `scripts/run_strategy_search.py`
- Core modules:
  - `trader/data/binance_futures_historical.py`
  - `trader/research/strategy_search.py`
- Output artifacts:
  - `out/strategy_search/summary.csv`
  - `out/strategy_search/by_symbol.csv`
  - `out/strategy_search/top_strategies.md`
- Docs updates:
  - `docs/research.md`
  - `docs/plan.md`
  - `docs/todo.md`
  - `docs/decisions.md`
  - `docs/notes.md`
  - `guide/EXPERIMENT_LOG.md`
  - `README.md` if command documentation is needed

## 5. Verification

- Required:
  - `uv run --active pytest -q`
- Functional checks:
  - fetcher saves and reloads sorted candles without duplicate timestamps
  - strategy search runs from saved local files
  - output CSV/MD artifacts are generated deterministically
- Final runnable commands:
  - `uv run --active python scripts/fetch_futures_historical.py --symbols BTCUSDT ETHUSDT XRPUSDT TRXUSDT ADAUSDT SOLUSDT --interval 1h --days 365`
  - `uv run --active python scripts/run_strategy_search.py --symbols BTCUSDT ETHUSDT XRPUSDT TRXUSDT ADAUSDT SOLUSDT --interval 1h`

## 2026-03-12 Broad Sweep Discovery Plan (COMPLETED)

### Why broaden now

- the prior single-lever sequence improved some branches but produced zero hard-gate winners
- the next useful step is discovery, not another narrow tweak on already-weak candidates
- the sweep still stays historical-data-first, fee-inclusive, and OOS-ranked

### Status: COMPLETED
- Broad sweep framework implemented and executed
- 8 strategy families tested across 1h/4h intervals
- Result: No hard-gate pass on current 1-year/6-symbol/fee-inclusive setup
- Finding: Trend-following families (donchian_breakout, ema_cross @ 4h) least-bad, mean-reversion families weaker

## 2026-03-14 Post-Sweep Next Actions

### Current State
- Historical research infrastructure is STRONG (data fetch, broad sweep, OOS ranking, family comparison)
- Operational validation infrastructure is STRONG (testnet runners, budget guards, protective orders)
- Strategy edge discovery is WEAK (zero hard-gate passes on current universe/intervals/families)

### Recommended Next Research Directions (Pick ONE)

**Option A: Broader Universe Exploration**
- Expand symbol universe beyond current 6 (test 15-20 symbols across market cap tiers)
- Hypothesis: Edge may be concentrated in specific symbols not in current universe
- Cost: More data fetch time, but same backtest framework

**Option B: Alternative Timeframe Exploration**
- Test 15m, 2h, 8h, 1d intervals
- Hypothesis: Current 1h/4h may be suboptimal for trend/mean-reversion balance
- Cost: Minimal (just re-fetch and re-run search)

**Option C: Regime-Conditional Strategy Layers**
- Add volatility regime filters (VIX-like, ATR percentile, realized vol buckets)
- Add trend strength filters (ADX bands, slope consistency)
- Hypothesis: Unconditional strategies fail, but regime-conditional may pass
- Cost: Moderate implementation work on strategy layer

**Option D: Portfolio Cross-Sectional Approach**
- Shift from single-symbol directional to multi-symbol long/short cross-section
- Use existing portfolio experiment suite
- Hypothesis: Single-symbol edge is weak, but relative value edge may exist
- Cost: Already implemented in `trader/experiments/`, just needs historical data input mode

**Recommendation: Start with Option B (15m exploration) - lowest cost, fastest feedback**

### Families in scope

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

- intervals: `2` (`1h`, `4h`)
- symbols: `6`
- walk-forward windows per symbol/interval on the latest 1-year data: `3`
- raw combos: `234`
- default selected combos after round-robin cap: `96`
- approximate backtest count:
  - `96 combos x 6 symbols x 2 intervals x 3 windows x 2 train/test passes = 6912`
- observed broad-sweep runtime on this machine:
  - about `495s` (`8.25` minutes) with `jobs=8`
- conclusion:
  - the capped default run is comfortably inside the 6-hour budget and leaves room for narrower follow-up sweeps

### Ranking contract

- `rank_score` is composite, not pure return sort
- score priorities:
  - OOS total return first
  - OOS sharpe bonus
  - mean max drawdown penalty
  - positive-symbol bonus
  - fee-drag penalty
  - trade-count penalty at extremes
- hard gate remains explicit and separate from the score:
  - positive OOS return
  - positive OOS sharpe
  - non-excessive drawdown
  - positive symbols `>= 3`
  - fee ratio not consuming the edge
