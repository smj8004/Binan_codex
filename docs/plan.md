# Plan

Date: 2026-03-20
Status: planning only

## 1. Problem Statement

The repository already contains meaningful strategy research and meaningful Binance Futures runtime validation, but those two strengths are not yet joined into one disciplined system that can reliably discover robust candidates, reject overfit candidates early, and validate execution realism before any live progression.

## 2. Goals

- Discover strategy candidates with realistic net profitability potential on Binance USDT-M Futures.
- Reject candidates that fail holdout, cost stress, breadth, or regime robustness.
- Validate that the runtime layer can support shortlisted candidates safely under Binance constraints.
- Produce a final shortlist with explicit promotion and rejection gates.

## 3. Non-Goals

- No broad runtime feature expansion for strategies that do not yet show robust edge.
- No live deployment or live-money progression in this phase.
- No architecture rewrite for style reasons.
- No duplicate research framework if an existing repo path can be reused.

## 4. Current State Summary

Based on `docs/research.md`:
- The strongest current alpha survivor is the regime-gated MACD directional candidate.
- Donchian was a temporary finalist but failed stricter holdout validation.
- Track A, Track B, and Track C already exist in `trader/experiments/runner.py` and `guide/SYSTEM_CANDIDATES.md`, but they are not yet the center of the current shortlist flow.
- The runtime layer is comparatively mature and already has regression tests for major failure classes.
- The biggest execution gap is long-duration runtime validation, not short-path order semantics.
- The biggest research gap is split evaluation logic and incomplete funding-aware integration into the main promotion ladder.

## 5. Main Repo Weaknesses Blocking Live-Viable Profitability

- Research evidence is split across two partially different stacks.
- Track A carry-aware logic is not yet promoted through the strongest existing holdout pipeline.
- Funding, premium, and carry realism are present but not yet mandatory for candidate promotion where relevant.
- The portfolio-system stack does not yet share one consistent gate schema with the directional finalist stack.
- Long-run runtime validation still has an open FAIL artifact.
- Execution realism is stronger in runtime than in some research paths.

## 6. Proposed System Changes

### 6.1 Research Stack Unification

- Unify candidate evaluation outputs across `trader/research/strategy_search.py` and `trader/experiments/runner.py`.
- Reuse the shared `BacktestEngine` cost and order-model realism as the primary evaluation kernel.
- Standardize the same candidate summary fields across all tracks: walk-forward OOS return, stress return, trade count, turnover, concentration, breadth across symbols, regime coverage, parameter-neighborhood survival, and holdout survival.

### 6.2 Track Prioritization

- Track A: cross-sectional carry plus momentum must become a first-class candidate track, using observed funding and premium inputs whenever promotion is considered.
- Track B: regime-switching trend versus mean-reversion must be evaluated under explicit regime-detection and coverage metrics.
- Track C: breakout plus ATR risk must be evaluated under execution-aware assumptions, including limit-first entry, timeout, fallback, and protective lifecycle expectations.
- MACD remains the current baseline-to-beat because it is the best existing survivor in repo evidence.

### 6.3 Anti-Overfitting Defenses

- Keep strict train, test, and holdout separation.
- Add parameter-neighborhood and family-neighborhood survival as explicit promotion criteria.
- Require universe expansion and regime robustness before shortlist promotion.
- Require cost and slippage stress survival.
- Require trade-level failure-case review for finalists.

### 6.4 Execution-Realism Hardening

- Ensure the research stack uses exchange constraints and execution assumptions that match Binance docs where relevant.
- Tighten funding-aware evaluation for carry strategies.
- Add explicit execution-aware simulation criteria before runtime promotion.
- Strengthen long-run runtime observability, reconnect detection, and bar-ingestion verification before any candidate is considered paper or testnet ready.

## 7. Exact File-Level Change List

Planned research and evaluation files:
- `trader/experiments/runner.py`
- `trader/research/strategy_search.py`
- `trader/backtest/engine.py`
- `trader/backtest/metrics.py`
- `trader/funding_rate.py`
- `trader/strategy/carry.py`
- `scripts/run_strategy_search.py`
- `scripts/run_final_showdown.py`
- `scripts/run_holdout_validation.py`
- `tests/test_strategy_search.py`

Planned runtime and observability files:
- `trader/runtime.py`
- `trader/broker/live_binance.py`
- `trader/storage.py`
- `tests/test_multisymbol_runtime_sync.py`
- `tests/test_runtime_live_recovery.py`
- `tests/test_runtime_protective_fail_safe.py`
- `tests/test_live_testnet_order_path_smoke.py`

Planned docs and validation files:
- `docs/research.md`
- `docs/plan.md`
- `docs/todo.md`
- `docs/decisions.md`
- `docs/notes.md`
- relevant new or updated outputs under `out/strategy_search_compare/*`
- relevant new or updated outputs under `out/operational_validation/*`

## 8. Strategy Research Plan

### 8.1 Candidate Tracks

Ranked starting tracks:
1. Track A: cross-sectional carry plus momentum
2. Track B: regime switch trend versus range
3. Track C: execution-aware breakout
4. Existing MACD finalist as incumbent benchmark

