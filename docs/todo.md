# Todo

Date: 2026-03-20
Status: planning checklist

## Data

- [ ] Verify all research tracks use the same symbol universe definition and documented data coverage.
- [ ] Audit availability of observed funding and premium inputs for Track A promotion use.
- [ ] Document any remaining proxy-only inputs that block live-faithful research.

## Research

- [x] Standardize one candidate evidence schema across `trader/research/strategy_search.py` and `trader/experiments/runner.py`.
- [x] Add explicit promotion and rejection verdict fields for every candidate output.
- [x] Re-run Track A under walk-forward, cost stress, breadth, and holdout gates.
- [x] Re-run Track B under walk-forward, cost stress, breadth, and holdout gates.
- [x] Re-run Track C under walk-forward, cost stress, breadth, and holdout gates.
- [x] Compare Track A, Track B, Track C, and incumbent MACD on the same scoreboard.
- [x] Record which candidates fail due to overfitting, weak breadth, weak holdout, or execution realism.

## Backtest Engine

- [x] Confirm `BacktestEngine` is the shared realism kernel for promoted research paths.
- [ ] Close any remaining gap between portfolio-system simulation and shared backtest-engine assumptions.
- [ ] Make funding-cost handling mandatory for carry-aware shortlist promotion.
- [ ] Add explicit turnover, concentration, and parameter-neighborhood outputs where missing.
- [ ] Add execution-aware simulation checks for Track C limit-first and timeout behavior.

## Runtime / Broker / State

- [x] Audit long-run testnet FAIL path and document the liveness or ingestion root cause.
- [x] Add runtime observability for reconnects, stale state, and bar-ingestion continuity where needed.
- [x] Make pipeline-proof and real-strategy artifact semantics explicit enough to prevent verdict confusion.
- [ ] Verify restart reconciliation against existing runtime-state persistence and fill provenance.
- [ ] Verify exchange-filter, reduce-only, and protective-order assumptions against official Binance docs in code paths touched.
- [x] Fix the live multisymbol startup transition blind spot between preflight and websocket startup with explicit worker/feed/websocket milestones.
- [x] Produce a fast pipeline-proof MACD runtime artifact with `first_closed_kline_received`, `first_bar_dispatched`, non-zero live bars, and graceful `runtime_stopped`.
- [ ] Produce a fresh real-timeframe MACD runtime artifact that reaches at least one closed 4h kline and graceful `runtime_stopped` finalization.
- [x] Document the post-pipeline runtime confidence ladder so first-live-bar proof is not overclaimed.

## Risk

- [ ] Ensure candidate promotion includes turnover burden and concentration limits.
- [ ] Verify min-notional, max-position-notional, and budget-guard behavior remain unchanged for runtime edits.
- [ ] Confirm no candidate is promoted without explicit cost-stress survival.

## Tests

- [x] Extend research tests to cover unified gate outputs and candidate promotion logic.
- [x] Keep runtime regressions green for multisymbol isolation, protective fail-safe, live recovery, and min-notional behavior.
- [ ] Add or update tests for any new funding-aware or execution-aware research logic.
- [x] Produce fresh validation artifacts in `out/strategy_search_compare/*` for the new shortlist process.
- [x] Produce a pipeline-proof operational validation artifact with non-zero processed live bars.
- [ ] Produce a real 4h operational validation artifact with non-zero processed live bars before any live eligibility discussion.
- [x] Add regression coverage for long-run runtime diagnostic verdict generation and websocket reconnect milestone capture.

## Docs

- [x] Refresh `docs/research.md`.
- [x] Refresh `docs/decisions.md`.
- [x] Refresh `docs/plan.md`.
- [x] Refresh `docs/todo.md`.
- [x] Keep `docs/notes.md` updated with assumptions, review notes, and implementation drift.
- [ ] Record candidate promotion outcomes and rejection reasons in docs as implementation progresses.
- [x] Record the exact long-run MACD diagnostic command and current closure criteria in implementation notes.
- [x] Record the exact pipeline-proof command and the real 4h follow-up command in implementation notes.
- [x] Record the runtime confidence ladder stages `R1` through `R5` in implementation notes.
