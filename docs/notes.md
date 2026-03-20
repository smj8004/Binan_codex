# Notes

Date: 2026-03-20
Status: planning log

## Note Log

### 2026-03-20 01: Repository Re-baseline

Facts:
- Existing planning docs were stale relative to later repo evidence.
- The repo already contains both alpha-selection artifacts and operational-validation artifacts.
- The strongest current alpha survivor is the regime-gated MACD candidate, not Donchian.
- Track A, Track B, and Track C already exist as named system candidates in `trader/experiments/runner.py`.
- Short operational validation passes exist for the MACD finalist in paper and testnet.
- Long-run testnet validation remains open because the latest long artifact is a FAIL with zero processed bars.

Assumptions chosen:
- The next implementation phase should start with research-stack unification and candidate-gate hardening, not with broad runtime feature work.
- The runtime layer is good enough to support focused validation work without large redesign.
- Conservative interpretation is required whenever alpha and operational evidence conflict.

### 2026-03-20 02: Evidence Separation

Review rule:
- Never treat `out/operational_validation/*` PASS verdicts as profitability proof.

Applied consequence:
- Alpha shortlist decisions will be based on walk-forward, stress, breadth, and holdout evidence first.
- Runtime PASS is a later gate for strategies that already have alpha evidence.

### 2026-03-20 03: Official Binance Doc Verification

Verified points:
- Exchange filters must come from `exchangeInfo` filters, not precision fields.
- `reduceOnly` cannot be sent in hedge mode.
- `positionSide` behavior differs between one-way and hedge mode.
- market-stream websocket connections must be treated as reconnecting infrastructure, not permanent infrastructure.
- kline streams provide an explicit closed-candle flag.
- funding history semantics and rate limits must be respected.
- account updates contain only changed positions and have special funding-fee event behavior.

Operational interpretation:
- The repo's current emphasis on symbol filters, state reconciliation, and reconnect-aware runtime logic is directionally correct.
- User-stream-only accounting is not safe enough given local validation evidence and Binance stream behavior; REST reconciliation should remain part of the design.

### 2026-03-20 04: Candidate Priority

Priority order for implementation:
1. Track A cross-sectional carry plus momentum
2. Track B regime switch trend versus range
3. Track C execution-aware breakout
4. incumbent regime-gated MACD benchmark

Reason:
- The user explicitly prioritized Track A, Track B, and Track C.
- The repo evidence still requires the incumbent MACD benchmark because it is the strongest existing survivor.

### 2026-03-20 05: Review Handling Log

Handled without clarifying questions:
- Ambiguity about whether to preserve old planning docs verbatim: resolved conservatively by replacing stale planning content with a current baseline and recording the rationale here.
- Ambiguity about runtime-first versus alpha-first work: resolved in favor of alpha-first, consistent with repo evidence and `docs/REPO_DIRECTION_2026-03-14.md`.
- Ambiguity about whether Track A, Track B, and Track C should displace MACD immediately: resolved conservatively by keeping MACD as the benchmark-to-beat until new gates are rerun.

### 2026-03-20 06: Phase 1 Implementation

Implemented:
- Added shared promotion-schema logic in `trader/research/promotion.py`.
- Updated `trader/experiments/runner.py` to emit comparable promotion outputs and to support a promotion-only shortlist path.
- Updated `trader/research/strategy_search.py` to expose incumbent-candidate holdout and promotion helpers.
- Added `scripts/run_unified_candidate_ladder.py`.

Verification:
- Focused research regression suites passed after the new schema was added.

### 2026-03-20 07: Phase 2 Shortlist Result

Artifact:
- `out/strategy_search_compare/unified_candidate_ladder/shortlist_summary.csv`
- `out/strategy_search_compare/unified_candidate_ladder/shortlist.md`

Current standings:
1. `incumbent_macd_regime_gated`
2. `B_regime_switch_trend_range`
3. `C_breakout_atr_risk_template`
4. `A_beta_hedged_carry_momo`

Promotion and rejection outcomes:
- Incumbent MACD passed the unified ladder and remains the benchmark-to-beat.
- Track B failed at stress.
- Track C failed at stress.
- Track A failed at walk-forward and also lacks observed carry-input columns in the local candle files, so its current result should be treated as proxy-based only.

### 2026-03-20 08: Phase 3 Runtime Observability

Implemented:
- Runtime feed callbacks now accumulate structured feed-event counts and last-event context.
- Runtime emits `first_bar_processed` with feed context.
- Runtime emits `zero_bar_session_detected` with feed-health details when a session ends with zero processed bars.
- `BinanceLiveFeed` now exposes a feed-health snapshot for runtime diagnostics.

Verification:
- Added runtime tests for zero-bar sessions and first-bar observability.
- Existing runtime safety regressions stayed green.

### 2026-03-20 09: Phase 4 Long-Run MACD Diagnostic Path

Implemented:
- Added websocket payload milestones in `trader/data/binance_live.py`:
  - `first_ws_payload_received`
  - `first_kline_payload_received`
  - `first_closed_kline_received`
