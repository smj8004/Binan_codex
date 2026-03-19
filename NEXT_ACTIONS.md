# Next Actions (2026-03-15)

## Current Situation
- fixed operational candidate remains:
  - `macd @ 4h`
  - `fast=12`
  - `slow=26`
  - `signal=9`
  - tightened regime gating baked into `macd_final_candidate`
- 3-symbol operational validation status:
  - `paper`: `PASS`
    - `run_id=185d99c7961f4681a49b8f18fe7442fa`
    - orders/fills/trades: `11/6/3`
    - `fills_accounted_count=6`
    - `fill_provenance_breakdown={"by_source":{"direct_runtime":6},"fills_reconciled_count":0,"fills_with_source_history_count":0}`
    - `accounting_consistency_pass=true`
    - protective orders created: `3`
  - `testnet`: `PASS`
    - `run_id=392e3990ee3547ee8c30a98f7f0356b8`
    - orders/fills/trades: `12/8/3`
    - `fills_accounted_count=8`
    - `fills_reconciled_count=8`
    - `fills_from_rest_reconcile_count=8`
    - `fills_from_aggregated_fallback_count=0`
    - `partial_fills_count=4`
    - `reconciled_missing_ws_fill_count=8`
    - `trade_query_unavailable_count=0`
    - `fill_provenance_breakdown={"by_source":{"rest_trade_reconcile":8},"fills_reconciled_count":8,"fills_with_source_history_count":0}`
    - `partial_fill_audit_summary={"partial_fill_groups_count":2,"partial_fill_rows_count":4,"aggregated_fallback_fill_count":0,"reconciled_missing_ws_fill_count":8,"trade_query_unavailable_count":0,"fills_with_multiple_source_history_count":0}`
    - `accounting_consistency_pass=true`
    - protective orders created: `3`
    - `user_stream_disconnect_count=14`
    - `user_stream_dns_reconnect_count=14`
  - `testnet_long`: `FAIL`
    - `run_id=""`
    - `previous_latest_run_id=17b3afcd5fbe45efb4ef4ee043de939f`
    - duration: `5.18m`
    - `startup_stalled_before_run_id=true`
    - `user_stream_disconnect_count=18`
    - `user_stream_dns_reconnect_count=18`
    - orders/fills/trades: `0/0/0`
    - `accounting_consistency_pass=false`

## What Was Fixed
- volatility breaker no longer blocks validation:
  - validation wrappers now relax `MAX_ATR_PCT` only for this controlled execution check
- order path is now exercised deterministically:
  - validation probe forces one entry/exit cycle without changing strategy params
  - live backfill execution is allowed only for this validation probe path
- user-stream loop bug is fixed:
  - `user_stream_no_running_event_loop_count=0` on latest testnet rerun
- protective trigger orders no longer fail waiting for an impossible terminal status at creation time
- live broker preflight is cached across symbols:
  - avoids self-induced `-1003` rate-limit bans on 3-symbol testnet starts
- multi-symbol trade ids are now unique:
  - summary/status trade counts stay aligned on shared-run validation
- fill provenance / partial-fill observability is now explicit:
  - each fill row records `source`, `provenance_detail`, `source_history`, partial-fill group metadata, and reconciliation flags
  - `trader status`, `summary.json`, and DB rows now expose WS vs REST vs aggregate fallback counts directly
- fixed-candidate long-run runner now exists:
  - `scripts/run_macd_final_candidate_testnet_long.ps1`
  - emits `doctor_preflight.txt`, `status_final.txt`, `summary.json`, `status_snapshots/`, `reconciliation_audit.json`

## Remaining Issue
- raw Binance testnet logs still show user-stream DNS reconnect churn:
  - `Could not contact DNS servers`
- consequence:
  - short validation runs still recover accounting/provenance correctly
  - the startup-stall blocker is now removed, but the latest recovery rerun still stopped before any bar/order lifecycle was observed

## Immediate Focus
- keep the fixed candidate and 3-symbol scope unchanged
- keep the startup-stall fix in place
- rerun `powershell -ExecutionPolicy Bypass -File scripts/run_macd_final_candidate_testnet_long.ps1 -SnapshotEverySec 60`
- do not reopen research, broad sweep, holdout work, or 14-symbol expansion until the 3-symbol long-run path produces a fresh `run_id` and meaningful bar/order lifecycle activity

## Startup Stall Resolution
- root cause:
  - runner detection relied on `runtime_state` / `status --latest`
  - `RuntimeEngine.start_session()` previously did not persist `runtime_state` before first bar
- fix shipped:
  - initial `runtime_state` is now saved at session start
  - long-run summary/status now record startup-specific evidence:
    - `attempted_process_started`
    - `fresh_run_id_detected`
    - `startup_phase`
    - `startup_failure_reason`
    - `first_status_seen`
    - `first_event_seen`
    - `first_bar_seen`
    - `first_order_seen`
- latest recovery rerun:
  - `run_id=301a138e8d1e49aa9462eea7b02507af`
  - `startup_stalled_before_run_id=false`
  - `whether_fixed_params_loaded=true`
  - `startup_phase=status_written`
  - `processed_bars_total=0`
  - `orders/fills/trades=0/0/0`

## Quick Checks
```bash
uv run --active pytest -q
uv run --active trader doctor --env testnet
cat out/operational_validation/macd_final_candidate_paper/summary.json
cat out/operational_validation/macd_final_candidate_testnet/summary.json
```
