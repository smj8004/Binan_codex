# Research Baseline

Date: 2026-03-20
Status: planning only, no implementation started

## Scope And Method

This document records repository understanding only. Facts are evidence-backed from local repo artifacts and official Binance USD-M Futures docs. Hypotheses are labeled explicitly and are verification-oriented only.

Folder inventories reviewed:
- `docs/*`
- `guide/*`
- `out/*`
- `scripts/*`
- `trader/*`
- `tests/*`

Deep-read files reviewed:
- `docs/research.md`
- `docs/plan.md`
- `docs/todo.md`
- `docs/decisions.md`
- `docs/notes.md`
- `docs/REPO_DIRECTION_2026-03-14.md`
- `docs/live_entry_sizing_guard.md`
- `guide/BASELINE_STATE.md`
- `guide/SYSTEM_CANDIDATES.md`
- `guide/EXPERIMENT_LOG.md`
- `guide/INCIDENT_MULTISYMBOL_PRICE_SYNC_2026-03-08.md`
- `guide/INCIDENT_TESTNET_2014_2026-03-08.md`
- `guide/INCIDENT_TESTNET_2015_2026-03-08.md`
- `out/strategy_search_matrix/top_strategies.md`
- `out/strategy_search_compare/universe_compare/comparison.md`
- `out/strategy_search_compare/universe_14_regime_vs_1h4h/comparison.md`
- `out/strategy_search_compare/universe_14_regime_pruned_tightened_vs_pruned/comparison.md`
- `out/strategy_search_compare/universe_14_regime_stress/stress_comparison.md`
- `out/strategy_search_compare/final_showdown_donchian_vs_macd/showdown.md`
- `out/strategy_search_compare/final_showdown_donchian_vs_macd_holdout/holdout_validation.md`
- `out/strategy_search_compare/macd_extended_holdout_confirmation/macd_extended_holdout_validation.md`
- `out/operational_validation/macd_final_candidate_paper/summary.json`
- `out/operational_validation/macd_final_candidate_testnet/summary.json`
- `out/operational_validation/macd_final_candidate_testnet_long/summary.json`
- `scripts/run_strategy_search.py`
- `scripts/run_final_showdown.py`
- `scripts/run_holdout_validation.py`
- `trader/research/strategy_search.py`
- `trader/experiments/runner.py`
- `trader/experiments/walk_forward.py`
- `trader/experiments/cost_stress.py`
- `trader/experiments/regime_gate.py`
- `trader/backtest/engine.py`
- `trader/backtest/metrics.py`
- `trader/broker/live_binance.py`
- `trader/broker/paper.py`
- `trader/runtime.py`
- `trader/risk/guards.py`
- `trader/storage.py`
- `trader/config.py`
- `trader/funding_rate.py`
- `trader/funding_arbitrage.py`
- `trader/strategy/carry.py`
- `trader/strategy/macd_final_candidate.py`
- `tests/test_strategy_search.py`
- `tests/test_multisymbol_runtime_sync.py`
- `tests/test_runtime_live_recovery.py`
- `tests/test_runtime_min_order_guard.py`
- `tests/test_runtime_protective_fail_safe.py`
- `tests/test_live_testnet_order_path_smoke.py`

Official Binance docs verified:
- `https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Exchange-Information`
- `https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api/New-Order`
- `https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-History`
- `https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams`
- `https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Kline-Candlestick-Streams`
- `https://developers.binance.com/docs/derivatives/usds-margined-futures/user-data-streams/Event-Order-Update`
- `https://developers.binance.com/docs/derivatives/usds-margined-futures/user-data-streams/Event-Balance-and-Position-Update`

## Facts

### Architecture Map

Research and candidate generation:
- `trader/research/strategy_search.py` is the newer single-symbol directional strategy search stack.
- `scripts/run_strategy_search.py`, `scripts/run_final_showdown.py`, and `scripts/run_holdout_validation.py` orchestrate the current broad sweep, showdown, and holdout process.
- `trader/experiments/runner.py` is a separate portfolio and system-candidate stack. It already defines Track A, Track B, and Track C candidates.
- `trader/strategy/carry.py` implements carry and carry-momentum logic, but that track is not the currently promoted final candidate path.

