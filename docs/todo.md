# TODO: Binance Futures Historical Research

Date: 2026-03-11
Status: In progress

## Phase A

- [x] Read current repo data/backtest/experiment code
- [x] Identify reusable futures historical fetch paths
- [x] Identify reusable backtest/cost/output components
- [x] Rewrite `docs/research.md`
- [x] Rewrite `docs/plan.md`
- [x] Rewrite `docs/todo.md`
- [x] Rewrite `docs/decisions.md`
- [x] Rewrite `docs/notes.md`

## Phase B

- [x] Fix target storage layout for dedicated historical candles
- [x] Fix target output artifacts for strategy search
- [x] Fix walk-forward defaults and evaluation contract

## Phase C

- [x] Add `trader/data/binance_futures_historical.py`
- [x] Add `scripts/fetch_futures_historical.py`
- [x] Add `trader/research/__init__.py`
- [x] Add `trader/research/strategy_search.py`
- [x] Add `scripts/run_strategy_search.py`
- [x] Add tests for:
  - [x] historical candle normalization
  - [x] save/reload row count and ordering
  - [x] strategy search smoke run
  - [x] output CSV generation
- [x] Update `guide/EXPERIMENT_LOG.md`
- [x] Update `README.md` if command docs need a dedicated section
- [x] Fetch real Binance USDT-M 1-year `1h` data for all 6 symbols
- [x] Run strategy search on saved local data
- [x] Verify `out/strategy_search/*` artifacts
- [x] Run `uv run --active pytest -q`

## Acceptance checklist

- [x] Real Binance USDT-M historical candles stored locally for all 6 symbols
- [x] Re-running fetch merges without duplicate rows
- [x] At least 3 strategy families compared under the same cost model
- [x] Train/test and rolling OOS included
- [x] `summary.csv`, `by_symbol.csv`, `top_strategies.md` generated
- [x] Final commands are reproducible from repository root

## 2026-03-12 Broad Sweep Extension

- [x] Expand the research runner to support 8 strategy families in one matrix
- [x] Add `broad-sweep` CLI mode with `--intervals`, `--families`, `--time-budget-hours`, `--max-combos`, and `--jobs`
- [x] Add capped round-robin combo selection to avoid uncontrolled combinatorial growth
- [x] Add composite `rank_score` and explicit broad hard-gate flagging
- [x] Generate `out/strategy_search_matrix/summary.csv`
- [x] Generate `out/strategy_search_matrix/by_symbol.csv`
- [x] Generate `out/strategy_search_matrix/window_results.csv`
- [x] Generate `out/strategy_search_matrix/top_strategies.md`
- [x] Generate `out/strategy_search_matrix/strategy_family_summary.csv`
- [x] Add broad sweep smoke coverage for module and CLI paths
- [x] Execute the real 6-symbol / 1-year / `1h+4h` broad sweep on local Binance futures history
- [x] Record the result even though hard-gate pass count stayed at `0`
