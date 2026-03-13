# Repo Direction Clarification (2026-03-14)

## TL;DR

**This repository is for finding profitable trading strategies FIRST, operational validation SECOND.**

Recent work correctly improved operational infrastructure (testnet runners, budget guards, protective orders), but became misaligned with the primary objective: discovering strategies that actually make money on real historical data.

## Current State Assessment

### What's STRONG ✓

1. **Historical Research Infrastructure**
   - Real Binance USDT-M Futures data fetcher: `scripts/fetch_futures_historical.py`
   - Broad strategy family sweep framework: 8 families, multi-interval support
   - Walk-forward OOS evaluation with realistic fees (5 bps) and slippage (2 bps)
   - Composite ranking with hard-gate flagging
   - Clean CSV/MD output artifacts

2. **Operational Validation Infrastructure**
   - Testnet/live-forward runners: 2h, 6h, 12h, 16h
   - Budget guard with available-balance checks
   - Protective order flow (SL/TP auto-creation, paired cancellation)
   - Multi-symbol state isolation
   - Preflight checks and -2015 diagnostic guidance

3. **Code Quality**
   - All 50 tests passing
   - Clean separation between research and runtime code paths
   - Well-documented decisions and incident logs

### What's WEAK ✗

1. **Strategy Edge Discovery**
   - **Zero hard-gate winners** in broad sweep (1-year, 6 symbols, 1h/4h, 8 families)
   - All tested strategies have negative mean OOS return after fees/slippage
   - Current universe/intervals/families not sufficient for profitable edge

2. **Misaligned Effort Distribution (Recent History)**
   - Heavy investment in testnet/live-forward validation over past week
   - Multiple long-duration runs (12h, 16h) without first finding a profitable strategy
   - Operational validation is premature when zero strategies pass OOS gates

## Critical Distinction: Strategy Discovery vs Operational Validation

| Aspect | Strategy Discovery | Operational Validation |
|--------|-------------------|------------------------|
| **Objective** | Find strategies that make money | Verify order execution works correctly |
| **Data Source** | Real Binance USDT-M mainnet historical | Testnet/demo (may differ from mainnet) |
| **Evaluation** | Walk-forward OOS with realistic costs | Runtime stability, order flow, budget compliance |
| **Output** | Hard-gate flags, OOS return/sharpe, fee analysis | Halt counts, protective order coverage, DB logs |
| **Code Location** | `trader/research/`, `scripts/run_strategy_search.py` | `trader/runtime.py`, `trader/broker/`, `scripts/run_live_forward_*.ps1` |
| **When to Run** | ALWAYS (continuous research) | ONLY after finding hard-gate winner from historical |

**Key Insight:** Long testnet runs prove operational stability, NOT strategy profitability. Demo data ≠ mainnet historical data.

## Recommended Research Roadmap

### Phase 1: Expand Discovery (Pick ONE)

**Option A: Alternative Timeframe Exploration (RECOMMENDED - lowest cost)**
- Test 15m, 2h, 8h, 1d intervals
- Hypothesis: Current 1h/4h may be suboptimal
- Cost: Minimal (just re-fetch and re-run)
- Command:
  ```bash
  uv run --active python scripts/fetch_futures_historical.py --symbols BTCUSDT ETHUSDT XRPUSDT TRXUSDT ADAUSDT SOLUSDT --interval 15m --days 365
  uv run --active python scripts/run_strategy_search.py --symbols BTCUSDT ETHUSDT XRPUSDT TRXUSDT ADAUSDT SOLUSDT --intervals 15m --mode broad-sweep
  ```

**Option B: Broader Universe Exploration**
- Test 15-20 symbols across market cap tiers
- Hypothesis: Edge may be concentrated in specific symbols not in current universe
- Cost: More data fetch time, same backtest framework
- Command:
  ```bash
  # Expand to: BTC ETH BNB SOL XRP ADA DOGE TRX MATIC DOT AVAX LINK UNI ATOM LTC
  uv run --active python scripts/run_strategy_search.py --symbols <15-symbols> --intervals 1h 4h --mode broad-sweep
  ```

**Option C: Regime-Conditional Strategy Layers**
- Add volatility regime filters (VIX-like, ATR percentile)
- Add trend strength filters (ADX bands, slope consistency)
- Hypothesis: Unconditional strategies fail, regime-conditional may pass
- Cost: Moderate implementation work
- Location: Extend `trader/research/strategy_search.py` with regime-aware strategy definitions

**Option D: Portfolio Cross-Sectional Approach**
- Shift from single-symbol directional to multi-symbol long/short
- Leverage existing `trader/experiments/runner.py` portfolio suite
- Hypothesis: Single-symbol edge is weak, cross-sectional edge may exist
- Cost: Already implemented, just needs historical data input mode

### Phase 2: Operational Validation (ONLY if Phase 1 finds hard-gate winner)

Once at least one strategy passes hard gate on historical OOS:
1. Run short testnet live-forward (2h) to verify order flow
2. Confirm protective orders, budget guards, halt behavior
3. Check multi-symbol state isolation
4. If stable, consider longer runs (6h-12h)

**DO NOT run long operational validation until Phase 1 produces at least one hard-gate winner.**

## What Changed in This Reorientation (2026-03-14)

### Documentation Changes
- `README.md`: Rewritten intro to emphasize "Historical Research First, Operational Validation Second"
- `README.md`: Moved historical workflow to top, ahead of live/testnet sections
- `docs/notes.md`: Added "Repo Direction Clarity" section with explicit separation of objectives
- `docs/plan.md`: Marked broad sweep COMPLETED, added "Post-Sweep Next Actions" with 4 concrete research directions
- `guide/EXPERIMENT_LOG.md`: Added 2026-03-14 reorientation entry with recommended next 3 actions
- This document: `docs/REPO_DIRECTION_2026-03-14.md`

### Code Changes
- `scripts/run_strategy_search.py`: Enhanced output with hard-gate pass count summary
- `scripts/run_strategy_search.py`: Added actionable next-step guidance when zero hard-gate winners found
- Fixed unicode encoding issue for Windows terminal (changed emoji to ASCII brackets)

### What Did NOT Change (Correct Boundaries)
- Live/testnet execution code: UNCHANGED (operational validation remains sound)
- Historical research code: UNCHANGED (already well-designed)
- Broad sweep framework: UNCHANGED (complete and functional)
- Test suite: UNCHANGED (all 50 tests still pass)

## Verification Checklist

- [x] All tests pass: `uv run --active pytest -q` (50 passed)
- [x] Enhanced script output works: `scripts/run_strategy_search.py --help`
- [x] Documentation updated: README, notes, plan, EXPERIMENT_LOG
- [x] Hard-gate summary added to both legacy and broad-sweep modes
- [x] Next-action guidance visible when zero hard-gate winners found
- [x] Windows terminal unicode encoding issue fixed

## Final Guidance

**For the next week:**
1. PAUSE testnet/live-forward work
2. FOCUS on research exploration (start with 15m interval broad sweep)
3. DO NOT resume operational validation until at least one hard-gate winner emerges

**Success criteria for resuming operational validation:**
- At least 1 strategy with `oos_total_return_mean > 0`
- At least 1 strategy with `oos_sharpe_mean > 0.5`
- At least 1 strategy with `positive_symbols >= 4/6`
- At least 1 strategy passing all 5 hard-gate criteria

**Remember:** Testnet runs prove order execution quality, NOT strategy edge. Historical OOS performance is the ONLY valid criterion for strategy selection.
