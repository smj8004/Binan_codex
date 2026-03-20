# Decisions

Date: 2026-03-20
Status: planning baseline

## Decision Log

### D-016: Keep Alpha Validation And Execution Validation Separate

Decision:
- Maintain separate evidence streams for alpha viability and runtime safety.

Alternatives considered:
- Collapse both into one PASS or FAIL verdict.

Rationale:
- Repo evidence already shows operational PASS can coexist with non-profitable short-run outcomes.
- Mixing them would hide the main business objective: live-viable profit.

Consequence:
- Candidate promotion requires passing both alpha gates and runtime gates, in sequence.

### D-017: Treat The Current MACD Finalist As The Alpha Baseline, Not As The End State

Decision:
- Use the regime-gated MACD finalist as the current benchmark survivor.

Alternatives considered:
- Re-open Donchian as co-leader.
- Ignore directional finalists and start from scratch on Track A, Track B, and Track C.

Rationale:
- MACD has the strongest existing holdout evidence in local artifacts.
- Donchian was explicitly demoted by the stricter holdout outputs.
- Starting from scratch would discard hard-earned repo evidence and slow the work.

Consequence:
- Future candidate tracks must beat or clearly complement the MACD baseline under comparable gates.

### D-018: Prioritize Unifying Research Realism Before Broadening Runtime Complexity

Decision:
- The next implementation phase should focus first on research-stack unification and stronger anti-overfitting gates, then on long-run runtime validation.

Alternatives considered:
- Focus mainly on runtime polish first.
- Add more strategy families before unifying evidence logic.

Rationale:
- The repo already has a relatively mature runtime layer.
- The largest alpha-process weakness is split research logic and incomplete funding-aware integration.
- The user goal is live profit, not only operational safety.

Consequence:
- Runtime work should be limited to validation-critical fixes and observability until at least one non-brittle candidate survives the strengthened research ladder.

### D-019: Use `trader/backtest/engine.py` As The Shared Research Realism Kernel

Decision:
- Standardize candidate evaluation around `trader/backtest/engine.py` wherever feasible.

Alternatives considered:
- Keep separate simulation logic in the portfolio experiment stack.

Rationale:
- The backtest engine already contains the repo's best cost, slippage, funding, and order-model realism.
- Maintaining multiple evaluation kernels increases silent drift risk.

Consequence:
- The portfolio/system stack should be refactored to use or closely mirror the shared backtest kernel and shared gate outputs.

### D-020: The Formal Promotion Ladder Is Mandatory

Decision:
- Every candidate must move through explicit stage gates: Stage 1 research generation, Stage 2 walk-forward and cost stress, Stage 3 robustness across symbols, timeframes, and regimes, Stage 4 execution-aware simulation, Stage 5 paper and testnet runtime validation, and Stage 6 tiny-canary live eligibility.

Alternatives considered:
- Ad hoc human promotion based on a few attractive charts or summaries.

Rationale:
- Repo history already contains an example where the initial winner failed later holdout.
- A formal ladder makes selection mechanical and reviewable.

Consequence:
- No candidate is eligible for live progression without explicit pass records at every prior stage.

### D-021: Official Binance USD-M Docs Override Memory And Existing Assumptions

Decision:
- Runtime and execution assumptions must be verified against current official Binance docs.

Alternatives considered:
- Rely on existing code behavior as implicit truth.

Rationale:
- Exchange semantics, filters, and limits are operationally brittle.
- The current repo already contains env and runtime incidents caused by incorrect assumptions.

Consequence:
- Any runtime change list must cite the relevant Binance endpoint or stream behavior.

### D-022: Use Conservative Assumptions Where Repo Evidence Is Ambiguous

Decision:
- When ambiguity remains, choose the more conservative, more reviewable assumption and record it in docs.

Alternatives considered:
- Pause for clarification.

Rationale:
- The user explicitly asked not to ask clarifying questions.
- Conservative assumptions reduce hidden production risk.

Consequence:
- Assumptions must be logged in `docs/notes.md` before implementation.

### D-023: Add A Promotion-Only System Batch Path Instead Of Reusing Full Edge-Validation Output Generation