Backtest and realism layer:
- `trader/backtest/engine.py` is the most realistic shared backtest kernel in the repo.
- It models maker/taker fees, fixed and ATR-based slippage, limit timeout fallback, latency bars, funding costs when funding-rate data exists, and several sizing modes.
- `trader/experiments/runner.py` uses `BacktestEngine` in some paths, but the portfolio experiment stack still contains its own simulation logic and reporting flow.

Runtime and execution layer:
- `trader/runtime.py` is the main execution/state engine for paper and live modes.
- `trader/broker/live_binance.py` handles Binance USD-M execution, exchange filters, user-stream integration, and REST reconciliation fallback.
- `trader/broker/paper.py` is the paper broker used for operational validation and runtime tests.
- `trader/storage.py` persists runtime state, fills, observability, and run status.
- `trader/risk/guards.py` enforces order, position, loss, ATR, and allocation limits.

Validation and evidence:
- `out/strategy_search_compare/*` holds alpha-selection evidence.
- `out/operational_validation/*` holds execution/runtime validation evidence.
- `guide/INCIDENT_*` and `guide/EXPERIMENT_LOG.md` preserve prior failures and fixes.
- `tests/*` contain focused regression coverage for runtime safety, multisymbol isolation, protective orders, min-notional behavior, and research scripts.

### Research Pipeline Map

Current directional research pipeline:
1. Local historical candles are loaded from `data/futures_historical/<SYMBOL>/<INTERVAL>.csv`.
2. `scripts/run_strategy_search.py` runs walk-forward search over multiple families in `trader/research/strategy_search.py`.
3. Candidate ranking uses OOS return, sharpe-like score, breadth, trade counts, and hard-gate logic.
4. `scripts/run_final_showdown.py` narrows the search to Donchian versus MACD finalist pockets.
5. `scripts/run_holdout_validation.py` validates finalists on trailing holdout windows.
6. Winning candidate parameters are frozen into a runtime candidate such as `trader/strategy/macd_final_candidate.py`.

Current portfolio/system pipeline:
1. `trader/experiments/runner.py` defines `default_system_candidates()`.
2. Track A is `carry:momentum`.
3. Track B is `regime_switch`.
4. Track C is `breakout:atr_channel`.
5. The runner supports walk-forward, regime gating, cost stress, and risk templates, but its evidence flow is not the current promoted shortlist path.

### Execution And Runtime Pipeline Map

Execution/runtime flow:
1. `trader/config.py` builds environment-specific runtime configuration and API-key selection.
2. `trader/broker/live_binance.py` pulls exchange info, symbol constraints, and live account state.
3. `trader/runtime.py` sizes entries, applies risk guards, respects min-notional and symbol filters, places entries, then creates reduce-only protective orders.
4. Runtime state is persisted to `trader/storage.py`.
5. User-stream events are consumed when available; REST reconciliation fills gaps when user-stream delivery fails.
6. Multisymbol orchestration uses symbol-scoped state, budget guards, and per-symbol order/fill isolation.
7. Operational validation outputs are written under `out/operational_validation/*`.

### Invariants That Must Not Break

- Alpha evidence and operational evidence must remain separate. A runtime PASS does not mean the strategy is profitable.
- Symbol isolation must hold across order ids, fills, trigger polling, and runtime state.
- Protective orders must remain reduce-only and must be recreated or trigger emergency flatten-and-halt on failure.
- Entry sizing must respect exchange constraints, min notional, max position notional, and budget availability.
- Restart recovery must reconcile live positions and runtime state before taking new action.
- Runtime should not backfill-trade stale bars in live mode except under explicit validation-probe settings.
- Research candidate selection must remain OOS-first, not chart-first.

### Current Strengths Already Present

