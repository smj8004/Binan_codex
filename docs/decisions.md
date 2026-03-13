# Decisions

Date: 2026-03-11
Status legend: `accepted`, `open`

## D-001 Historical source
- status: accepted
- decision: use Binance USDT-M Futures mainnet public historical candles only
- rationale: the task explicitly forbids demo/testnet candle data for evaluation

## D-002 Dedicated storage path
- status: accepted
- decision: store this research dataset under `data/futures_historical/<SYMBOL>/<INTERVAL>.csv`
- rationale: keep this workflow simple, explicit, and isolated from broader `data/futures/` pipelines

## D-003 Reuse the existing execution engine
- status: accepted
- decision: use `trader/backtest/engine.py` for fills, fees, slippage, and trade accounting
- rationale: avoids inventing another fill model and keeps cost handling consistent

## D-004 Cost model stance
- status: accepted
- decision: use market-order execution with taker fee plus slippage for all strategy-family comparisons
- rationale: conservative and comparable across strategies

## D-005 Live/testnet separation
- status: accepted
- decision: do not modify `trader/runtime.py`, `trader/broker/*`, or live/testnet order paths
- rationale: this task is historical research only

## D-006 Search granularity
- status: accepted
- decision: compare strategy families under rolling walk-forward optimization rather than hand-picking one fixed parameter set in advance
- rationale: matches the requirement to avoid pre-committing to a single strategy logic and reduces single-sample bias

## D-007 Leverage handling
- status: accepted
- decision: keep leverage fixed and do not sweep leverage in this task
- rationale: strategy edge and leverage should not be mixed into the same search pass

## D-008 Deliverable priority
- status: accepted
- decision: prioritize deterministic CSV/markdown artifacts over plots or GUI output
- rationale: the task explicitly asks for reusable local files and reproducible commands first

## D-009 OOS-first ranking
- status: accepted
- decision: final ranking sorts primarily by OOS metrics, with hard-gate flags reported explicitly
- rationale: the task explicitly rejects in-sample-only conclusions

## D-010 Interval extensibility
- status: accepted
- decision: default to `1h`, but keep fetch/load/search code interval-parametric so `15m` and `4h` can reuse the same path
- rationale: required by scope

## D-011 Broad sweep mode
- status: accepted
- decision: add a dedicated `broad-sweep` execution mode instead of overloading the narrower legacy search path
- rationale: discovery runs need interval sets, family selection, combo capping, and separate outputs without destabilizing the earlier focused workflow

## D-012 Discovery family set
- status: accepted
- decision: include 8 common indicator families in the first broad sweep:
  - `ema_cross`
  - `donchian_breakout`
  - `supertrend`
  - `price_adx_breakout`
  - `rsi_mean_reversion`
  - `bollinger`
  - `macd`
  - `stoch_rsi`
- rationale: this is broad enough to test trend, mean-reversion, and hybrid momentum families under one cost model without exploding the search space

## D-013 Combo-budget control
- status: accepted
- decision: keep the raw matrix definition at `234` combos, but default the executed broad sweep to a fair round-robin cap of `96` combos unless the user overrides `--max-combos`
- rationale: the run must stay comfortably within a 6-hour budget while still giving every family representation in the same sweep

## D-014 Broad ranking rule
- status: accepted
- decision: use a composite `rank_score` that rewards OOS return and sharpe, penalizes drawdown and fee drag, and adds robustness weight through positive-symbol breadth
- rationale: pure return ranking overstates fragile or overtraded candidates

## D-015 Historical discovery isolation
- status: accepted
- decision: keep the broad sweep entirely inside research code and output folders, with no live/testnet execution-path changes
- rationale: strategy discovery and execution validation remain separate concerns