- Added explicit websocket receive-timeout events in the worker loop.
- Added live-versus-backfill processed-bar accounting in `trader/runtime.py`.
- Added `trader/runtime_diagnostics.py` to generate machine-readable and human-readable long-run runtime summaries.
- Updated `scripts/run_macd_final_candidate_testnet_long.ps1` to launch `uv` directly and to write diagnostic context plus `diagnostic_summary.json` / `diagnostic_summary.md`.

Verification:
- Targeted runtime suites passed after the new milestone and summary logic was added.
- Fresh artifact path created:
  - `out/operational_validation/macd_final_candidate_testnet_long_20260320_diag/diagnostic_summary.json`
  - `out/operational_validation/macd_final_candidate_testnet_long_20260320_diag/diagnostic_summary.md`

Fresh evidence:
- Run ID `95df99b4b76b411ba432aedfea16452f` registered cleanly and completed preflight for all three symbols.
- No websocket worker start, connect, payload, kline, or bar milestone was recorded in the first 31.81 minutes.
- The fresh diagnostic verdict is `zero_bar_no_feed_connection`.
- User-stream disconnect noise was present, but the market-data path did not emit any websocket milestone at all, so the current failure class is now localized to the transition between post-preflight runtime startup and feed-worker activity.

Runbook:
- Fresh diagnostic command:
  - `powershell -NoProfile -File scripts/run_macd_final_candidate_testnet_long.ps1 -Hours 0.20 -SnapshotEverySec 60 -MaxWallBufferMinutes 0 -OutDir out/operational_validation/macd_final_candidate_testnet_long_YYYYMMDD_diag`
- Inspect:
  - `diagnostic_summary.json` for machine-readable verdict and stage timings
  - `diagnostic_summary.md` for fast human triage
- Closure criteria for the long-run gap:
  - at least one live processed bar
  - no unexplained stall between feed milestones
  - working reconnect and stale-stream visibility
  - enough artifact detail to diagnose the next failure without manual guesswork

### 2026-03-20 10: Multisymbol Startup Handoff Localization

Implemented:
- `RuntimeOrchestrator` now persists worker-stage milestones from the main thread:
  - `feed_worker_thread_created`
  - `feed_worker_thread_started`
  - `feed_worker_entered`
  - `feed_worker_entered_iter_closed_bars`
  - `feed_worker_exception`
  - `feed_worker_completed`
- `BinanceLiveFeed` now emits:
  - `binance_live_feed_initialized`
  - `websocket_worker_start_called`
  - `first_market_payload_received`
- Runtime now emits `first_bar_dispatched` before the existing `first_bar_processed`.
- `runtime_diagnostics.py` now reports:
  - last confirmed healthy stage
  - first missing stage
  - runtime stopped status
  - user-stream correlation relative to market-feed startup

Fresh evidence:
- Artifact directory:
  - `out/operational_validation/macd_final_candidate_testnet_handoff_20260320`
- Fresh run verdict from `diagnostic_summary.json`:
  - `WARNING`
  - `diagnostic_verdict=insufficient_elapsed_time_for_closed_bar`
- New stage evidence from run `8b5682e692584bf98106191dbffa7463`:
  - all 3 symbols reached `feed_worker_thread_created`
  - all 3 symbols reached `feed_worker_thread_started`
  - all 3 symbols reached `feed_worker_entered_iter_closed_bars`
  - all 3 symbols reached `binance_live_feed_initialized`
  - all 3 symbols reached `websocket_worker_start_called`
  - all 3 symbols reached `ws_worker_started`
  - all 3 symbols reached `ws_worker_connected`
  - all 3 symbols reached `first_market_payload_received`
  - all 3 symbols reached `first_kline_payload_received`
  - no symbol reached `first_closed_kline_received`
  - no bar was dispatched because the bounded run lasted only 3.15 minutes on a 4h stream

Interpretation:
- The previously suspected post-preflight handoff gap is no longer supported by fresh artifact evidence.
- The path is now localized as healthy through websocket connect and kline ingestion.
- The remaining zero-bar result in the bounded run is expected because the run duration is shorter than one 4h candle close.
- `runtime_stopped` is still missing in the bounded forced-stop path, but that is now explicitly classified rather than hidden.

### 2026-03-20 11: Pipeline-Proof Feed/Runtime Chain

Implemented:
- `trader/runtime_diagnostics.py` now requires explicit graceful stop classification before it can declare the real long-run gap closed.
- `trader/runtime_diagnostics.py` now reports `runtime_validation_confidence_advanced` separately from `long_run_gap_closed`.
- Single-symbol direct-feed runs no longer pretend orchestrator worker-thread stages are missing once later feed stages are present.
- `scripts/run_macd_final_candidate_testnet_long.ps1` now supports explicit pipeline-proof invocation with:
  - `-ValidationMode pipeline_proof`
  - `-Timeframe 1m`
  - `-StopAfterFirstLiveBar`
  - next-close metadata persisted into `diagnostic_context.json`

Verification:
- Focused runtime suites passed after the stop-classification and stage-progression fixes.
- Fresh artifact directory:
  - `out/operational_validation/macd_pipeline_proof_20260320`