- The repo already distinguishes research outputs from operational validation outputs.
- The runtime layer is materially more mature than a toy bot, including symbol-filter aware sizing, reduce-only recovery, protective-order lifecycle handling, user-stream plus REST fill reconciliation, storage-backed runtime-state restore, and a shared-budget guard for multisymbol live operation.
- The test suite already targets real failure classes instead of only unit-level arithmetic.
- The research stack already moved from naive family sweeps to walk-forward, regime-gated search and explicit holdout validation.
- There is already evidence-based demotion and promotion of candidates in repo history.

### Current Gaps Between Backtest And Real Binance Futures Execution

- Funding realism is only partially integrated. `BacktestEngine` can charge funding if candles include `funding_rate`, but the promoted search path does not appear to make funding a first-class input.
- Track A carry logic exists, but observed funding and premium data are not yet the center of the main shortlist pipeline.
- The portfolio/system experiment stack and the newer directional search stack are not fully unified around one evidence schema and one realism kernel.
- The paper broker is operationally useful, but it does not model realistic queue position, adverse selection, or exchange-side partial fill behavior with live fidelity.
- Long-duration runtime behavior remains weaker than short-path validation. The latest long-run testnet artifact still shows zero processed bars and FAIL.
- The current research shortlist is heavily shaped by single-symbol directional family search rather than the repo's already-defined Track A/B/C system tracks.

### Hidden Failure Modes Found From Old Tests, Incidents, And Results

- Multisymbol contamination already happened before. `guide/INCIDENT_MULTISYMBOL_PRICE_SYNC_2026-03-08.md` shows symbol-free client order ids and global trigger polling caused cross-symbol fill confusion.
- Environment-source ambiguity already caused failed testnet access. `guide/INCIDENT_TESTNET_2014_2026-03-08.md` shows shell environment variables silently overrode `.env`.
- Key/permission mismatches already caused failed testnet authentication. `guide/INCIDENT_TESTNET_2015_2026-03-08.md` shows env-specific key selection was required.
- Broad family search can produce appealing near-zero or slightly positive candidates that disappear under universe expansion or holdout.
- Donchian initially won the showdown but failed the stricter 120-day holdout; MACD survived. This is direct evidence that earlier selection logic could still overrate brittle candidates.
- Operational PASS can coexist with negative short-run PnL. The paper and testnet MACD operational validations passed even though those runs are not profit evidence.
- User-stream reliability is weak enough that REST reconciliation is not optional. The short testnet PASS shows all fills recovered from REST reconciliation, not from user-stream.

### Existing Reusable Patterns With Exact File Paths

- Track A/B/C reusable candidate definitions: `trader/experiments/runner.py`
- Realistic shared backtest kernel: `trader/backtest/engine.py`
- Risk template wrapper and regime gross profiles: `trader/experiments/runner.py`
- Directional walk-forward and holdout search scaffolding: `trader/research/strategy_search.py`
- Current finalist orchestration: `scripts/run_final_showdown.py`, `scripts/run_holdout_validation.py`
- Exchange-filter handling and order normalization: `trader/broker/live_binance.py`
- Protective-order fail-safe behavior: `trader/runtime.py`, `tests/test_runtime_protective_fail_safe.py`
- Multisymbol isolation regressions: `tests/test_multisymbol_runtime_sync.py`
- Live recovery regressions: `tests/test_runtime_live_recovery.py`
- Min-notional and shared-budget guards: `tests/test_runtime_min_order_guard.py`, `tests/test_live_testnet_order_path_smoke.py`
- Runtime observability and fill provenance: `trader/storage.py`

### Existing Alpha Evidence

- `out/strategy_search_matrix/top_strategies.md` shows the initial broad sweep on 6 symbols produced zero hard-gate winners.
- `out/strategy_search_compare/universe_compare/comparison.md` shows universe expansion from 6 to 15 symbols removed the lone weak positive.
- `out/strategy_search_compare/universe_14_regime_vs_1h4h/comparison.md` shows regime-gated search on 14 symbols materially improved OOS results and hard-gate counts.
- `out/strategy_search_compare/universe_14_regime_stress/stress_comparison.md` shows MACD and Donchian retained some survivability under fee/slippage stress, with MACD remaining more resilient through later validation.
- `out/strategy_search_compare/final_showdown_donchian_vs_macd_holdout/holdout_validation.md` shows Donchian failed the 120-day holdout while MACD stayed positive.
- `out/strategy_search_compare/macd_extended_holdout_confirmation/macd_extended_holdout_validation.md` shows MACD remained positive across 60-day, 90-day, and 120-day holdouts under baseline and mixed_2x stress.