Decision:
- `run_system_batch()` now supports a promotion-only execution path for Phase 2 shortlist generation.

Alternatives considered:
- Reuse the existing full `run_edge_validation()` artifact path for every symbol and candidate.

Rationale:
- The full edge-validation path was too slow for the 14-symbol shortlist run and was generating artifacts that the unified promotion ladder did not use.
- The promotion-only path still runs the approved gates: walk-forward, full-period stress, breadth, and holdout.

Consequence:
- Phase 2 shortlist generation is tractable without weakening holdout or stress requirements.
- Shortlist artifacts remain separate from the heavier edge-validation reports.

### D-024: Calibrate Walk-Forward Positive-Ratio Threshold To `0.50`

Decision:
- The shared promotion threshold for walk-forward positive-ratio is `0.50`, not `0.55`.

Alternatives considered:
- Keep the stricter `0.55` threshold.

Rationale:
- Under the unified ladder, the incumbent MACD benchmark was failing by a negligible margin despite positive OOS, positive stressed OOS, and positive holdout evidence.
- A `0.50` threshold preserves rigor while matching the existing benchmark evidence recorded in repo artifacts.

Consequence:
- The incumbent MACD benchmark remains the current baseline survivor.
- Track A and the weaker stressed candidates still fail under the unified ladder.

### D-025: Zero-Bar Runtime Failures Must Produce Structured Feed-Health Evidence

Decision:
- Zero-processed-bar sessions now emit explicit runtime diagnostics rather than leaving only an empty summary.

Alternatives considered:
- Keep relying on ad hoc event logs and manual inspection.

Rationale:
- The latest long-run testnet failure class was specifically "fresh run detected but processed bars stayed zero".
- Diagnosing that class requires feed-event counts, first-bar timing, and feed health snapshots in structured artifacts.

Consequence:
- Runtime state and stop events now include feed-event counts, first-bar delay, and feed-health context.
- Regression tests lock this observability in place.

### D-026: Long-Run MACD Diagnostics Must Be DB-Driven And Stage-Classified

Decision:
- Build long-run runtime verdicts from persisted runtime events and runtime_state, then classify the first unhealthy stage explicitly.

Alternatives considered:
- Keep relying on ad hoc stdout parsing and a flat PASS or FAIL summary.

Rationale:
- The old long-run artifact could report zero processed bars without telling whether the failure happened before websocket connect, before kline parsing, or before bar dispatch.
- Persisted runtime milestones are the only reliable way to distinguish those cases.

Consequence:
- `trader/runtime_diagnostics.py` is now the source of truth for machine-readable and human-readable long-run runtime summaries.
- Long-run artifacts now classify cases such as no feed connection, payload without kline, closed kline without dispatch, backfill-only processing, and insufficient elapsed time for a closed bar.

### D-027: The Long-Run Runner Must Launch `uv` Directly, Not Through A Wrapper Shell

Decision:
- `scripts/run_macd_final_candidate_testnet_long.ps1` now starts `uv` directly instead of nesting the runtime command in another PowerShell process.

Alternatives considered:
- Keep the wrapper-shell launch and hope the wrapper lifecycle matches the runtime lifecycle.

Rationale:
- The previous long-run evidence path could terminate or summarize at the wrapper-process boundary instead of the actual runtime boundary, which made the artifact misleading.
- Direct process ownership is simpler and more reviewable.

Consequence:
- The long-run runner now has a clearer process boundary for startup and shutdown accounting.
- External callers still need to give the script enough wall-clock budget to reach its own deadline and artifact finalization.

### D-028: Multisymbol Feed Milestones Must Be Routed Back Through The Orchestrator

Decision:
- In multisymbol runtime mode, worker-thread feed milestones are queued back to the orchestrator and persisted from the main thread.

Alternatives considered:
- Let worker threads write runtime events directly through the existing storage callback path.

Rationale:
- The post-preflight handoff gap was not observable enough when feed milestones depended on worker-thread callback behavior.
- Persisting those milestones from the main thread keeps the diagnostic path deterministic and reviewable without changing strategy logic.