Fresh evidence:
- Fresh run ID: `1bf74f0d28dd4b498d190607e5e435a7`
- `diagnostic_summary.json` verdict: `PASS`
- `validation_mode=pipeline_proof`
- `strategy_evidence_allowed=false`
- `feed_runtime_chain_proven=true`
- `runtime_validation_confidence_advanced=true`
- `long_run_gap_closed=false`
- `runtime_stopped_status=normal`
- `stop_classification=normal_graceful_stop`
- `processed_bars_total=1`
- `processed_live_bars=1`
- `first_closed_kline_received=2026-03-20T07:23:00.434134+00:00`
- `first_bar_dispatched=2026-03-20T07:23:00.445754+00:00`
- `first_bar_processed=2026-03-20T07:23:00.449546+00:00`
- `first_missing_stage=none`
- `last_confirmed_healthy_stage=runtime_stopped`

Interpretation:
- The closed-kline -> bar-dispatch -> processed-live-bar -> graceful-stop chain is now proven on a live testnet websocket runtime path.
- This artifact is runtime-pipeline evidence only; it is not strategy-promotion evidence and it does not close the real 4h incumbent gap.
- The remaining open item is a real 4h incumbent run that survives long enough to record the same chain on the intended timeframe.

Runbook:
- Fast pipeline-proof command:
  - `powershell -NoProfile -File scripts/run_macd_final_candidate_testnet_long.ps1 -ValidationMode pipeline_proof -Timeframe 1m -Symbols BTC/USDT -StopAfterFirstLiveBar -Hours 0.10 -SnapshotEverySec 60 -MaxWallBufferMinutes 0 -OutDir out/operational_validation/macd_pipeline_proof_YYYYMMDD`
- Real 4h incumbent follow-up command:
  - `powershell -NoProfile -File scripts/run_macd_final_candidate_testnet_long.ps1 -ValidationMode real_strategy -Timeframe 4h -Symbols BTC/USDT,ETH/USDT,BNB/USDT -StopAfterFirstLiveBar -Hours 4.50 -SnapshotEverySec 300 -MaxWallBufferMinutes 0 -OutDir out/operational_validation/macd_final_candidate_testnet_4h_first_live_bar_YYYYMMDD`

### 2026-03-20 12: Artifact Semantics Cleanup And Runtime Confidence Ladder

Implemented:
- `summary.json` for the MACD long-run runner now takes its top-level `verdict` from `diagnostic_summary.json` rather than from strategy-lifecycle placeholders such as `fills_missing`.
- `summary.json` and `diagnostic_summary.json` now agree on:
  - `validation_mode`
  - `evidence_scope`
  - whether strategy lifecycle validation is applicable
  - whether runtime-chain proof advanced
- `summary.json` now preserves any non-applicable strategy-lifecycle checks separately:
  - `strategy_lifecycle_verdict`
  - `strategy_lifecycle_issues`
  - `strategy_lifecycle_warnings`
- `summary.json` now records:
  - `primary_verdict_source=diagnostic_summary.json`
  - `summary_interpretation`

Verification:
- Refreshed pipeline-proof artifact directory:
  - `out/operational_validation/macd_pipeline_proof_20260320`
- Fresh run ID after semantic cleanup:
  - `ea9e0e0d832147c4a46db2de9d00e7c3`
- Fresh artifact outcome:
  - `summary.json verdict=PASS`
  - `diagnostic_summary.json verdict=PASS`
  - `evidence_scope=runtime_pipeline_only`
  - `strategy_lifecycle_validation_applicable=false`
  - `runtime_chain_proof_advanced=true`
  - `long_run_gap_closed=false`

Interpretation:
- Artifact ambiguity between `summary.json` and `diagnostic_summary.json` is now removed for pipeline-proof mode.
- A reviewer can now read either file without mistaking a runtime-chain proof for a trading-lifecycle failure.

Real 4h readiness:
- The real incumbent command is ready and remains:
  - `powershell -NoProfile -File scripts/run_macd_final_candidate_testnet_long.ps1 -ValidationMode real_strategy -Timeframe 4h -Symbols BTC/USDT,ETH/USDT,BNB/USDT -StopAfterFirstLiveBar -Hours 4.50 -SnapshotEverySec 300 -MaxWallBufferMinutes 0 -OutDir out/operational_validation/macd_final_candidate_testnet_4h_first_live_bar_YYYYMMDD`
- Current blocker to producing a fresh real 4h first-live-bar artifact in this pass:
  - the next 4h candle close was no longer near after the semantic-cleanup rerun, so a short run would only reproduce `insufficient_elapsed_time` rather than useful first-live-bar evidence

Runtime confidence ladder:
- `R1`: pipeline-proof runtime-chain proven
- `R2`: real incumbent timeframe first-live-bar proven
- `R3`: multi-bar real incumbent runtime proven
- `R4`: longer soak with reconnect, health, and graceful-stop evidence
- `R5`: paper/testnet runtime confidence sufficient for broader candidate evaluation

Guardrail:
- `R2` is not enough to claim long-run stability; it only proves the intended 4h path can process at least one real live bar and stop cleanly.