### Existing Execution Safety Evidence

- `out/operational_validation/macd_final_candidate_paper/summary.json` is a PASS for short paper execution-cycle validation.
- `out/operational_validation/macd_final_candidate_testnet/summary.json` is a PASS for short testnet validation with protective orders, partial fills, and degraded-mode reconciliation.
- `out/operational_validation/macd_final_candidate_testnet_long/summary.json` is a FAIL for long-duration testnet validation because the full entry-protective-exit cycle was not observed and zero processed bars were recorded.
- `tests/test_multisymbol_runtime_sync.py`, `tests/test_runtime_live_recovery.py`, and `tests/test_runtime_protective_fail_safe.py` show the runtime layer has explicit regression coverage for previously observed execution failures.

### Binance USD-M Futures Facts Verified Against Official Docs

- Exchange filters must come from `exchangeInfo` filters, not from `pricePrecision` or `quantityPrecision`. Binance explicitly warns not to use those fields as tick size or step size.
- Exchange info exposes `PRICE_FILTER`, `LOT_SIZE`, `MARKET_LOT_SIZE`, and order-count limits, plus `triggerProtect`.
- New order semantics that matter here are: `positionSide=BOTH` is the default in one-way mode, `positionSide` must be sent in hedge mode, `reduceOnly` cannot be sent in hedge mode, `newClientOrderId` must be unique among open orders, and order submission consumes order-rate limits, not generic IP-weight only.
- Kline streams update every 250 ms for the current candle and include an explicit closed-candle flag.
- Websocket market-stream connections are limited and expire after 24 hours; the runtime must expect reconnects and refresh connection state.
- Funding history is queried from `/fapi/v1/fundingRate`, returned in ascending order, and the endpoint shares rate limits with funding-info queries.
- `ACCOUNT_UPDATE` only includes changed positions and funding-fee events can arrive as balance-only or balance-plus-position updates depending on crossed versus isolated context.

## Hypotheses

- Hypothesis H1: Track A is under-explored relative to its potential because carry and premium data exist in the repo but are not yet integrated into the main shortlist pipeline with the same rigor used for MACD/Donchian holdout promotion.
- Hypothesis H2: The largest remaining runtime risk is liveness and state freshness over long-duration sessions, not basic order semantics. This fits the short PASS and long FAIL pattern, but still requires explicit verification.
- Hypothesis H3: The research system's biggest architectural weakness is split evidence logic between `trader/research/strategy_search.py` and `trader/experiments/runner.py`, which increases the risk of candidate selection under inconsistent realism assumptions.

## Unknowns And How To Verify Them

- Unknown: whether the portfolio Track A/B/C stack can already pass the same hard-gate and holdout discipline as the newer MACD/Donchian path.
- Verification: run those tracks through the same candidate-promotion metrics and holdout outputs used by `scripts/run_final_showdown.py` and `scripts/run_holdout_validation.py`.
- Unknown: whether funding-aware carry models remain positive after realistic funding, fee, and slippage stress on the actual traded universe.
- Verification: feed observed funding history into the shared backtest kernel and rerun walk-forward plus holdout.
- Unknown: whether long-run testnet failure is caused by feed liveness, reconnect handling, scheduling, or startup orchestration.
- Verification: add heartbeat, reconnect, and bar-ingestion observability to runtime validation runs before changing strategy logic.
- Unknown: whether the currently strongest MACD candidate remains best once Track A/B/C are evaluated under the same gates.
- Verification: unify promotion criteria and compare all tracks on the same scoreboard.
- Unknown: whether paper-broker optimism materially changes Track C rankings.
- Verification: add stricter execution-aware simulation checks before any paper or live promotion.