### 8.2 Required Research Evaluations

Every candidate must be evaluated with:
- walk-forward evaluation
- strict IS versus OOS separation
- holdout validation
- fee stress
- slippage stress
- symbol expansion
- timeframe robustness where applicable
- parameter sensitivity and neighborhood survival
- regime robustness
- turnover analysis
- concentration analysis
- trade-level failure review

### 8.3 Candidate Scorecard

Each candidate result should produce:
- baseline OOS metrics
- stressed OOS metrics
- holdout metrics
- symbol breadth metrics
- regime coverage metrics
- turnover and fee burden metrics
- concentration and dispersion metrics
- promotion verdict with explicit reasons

## 9. Execution And Runtime Validation Plan

Before paper or testnet promotion:
- confirm exchange-filter compatibility from exchange info
- confirm one-way versus hedge assumptions
- confirm reduce-only semantics
- confirm protective stop and take-profit lifecycle assumptions
- confirm websocket and REST reconciliation assumptions
- confirm restart reconciliation and stale-state handling
- confirm multisymbol isolation and budget-guard behavior

Validation ladder:
1. Execution-aware backtest or simulation
2. Short paper operational validation
3. Short testnet operational validation
4. Long-run paper or testnet validation with reconnects and bar-ingestion evidence
5. Only then mark live eligible

## 10. Rollout Order

1. Unify candidate evidence schema and promotion gates.
2. Upgrade Track A, Track B, and Track C evaluation to the shared realism kernel.
3. Re-run research and holdout promotion against the incumbent MACD baseline.
4. Strengthen execution-aware simulation for surviving finalists.
5. Close the long-run runtime validation gap.
6. Produce the shortlist and explicit next-live blockers.

## 11. Rollback And Safety Considerations

- Keep changes incremental and test-backed.
- Do not replace working runtime behaviors without preserving the existing regression suite.
- If research unification causes result drift, record the drift and compare old versus new outputs before promoting anything.
- If runtime validation changes alter protective-order or sizing behavior, rerun the focused runtime regressions before broader tests.

## 12. Testing And Verification Plan

Research verification:
- unit and integration tests around candidate metrics and gate logic
- reproducible script outputs for search, showdown, and holdout
- comparison outputs stored in `out/strategy_search_compare/*`

Runtime verification:
- focused runtime regression tests for multisymbol isolation, protective fail-safe, min-notional, recovery, and shared budget
- operational validation outputs for paper and testnet
- explicit long-run validation artifact with non-zero processed bars and full entry-protective-exit observation

## 13. Candidate Promotion Gates

### Stage 1: Research Candidate Generation

Pass criteria:
- strategy family and parameter set are reproducible
- data coverage is sufficient
- trade count exceeds the minimum threshold

Reject criteria:
- insufficient data
- trivial trade count
- relies on unavailable live inputs

### Stage 2: Walk-Forward And Cost-Stress Survival

Pass criteria:
- positive or clearly superior risk-adjusted OOS performance under baseline
- survives predefined fee and slippage stress without collapsing
- no single window dominates the full result

Reject criteria:
- negative or unstable OOS profile
- strong dependence on one window only
- collapse under modest cost stress

### Stage 3: Robustness Across Symbols, Timeframes, And Regimes

Pass criteria:
- breadth across symbols
- acceptable dispersion and concentration
- acceptable regime coverage
- reasonable parameter-neighborhood survival

Reject criteria:
- edge concentrated in one or two symbols
- strong parameter brittleness
- performance only in one narrow regime pocket

### Stage 4: Execution-Aware Simulation Survival

Pass criteria:
- candidate remains viable under realistic order model, timeout, fallback, and funding assumptions
- turnover and fee burden remain acceptable

Reject criteria:
- edge disappears when execution realism is applied
- candidate requires unrealistic fill assumptions

### Stage 5: Paper And Testnet Runtime Validation Survival

Pass criteria:
- entry, protective, and exit lifecycle observed
- state reconciliation works after reconnect or restart
- no multisymbol contamination
- no unresolved protective-order failures

Reject criteria:
- runtime halts unexpectedly
- fills cannot be reconciled
- protective lifecycle is inconsistent

### Stage 6: Tiny-Canary Live Eligibility

Pass criteria:
- every previous stage is passed
- long-run runtime validation is clean
- residual live-only risks are documented and accepted

Reject criteria:
- any prior gate remains open
- long-run runtime evidence is missing or failing

## 14. Criteria For Rejecting A Candidate

- Fails holdout after looking good in walk-forward
- Fails under cost stress or slippage stress
- Fails breadth or concentration checks
- Depends on proxy inputs that are unavailable or weakly modeled in live trading
- Requires runtime behavior that the current Binance execution layer cannot support safely

## 15. Criteria For Promotion Research To Forward To Runtime

Research to forward:
- passes Stages 1 through 4

Forward to runtime:
- passes Stage 4 and has an implementation profile supported by the current runtime architecture

Runtime to live-eligible:
- passes Stage 5 and Stage 6 with documented residual risks