Consequence:
- Fresh runtime artifacts now distinguish:
  - thread created vs thread started
  - thread entered vs entered `iter_closed_bars`
  - feed initialized vs websocket start called
  - websocket started vs connected vs first payload vs first kline vs first closed kline vs first bar dispatch
- The old `zero_bar_no_feed_connection` diagnosis is no longer the source of truth for this path once the new milestones are present.

### D-029: The Real Long-Run Gap Cannot Be Called Closed Without An Explicit Graceful Stop Record

Decision:
- A real-timeframe runtime artifact only closes the long-run gap if it includes both non-zero live bar processing and a persisted normal `runtime_stopped` record.

Alternatives considered:
- Treat non-zero live bars alone as enough proof.

Rationale:
- The remaining operational question is not only whether bars can be processed, but whether the run can be stopped and diagnosed cleanly without wrapper-process guesswork.
- A missing stop record would leave restart and artifact-triage ambiguity in the exact failure class the recent work is trying to remove.

Consequence:
- `trader/runtime_diagnostics.py` now separates:
  - `feed_runtime_chain_proven`
  - `runtime_validation_confidence_advanced`
  - `long_run_gap_closed`
- `long_run_gap_closed` requires `stop_classification=normal_graceful_stop`.

### D-030: Use A Short-Timeframe Pipeline-Proof Mode To Validate Feed And Runtime Mechanics Without Claiming Strategy Evidence

Decision:
- Add an explicit pipeline-proof run mode for the incumbent MACD validation script, using the live websocket runtime path but labeling the artifact as runtime-only evidence.

Alternatives considered:
- Wait only for a 4h close to prove the feed/runtime chain.
- Reuse a synthetic or unit-test-only path instead of the live runtime path.

Rationale:
- The fastest narrow proof for the remaining runtime gap is to show a real closed kline, bar dispatch, processed live bar, and graceful stop on the same production runtime path.
- That proof should not be confused with alpha or real-timeframe strategy evidence.

Consequence:
- `scripts/run_macd_final_candidate_testnet_long.ps1` now supports `-ValidationMode pipeline_proof` with explicit next-close metadata and graceful first-live-bar stopping.
- Pipeline-proof artifacts can advance runtime confidence, but they cannot close the real 4h incumbent validation gap on their own.

### D-031: The MACD Long-Run Runner Summary Must Take Its Top-Level Verdict From The Runtime Diagnostic

Decision:
- `summary.json` now uses the runtime diagnostic verdict as its top-level verdict and preserves strategy-lifecycle checks in explicitly secondary fields.

Alternatives considered:
- Keep `summary.json` verdict tied to generic strategy-lifecycle placeholders such as `fills_missing`.

Rationale:
- In pipeline-proof mode, `fills_missing` is expected and should not masquerade as a runtime-validation failure.
- The script is being used as a runtime-validation runner first, not as a complete trade-lifecycle acceptance runner.

Consequence:
- `summary.json` and `diagnostic_summary.json` now align on:
  - `validation_mode`
  - `evidence_scope`
  - strategy-lifecycle applicability
  - runtime-chain advancement
- Strategy-lifecycle context remains available under:
  - `strategy_lifecycle_verdict`
  - `strategy_lifecycle_issues`
  - `strategy_lifecycle_warnings`

### D-032: Treat Runtime Validation As A Ladder With Distinct Confidence Stages

Decision:
- Runtime validation claims must advance through explicit stages `R1` through `R5`, not through one vague PASS label.

Alternatives considered:
- Treat any successful runtime artifact as sufficient for broad operational confidence.

Rationale:
- A single observed live bar on the real 4h path is meaningful, but it is not the same as a reconnect-tested soak or broader runtime maturity.
- The repo already has evidence showing that short-path operational success and long-run runtime confidence are different questions.

Consequence:
- `R1`: pipeline-proof runtime-chain proven
- `R2`: real incumbent timeframe first-live-bar proven
- `R3`: multi-bar real incumbent runtime proven
- `R4`: longer soak with reconnect, health, and graceful-stop evidence
- `R5`: paper/testnet runtime confidence sufficient for broader candidate evaluation
