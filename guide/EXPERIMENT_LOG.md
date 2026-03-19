# Experiment Log

## 2026-03-10 - 12h Live-Forward Execution Template

### Run Command (1 line)
- `powershell -ExecutionPolicy Bypass -File scripts/run_live_forward_12h.ps1`

### Start / End Template
- start_utc:
- end_utc:
- duration_minutes:
- run_id:
- out_dir:
- summary_json:

### Summary Decision Criteria
- PASS:
  - `halted=false`
  - `symbols_halted=0`
  - no `protective_orders_not_2_for_open_positions`
- WARNING:
  - `trades=0` (stability pass 가능, 거래 없음)
- FAIL:
  - `halted=true` or `symbols_halted>0`
  - open position exists without 2 protective orders
  - repeated min-size issues without meaningful order attempts (`rejected_by_min_notional_count` 과다)

## 2026-03-19 - 3-symbol fixed MACD long-run testnet pilot

### Run Command
- `powershell -ExecutionPolicy Bypass -File scripts/run_macd_final_candidate_testnet_long.ps1 -SnapshotEverySec 60`

### Fixed Scope
- `strategy=macd_final_candidate`
- `timeframe=4h`
- `symbols=BTC/USDT,ETH/USDT,BNB/USDT`
- preset `macd_final_candidate_ops`
- fixed params `fast=12 slow=26 signal=9`

### Artifact Dir
- `out/operational_validation/macd_final_candidate_testnet_long/`

### Result
- verdict `FAIL`
- duration `5.18` minutes
- `startup_stalled_before_run_id=true`
- `run_id=""`
- `previous_latest_run_id=17b3afcd5fbe45efb4ef4ee043de939f`
- `user_stream_disconnect_count=18`
- `user_stream_dns_reconnect_count=18`
- `orders/fills/trades=0/0/0`
- `fills_accounted_count=0`
- `fills_reconciled_count=0`
- `fills_from_rest_reconcile_count=0`
- `fills_from_aggregated_fallback_count=0`

### Operational Read
- the new long-run runner itself works:
  - doctor, snapshot loop, `summary.json`, `status_final.txt`, and `reconciliation_audit.json` were all produced
- the runtime did not become a new active run before the startup timeout:
  - repeated user-stream DNS reconnect churn dominated startup
  - no trustworthy long-run state/accounting/protective lifecycle could be evaluated

### Decision
- 3-symbol short operational validation: still `PASS`
- 3-symbol long-run operational validation: `FAIL`
- 14-symbol expansion: not allowed yet
- next step:
  - remove the startup stall on the 3-symbol fixed-candidate testnet path, then rerun the same long-run command unchanged

## 2026-03-20 - 3-symbol fixed MACD long-run startup recovery

### Run Command
- `powershell -ExecutionPolicy Bypass -File scripts/run_macd_final_candidate_testnet_long.ps1 -SnapshotEverySec 60`

### Diagnosis
- the blocker was a startup detection bug, not a strategy/preset init failure
- before the fix:
  - `runtime_started` events existed, but no `runtime_state` row was persisted until first bar / finish
  - `status --latest` and the runner therefore confused an alive 4h websocket startup with "no fresh run"

### Fix
- `RuntimeEngine.start_session()` now persists an initial `runtime_state`
- the long-run runner now records:
  - `attempted_process_started`
  - `fresh_run_id_detected`
  - `startup_phase`
  - `startup_failure_reason`
  - `first_status_seen`
  - `first_event_seen`
  - `first_bar_seen`
  - `first_order_seen`
- once a fresh `run_id` is known, status snapshots are pinned to `--run-id` instead of continuing to trust `--latest`

### Result
- fresh `run_id=301a138e8d1e49aa9462eea7b02507af`
- `startup_stalled_before_run_id=false`
- `whether_fixed_params_loaded=true`
- `startup_phase=status_written`
- `first_event_seen=true`
- `first_status_seen=true`
- `processed_bars_total=0`
- `orders/fills/trades=0/0/0`
- `user_stream_disconnect_count=19`
- `user_stream_dns_reconnect_count=19`

### Operational Read
- startup recovery: `PASS`
- full 12h operational validation: still pending
- 14-symbol expansion: still blocked until a true unattended 12h rerun completes

## 2026-03-08 - Live Testnet Budget Guard and Demo-Visible Order Path

### Scope
- changed area: runtime/broker/cli safety and execution path (no strategy lever change)
- goals:
  - make live-forward testnet runs visible in demo futures UI
  - enforce pre-order available-balance budget checks
  - keep protective-order flow intact under sufficient budget

### Implementation
- added testnet-only enforcement for `run --mode live`
- added pre-order account budget guard (default ON)
- added `insufficient_budget` event path when entry order is skipped
- added shared budget guard support for multi-symbol account-level consistency

### Verification Gates
- `uv run --active pytest -q`
- `uv run --active trader doctor --env testnet`

### Gate Results (2026-03-08)
- `pytest -q`: PASS (`22 passed`)
- `doctor --env testnet`: PASS (credentials/source diagnostics + auth/time/symbol checks all green)
- negative doctor check (intentionally bad key/secret): FAIL with clear `-2014` message and remediation hint table

### Demo Run Commands
- 1 symbol, 10 minutes:
  - `uv run --active trader run --mode live --env testnet --data-mode websocket --symbols BTC/USDT --timeframe 1m --strategy ema_cross --max-bars 10 --halt-on-error --yes-i-understand-live-risk`
- 3 symbols, 60 minutes:
  - `uv run --active trader run --mode live --env testnet --data-mode websocket --symbols BTC/USDT,ETH/USDT,BNB/USDT --timeframe 1m --strategy ema_cross --max-bars 60 --halt-on-error --yes-i-understand-live-risk`

### E2E Result Snapshot (2026-03-08)
- key incident root cause on this machine: process env overrides shadowed root `.env` (see `guide/INCIDENT_TESTNET_2014_2026-03-08.md`)
- additional runtime alignment found:
  - preflight fails if expected leverage mismatches account leverage (`expected=3`, account `live_leverage=20`)
  - this machine/account requires `LEVERAGE=20` (or exchange-side leverage reset to 3)
- real testnet live-forward evidence:
  - `run_id=91471b8aa4e74d578cbd9add56580e8d`
  - command env used: `LIVE_TRADING=true`, `LEVERAGE=20`, `--env testnet`
  - orders recorded in DB:
    - `BNB/USDT` `SELL MARKET` `filled` + `BUY STOP_MARKET/TAKE_PROFIT_MARKET` `new`
    - `ETH/USDT` `SELL MARKET` `filled` + `BUY STOP_MARKET/TAKE_PROFIT_MARKET` `new`
  - fills recorded in DB for both symbols
  - runtime log includes `open_protective=2` while position is open

### 2h Wall-Clock Durability Attempt (2026-03-08 UTC)
- target profile:
  - mode: `live`
  - env: `testnet`
  - data: `websocket`
  - symbols: `BTC/USDT,ETH/USDT,BNB/USDT`
  - bars: `120`
  - realtime guard: `--realtime-only` (new CLI option, no bootstrap backfill)
- codex session run artifact:
  - out dir: `out/experiments/live_forward_2h_20260308_131442`
  - runtime run_id: `5db848d01bec4045b4b74b153489decb`
  - observed UTC window: `2026-03-08T13:14:42Z` ~ `2026-03-08T13:24:00Z` (about 9m 18s)
  - status snapshots saved: `2` (`status_*.txt`, `status_final.txt`)
  - halted: `False`
  - orders/fills in this short window: `0/0` (all symbols `hold`)
- note:
  - this codex execution environment terminates long-running commands around ~10 minutes, so full 2h continuous wall-clock completion cannot be finalized inside one tool session.
  - use `scripts/run_live_forward_2h.ps1` on a normal terminal to complete full 2h unattended run with 5-minute snapshots and summary output.

### Demo UI Manual Checklist (for 2h run completion)
- [ ] Positions: 3 symbols are visible and per-symbol position/entry price are separated correctly
- [ ] Open Orders: per-position protective orders remain 2 (`SL/TP`) while position is open
- [ ] Reverse flow: old `SL/TP` canceled, new `SL/TP` created
- [ ] Assets: available balance changes are visible

### Notes
- live mode is restricted to testnet in this repo
- if available balance is insufficient, order submission is skipped with reason `insufficient_budget`

## 2026-03-08 - 6h Live-Forward Template (Pre-Execution)

### Goal
- run testnet futures live-forward for 6 wall-clock hours with websocket realtime bars only
- store 5-minute status snapshots and final PASS/FAIL summary artifact

### Command (1 line)
- `powershell -ExecutionPolicy Bypass -File scripts/run_live_forward_6h.ps1`

### Optional Overrides
- symbols override:
  - `powershell -ExecutionPolicy Bypass -File scripts/run_live_forward_6h.ps1 -Symbols "BTC/USDT,ETH/USDT,BNB/USDT,SOL/USDT"`
- quick validation mode:
  - `powershell -ExecutionPolicy Bypass -File scripts/run_live_forward_6h.ps1 -Hours 0.1 -MaxBars 5 -SnapshotEverySec 60`

### Runtime Contract
- hard fixed by script:
  - process env override cleanup for Binance keys
  - `--env testnet`
  - `--data-mode websocket`
  - `--realtime-only`
  - doctor gate must PASS before runtime start
- defaults:
  - `LIVE_TRADING=true`
  - `LEVERAGE=20`
  - `Hours=6` -> `max-bars=360`

### Artifact Layout
- output root: `out/experiments/live_forward_6h_<timestamp>/`
- files:
  - `doctor_preflight.txt`
  - `run_stdout.log`
  - `run_stderr.log`
  - `status_snapshots/status_*.txt` (every 5 minutes)
  - `status_final.txt`
  - `summary.json`
  - `trader.log`

### PASS / FAIL Rule
- PASS:
  - `halted=false`
  - `symbols_halted=0`
  - each symbol with open position keeps protective `open_orders=2`
- WARNING only:
  - `trades=0` (stability PASS 가능, 거래 없음 경고)
- FAIL:
  - any halt
  - any symbol halted
  - any open-position symbol with protective order count not equal to 2

### Demo UI Manual Checklist
- [ ] Positions: per-symbol position/entry price separation
- [ ] Open Orders: open position symbols keep SL/TP 2 orders
- [ ] Reverse: old SL/TP cancel + new SL/TP create
- [ ] Assets: available balance change visible

## 2026-03-05 - Shock Cooldown A/B (48 vs 36)

### Scope
- changed lever only: `shock_cooldown_bars` (`48` vs `36`)
- A: `shock_cooldown_bars=48` (baseline)
- B: `shock_cooldown_bars=36`
- all fixed baseline values unchanged (`testnet=False`, `k=4`, `rank_buffer=2`, `lookback_score_mode=median_3`, `shock_mode=downweight`, `shock_freeze_min_fraction=0.40`, `atr_shock_threshold=2.7`, `gap_shock_threshold=0.12`, `shock_weight_mult_gap=0.15`, `shock_weight_mult_atr=0.30`, `extreme_regime_mode=delever`, `extreme_gross_mult=0.5`, cost model unchanged)
- sweep artifact dir: `out/experiments/shock_cooldown_48_vs_36_ab_20260305_142036`

### Run IDs
- Run A (`shock_cooldown_bars=48`): `portfolio_20260305_142036_128bd130`
- Run B (`shock_cooldown_bars=36`): `portfolio_20260305_142310_6028f6fb`

### Metrics

| scenario | shock_cooldown_bars | net_pnl | max_drawdown | fee_cost_total | oos_positive_ratio | avg_turnover_ratio | skipped_ratio | avg_effective_gross | shock_skip_ratio | extreme_skip_ratio | extreme_no_trade_ratio | atr_shock_count | gap_shock_count | liquidation_count | eq0_count |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 48 | 13880.3947 | -0.156128 | 989.4938 | 0.588235 | 0.243189 | 0.007147 | 0.625261 | 0.007147 | 0.000000 | 0.028037 | 218 | 7 | 0 | 0 |
| B | 36 | 13880.3947 | -0.156128 | 989.4938 | 0.588235 | 0.243189 | 0.007147 | 0.625261 | 0.007147 | 0.000000 | 0.028037 | 218 | 7 | 0 | 0 |

### Hard-Gate Check
- Run A: pass (`liquidation_count=0`, `equity_zero_or_negative_count=0`, `fee_cost_total<=2000`)
- Run B: pass (`liquidation_count=0`, `equity_zero_or_negative_count=0`, `fee_cost_total<=2000`)

### Conclusion (1 line)
- **TIE**: all decision metrics are equal, so keep baseline `shock_cooldown_bars=48`.

### Next Lever (1 only)
- Change only `extreme_gross_mult` from `0.5` to `0.4`, keep all other fixed values unchanged.

## 2026-03-05 - Shock Weight Mult ATR A/B (0.25 vs 0.30)

### Scope
- changed lever only: `shock_weight_mult_atr` (`0.25` vs `0.30`)
- A: `shock_weight_mult_atr=0.25` (baseline)
- B: `shock_weight_mult_atr=0.30`
- all fixed baseline values unchanged (`testnet=False`, `k=4`, `rank_buffer=2`, `lookback_score_mode=median_3`, `shock_mode=downweight`, `shock_cooldown_bars=48`, `atr_shock_threshold=2.7`, `gap_shock_threshold=0.12`, `shock_weight_mult_gap=0.15`, `extreme_regime_mode=delever`, `extreme_gross_mult=0.5`, cost model unchanged)
- sweep artifact dir: `out/experiments/shock_weight_mult_atr_ab_20260305_140503`

### Run IDs
- Run A (`shock_weight_mult_atr=0.25`): `portfolio_20260305_140503_8e754fee`
- Run B (`shock_weight_mult_atr=0.30`): `portfolio_20260305_140736_9e611134`

### Metrics

| scenario | shock_weight_mult_atr | net_pnl | max_drawdown | fee_cost_total | oos_positive_ratio | avg_turnover_ratio | skipped_ratio | avg_effective_gross | shock_skip_ratio | extreme_skip_ratio | extreme_no_trade_ratio | atr_shock_count | gap_shock_count | liquidation_count | eq0_count |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 0.25 | 13799.0023 | -0.156239 | 989.0487 | 0.588235 | 0.243403 | 0.007147 | 0.625261 | 0.007147 | 0.000000 | 0.028037 | 218 | 7 | 0 | 0 |
| B | 0.30 | 13880.3947 | -0.156128 | 989.4938 | 0.588235 | 0.243189 | 0.007147 | 0.625261 | 0.007147 | 0.000000 | 0.028037 | 218 | 7 | 0 | 0 |

### Hard-Gate Check
- Run A: pass (`liquidation_count=0`, `equity_zero_or_negative_count=0`, `fee_cost_total<=2000`)
- Run B: pass (`liquidation_count=0`, `equity_zero_or_negative_count=0`, `fee_cost_total<=2000`)

### Conclusion (1 line)
- **SUCCESS**: both passed hard gate, and within 5% `net_pnl` band `B(0.30)` had less severe `max_drawdown`, so `shock_weight_mult_atr=0.30` wins.

### Next Lever (1 only)
- Change only `extreme_gross_mult` from `0.5` to `0.4`, keep all other fixed values unchanged.

## 2026-03-05 - Shock Weight Mult Gap A/B (0.10 vs 0.15)

### Scope
- changed lever only: `shock_weight_mult_gap` (`0.10` vs `0.15`)
- A: `shock_weight_mult_gap=0.10` (baseline)
- B: `shock_weight_mult_gap=0.15`
- all fixed baseline values unchanged (`testnet=False`, `k=4`, `rank_buffer=2`, `lookback_score_mode=median_3`, `shock_mode=downweight`, `shock_cooldown_bars=48`, `atr_shock_threshold=2.7`, `gap_shock_threshold=0.12`, `extreme_regime_mode=delever`, `extreme_gross_mult=0.5`, cost model unchanged)
- sweep artifact dir: `out/experiments/shock_weight_mult_gap_ab_20260305_134817`

### Run IDs
- Run A (`shock_weight_mult_gap=0.10`): `portfolio_20260305_134817_6d96b47a`
- Run B (`shock_weight_mult_gap=0.15`): `portfolio_20260305_135052_e6302363`

### Metrics

| scenario | shock_weight_mult_gap | net_pnl | max_drawdown | fee_cost_total | oos_positive_ratio | avg_turnover_ratio | skipped_ratio | avg_effective_gross | shock_skip_ratio | extreme_skip_ratio | extreme_no_trade_ratio | gap_shock_count | atr_shock_count | liquidation_count | eq0_count |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 0.10 | 13778.1178 | -0.156239 | 988.1654 | 0.588235 | 0.243408 | 0.007147 | 0.625261 | 0.007147 | 0.000000 | 0.028037 | 7 | 218 | 0 | 0 |
| B | 0.15 | 13799.0023 | -0.156239 | 989.0487 | 0.588235 | 0.243403 | 0.007147 | 0.625261 | 0.007147 | 0.000000 | 0.028037 | 7 | 218 | 0 | 0 |

### Hard-Gate Check
- Run A: pass (`liquidation_count=0`, `equity_zero_or_negative_count=0`, `fee_cost_total<=2000`)
- Run B: pass (`liquidation_count=0`, `equity_zero_or_negative_count=0`, `fee_cost_total<=2000`)

### Conclusion (1 line)
- **SUCCESS**: both passed hard gate, and within 5% `net_pnl` band `B(0.15)` had marginally less severe `max_drawdown`, so `shock_weight_mult_gap=0.15` wins.

### Next Lever (1 only)
- Change only `shock_weight_mult_atr` from `0.25` to `0.20`, keep all other fixed values unchanged.

## 2026-03-05 - GAP Shock Threshold A/B (0.10 vs 0.12)

### Scope
- changed lever only: `gap_shock_threshold` (`0.10` vs `0.12`)
- A: `gap_shock_threshold=0.10` (current baseline)
- B: `gap_shock_threshold=0.12`
- all fixed baseline values unchanged (`testnet=False`, `k=4`, `rank_buffer=2`, `lookback_score_mode=median_3`, `shock_mode=downweight`, `shock_freeze_min_fraction=0.40`, `shock_cooldown_bars=48`, `atr_shock_threshold=2.7`, `extreme_regime_mode=delever`, `extreme_gross_mult=0.5`, cost model unchanged)
- sweep artifact dir: `out/experiments/gap_shock_threshold_ab_20260305_133424`

### Run IDs
- Run A (`gap_shock_threshold=0.10`): `portfolio_20260305_133424_bccc6c0c`
- Run B (`gap_shock_threshold=0.12`): `portfolio_20260305_133656_67f2b68d`

### Metrics

| scenario | gap_shock_threshold | net_pnl | max_drawdown | fee_cost_total | oos_positive_ratio | avg_turnover_ratio | skipped_ratio | avg_effective_gross | shock_skip_ratio | extreme_skip_ratio | extreme_no_trade_ratio | atr_shock_count | gap_shock_count | liquidation_count | eq0_count |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 0.10 | 13917.8080 | -0.156239 | 994.1668 | 0.588235 | 0.243492 | 0.007147 | 0.625261 | 0.007147 | 0.000000 | 0.028037 | 218 | 12 | 0 | 0 |
| B | 0.12 | 13778.1178 | -0.156239 | 988.1654 | 0.588235 | 0.243408 | 0.007147 | 0.625261 | 0.007147 | 0.000000 | 0.028037 | 218 | 7 | 0 | 0 |

### Hard-Gate Check
- Run A: pass (`liquidation_count=0`, `equity_zero_or_negative_count=0`, `fee_cost_total<=2000`)
- Run B: pass (`liquidation_count=0`, `equity_zero_or_negative_count=0`, `fee_cost_total<=2000`)

### Conclusion (1 line)
- **SUCCESS**: both passed hard gate, and within 5% `net_pnl` band `B(0.12)` had marginally less severe `max_drawdown` (and lower fee), so `gap_shock_threshold=0.12` wins.

### Next Lever (1 only)
- Change only `shock_weight_mult_gap` from `0.10` to `0.12`, keep all other fixed values unchanged.

## 2026-03-05 - ATR Shock Threshold A/B (2.5 vs 2.7)

### Scope
- changed lever only: `atr_shock_threshold` (`2.5` vs `2.7`)
- A: `atr_shock_threshold=2.5` (current baseline)
- B: `atr_shock_threshold=2.7`
- all fixed baseline values unchanged (`testnet=False`, `k=4`, `rank_buffer=2`, `lookback_score_mode=median_3`, `shock_mode=downweight`, `shock_freeze_min_fraction=0.40`, `shock_cooldown_bars=48`, `extreme_regime_mode=delever`, `extreme_gross_mult=0.5`, cost model unchanged)
- sweep artifact dir: `out/experiments/atr_shock_threshold_ab_20260305_131735`

### Run IDs
- Run A (`atr_shock_threshold=2.5`): `portfolio_20260305_131735_0c7ab5d4`
- Run B (`atr_shock_threshold=2.7`): `portfolio_20260305_132016_a641bd03`

### Metrics

| scenario | atr_shock_threshold | net_pnl | max_drawdown | fee_cost_total | oos_positive_ratio | avg_turnover_ratio | skipped_ratio | avg_effective_gross | shock_skip_ratio | extreme_skip_ratio | extreme_no_trade_ratio | atr_shock_count | gap_shock_count | liquidation_count | eq0_count |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 2.5 | 13274.7198 | -0.157939 | 972.7150 | 0.588235 | 0.244846 | 0.009896 | 0.625261 | 0.009896 | 0.000000 | 0.028037 | 275 | 12 | 0 | 0 |
| B | 2.7 | 13917.8080 | -0.156239 | 994.1668 | 0.588235 | 0.243492 | 0.007147 | 0.625261 | 0.007147 | 0.000000 | 0.028037 | 218 | 12 | 0 | 0 |

### Hard-Gate Check
- Run A: pass (`liquidation_count=0`, `equity_zero_or_negative_count=0`, `fee_cost_total<=2000`)
- Run B: pass (`liquidation_count=0`, `equity_zero_or_negative_count=0`, `fee_cost_total<=2000`)

### Conclusion (1 line)
- **SUCCESS**: both passed hard gate, and within 5% `net_pnl` band `B(2.7)` had less severe `max_drawdown`, so `atr_shock_threshold=2.7` wins.

### Next Lever (1 only)
- Change only `shock_weight_mult_atr` from `0.25` to `0.20`, keep all other fixed values unchanged.

## 2026-03-05 - Shock Cooldown Bars A/B (72 vs 48)

### Scope
- changed lever only: `shock_cooldown_bars` (`72` vs `48`)
- A: `shock_cooldown_bars=72` (current baseline)
- B: `shock_cooldown_bars=48`
- all fixed baseline values unchanged (`testnet=False`, `k=4`, `rank_buffer=2`, `lookback_score_mode=median_3`, `shock_mode=downweight`, `shock_freeze_min_fraction=0.40`, `extreme_regime_mode=delever`, `extreme_gross_mult=0.5`, cost model unchanged)
- sweep artifact dir: `out/experiments/shock_cooldown_ab_20260305_125434`

### Run IDs
- Run A (`shock_cooldown_bars=72`): `portfolio_20260305_125434_fc8457eb`
- Run B (`shock_cooldown_bars=48`): `portfolio_20260305_125714_fe235d26`

### Metrics

| scenario | shock_cooldown_bars | net_pnl | max_drawdown | fee_cost_total | oos_positive_ratio | avg_turnover_ratio | skipped_ratio | avg_effective_gross | shock_skip_ratio | extreme_skip_ratio | extreme_no_trade_ratio | liquidation_count | eq0_count |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 72 | 13192.0394 | -0.164491 | 977.3115 | 0.568627 | 0.246750 | 0.015943 | 0.625261 | 0.015943 | 0.000000 | 0.028037 | 0 | 0 |
| B | 48 | 13274.7198 | -0.157939 | 972.7150 | 0.588235 | 0.244846 | 0.009896 | 0.625261 | 0.009896 | 0.000000 | 0.028037 | 0 | 0 |

### Hard-Gate Check
- Run A: pass (`liquidation_count=0`, `equity_zero_or_negative_count=0`, `fee_cost_total<=2000`)
- Run B: pass (`liquidation_count=0`, `equity_zero_or_negative_count=0`, `fee_cost_total<=2000`)

### Conclusion (1 line)
- **SUCCESS**: both passed hard gate, and within 5% `net_pnl` band `B(48)` had less severe `max_drawdown` (and lower fee), so `shock_cooldown_bars=48` wins.

### Next Lever (1 only)
- Change only `shock_weight_mult_atr` from `0.25` to `0.20`, keep all other fixed values unchanged.

## 2026-03-05 - Lookback Score Mode A/B (median_3 vs single 28d)

### Scope
- changed lever only: `lookback_score_mode` (`median_3` vs `single`)
- A: `lookback_score_mode=median_3` with `lookback_bars=168` (7d/14d/28d median)
- B: `lookback_score_mode=single` with `lookback_bars=672` (28d)
- all fixed baseline values unchanged (`testnet=False`, `k=4`, `rank_buffer=2`, extreme definition/handling unchanged, `shock_freeze_min_fraction=0.40`, `extreme_regime_mode=delever`, `extreme_gross_mult=0.5`)
- sweep artifact dir: `out/experiments/lookback_mode_ab_20260305_121903`

### Run IDs
- Run A (`median_3`): `portfolio_20260305_121903_ed15ddf0`
- Run B (`single`, `lookback_bars=672`): `portfolio_20260305_122146_1824740e`

### Metrics

| scenario | lookback_score_mode | lookback_bars | net_pnl | max_drawdown | fee_cost_total | oos_positive_ratio | avg_turnover_ratio | skipped_ratio | avg_effective_gross | liquidation_count | eq0_count |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | median_3 | 168 | 13192.0394 | -0.164491 | 977.3115 | 0.568627 | 0.246750 | 0.015943 | 0.625261 | 0 | 0 |
| B | single | 672 | 6477.7464 | -0.199684 | 584.6865 | 0.549020 | 0.193439 | 0.016685 | 0.623828 | 0 | 0 |

### Hard-Gate Check
- Run A: pass (`liquidation_count=0`, `equity_zero_or_negative_count=0`, `fee_cost_total<=2000`)
- Run B: pass (`liquidation_count=0`, `equity_zero_or_negative_count=0`, `fee_cost_total<=2000`)

### Conclusion (1 line)
- **FAIL**: hard gate is safe, but `single(28d)` materially degraded both `net_pnl` and `max_drawdown`; keep `median_3`.

### Next Lever (1 only)
- Change only median base lookback (`lookback_bars` for `median_3`) from `168` to `192` (8d/16d/32d), keep all other fixed values.

## 2026-03-05 - K A/B (4 vs 5)

### Scope
- changed lever only: `k` (`4` vs `5`)
- fixed: `lookback_score_mode=median_3(7/14/28)`, `rank_buffer=2`, `extreme_no_trade=ON`, `extreme_high_vol_percentile=0.90`, `extreme_non_trend_logic=OR`, `trend_slope_threshold=0.0015`, `extreme_regime_mode=delever`, `extreme_gross_mult=0.5`, `shock_freeze_min_fraction=0.40`
- all fixed baseline values unchanged (`testnet=False`, safety stack unchanged)
- sweep artifact dir: `out/experiments/extreme_no_trade_ab_20260304_150839`

### Run IDs
- Run A (`k=4`): `portfolio_20260304_150839_ab114d03`
- Run B (`k=5`): `portfolio_20260304_151111_66ff026a`

### Metrics

| scenario | k | net_pnl | max_drawdown | fee_cost_total | oos_positive_ratio | avg_turnover_ratio | skipped_ratio | avg_effective_gross | liquidation_count | eq0_count |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 4 | 13192.0394 | -0.164491 | 977.3115 | 0.568627 | 0.246750 | 0.015943 | 0.625261 | 0 | 0 |
| B | 5 | 5293.6255 | -0.145177 | 908.2613 | 0.607843 | 0.347000 | 0.016493 | 0.629590 | 0 | 0 |

### Hard-Gate Check
- Run A: pass (`liquidation_count=0`, `equity_zero_or_negative_count=0`, `fee_cost_total<=2000`)
- Run B: pass (`liquidation_count=0`, `equity_zero_or_negative_count=0`, `fee_cost_total<=2000`)

### Conclusion (1 line)
- **FAIL**: hard gate is safe, but `k=5` severely degraded `net_pnl`; keep `k=4`.

### Next Lever (1 only)
- No additional k lever in this branch; keep `k=4` finalized.

## 2026-03-04 - Extreme Delever Multiplier A/B (0.5 vs 0.7)

### Scope
- changed lever only: `extreme_gross_mult` (`0.5` vs `0.7`)
- fixed: `extreme_regime_mode=delever`, `extreme_no_trade=ON`, `extreme_high_vol_percentile=0.90`, `extreme_non_trend_logic=OR`, `trend_slope_threshold=0.0015`
- all fixed baseline values unchanged (`testnet=False`, `k=4`, `rank_buffer=2`, `lookback_score_mode=median_3`, `shock_freeze_min_fraction=0.40`)
- sweep artifact dir: `out/experiments/extreme_no_trade_ab_20260304_143455`

### Run IDs
- Run A (`extreme_gross_mult=0.5`): `portfolio_20260304_143455_5128e8ea`
- Run B (`extreme_gross_mult=0.7`): `portfolio_20260304_143736_badac645`

### Metrics

| scenario | extreme_gross_mult | net_pnl | max_drawdown | fee_cost_total | oos_positive_ratio | avg_effective_gross | liquidation_count | eq0_count |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 0.5 | 13192.0394 | -0.164491 | 977.3115 | 0.568627 | 0.625261 | 0 | 0 |
| B | 0.7 | 12872.8665 | -0.166731 | 970.8628 | 0.549020 | 0.630713 | 0 | 0 |

### Hard-Gate Check
- Run A: pass (`liquidation_count=0`, `equity_zero_or_negative_count=0`, `fee_cost_total<=2000`)
- Run B: pass (`liquidation_count=0`, `equity_zero_or_negative_count=0`, `fee_cost_total<=2000`)

### Conclusion (1 line)
- **FAIL**: hard gate is safe, but `0.7` worsened both `net_pnl` and `max_drawdown` versus `0.5`; finalize `0.5`.

### Next Lever (1 only)
- No additional lever in this branch; keep `extreme_gross_mult=0.5` as final.

## 2026-03-04 - Extreme Delever Multiplier A/B (0.5 vs 0.3)

### Scope
- changed lever only: `extreme_gross_mult` (`0.5` vs `0.3`)
- fixed: `extreme_regime_mode=delever`, `extreme_no_trade=ON`, `extreme_high_vol_percentile=0.90`, `extreme_non_trend_logic=OR`, `trend_slope_threshold=0.0015`
- all fixed baseline values unchanged (`testnet=False`, `k=4`, `rank_buffer=2`, `lookback_score_mode=median_3`, `shock_freeze_min_fraction=0.40`)
- sweep artifact dir: `out/experiments/extreme_no_trade_ab_20260304_141543`

### Run IDs
- Run A (`extreme_gross_mult=0.5`): `portfolio_20260304_141543_63c69247`
- Run B (`extreme_gross_mult=0.3`): `portfolio_20260304_141823_0d36ea8b`

### Metrics

| scenario | extreme_gross_mult | net_pnl | max_drawdown | fee_cost_total | oos_positive_ratio | avg_effective_gross | liquidation_count | eq0_count |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 0.5 | 13192.0394 | -0.164491 | 977.3115 | 0.568627 | 0.625261 | 0 | 0 |
| B | 0.3 | 13177.5134 | -0.165330 | 977.2980 | 0.568627 | 0.623966 | 0 | 0 |

### Hard-Gate Check
- Run A: pass (`liquidation_count=0`, `equity_zero_or_negative_count=0`, `fee_cost_total<=2000`)
- Run B: pass (`liquidation_count=0`, `equity_zero_or_negative_count=0`, `fee_cost_total<=2000`)

### Conclusion (1 line)
- **FAIL**: hard gate is safe, but `0.3` worsened both `net_pnl` and `max_drawdown` versus `0.5`.

### Next Lever (1 only)
- Change only `extreme_gross_mult` to `0.7` (single parameter).

## 2026-03-04 - Extreme Handling Mode A/B (skip vs delever 0.5)

### Scope
- changed lever only: `extreme_regime_mode` (`skip` vs `delever`)
- variant multiplier: `extreme_gross_mult=0.5` (A uses `1.0`)
- fixed: `extreme_high_vol_percentile=0.90`, `extreme_non_trend_logic=OR`, `trend_slope_threshold=0.0015`, `extreme_no_trade=ON`
- all fixed baseline values unchanged (`testnet=False`, `k=4`, `rank_buffer=2`, `lookback_score_mode=median_3`, `shock_freeze_min_fraction=0.40`)
- sweep artifact dir: `out/experiments/extreme_no_trade_ab_20260304_140700`

### Run IDs
- Run A (`extreme_regime_mode=skip`): `portfolio_20260304_140700_bcf0d4f6`
- Run B (`extreme_regime_mode=delever`, `extreme_gross_mult=0.5`): `portfolio_20260304_140942_b617bf25`

### Metrics

| scenario | extreme_regime_mode | extreme_gross_mult | net_pnl | max_drawdown | fee_cost_total | oos_positive_ratio | skipped_ratio | shock_skip_ratio | extreme_skip_ratio | extreme_no_trade_ratio | avg_effective_gross | liquidation_count | eq0_count |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | skip | 1.0 | 12414.3370 | -0.159377 | 957.4116 | 0.549020 | 0.023090 | 0.015943 | 0.007147 | 0.028037 | 0.650371 | 0 | 0 |
| B | delever | 0.5 | 13192.0394 | -0.164491 | 977.3115 | 0.568627 | 0.015943 | 0.015943 | 0.000000 | 0.028037 | 0.625261 | 0 | 0 |

### Hard-Gate Check
- Run A: pass (`liquidation_count=0`, `equity_zero_or_negative_count=0`, `fee_cost_total<=2000`)
- Run B: pass (`liquidation_count=0`, `equity_zero_or_negative_count=0`, `fee_cost_total<=2000`)

### Conclusion (1 line)
- **SUCCESS**: `delever(0.5)` improved `net_pnl` versus `skip` under the same fixed baseline and hard-gate constraints.

### Next Lever (1 only)
- Change only `extreme_gross_mult` (`0.5 -> 0.4`) to test whether drawdown can improve without giving back the pnl gain.

## 2026-03-04 - Extreme Trend Slope Threshold A/B (0.0015 vs 0.0020)

### Scope
- changed lever only: `trend_slope_threshold` (`0.0015` vs `0.0020`)
- fixed: `extreme_no_trade=ON`, `extreme_non_trend_logic=OR`, `extreme_high_vol_percentile=0.90`
- all fixed baseline values unchanged (`testnet=False`, `k=4`, `rank_buffer=2`, `lookback_score_mode=median_3`, `shock_freeze_min_fraction=0.40`)
- sweep artifact dir: `out/experiments/extreme_no_trade_ab_20260304_133826`

### Run IDs
- Run A (`slope_threshold=0.0015`): `portfolio_20260304_133826_f173501d`
- Run B (`slope_threshold=0.0020`): `portfolio_20260304_134103_6cf0996a`

### Metrics

| scenario | trend_slope_threshold | net_pnl | max_drawdown | fee_cost_total | oos_positive_ratio | skipped_ratio | shock_skip_ratio | extreme_skip_ratio | extreme_no_trade_ratio | liquidation_count | eq0_count |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 0.0015 | 12414.3370 | -0.159377 | 957.4116 | 0.549020 | 0.023090 | 0.015943 | 0.007147 | 0.028037 | 0 | 0 |
| B | 0.0020 | 11062.4203 | -0.159991 | 903.2465 | 0.529412 | 0.025838 | 0.015943 | 0.009896 | 0.033535 | 0 | 0 |

### Hard-Gate Check
- Run A: pass (`liquidation_count=0`, `equity_zero_or_negative_count=0`, `fee_cost_total<=2000`)
- Run B: pass (`liquidation_count=0`, `equity_zero_or_negative_count=0`, `fee_cost_total<=2000`)

### Conclusion (1 line)
- **FAIL**: hard gate is safe, but `0.0020` degraded both `net_pnl` and `max_drawdown` versus `0.0015`.

### Next Lever (1 only)
- Change only `trend_slope_threshold` to `0.0010` (single parameter).

## 2026-03-04 - Extreme Non-Trend Logic A/B (OR vs AND)

### Scope
- changed lever only: `extreme_non_trend_logic` (`OR` vs `AND`)
- both runs fixed as `extreme_no_trade=ON` and `extreme_high_vol_percentile=0.90`
- all fixed baseline values unchanged (`testnet=False`, `k=4`, `rank_buffer=2`, `lookback_score_mode=median_3`, `shock_freeze_min_fraction=0.40`)
- sweep artifact dir: `out/experiments/extreme_no_trade_ab_20260304_132642`

### Run IDs
- Run A (`extreme_no_trade=ON`, `non_trend=OR`): `portfolio_20260304_132642_529581d3`
- Run B (`extreme_no_trade=ON`, `non_trend=AND`): `portfolio_20260304_132924_9f7f0c8b`

### Metrics

| scenario | non_trend_logic | net_pnl | max_drawdown | fee_cost_total | oos_positive_ratio | skipped_ratio | shock_skip_ratio | extreme_skip_ratio | extreme_no_trade_ratio | liquidation_count | eq0_count |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | OR | 12414.3370 | -0.159377 | 957.4116 | 0.549020 | 0.023090 | 0.015943 | 0.007147 | 0.028037 | 0 | 0 |
| B | AND | 12059.9580 | -0.178526 | 960.1745 | 0.549020 | 0.016493 | 0.015943 | 0.000550 | 0.003848 | 0 | 0 |

### Hard-Gate Check
- Run A: pass (`liquidation_count=0`, `equity_zero_or_negative_count=0`, `fee_cost_total<=2000`)
- Run B: pass (`liquidation_count=0`, `equity_zero_or_negative_count=0`, `fee_cost_total<=2000`)

### Conclusion (1 line)
- **FAIL**: hard gate is safe, but `AND` worsened both `net_pnl` and `max_drawdown` versus `OR`.

### Next Lever (1 only)
- Adjust only `trend_slope_threshold` (single parameter).

## 2026-03-04 - Extreme High-Vol Percentile A/B (0.90 vs 0.95)

### Scope
- changed lever only: `extreme_high_vol_percentile` (`0.90` vs `0.95`)
- both runs fixed as `extreme_no_trade=ON`
- all fixed baseline values unchanged (`testnet=False`, `k=4`, `rank_buffer=2`, `lookback_score_mode=median_3`, `shock_freeze_min_fraction=0.40`)
- sweep artifact dir: `out/experiments/extreme_no_trade_ab_20260304_130356`

### Run IDs
- Run A (`extreme_no_trade=ON`, `extreme_high_vol_percentile=0.90`): `portfolio_20260304_130356_63f7b670`
- Run B (`extreme_no_trade=ON`, `extreme_high_vol_percentile=0.95`): `portfolio_20260304_130641_cef0eb38`

### Metrics

| scenario | extreme_high_vol_percentile | net_pnl | max_drawdown | fee_cost_total | oos_positive_ratio | skipped_ratio | shock_skip_ratio | extreme_skip_ratio | extreme_no_trade_ratio | liquidation_count | eq0_count |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 0.90 | 12414.3370 | -0.159377 | 957.4116 | 0.549020 | 0.023090 | 0.015943 | 0.007147 | 0.028037 | 0 | 0 |
| B | 0.95 | 12376.4986 | -0.164910 | 958.4537 | 0.549020 | 0.020341 | 0.015943 | 0.004398 | 0.015393 | 0 | 0 |

### Hard-Gate Check
- Run A: pass (`liquidation_count=0`, `equity_zero_or_negative_count=0`, `fee_cost_total<=2000`)
- Run B: pass (`liquidation_count=0`, `equity_zero_or_negative_count=0`, `fee_cost_total<=2000`)

### Conclusion (1 line)
- **FAIL**: hard gate is safe, but `0.95` degraded both `net_pnl` and `max_drawdown` versus `0.90`.

### Next Lever (1 only)
- Change only `extreme_high_vol_percentile`: `0.95 -> 0.97` (no other parameter changes).

## 2026-03-04 - Extreme High-Vol Percentile A/B (single lever)

### Scope
- changed lever only: `extreme_high_vol_percentile` (`0.90` vs `0.92`)
- both runs fixed as `extreme_no_trade=ON`
- all fixed baseline values unchanged (`testnet=False`, `k=4`, `rank_buffer=2`, `lookback_score_mode=median_3`, `shock_freeze_min_fraction=0.40`)
- sweep artifact dir: `out/experiments/extreme_no_trade_ab_20260304_124422`

### Run IDs
- Run A (`extreme_no_trade=ON`, `extreme_high_vol_percentile=0.90`): `portfolio_20260304_124422_248f23b3`
- Run B (`extreme_no_trade=ON`, `extreme_high_vol_percentile=0.92`): `portfolio_20260304_124705_81f7ee12`

### Metrics

| scenario | extreme_high_vol_percentile | net_pnl | max_drawdown | fee_cost_total | oos_positive_ratio | skipped_ratio | shock_skip_ratio | extreme_skip_ratio | extreme_no_trade_ratio | liquidation_count | eq0_count |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 0.90 | 12414.3370 | -0.159377 | 957.4116 | 0.549020 | 0.023090 | 0.015943 | 0.007147 | 0.028037 | 0 | 0 |
| B | 0.92 | 12414.3370 | -0.159377 | 957.4116 | 0.549020 | 0.023090 | 0.015943 | 0.007147 | 0.026938 | 0 | 0 |

### Hard-Gate Check
- Run A: pass (`liquidation_count=0`, `equity_zero_or_negative_count=0`, `fee_cost_total<=2000`)
- Run B: pass (`liquidation_count=0`, `equity_zero_or_negative_count=0`, `fee_cost_total<=2000`)

### Conclusion (1 line)
- **FAIL**: hard gate is safe but `0.92` did not improve `net_pnl` and did not improve `max_drawdown` versus `0.90`.

### Next Lever (1 only)
- Change only `extreme_high_vol_percentile`: `0.92 -> 0.95` (no other parameter changes).

## 2026-03-04 - Extreme Regime No-Trade A/B (single lever)

### Scope
- changed lever only: `extreme_no_trade` (`OFF` vs `ON`)
- fixed rule: `extreme := (BTC ATR vol_percentile >= 0.90) AND ((ADX < 20) OR (abs(trend_slope) < slope_threshold))`
- all other parameters fixed to baseline (`k=4`, `rank_buffer=2`, `lookback_score_mode=median_3`, safety stack unchanged)
- data source guard: `binance` mainnet historical (`testnet=False`)
- sweep artifact dir: `out/experiments/extreme_no_trade_ab_20260304_121156`

### Run IDs
- Run A baseline (`extreme_no_trade=OFF`): `portfolio_20260304_121156_f795836a`
- Run B variant (`extreme_no_trade=ON`): `portfolio_20260304_121451_f7bd5016`

### Metrics

| scenario | net_pnl | max_drawdown | fee_cost_total | oos_positive_ratio | skipped_ratio | shock_skip_ratio | extreme_skip_ratio | extreme_no_trade_ratio | liquidation_count | eq0_count |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A (OFF) | 11993.9845 | -0.180977 | 960.3784 | 0.549020 | 0.015943 | 0.015943 | 0.000000 | 0.000000 | 0 | 0 |
| B (ON) | 12414.3370 | -0.159377 | 957.4116 | 0.549020 | 0.023090 | 0.015943 | 0.007147 | 0.028037 | 0 | 0 |

### Hard-Gate Check
- Run A: pass (`liquidation_count=0`, `equity_zero_or_negative_count=0`, `fee_cost_total<=2000`)
- Run B: pass (`liquidation_count=0`, `equity_zero_or_negative_count=0`, `fee_cost_total<=2000`)

### Conclusion (1 line)
- **SUCCESS**: `extreme_no_trade=ON` improved `net_pnl` and improved drawdown while preserving hard-gate safety.

### Next Lever (1 only)
- Tune one parameter only: `extreme high-vol percentile` from `0.90` to `0.92` (keep all other parameters fixed).

## 2026-03-03 - Rank Buffer Sweep (k fixed at 4, median_3 fixed)

### Scope
- changed lever only: `rank_buffer`
- candidates: `0`, `1`, `2`
- all other parameters fixed to current baseline overlays and safety stack
- sweep artifact dir: `out/experiments/rank_buffer_sweep_20260303_141548`

### Run IDs
- baseline (`rank_buffer=0`): `portfolio_20260303_141548_d261ab43`
- variant (`rank_buffer=1`): `portfolio_20260303_141820_ffdf30cc`
- variant (`rank_buffer=2`): `portfolio_20260303_142053_4c986473`

### Metrics

| rank_buffer | net_pnl | max_drawdown | fee_cost_total | liquidation_count | eq0_count | avg_turnover_ratio | skipped_ratio | turnover_notional_sum | trade_count_sum |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | 9754.7803 | -0.156118 | 1117.4540 | 0 | 0 | 0.359440 | 0.015943 | 5527075.2677 | 18253 |
| 1 | 10709.3868 | -0.203347 | 1092.4343 | 0 | 0 | 0.296535 | 0.015943 | 5416469.6291 | 17163 |
| 2 | 11993.9845 | -0.180977 | 960.3784 | 0 | 0 | 0.249194 | 0.015943 | 4754866.7149 | 16309 |

### Hard-Gate Check
- all three runs passed hard gate: `liquidation_count=0`, `equity_zero_or_negative_count=0`, `fee_cost_total<=2000`.

### Conclusion (1 line)
- Recommended `rank_buffer=2`: it has the best `net_pnl` among the gated runs and the lowest turnover/fee profile.

### Next Lever (1 only)
- Introduce `Extreme regime no-trade` gating as the next single-lever improvement.



# Experiment Log

## 2026-03-11 - Binance USDT-M 1Y Historical Research (1h, 6 symbols)

### Scope
- objective:
  - fetch real Binance USDT-M Futures historical candles for `BTCUSDT`, `ETHUSDT`, `XRPUSDT`, `TRXUSDT`, `ADAUSDT`, `SOLUSDT`
  - store reusable local `1h` candle files for the last 365 days
  - compare `ema_cross`, `donchian_breakout`, `rsi_mean_reversion` in one walk-forward framework
- live/testnet code path: unchanged
- execution model:
  - `MARKET`
  - taker fee `5 bps`
  - slippage `2 bps`
  - walk-forward `180d train / 60d test / 60d step`

### Fetch command
- `uv run --active python scripts/fetch_futures_historical.py --symbols BTCUSDT ETHUSDT XRPUSDT TRXUSDT ADAUSDT SOLUSDT --interval 1h --days 365`

### Fetch result
- first run:
  - all 6 symbols saved under `data/futures_historical/<SYMBOL>/1h.csv`
  - each file rows: `8760`
  - UTC range: `2025-03-10T16:00:00Z` to `2026-03-10T15:00:00Z`
- rerun validation:
  - all 6 symbols returned `fetched_rows=0`
  - confirms merge/reuse path without duplicate redownload

### Search command
- `uv run --active python scripts/run_strategy_search.py --symbols BTCUSDT ETHUSDT XRPUSDT TRXUSDT ADAUSDT SOLUSDT --interval 1h`

### Output artifacts
- `out/strategy_search/summary.csv`
- `out/strategy_search/by_symbol.csv`
- `out/strategy_search/top_strategies.md`
- `out/strategy_search/window_results.csv`

### Result snapshot
- ranking by OOS:
  1. `donchian_breakout`
  2. `rsi_mean_reversion`
  3. `ema_cross`
- important finding:
  - none of the three passed the hard gate
  - all three had negative mean OOS return on this 6-symbol / 1-year / 1h setup
- top row metrics:
  - `donchian_breakout`
  - `oos_total_return_mean=-0.0230`
  - `oos_sharpe_mean=-1.3444`
  - `symbol_consistency_count=1/6`

### Interpretation
- current basic `1h` candidate set does not justify a positive-edge conclusion
- the data and framework are usable, but the first-pass parameterized strategy set is not robust OOS
- next work should change one research lever at a time:
  - interval (`15m` or `4h`)
  - narrower/family-specific parameter ranges
  - alternate exit logic or regime filter

### Verification
- `uv run --active pytest -q`: PASS (`46 passed`)

## 2026-03-12 - Donchian Breakout + ADX Regime Filter (1h, 6 symbols)

### Scope
- changed lever only: add `ADX` regime filter to `donchian_breakout` entry logic
- baseline: `donchian_breakout`
- variant: `donchian_breakout_adx`
- fixed across both:
  - symbols: `BTCUSDT ETHUSDT XRPUSDT TRXUSDT ADAUSDT SOLUSDT`
  - interval: `1h`
  - data: saved real Binance USDT-M Futures historical candles only
  - walk-forward: `180d train / 60d test / 60d step`
  - execution: `MARKET`
  - taker fee: `5 bps`
  - slippage: `2 bps`
  - breakout entry/exit logic: unchanged except variant entry must satisfy `ADX >= threshold`
- ADX variant search range:
  - `adx_window`: `10, 14, 20`
  - `adx_threshold`: `15, 20, 25, 30`

### Why this candidate
- `donchian_breakout` was the least bad strategy in the prior baseline search:
  - rank `1/3`
  - `oos_total_return_mean=-0.0230`
  - `oos_sharpe_mean=-1.3444`
  - positive symbols `1/6`
- the next lever should therefore stay inside the same family and try to reduce noisy range-market breakouts rather than replacing the strategy.

### Why ADX
- the failure pattern was consistent with too many weak-trend breakout entries on `1h`.
- `ADX` is a single regime-strength lever that can gate entries without changing the existing Donchian breakout or exit rules.
- this matches the one-lever-only constraint and keeps the rest of the framework unchanged.

### Run Commands
- focused comparison:
  - `uv run --active python scripts/run_strategy_search.py --symbols BTCUSDT ETHUSDT XRPUSDT TRXUSDT ADAUSDT SOLUSDT --interval 1h --strategies donchian_breakout donchian_breakout_adx`
- default full search validation:
  - `uv run --active python scripts/run_strategy_search.py --symbols BTCUSDT ETHUSDT XRPUSDT TRXUSDT ADAUSDT SOLUSDT --interval 1h`

### Artifacts
- `out/strategy_search/summary.csv`
- `out/strategy_search/by_symbol.csv`
- `out/strategy_search/top_strategies.md`
- `out/strategy_search/window_results.csv`

### Baseline vs Variant

| strategy | oos_total_return_mean | oos_sharpe_mean | oos_max_drawdown_mean | trade_count_mean | fee_cost_total | positive_symbols | symbol_return_std | hard_gate_pass |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| donchian_breakout | -0.0230 | -1.3444 | -0.0380 | 44.0 | 262.0895 | 1/6 | 0.0273 | False |
| donchian_breakout_adx | -0.0168 | -1.1932 | -0.0323 | 36.5 | 216.5757 | 2/6 | 0.0205 | False |

### Symbol Snapshot
- baseline positive symbols:
  - `BTCUSDT`
- variant positive symbols:
  - `BTCUSDT`
  - `XRPUSDT`
- largest remaining drag under variant:
  - `SOLUSDT` `oos_total_return=-0.0517`
  - `ETHUSDT` `oos_total_return=-0.0357`

### Interpretation
- result status: `PARTIAL`
- the `ADX` gate improved the mean OOS return, OOS sharpe, mean max drawdown, fee drag, and positive-symbol count while reducing cross-symbol dispersion.
- however, the variant still did not flip the aggregate OOS return positive and still failed the hard gate (`3/5`), so this is not a production candidate yet.
- the result is useful and should be kept exactly as-is rather than hidden because it narrows the search to a better Donchian branch.

### Verification
- `uv run --active pytest -q tests/test_strategy_search.py`: PASS (`3 passed`)
- `uv run --active pytest -q`: PASS (`47 passed`)

### Next Lever (1 only)
- keep `donchian_breakout_adx` fixed and change only the timeframe lever from `1h` to `4h`.

## 2026-03-12 - Donchian Breakout + ADX Timeframe A/B (1h vs 4h)

### Scope
- changed lever only: timeframe (`1h` vs `4h`)
- baseline: `donchian_breakout_adx @ 1h`
- variant: `donchian_breakout_adx @ 4h`
- fixed across both:
  - strategy logic: identical `donchian_breakout_adx`
  - ADX search range: `adx_window={10,14,20}`, `adx_threshold={15,20,25,30}`
  - symbols: `BTCUSDT ETHUSDT XRPUSDT TRXUSDT ADAUSDT SOLUSDT`
  - data source: real Binance USDT-M Futures historical candles only
  - date windows:
    - `1h`: `2025-03-12T12:00:00Z` to `2026-03-12T11:00:00Z`
    - `4h`: `2025-03-12T12:00:00Z` to `2026-03-12T08:00:00Z`
  - walk-forward: `180d train / 60d test / 60d step`
  - execution: `MARKET`
  - taker fee: `5 bps`
  - slippage: `2 bps`

### Why 4h
- the prior `1h` `donchian_breakout_adx` run was less bad than the raw Donchian baseline, but still negative OOS and still below hard gate.
- the most defensible next single lever was timeframe, because a slower bar can reduce noise and focus the same breakout + ADX regime logic on larger trend structure.

### Run Commands
- fetch 4h:
  - `uv run --active python scripts/fetch_futures_historical.py --symbols BTCUSDT ETHUSDT XRPUSDT TRXUSDT ADAUSDT SOLUSDT --interval 4h --days 365`
- refresh 1h baseline to the same latest 1-year window:
  - `uv run --active python scripts/fetch_futures_historical.py --symbols BTCUSDT ETHUSDT XRPUSDT TRXUSDT ADAUSDT SOLUSDT --interval 1h --days 365`
- variant search:
  - `uv run --active python scripts/run_strategy_search.py --symbols BTCUSDT ETHUSDT XRPUSDT TRXUSDT ADAUSDT SOLUSDT --interval 4h --strategies donchian_breakout_adx`
- baseline rerun:
  - `uv run --active python scripts/run_strategy_search.py --symbols BTCUSDT ETHUSDT XRPUSDT TRXUSDT ADAUSDT SOLUSDT --interval 1h --strategies donchian_breakout_adx`

### Artifact Snapshots
- `out/strategy_search_compare/1h/summary.csv`
- `out/strategy_search_compare/1h/by_symbol.csv`
- `out/strategy_search_compare/1h/top_strategies.md`
- `out/strategy_search_compare/1h/window_results.csv`
- `out/strategy_search_compare/4h/summary.csv`
- `out/strategy_search_compare/4h/by_symbol.csv`
- `out/strategy_search_compare/4h/top_strategies.md`
- `out/strategy_search_compare/4h/window_results.csv`

### 1h vs 4h

| interval | oos_total_return_mean | oos_sharpe_mean | oos_max_drawdown_mean | trade_count_mean | fee_cost_total | positive_symbols | symbol_return_std | hard_gate_count | hard_gate_pass |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1h | -0.0160 | -1.0283 | -0.0316 | 39.5 | 234.2254 | 2/6 | 0.0208 | 3/5 | False |
| 4h | -0.0129 | -0.9962 | -0.0254 | 10.7 | 62.9543 | 0/6 | 0.0093 | 2/5 | False |

### Improvement / Degradation
- improved on `4h`:
  - mean OOS return less negative
  - OOS sharpe less negative
  - mean max drawdown less severe
  - trade count dropped sharply
  - fee cost total fell from `234.2254` to `62.9543`
  - cross-symbol dispersion dropped from `0.0208` to `0.0093`
- worsened on `4h`:
  - positive symbols fell from `2/6` to `0/6`
  - hard-gate score fell from `3/5` to `2/5`
- symbol-level note:
  - `1h` had positive `BTCUSDT` and `XRPUSDT`
  - `4h` had no positive symbol, but losses became more tightly clustered

### Interpretation
- result status: `PARTIAL`
- `4h` does look cleaner and cheaper than `1h`, which is consistent with the larger-trend hypothesis.
- however, the cleaner profile did not translate into broad positive symbol coverage, so the branch is still not a hard-gate candidate.
- this should be recorded as mixed evidence rather than a clean success or failure: the timeframe lever improved quality-of-trading metrics, but not robustness.

### Verification
- `uv run --active pytest -q`: PASS (`48 passed`)

### Next Lever (1 only)
- keep `donchian_breakout_adx @ 4h` fixed and change only the symbol-universe lever by removing the weakest tail symbol first (`SOLUSDT`) to test whether the edge is concentrated in a smaller subset.

## 2026-03-12 - Donchian Breakout + ADX 4h Universe A/B (with SOL vs without SOL)

### Scope
- changed lever only: symbol universe
- baseline: `donchian_breakout_adx @ 4h` with `BTCUSDT ETHUSDT XRPUSDT TRXUSDT ADAUSDT SOLUSDT`
- variant: `donchian_breakout_adx @ 4h` with `BTCUSDT ETHUSDT XRPUSDT TRXUSDT ADAUSDT`
- fixed across both:
  - strategy logic: identical `donchian_breakout_adx`
  - timeframe: `4h`
  - data source: real Binance USDT-M Futures historical candles only
  - date window: last 1 year (`2025-03-12T12:00:00Z` to `2026-03-12T08:00:00Z`)
  - walk-forward: `180d train / 60d test / 60d step`
  - execution: `MARKET`
  - taker fee: `5 bps`
  - slippage: `2 bps`

### Why SOLUSDT
- `SOLUSDT` was the weakest symbol in the prior `4h` baseline:
  - `oos_total_return=-0.0297`
  - `oos_sharpe=-1.2627`
  - `oos_max_drawdown=-0.0519`
  - `trade_count=19`
- it was the deepest drag in the universe and did not contribute to positive-symbol breadth, so it was the cleanest single-symbol exclusion lever.

### Run Commands
- with `SOLUSDT`:
  - `uv run --active python scripts/run_strategy_search.py --symbols BTCUSDT ETHUSDT XRPUSDT TRXUSDT ADAUSDT SOLUSDT --interval 4h --strategies donchian_breakout_adx --out-root out/strategy_search_compare/4h_with_sol`
- without `SOLUSDT`:
  - `uv run --active python scripts/run_strategy_search.py --symbols BTCUSDT ETHUSDT XRPUSDT TRXUSDT ADAUSDT --interval 4h --strategies donchian_breakout_adx --out-root out/strategy_search_compare/4h_without_sol`

### Artifact Snapshots
- `out/strategy_search_compare/4h_with_sol/summary.csv`
- `out/strategy_search_compare/4h_with_sol/by_symbol.csv`
- `out/strategy_search_compare/4h_with_sol/top_strategies.md`
- `out/strategy_search_compare/4h_with_sol/window_results.csv`
- `out/strategy_search_compare/4h_without_sol/summary.csv`
- `out/strategy_search_compare/4h_without_sol/by_symbol.csv`
- `out/strategy_search_compare/4h_without_sol/top_strategies.md`
- `out/strategy_search_compare/4h_without_sol/window_results.csv`

### with SOL vs without SOL

| universe | oos_total_return_mean | oos_sharpe_mean | oos_max_drawdown_mean | trade_count_mean | fee_cost_total | positive_symbols | symbol_return_std | hard_gate_count | hard_gate_pass |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| with SOL | -0.0129 | -0.9962 | -0.0254 | 10.7 | 62.9543 | 0/6 | 0.0093 | 2/5 | False |
| without SOL | -0.0096 | -0.9428 | -0.0201 | 9.0 | 44.5202 | 0/5 | 0.0060 | 2/5 | False |

### Improvement / Degradation
- improved without `SOLUSDT`:
  - OOS mean return less negative
  - OOS sharpe less negative
  - OOS max drawdown less severe
  - trade count slightly lower
  - fee cost total lower
  - cross-symbol dispersion lower
- unchanged / still problematic:
  - positive symbols stayed at `0`
  - hard-gate score stayed at `2/5`
- interpretation:
  - the universe got cleaner, but not materially more robust.
  - this is evidence that `SOLUSDT` was a drag, but removing it alone is not enough to rescue the branch.

### Verification
- `uv run --active pytest -q`: PASS (`48 passed`)

### Next Lever (1 only)
- keep `donchian_breakout_adx @ 4h` and the reduced universe fixed, then change only the exit-speed lever by tightening `exit_period` around the winning branch.

## 2026-03-12 - Broad Sweep Strategy Discovery Matrix

### Scope
- objective:
  - run an aggressive but budgeted discovery sweep over multiple strategy families on real Binance USDT-M Futures history
- fixed across the sweep:
  - data source: local mainnet Binance futures historical candles only
  - symbols: `BTCUSDT ETHUSDT XRPUSDT TRXUSDT ADAUSDT SOLUSDT`
  - period: latest 1 year
  - intervals: `1h`, `4h`
  - walk-forward: `180d train / 60d test / 60d step`
  - execution: `MARKET`
  - taker fee: `5 bps`
  - slippage: `2 bps`
- families executed:
  - `ema_cross`
  - `donchian_breakout`
  - `supertrend`
  - `price_adx_breakout`
  - `rsi_mean_reversion`
  - `bollinger`
  - `macd`
  - `stoch_rsi`

### Matrix Budget

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

- default executed cap: `96` combos
- estimated backtests: `6912`
- observed runtime: about `495s` (`8.25` minutes) with `jobs=8`

### Run Commands
- data refresh:
  - `uv run --active python scripts/fetch_futures_historical.py --symbols BTCUSDT ETHUSDT XRPUSDT TRXUSDT ADAUSDT SOLUSDT --interval 1h --days 365`
  - `uv run --active python scripts/fetch_futures_historical.py --symbols BTCUSDT ETHUSDT XRPUSDT TRXUSDT ADAUSDT SOLUSDT --interval 4h --days 365`
- broad sweep:
  - `uv run --active python scripts/run_strategy_search.py --symbols BTCUSDT ETHUSDT XRPUSDT TRXUSDT ADAUSDT SOLUSDT --intervals 1h 4h --mode broad-sweep --time-budget-hours 6`

### Artifacts
- `out/strategy_search_matrix/summary.csv`
- `out/strategy_search_matrix/by_symbol.csv`
- `out/strategy_search_matrix/window_results.csv`
- `out/strategy_search_matrix/top_strategies.md`
- `out/strategy_search_matrix/strategy_family_summary.csv`

### Top Family Candidates

| family | interval | oos_total_return_mean | oos_sharpe_mean | oos_max_drawdown_mean | trade_count_mean | fee_cost_total | positive_symbols | symbol_return_std | hard_gate_pass |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| donchian_breakout | 4h | -0.0035 | -0.5540 | -0.0141 | 7.8 | 46.9741 | 2 | 0.0091 | False |
| ema_cross | 4h | -0.0003 | -0.2491 | -0.0010 | 0.5 | 2.9947 | 1 | 0.0016 | False |
| price_adx_breakout | 1h | -0.0002 | -0.1241 | -0.0226 | 52.3 | 313.7033 | 2 | 0.0078 | False |
| macd | 4h | -0.0081 | -0.2471 | -0.0435 | 62.2 | 372.8684 | 2 | 0.0126 | False |
| stoch_rsi | 4h | -0.0078 | -0.8326 | -0.0249 | 63.3 | 378.7668 | 1 | 0.0161 | False |

### Interpretation
- result status: `PARTIAL`
- hard-gate pass count stayed at `0`, so the sweep produced no production-ready winner.
- the discovery value is still real:
  - trend-following families dominated the upper ranks
  - `4h` generally beat `1h` among the best candidates
  - mean-reversion families were materially weaker after fees/slippage on this universe
- the sweep narrowed the next research set to a few less-bad branches instead of eight equally plausible families.

### Next Lever (1 only)
- keep the best `donchian_breakout @ 4h` parameter pocket fixed and add only an `ADX` regime filter in the next follow-up.

## 2026-03-14 - Repo Reorientation: Historical-First Philosophy Reinforcement

### Motivation
- Recent work over-invested in testnet/live-forward operational validation (12h/16h runners, budget guards, protective orders)
- This is correct for operational validation, but became misaligned with the PRIMARY objective: finding profitable strategies
- Live/testnet proves order execution quality, NOT strategy edge
- Demo data may differ from mainnet historical data
- Zero hard-gate winners found in broad sweep means continued operational validation is premature

### What Changed (Documentation and Guidance)
- README.md: rewritten to emphasize "Historical Research First, Operational Validation Second"
- README.md: moved historical research workflow to the top, ahead of live/testnet sections
- docs/notes.md: added explicit "Repo Direction Clarity" section separating strategy discovery from operational validation
- docs/plan.md: marked broad sweep as COMPLETED, added "Post-Sweep Next Actions" with 4 concrete research directions
- scripts/run_strategy_search.py: enhanced output with hard-gate summary and next-action guidance

### What Changed (Code Improvements)
- scripts/run_strategy_search.py: added hard-gate pass count summary to both legacy and broad-sweep modes
- scripts/run_strategy_search.py: added actionable next-step guidance when zero hard-gate winners found
- Output now explicitly warns when no strategies pass hard gate and suggests concrete follow-up research directions

### What Did NOT Change (Correct Boundaries)
- Live/testnet execution code: unchanged (operational validation remains sound)
- Historical research code: unchanged (already well-designed)
- Broad sweep framework: unchanged (already complete and functional)
- Test suite: unchanged (already passing)

### Verification
- `uv run --active pytest -q`: confirms all tests still pass
- `uv run --active python scripts/run_strategy_search.py --help`: confirms enhanced CLI still works
- Dry-run broad sweep execution: confirms enhanced output formatting

### Key Takeaway
- The repo infrastructure is STRONG on both research and operational sides
- The weakness is strategy edge discovery, not code quality
- Next work should focus on research exploration (broader universe, alternative timeframes, regime filters, cross-sectional approaches)
- Testnet/live-forward should PAUSE until at least one hard-gate winner emerges from historical research

### Recommended Next 3 Actions (Research-First)
1. **15m interval exploration**: Run broad sweep on 15m (hypothesis: higher frequency may reveal scalping edge)
   ```bash
   uv run --active python scripts/fetch_futures_historical.py --symbols BTCUSDT ETHUSDT XRPUSDT TRXUSDT ADAUSDT SOLUSDT --interval 15m --days 365
   uv run --active python scripts/run_strategy_search.py --symbols BTCUSDT ETHUSDT XRPUSDT TRXUSDT ADAUSDT SOLUSDT --intervals 15m --mode broad-sweep
   ```

2. **Expanded universe exploration**: Test 15-20 symbols across market cap tiers (hypothesis: edge concentrated in specific symbols)
   ```bash
   uv run --active python scripts/fetch_futures_historical.py --symbols BTCUSDT ETHUSDT BNBUSDT SOLUSDT XRPUSDT ADAUSDT DOGEUSDT TRXUSDT MATICUSDT DOTUSDT AVAXUSDT LINKUSDT UNIUSDT ATOMUSDT LTCUSDT --interval 1h --days 365
   uv run --active python scripts/run_strategy_search.py --symbols <15-symbols> --intervals 1h 4h --mode broad-sweep
   ```

3. **Portfolio cross-sectional approach**: Pivot from single-symbol directional to multi-symbol relative value
   - Leverage existing `trader/experiments/runner.py` portfolio suite
   - Input: historical data mode instead of live/testnet
   - Hypothesis: Single-symbol directional edge is weak, but cross-sectional edge may exist

## 2026-03-14 - Broad Sweep Universe Expansion (6 vs 15 symbols)

### Scope
- changed lever only: symbol universe
- baseline universe:
  - `BTCUSDT ETHUSDT XRPUSDT TRXUSDT ADAUSDT SOLUSDT`
- variant universe:
  - `BTCUSDT ETHUSDT BNBUSDT SOLUSDT XRPUSDT ADAUSDT DOGEUSDT TRXUSDT AVAXUSDT LINKUSDT DOTUSDT LTCUSDT ATOMUSDT UNIUSDT MATICUSDT`
- fixed across both:
  - historical Binance USDT-M Futures candles only
  - latest 1-year window
  - intervals: `1h`, `4h`
  - walk-forward: `180d train / 60d test / 60d step`
  - execution: `MARKET`
  - taker fee: `5 bps`
  - slippage: `2 bps`
  - broad sweep families and ranking logic unchanged

### Why This Lever
- the earlier broad sweep proved that the current family set had no robust winner on the then-current 6-symbol run.
- the next clean question was whether the framework itself was weak, or whether the 6-symbol universe was too narrow and missing symbol-specific edge.
- this experiment kept the framework fixed and widened only the symbol universe.

### Data Sync Commands
- baseline refresh was already covered by existing `BTCUSDT ETHUSDT XRPUSDT TRXUSDT ADAUSDT SOLUSDT` local files.
- expanded universe sync:
  - `uv run --active python scripts/fetch_futures_historical.py --symbols BTCUSDT ETHUSDT BNBUSDT SOLUSDT XRPUSDT ADAUSDT DOGEUSDT TRXUSDT AVAXUSDT LINKUSDT DOTUSDT LTCUSDT ATOMUSDT UNIUSDT MATICUSDT --interval 1h --days 365`
  - `uv run --active python scripts/fetch_futures_historical.py --symbols BTCUSDT ETHUSDT BNBUSDT SOLUSDT XRPUSDT ADAUSDT DOGEUSDT TRXUSDT AVAXUSDT LINKUSDT DOTUSDT LTCUSDT ATOMUSDT UNIUSDT MATICUSDT --interval 4h --days 365`

### Data Sync Result
- first sync:
  - missing symbols were added successfully for both `1h` and `4h`
  - rerun validation returned `fetched_rows=0` for every populated symbol file on both intervals
- special case:
  - `MATICUSDT` returned `rows=0` and `fetched_rows=0` on both `1h` and `4h`
  - it remained in the variant universe because replacing it would have changed the experiment definition

### Broad Sweep Commands
- baseline rerun:
  - `uv run --active python scripts/run_strategy_search.py --symbols BTCUSDT ETHUSDT XRPUSDT TRXUSDT ADAUSDT SOLUSDT --intervals 1h 4h --mode broad-sweep --time-budget-hours 6 --out-root out/strategy_search_compare/universe_6`
- variant rerun:
  - `uv run --active python scripts/run_strategy_search.py --symbols BTCUSDT ETHUSDT BNBUSDT SOLUSDT XRPUSDT ADAUSDT DOGEUSDT TRXUSDT AVAXUSDT LINKUSDT DOTUSDT LTCUSDT ATOMUSDT UNIUSDT MATICUSDT --intervals 1h 4h --mode broad-sweep --time-budget-hours 6 --out-root out/strategy_search_compare/universe_15`
- comparison report:
  - `uv run --active python scripts/compare_strategy_search_runs.py --baseline-root out/strategy_search_compare/universe_6 --variant-root out/strategy_search_compare/universe_15 --out-root out/strategy_search_compare/universe_compare --baseline-label universe_6 --variant-label universe_15`

### Artifact Layout
- baseline:
  - `out/strategy_search_compare/universe_6/summary.csv`
  - `out/strategy_search_compare/universe_6/by_symbol.csv`
  - `out/strategy_search_compare/universe_6/window_results.csv`
  - `out/strategy_search_compare/universe_6/top_strategies.md`
  - `out/strategy_search_compare/universe_6/strategy_family_summary.csv`
- variant:
  - `out/strategy_search_compare/universe_15/summary.csv`
  - `out/strategy_search_compare/universe_15/by_symbol.csv`
  - `out/strategy_search_compare/universe_15/window_results.csv`
  - `out/strategy_search_compare/universe_15/top_strategies.md`
  - `out/strategy_search_compare/universe_15/strategy_family_summary.csv`
- comparison:
  - `out/strategy_search_compare/universe_compare/overall_comparison.csv`
  - `out/strategy_search_compare/universe_compare/family_comparison.csv`
  - `out/strategy_search_compare/universe_compare/comparison.md`

### Runtime
- baseline observed runtime: about `476.7s` (`7.95` minutes)
- variant observed runtime: about `1194.6s` (`19.9` minutes)
- both remained comfortably within the requested `~6h` wall-clock budget

### 6 vs 15 Headline Comparison

| metric | universe_6 | universe_15 |
|---|---:|---:|
| hard_gate_pass_count | 1 | 0 |
| best candidate | `ema_cross @ 4h` | `ema_cross @ 4h` |
| best OOS total return mean | 0.0009 | -0.0003 |
| best OOS sharpe mean | 0.3663 | 0.0177 |
| best OOS max drawdown mean | -0.0003 | -0.0021 |
| best positive symbols | 1 | 2 |
| best symbol return std | 0.0021 | 0.0028 |

### Family-Level Read
- improved best-candidate OOS return on the wider universe:
  - `rsi_mean_reversion`: `-0.0206 -> -0.0103`
  - `bollinger`: `-0.0157 -> -0.0081`
  - `supertrend`: `-0.0310 -> -0.0287`
- improved positive-symbol breadth but still degraded or stayed weak on return quality:
  - `ema_cross`: positive symbols `1 -> 2`, but best OOS return `0.0009 -> -0.0003`
  - `donchian_breakout`: positive symbols `1 -> 2`, but best OOS return `-0.0053 -> -0.0087`
  - `macd`: positive symbols `2 -> 4`, but fee cost total `376.1 -> 883.0` while best OOS return worsened
  - `stoch_rsi`: positive symbols `1 -> 3`, but fee cost total `381.7 -> 1000.6` while best OOS return worsened
- broad takeaway:
  - the wider universe helped some mean-reversion families become less bad, but it did not create a hard-gate winner
  - high-turnover families mostly converted extra symbols into extra fee drag rather than durable edge

### Major vs Alt Read
- using the top candidate from each universe and defining majors as `BTC/ETH/BNB`:
  - `universe_6` top candidate:
    - major OOS return mean: `0.0000`
    - alt OOS return mean: `0.0014`
    - only positive symbol: `TRXUSDT`
  - `universe_15` top candidate:
    - major OOS return mean: `-0.0009`
    - alt OOS return mean: `-0.0001`
    - only positive symbols: `TRXUSDT`, `AVAXUSDT`
- interpretation:
  - whatever residual signal exists in the current winner pocket is more visible in alt symbols than in the major basket
  - the expanded universe did not reveal a strong BTC/ETH/BNB-centered edge

### Interpretation
- result status: `FAIL`
- answer to the core question:
  - the 15-symbol expansion did **not** validate the hypothesis that the 6-symbol universe was simply too narrow
  - on the latest `2026-03-14` rerun, the 6-symbol universe actually produced `1` hard-gate pass while the 15-symbol universe produced `0`
- this does not prove the existing families are useless, but it does show that widening the universe alone is not enough to unlock robust edge under the current fee-inclusive framework
- therefore the next lever should not be another universe change

### Verification
- `uv run --active pytest -q`: PASS (`48 passed`)
- fetch rerun dedup check: PASS (`fetched_rows=0` on all populated `1h`/`4h` symbol files)
- broad sweep smoke: PASS for both requested commands and comparison report generation

### Next Lever (1 only)
- `15m` interval broad sweep
- reason:
  - universe expansion failed to improve hard-gate outcomes
  - the clean next test is to keep the family set and widened universe fixed and change only timeframe resolution

## 2026-03-14 - 15m Broad Sweep Interval Expansion (14 symbols)

### Scope
- changed lever only: interval
- baseline:
  - same 14-symbol populated universe on `1h/4h`
- variant:
  - same 14-symbol populated universe on `15m`
- fixed across both:
  - historical Binance USDT-M Futures candles only
  - latest 1-year window
  - walk-forward: `180d train / 60d test / 60d step`
  - execution: `MARKET`
  - taker fee: `5 bps`
  - slippage: `2 bps`
  - broad sweep families and ranking logic unchanged

### Why 15m
- the prior 14-symbol `1h/4h` baseline had `0` hard-gate winners.
- the prior 15-symbol expansion also failed to improve best return / best sharpe.
- the next clean hypothesis was whether shorter-term edge exists but was invisible on `1h/4h`.

### Why MATICUSDT Was Excluded
- `MATICUSDT` returned `0 rows` on the latest 1-year Binance futures sync.
- keeping it in the interval experiment would have mixed a dead symbol issue into an interval-only question.
- this run therefore used the same expanded universe minus `MATICUSDT`, resulting in `14` populated symbols.

### Data Command
- `uv run --active python scripts/fetch_futures_historical.py --symbols BTCUSDT ETHUSDT BNBUSDT SOLUSDT XRPUSDT ADAUSDT DOGEUSDT TRXUSDT AVAXUSDT LINKUSDT DOTUSDT LTCUSDT ATOMUSDT UNIUSDT --interval 15m --days 365`

### Data Result
- first sync:
  - all 14 symbols saved successfully
  - each file rows: `35040`
- rerun validation:
  - many symbols reported `fetched_rows=1`
  - but total rows stayed fixed at `35040`
  - interpretation:
    - this was rolling-window reuse, not duplicate accumulation
    - a newly closed `15m` candle was appended while the oldest candle rolled off

### Broad Sweep Commands
- same-universe `1h/4h` baseline:
  - `uv run --active python scripts/run_strategy_search.py --symbols BTCUSDT ETHUSDT BNBUSDT SOLUSDT XRPUSDT ADAUSDT DOGEUSDT TRXUSDT AVAXUSDT LINKUSDT DOTUSDT LTCUSDT ATOMUSDT UNIUSDT --intervals 1h 4h --mode broad-sweep --time-budget-hours 6 --out-root out/strategy_search_compare/universe_14_1h4h`
- `15m` variant:
  - `uv run --active python scripts/run_strategy_search.py --symbols BTCUSDT ETHUSDT BNBUSDT SOLUSDT XRPUSDT ADAUSDT DOGEUSDT TRXUSDT AVAXUSDT LINKUSDT DOTUSDT LTCUSDT ATOMUSDT UNIUSDT --intervals 15m --mode broad-sweep --time-budget-hours 6 --out-root out/strategy_search_compare/universe_14_15m`
- comparison report:
  - `uv run --active python scripts/compare_strategy_search_runs.py --baseline-root out/strategy_search_compare/universe_14_1h4h --variant-root out/strategy_search_compare/universe_14_15m --out-root out/strategy_search_compare/universe_14_15m_vs_1h4h --baseline-label universe_14_1h4h --variant-label universe_14_15m`

### Artifact Layout
- baseline:
  - `out/strategy_search_compare/universe_14_1h4h/summary.csv`
  - `out/strategy_search_compare/universe_14_1h4h/by_symbol.csv`
  - `out/strategy_search_compare/universe_14_1h4h/window_results.csv`
  - `out/strategy_search_compare/universe_14_1h4h/top_strategies.md`
  - `out/strategy_search_compare/universe_14_1h4h/strategy_family_summary.csv`
- variant:
  - `out/strategy_search_compare/universe_14_15m/summary.csv`
  - `out/strategy_search_compare/universe_14_15m/by_symbol.csv`
  - `out/strategy_search_compare/universe_14_15m/window_results.csv`
  - `out/strategy_search_compare/universe_14_15m/top_strategies.md`
  - `out/strategy_search_compare/universe_14_15m/strategy_family_summary.csv`
- comparison:
  - `out/strategy_search_compare/universe_14_15m_vs_1h4h/comparison.md`
  - `out/strategy_search_compare/universe_14_15m_vs_1h4h/overall_comparison.csv`
  - `out/strategy_search_compare/universe_14_15m_vs_1h4h/family_comparison.csv`

### Runtime
- `1h/4h` baseline runtime: about `1169.7s` (`19.5` minutes)
- `15m` runtime: about `92.7` minutes wall clock
- both fit the requested `~6h` experiment budget

### 15m vs 1h/4h Headline Comparison

| metric | 14 symbols @ 1h/4h | 14 symbols @ 15m |
|---|---:|---:|
| hard_gate_pass_count | 0 | 0 |
| best candidate | `ema_cross @ 4h` | `donchian_breakout @ 15m` |
| best OOS total return mean | -0.0003 | -0.0294 |
| best OOS sharpe mean | 0.0189 | -1.4658 |
| best OOS max drawdown mean | -0.0023 | -0.0521 |
| best positive symbols | 2 | 1 |
| best symbol return std | 0.0029 | 0.0179 |
| best trade count mean | 0.57 | 135.50 |
| best fee cost total | 7.9888 | 1881.1342 |

### Family-Level Read
- improved on `15m`: none
- least-bad families on `15m`:
  - `donchian_breakout`
  - `ema_cross`
  - `price_adx_breakout`
- but even the least-bad `15m` branches were materially worse than the `1h/4h` baseline:
  - `donchian_breakout`: return `-0.0093 -> -0.0294`, trade count `7.6 -> 135.5`, fee `106.3 -> 1881.1`
  - `ema_cross`: return `-0.0003 -> -0.0367`, positive symbols `2 -> 0`, fee `8.0 -> 1612.1`
  - `bollinger`: return `-0.0087 -> -0.0329`, positive symbols `3 -> 0`, fee `312.3 -> 2517.4`
  - `macd`, `rsi_mean_reversion`, `stoch_rsi` collapsed hardest on `15m` with huge trade-count and fee explosions

### Major vs Alt
- baseline top candidate:
  - majors (`BTC/ETH/BNB`) mean OOS return: `-0.0009`
  - alts mean OOS return: `-0.0001`
- `15m` top candidate:
  - majors mean OOS return: `-0.0312`
  - alts mean OOS return: `-0.0289`
- interpretation:
  - `15m` did not reveal a major-led or alt-led robust edge
  - both buckets degraded sharply, with alts only marginally less bad

### Interpretation
- result status: `FAIL`
- answer to the core questions:
  - hard-gate winner on `15m`: `0`
  - family improvement vs `1h/4h`: none
  - positive-symbol breadth vs `1h/4h`: worse on the top candidate (`2 -> 1`)
  - edge vs cost: `15m` looked dominated by fee/slippage and noise, not improved edge
  - robustness: the top `15m` candidates look brittle and false-positive-prone, not operationally credible
- this is exactly the type of result that should be kept as-is:
  - shorter timeframe increased activity dramatically
  - but the increase translated into much worse OOS return, sharpe, drawdown, and dispersion

### Verification
- `uv run --active pytest -q`: PASS (`50 passed`)
- fetch rerun reuse/dedup: PASS (fixed row count with rolling new-bar updates)
- broad sweep smoke: PASS for both baseline and `15m` variant commands

### Next Lever (1 only)
- `regime-conditional sweep`
- reason:
  - raw timeframe expansion to `15m` clearly amplified cost drag
  - the next defensible hypothesis is that edge may exist only in specific regimes, not across all bars

## 2026-03-14 - Regime-Conditional Broad Sweep (14 symbols, 1h/4h)

### Scope
- changed lever only: regime gating
- fixed across baseline and variant:
  - symbols: `BTCUSDT ETHUSDT BNBUSDT SOLUSDT XRPUSDT ADAUSDT DOGEUSDT TRXUSDT AVAXUSDT LINKUSDT DOTUSDT LTCUSDT ATOMUSDT UNIUSDT`
  - intervals: `1h`, `4h`
  - historical Binance USDT-M Futures candles only
  - latest 1-year window
  - walk-forward: `180d train / 60d test / 60d step`
  - execution: `MARKET`
  - taker fee: `5 bps`
  - slippage: `2 bps`
  - ranking and hard-gate logic unchanged
- baseline:
  - ungated broad sweep from `out/strategy_search_compare/universe_14_1h4h`
- variant:
  - same broad sweep with regime gating enabled

### Why Regime-Conditional Sweep
- `15m` failed decisively:
  - `0` hard-gate winners
  - worse OOS return / sharpe / drawdown
  - much larger trade count and fee drag
- the next clean hypothesis was therefore conditional activation:
  - keep the same families
  - keep the same timeframe set
  - change only whether a family is allowed to trade in a given market regime

### Regime Implementation
- trend-following families:
  - `ema_cross`
  - `donchian_breakout`
  - `supertrend`
  - `price_adx_breakout`
  - `macd`
- trend family gate:
  - `high_adx`
  - `not low_vol`
  - trend-aligned (`uptrend` for long, `downtrend` for short)
- mean-reversion families:
  - `rsi_mean_reversion`
  - `bollinger`
  - `stoch_rsi`
- mean-reversion gate:
  - `low_adx`
  - `low_vol`
  - `flat`
- regime internals used a shared parameter set:
  - `adx_window=14`
  - `low_adx_threshold=18`
  - `high_adx_threshold=25`
  - rolling realized-vol percentile window
  - slow EMA trend state with slope threshold

### Run Command
- regime-conditioned sweep:
  - `uv run --active python scripts/run_strategy_search.py --symbols BTCUSDT ETHUSDT BNBUSDT SOLUSDT XRPUSDT ADAUSDT DOGEUSDT TRXUSDT AVAXUSDT LINKUSDT DOTUSDT LTCUSDT ATOMUSDT UNIUSDT --intervals 1h 4h --mode broad-sweep --regime-mode family-default --time-budget-hours 6 --out-root out/strategy_search_compare/universe_14_regime`
- comparison report:
  - `uv run --active python scripts/compare_strategy_search_runs.py --baseline-root out/strategy_search_compare/universe_14_1h4h --variant-root out/strategy_search_compare/universe_14_regime --out-root out/strategy_search_compare/universe_14_regime_vs_1h4h --baseline-label universe_14_1h4h --variant-label universe_14_regime`

### Artifact Layout
- regime run:
  - `out/strategy_search_compare/universe_14_regime/summary.csv`
  - `out/strategy_search_compare/universe_14_regime/by_symbol.csv`
  - `out/strategy_search_compare/universe_14_regime/window_results.csv`
  - `out/strategy_search_compare/universe_14_regime/top_strategies.md`
  - `out/strategy_search_compare/universe_14_regime/strategy_family_summary.csv`
- comparison:
  - `out/strategy_search_compare/universe_14_regime_vs_1h4h/comparison.md`
  - `out/strategy_search_compare/universe_14_regime_vs_1h4h/overall_comparison.csv`
  - `out/strategy_search_compare/universe_14_regime_vs_1h4h/family_comparison.csv`

### Runtime
- observed runtime: about `1105s` (`18.4` minutes)
- still well inside the requested `~6h` budget

### Headline Comparison vs Ungated 1h/4h Baseline

| metric | 14 symbols @ 1h/4h | 14 symbols @ regime |
|---|---:|---:|
| hard_gate_pass_count | 0 | 70 |
| best candidate | `ema_cross @ 4h` | `donchian_breakout @ 1h` |
| best OOS total return mean | -0.0003 | 0.0062 |
| best OOS sharpe mean | 0.0189 | 0.3712 |
| best OOS max drawdown mean | -0.0023 | -0.0135 |
| best positive symbols | 2 | 10 |
| best symbol return std | 0.0029 | 0.0122 |
| best trade count mean | 0.57 | 20.64 |
| best fee cost total | 7.9888 | 289.8043 |
| best regime coverage ratio | 1.0000 | 0.4097 |

### Best Candidate Snapshot
- family: `donchian_breakout`
- interval: `1h`
- params:
  - `entry_period=30`
  - `exit_period=5`
  - `allow_short=false`
- regime:
  - `trend_high_adx_not_low_vol`
- key metrics:
  - `oos_total_return_mean=0.0062`
  - `oos_sharpe_mean=0.3712`
  - `oos_max_drawdown_mean=-0.0135`
  - positive symbols `10/14`
  - `trade_count_mean=20.64`
  - `fee_cost_total=289.80`
  - `regime_coverage_ratio=0.4097`

### Family-Level Read
- strongest improvement:
  - `supertrend`: `-0.0307 -> -0.0025`
  - `price_adx_breakout`: `-0.0151 -> 0.0050`
  - `stoch_rsi`: `-0.0200 -> -0.0003`
  - `macd`: `-0.0103 -> 0.0078`
  - `donchian_breakout`: `-0.0093 -> 0.0062`
- notable interpretation:
  - trend families improved the most under the trend-oriented gate, which is directionally consistent with the hypothesis
  - mean-reversion families also improved under very low-coverage low-vol / flat windows, but the absolute returns remain modest
  - `ema_cross` was the only family that failed to improve

### Major vs Alt
- top regime candidate:
  - major (`BTC/ETH/BNB`) mean OOS return: `0.0090`
  - alt mean OOS return: `0.0055`
  - positive symbols:
    - majors: `3/3`
    - alts: `7/11`
- interpretation:
  - the best regime-gated branch was positive across both buckets
  - majors were actually somewhat stronger than alts on the top candidate

### Interpretation
- result status: `PARTIAL`
- the regime-conditioned sweep did answer the main research question positively:
  - yes, hard-gate candidates can emerge when families are only active in more appropriate regimes
- but the result should not be treated as production-ready yet:
  - hard-gate pass count jumped to `70/192`, which is too large to accept without skepticism
  - the best candidate improved by trading only about `41%` of bars
  - trade count and fee still rose materially versus the ultra-sparse ungated top candidate
- practical read:
  - the gating hypothesis is promising
  - the next step should stress-test these candidates rather than celebrating the pass count

### Verification
- focused regime smoke:
  - `uv run --active pytest -q tests/test_strategy_search.py`: PASS (`8 passed`)
- full suite:
  - `uv run --active pytest -q`: PASS (`50 passed`)
- broad sweep smoke:
  - actual regime command completed and wrote all required outputs

### Next Lever (1 only)
- `fee/slippage stress`
- reason:
  - regime gating produced plausible candidates
  - the next validation should test whether those candidates survive harsher execution assumptions rather than widening the search again

## 2026-03-14 - Regime Fee/Slippage Stress (14 symbols, 1h/4h)

### Scope
- changed lever only: fee/slippage stress
- fixed across all scenarios:
  - symbols: `BTCUSDT ETHUSDT BNBUSDT SOLUSDT XRPUSDT ADAUSDT DOGEUSDT TRXUSDT AVAXUSDT LINKUSDT DOTUSDT LTCUSDT ATOMUSDT UNIUSDT`
  - intervals: `1h`, `4h`
  - mode: `broad-sweep`
  - regime mode: `family-default`
  - historical Binance USDT-M Futures candles only
  - latest 1-year window
  - walk-forward: `180d train / 60d test / 60d step`
  - ranking and hard-gate logic unchanged
- scenarios:
  - `baseline`: default `5 bps` taker fee, `2 bps` slippage
  - `fee_1p5x`: taker fee `1.5x`
  - `fee_2x`: taker fee `2.0x`
  - `slip_2x`: slippage `2.0x`
  - `slip_3x`: slippage `3.0x`
  - `mixed_2x`: taker fee `2.0x` and slippage `2.0x`

### Why Fee/Slippage Stress
- the regime-conditioned sweep finally produced positive, hard-gate-passing candidates.
- but the result was still suspicious:
  - `70/192` passes is high enough to raise multiple-testing / gate-inflation concerns
  - the best branch depended on only about `41%` coverage
- the next clean hypothesis was therefore execution-cost resilience:
  - if the edge is real, at least some candidates should survive harsher fees/slippage
  - if the edge is fragile, stress should collapse the table toward zero or near-zero OOS performance

### Implementation Notes
- CLI additions:
  - `--taker-fee-multiplier`
  - `--slippage-multiplier`
- comparison helper added:
  - `scripts/compare_regime_stress_runs.py`
- output layout:
  - `out/strategy_search_compare/universe_14_regime_stress/baseline/`
  - `out/strategy_search_compare/universe_14_regime_stress/fee_1p5x/`
  - `out/strategy_search_compare/universe_14_regime_stress/fee_2x/`
  - `out/strategy_search_compare/universe_14_regime_stress/slip_2x/`
  - `out/strategy_search_compare/universe_14_regime_stress/slip_3x/`
  - `out/strategy_search_compare/universe_14_regime_stress/mixed_2x/`
  - `out/strategy_search_compare/universe_14_regime_stress/overall_stress_comparison.csv`
  - `out/strategy_search_compare/universe_14_regime_stress/family_stress_comparison.csv`
  - `out/strategy_search_compare/universe_14_regime_stress/stress_comparison.md`

### Run Commands
- baseline:
  - `uv run --active python scripts/run_strategy_search.py --symbols BTCUSDT ETHUSDT BNBUSDT SOLUSDT XRPUSDT ADAUSDT DOGEUSDT TRXUSDT AVAXUSDT LINKUSDT DOTUSDT LTCUSDT ATOMUSDT UNIUSDT --intervals 1h 4h --mode broad-sweep --regime-mode family-default --time-budget-hours 6 --out-root out/strategy_search_compare/universe_14_regime_stress/baseline`
- fee `1.5x`:
  - `uv run --active python scripts/run_strategy_search.py --symbols BTCUSDT ETHUSDT BNBUSDT SOLUSDT XRPUSDT ADAUSDT DOGEUSDT TRXUSDT AVAXUSDT LINKUSDT DOTUSDT LTCUSDT ATOMUSDT UNIUSDT --intervals 1h 4h --mode broad-sweep --regime-mode family-default --time-budget-hours 6 --taker-fee-multiplier 1.5 --out-root out/strategy_search_compare/universe_14_regime_stress/fee_1p5x`
- fee `2.0x`:
  - `uv run --active python scripts/run_strategy_search.py --symbols BTCUSDT ETHUSDT BNBUSDT SOLUSDT XRPUSDT ADAUSDT DOGEUSDT TRXUSDT AVAXUSDT LINKUSDT DOTUSDT LTCUSDT ATOMUSDT UNIUSDT --intervals 1h 4h --mode broad-sweep --regime-mode family-default --time-budget-hours 6 --taker-fee-multiplier 2.0 --out-root out/strategy_search_compare/universe_14_regime_stress/fee_2x`
- slippage `2.0x`:
  - `uv run --active python scripts/run_strategy_search.py --symbols BTCUSDT ETHUSDT BNBUSDT SOLUSDT XRPUSDT ADAUSDT DOGEUSDT TRXUSDT AVAXUSDT LINKUSDT DOTUSDT LTCUSDT ATOMUSDT UNIUSDT --intervals 1h 4h --mode broad-sweep --regime-mode family-default --time-budget-hours 6 --slippage-multiplier 2.0 --out-root out/strategy_search_compare/universe_14_regime_stress/slip_2x`
- slippage `3.0x`:
  - `uv run --active python scripts/run_strategy_search.py --symbols BTCUSDT ETHUSDT BNBUSDT SOLUSDT XRPUSDT ADAUSDT DOGEUSDT TRXUSDT AVAXUSDT LINKUSDT DOTUSDT LTCUSDT ATOMUSDT UNIUSDT --intervals 1h 4h --mode broad-sweep --regime-mode family-default --time-budget-hours 6 --slippage-multiplier 3.0 --out-root out/strategy_search_compare/universe_14_regime_stress/slip_3x`
- mixed `2.0x`:
  - `uv run --active python scripts/run_strategy_search.py --symbols BTCUSDT ETHUSDT BNBUSDT SOLUSDT XRPUSDT ADAUSDT DOGEUSDT TRXUSDT AVAXUSDT LINKUSDT DOTUSDT LTCUSDT ATOMUSDT UNIUSDT --intervals 1h 4h --mode broad-sweep --regime-mode family-default --time-budget-hours 6 --taker-fee-multiplier 2.0 --slippage-multiplier 2.0 --out-root out/strategy_search_compare/universe_14_regime_stress/mixed_2x`
- comparison:
  - `uv run --active python scripts/compare_regime_stress_runs.py --stress-root out/strategy_search_compare/universe_14_regime_stress --out-root out/strategy_search_compare/universe_14_regime_stress`

### Runtime
- baseline: about `1056s` (`17.6` minutes)
- `fee_1p5x`: about `1069.7s` (`17.8` minutes)
- `fee_2x`: about `1131.2s` (`18.9` minutes)
- `slip_2x`: about `1119.0s` (`18.7` minutes)
- `slip_3x`: about `1106.1s` (`18.4` minutes)
- `mixed_2x`: about `1077.8s` (`18.0` minutes)
- full stress batch runtime: about `6560.0s` (`109.3` minutes)
- all six runs plus comparison remained far inside the requested `~6h` budget

### Overall Stress Comparison

| scenario | hard_gate_pass_count | best candidate | best OOS total return mean | best OOS sharpe mean | best OOS max drawdown mean | best positive symbols | best trade count mean | best fee cost total | best regime coverage ratio |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 70 | `donchian_breakout @ 1h` | 0.0062 | 0.3712 | -0.0135 | 10 | 20.64 | 289.8043 | 0.4097 |
| fee_1p5x | 57 | `macd @ 4h` | 0.0071 | 0.3181 | -0.0176 | 8 | 12.71 | 266.8588 | 0.4751 |
| fee_2x | 52 | `rsi_mean_reversion @ 4h` | 0.0001 | 0.2670 | -0.0009 | 5 | 0.57 | 15.9114 | 0.0038 |
| slip_2x | 60 | `macd @ 4h` | 0.0073 | 0.3283 | -0.0175 | 8 | 12.71 | 177.9397 | 0.4751 |
| slip_3x | 54 | `macd @ 4h` | 0.0068 | 0.2878 | -0.0177 | 8 | 12.71 | 177.9364 | 0.4751 |
| mixed_2x | 50 | `rsi_mean_reversion @ 4h` | 0.0001 | 0.2544 | -0.0010 | 5 | 0.57 | 15.9121 | 0.0038 |

### Baseline Top Candidate Under Stress
- baseline winner:
  - `donchian_breakout @ 1h`
  - regime: `trend_high_adx_not_low_vol`
  - `entry_period=30`, `exit_period=5`, `allow_short=false`
- same candidate under each scenario:
  - baseline: `return=0.0062`, `sharpe=0.3712`, `positive_symbols=10`, `hard_gate=True`
  - `fee_1p5x`: `return=0.0052`, `sharpe=0.2452`, `positive_symbols=7`, `hard_gate=True`
  - `fee_2x`: `return=0.0041`, `sharpe=0.1208`, `positive_symbols=7`, `hard_gate=True`
  - `slip_2x`: `return=0.0054`, `sharpe=0.2702`, `positive_symbols=8`, `hard_gate=True`
  - `slip_3x`: `return=0.0045`, `sharpe=0.1703`, `positive_symbols=7`, `hard_gate=True`
  - `mixed_2x`: `return=0.0033`, `sharpe=0.0225`, `positive_symbols=7`, `hard_gate=True`
- interpretation:
  - the original best candidate degraded materially, but it did not collapse into a false positive under the tested stress range
  - the weakest point was `mixed_2x`, where the candidate remained positive but almost lost all sharpe

### Family-Level Read
- most economically meaningful cost-resilient families:
  - `macd`
    - stayed positive in all five stressed scenarios
    - remained a top-ranked family under `fee_1p5x`, `slip_2x`, `slip_3x`, and `mixed_2x`
  - `donchian_breakout`
    - stayed positive in all five stressed scenarios
    - baseline winner remained hard-gate-pass in every stress case
- partial but weak resilience:
  - `rsi_mean_reversion`
  - `bollinger`
  - both stayed slightly positive under stress, but only by moving into extremely sparse low-coverage pockets
  - `fee_2x` / `mixed_2x` top candidate coverage dropped to `0.0038`, which is too narrow to treat as broad robustness
- weak families after stress:
  - `supertrend`: negative in every stressed scenario
  - `ema_cross`: some delta improvement versus a bad baseline family row, but still `0` hard-gate survival across stressed scenarios
  - `price_adx_breakout`: remained viable under mild stress, but collapsed badly under `fee_2x` and `mixed_2x`

### Major vs Alt Cost Sensitivity
- evaluated on the original baseline winner:
  - baseline:
    - majors mean OOS return: `0.0090`
    - alts mean OOS return: `0.0055`
  - `mixed_2x`:
    - majors mean OOS return: `0.0062`
    - alts mean OOS return: `0.0025`
- interpretation:
  - majors were consistently more resilient than alts for the most credible regime candidate
  - stress did not expose a hidden alt-specific robust edge

### Interpretation
- result status: `PARTIAL`
- answers to the core questions:
  - baseline winners under stress:
    - the original top regime-gated candidate stayed positive and hard-gate-pass in all six scenarios
    - so the regime result is not purely a low-cost illusion
  - hard-gate collapse:
    - pass count fell from `70` to `50` under `mixed_2x`
    - that is a meaningful drop, but not a total collapse
  - regime edge vs cost:
    - yes, part of the edge survives cost stress
    - but the table becomes more concentrated in a smaller subset of families
  - family-level resilience:
    - strongest: `macd`, `donchian_breakout`
    - marginal/coverage-sensitive: `rsi_mean_reversion`, `bollinger`
  - low-coverage fragility:
    - strongest stress winners shifted toward near-zero-coverage branches
    - this reinforces the view that pass count is still inflated relative to truly operational candidates
- practical read:
  - regime gating seems directionally valid
  - but the current search frontier is still too wide and too coverage-skewed to trust without reducing the multiple-testing surface

### Verification
- focused smoke:
  - `uv run --active pytest -q tests/test_strategy_search.py`: PASS (`10 passed`)
- full suite:
  - `uv run --active pytest -q`: PASS
- baseline compatibility:
  - the rerun baseline in `out/strategy_search_compare/universe_14_regime_stress/baseline` reproduced the earlier regime result (`70` hard-gate passes, same top candidate)
- comparison report generation:
  - PASS (`overall_stress_comparison.csv`, `family_stress_comparison.csv`, `stress_comparison.md` all written)

### Next Lever (1 only)
- `family pruning`
- reason:
  - cost stress did not kill the regime hypothesis
  - but it did show that credible signal is concentrated in fewer families, while some surviving winners depend on ultra-low coverage
  - the next clean test is to reduce the search space to the cost-resilient frontier without changing regime logic again

## 2026-03-14 - Regime Family Pruning (14 symbols, 1h/4h)

### Scope
- changed lever only: family set
- fixed across baseline and variant:
  - symbols: `BTCUSDT ETHUSDT BNBUSDT SOLUSDT XRPUSDT ADAUSDT DOGEUSDT TRXUSDT AVAXUSDT LINKUSDT DOTUSDT LTCUSDT ATOMUSDT UNIUSDT`
  - intervals: `1h`, `4h`
  - mode: `broad-sweep`
  - regime mode: `family-default`
  - historical Binance USDT-M Futures candles only
  - latest 1-year window
  - walk-forward: `180d train / 60d test / 60d step`
  - fee/slippage model unchanged
  - ranking and hard-gate logic unchanged
- baseline:
  - full regime sweep from `out/strategy_search_compare/universe_14_regime`
- variant:
  - pruned regime sweep with only:
    - `donchian_breakout`
    - `macd`
    - `price_adx_breakout`

### Why Family Pruning
- the full regime sweep and stress follow-up jointly suggested:
  - regime edge is partly real
  - but pass counts were still too high (`70` baseline, `50` under strongest stress)
  - low-coverage mean-reversion survivors were still distorting the top of some stressed tables
- the next clean lever was therefore not new logic, but a smaller search surface:
  - keep only the families with the clearest cost resilience
  - remove weaker or coverage-distorting families

### Run Commands
- pruned sweep:
  - `uv run --active python scripts/run_strategy_search.py --symbols BTCUSDT ETHUSDT BNBUSDT SOLUSDT XRPUSDT ADAUSDT DOGEUSDT TRXUSDT AVAXUSDT LINKUSDT DOTUSDT LTCUSDT ATOMUSDT UNIUSDT --intervals 1h 4h --families donchian_breakout macd price_adx_breakout --mode broad-sweep --regime-mode family-default --time-budget-hours 6 --out-root out/strategy_search_compare/universe_14_regime_pruned`
- comparison report:
  - `uv run --active python scripts/compare_strategy_search_runs.py --baseline-root out/strategy_search_compare/universe_14_regime --variant-root out/strategy_search_compare/universe_14_regime_pruned --out-root out/strategy_search_compare/universe_14_regime_pruned_vs_full --baseline-label universe_14_regime --variant-label universe_14_regime_pruned`

### Artifact Layout
- pruned run:
  - `out/strategy_search_compare/universe_14_regime_pruned/summary.csv`
  - `out/strategy_search_compare/universe_14_regime_pruned/by_symbol.csv`
  - `out/strategy_search_compare/universe_14_regime_pruned/window_results.csv`
  - `out/strategy_search_compare/universe_14_regime_pruned/top_strategies.md`
  - `out/strategy_search_compare/universe_14_regime_pruned/strategy_family_summary.csv`
- comparison:
  - `out/strategy_search_compare/universe_14_regime_pruned_vs_full/overall_comparison.csv`
  - `out/strategy_search_compare/universe_14_regime_pruned_vs_full/family_comparison.csv`
  - `out/strategy_search_compare/universe_14_regime_pruned_vs_full/comparison.md`

### Runtime
- observed runtime: about `312.4s` (`5.2` minutes)
- much faster than the full regime sweep because the search space was materially smaller

### Full Regime vs Pruned Regime

| metric | full regime | pruned regime | delta |
|---|---:|---:|---:|
| candidate count | 192 | 116 | -76 |
| hard_gate_pass_count | 70 | 42 | -28 |
| best OOS total return mean | 0.0062 | 0.0048 | -0.0014 |
| best OOS sharpe mean | 0.3712 | 0.1439 | -0.2273 |
| best OOS max drawdown mean | -0.0135 | -0.0162 | -0.0028 |
| best positive symbols | 10 | 9 | -1 |
| best symbol return std | 0.0122 | 0.0145 | +0.0023 |
| best trade count mean | 20.64 | 10.86 | -9.79 |
| best fee cost total | 289.8043 | 151.7073 | -138.0970 |
| best regime coverage ratio | 0.4097 | 0.4751 | +0.0655 |

### Top Family Read
- `macd`
  - pruned rank `1`
  - best branch:
    - `4h`
    - `fast_period=16`
    - `slow_period=32`
    - `signal_period=9`
    - `adx_filter=true`
  - metrics:
    - `oos_total_return_mean=0.0048`
    - `oos_sharpe_mean=0.1439`
    - positive symbols `9/14`
    - `trade_count_mean=10.86`
    - `fee_cost_total=151.71`
    - `regime_coverage_ratio=0.4751`
  - interpretation:
    - pruning promoted `macd` to rank 1 because it is cheaper and lower-turnover
    - but its best pruned winner is weaker in absolute return/sharpe than the strongest `donchian` branch
- `donchian_breakout`
  - pruned rank `5`
  - same best branch as before:
    - `1h`
    - `entry_period=30`
    - `exit_period=5`
    - `allow_short=false`
  - metrics unchanged:
    - `oos_total_return_mean=0.0062`
    - `oos_sharpe_mean=0.3712`
    - positive symbols `10/14`
    - `trade_count_mean=20.64`
    - `fee_cost_total=289.80`
    - `regime_coverage_ratio=0.4097`
  - interpretation:
    - this remains the strongest absolute edge candidate
    - pruning did not improve it, but importantly did not damage it either
- `price_adx_breakout`
  - pruned rank `20`
  - best branch:
    - `1h`
    - `breakout_lookback=30`
    - `exit_lookback=5`
    - `adx_threshold=30`
  - metrics:
    - `oos_total_return_mean=0.0056`
    - `oos_sharpe_mean=0.3148`
    - positive symbols `8/14`
    - `trade_count_mean=24.21`
    - `fee_cost_total=339.72`
    - `regime_coverage_ratio=0.4097`
  - interpretation:
    - this family improved modestly under pruning
    - still looks secondary, but it remains a credible backup branch

### Main Research Answers
- pass-count reduction:
  - full regime: `70/192`
  - strongest stress: `50/192`
  - pruned regime: `42/116`
  - interpretation:
    - pruning reduced both the numerator and denominator
    - it did not solve pass-count inflation completely, but it materially reduced the problem
- who is the real primary candidate?
  - by ranking score after pruning: `macd`
  - by absolute return / sharpe / breadth after pruning: `donchian_breakout`
  - practical conclusion:
    - `donchian_breakout` remains the primary research candidate
    - `macd` is the lower-turnover alternative
    - `price_adx_breakout` remains the backup candidate
- majors vs alts:
  - pruned top candidate (`macd @ 4h`) had:
    - majors mean OOS return: `0.0042`
    - alts mean OOS return: `0.0050`
  - compared with the full-regime `donchian` winner:
    - majors: `0.0090`
    - alts: `0.0055`
  - interpretation:
    - `donchian_breakout` looks more major-robust
    - `macd` looks more evenly distributed, with slightly better relative support from alts
- low coverage survivor question:
  - the low-coverage mean-reversion survivors disappeared from the pruned top table
  - top-family coverage remained around `0.41 - 0.48`, not `~0.0038`
  - pruning therefore improved interpretability even though it did not improve best absolute metrics

### Interpretation
- result status: `PARTIAL`
- what worked:
  - pruning reduced runtime
  - pruning reduced pass-count inflation
  - pruning removed the low-coverage survivor issue from the top-ranked table
- what did not work:
  - pruning did not improve the best overall return/sharpe frontier
  - the best pruned rank-1 branch (`macd`) is safer on turnover/cost, but weaker than the strongest surviving `donchian` branch on pure edge metrics
- practical read:
  - `donchian_breakout` remains the lead candidate
  - `macd` remains worth keeping because of lower turnover/cost
  - `price_adx_breakout` is still secondary support, not the lead

### Verification
- broad sweep smoke:
  - the requested pruned broad sweep command completed and wrote all required outputs
- full suite:
  - `uv run --active pytest -q`: PASS

### Next Lever (1 only)
- `regime parameter tightening`
- reason:
  - family pruning lowered search-surface inflation but still left `42` hard-gate passes
  - the next clean step is to keep the pruned family set fixed and tighten only regime thresholds so that surviving candidates are fewer and more trustworthy

## 2026-03-14 - Regime Parameter Tightening (14 symbols, pruned families, 1h/4h)

### Scope
- changed lever only: regime parameters
- fixed across baseline and variant:
  - symbols: `BTCUSDT ETHUSDT BNBUSDT SOLUSDT XRPUSDT ADAUSDT DOGEUSDT TRXUSDT AVAXUSDT LINKUSDT DOTUSDT LTCUSDT ATOMUSDT UNIUSDT`
  - intervals: `1h`, `4h`
  - families: `donchian_breakout`, `macd`, `price_adx_breakout`
  - mode: `broad-sweep`
  - historical Binance USDT-M Futures candles only
  - latest 1-year window
  - walk-forward: `180d train / 60d test / 60d step`
  - fee/slippage model unchanged
  - ranking and hard-gate logic unchanged
- baseline:
  - pruned regime sweep from `out/strategy_search_compare/universe_14_regime_pruned`
- variant:
  - tightened regime sweep from `out/strategy_search_compare/universe_14_regime_pruned_tightened`

### Why Tightening
- pruning removed low-coverage anomalies from the top table, but hard-gate pass count stayed at `42/116`.
- the next clean hypothesis was that stricter regime filters could:
  - cut more fragile candidates
  - preserve the real trend edge if it exists
  - clarify whether `donchian` or `macd` is the more trustworthy lead branch

### Tightened Regime Definition
- prior regime:
  - `high_adx_threshold=25`
  - `low/high vol quantile=0.35 / 0.65`
  - `trend_ema_span=80`
  - `trend_slope_lookback=12`
  - `trend_slope_threshold=0.0015`
- tightened regime:
  - `high_adx_threshold=30`
  - `vol_percentile_window=160`
  - `low/high vol quantile=0.20 / 0.80`
  - `trend_ema_span=100`
  - `trend_slope_lookback=16`
  - `trend_slope_threshold=0.0030`
  - `trend_distance_threshold=0.0050`
  - `min_coverage_ratio=0.20`
- regime name used in outputs:
  - `trend_tight_high_adx_extreme_vol_strict_trend`

### Run Commands
- tightened sweep:
  - `uv run --active python scripts/run_strategy_search.py --symbols BTCUSDT ETHUSDT BNBUSDT SOLUSDT XRPUSDT ADAUSDT DOGEUSDT TRXUSDT AVAXUSDT LINKUSDT DOTUSDT LTCUSDT ATOMUSDT UNIUSDT --intervals 1h 4h --families donchian_breakout macd price_adx_breakout --mode broad-sweep --regime-mode family-default --time-budget-hours 6 --out-root out/strategy_search_compare/universe_14_regime_pruned_tightened`
- comparison report:
  - `uv run --active python scripts/compare_strategy_search_runs.py --baseline-root out/strategy_search_compare/universe_14_regime_pruned --variant-root out/strategy_search_compare/universe_14_regime_pruned_tightened --out-root out/strategy_search_compare/universe_14_regime_pruned_tightened_vs_pruned --baseline-label universe_14_regime_pruned --variant-label universe_14_regime_pruned_tightened`

### Artifact Layout
- tightened run:
  - `out/strategy_search_compare/universe_14_regime_pruned_tightened/summary.csv`
  - `out/strategy_search_compare/universe_14_regime_pruned_tightened/by_symbol.csv`
  - `out/strategy_search_compare/universe_14_regime_pruned_tightened/window_results.csv`
  - `out/strategy_search_compare/universe_14_regime_pruned_tightened/top_strategies.md`
  - `out/strategy_search_compare/universe_14_regime_pruned_tightened/strategy_family_summary.csv`
- comparison:
  - `out/strategy_search_compare/universe_14_regime_pruned_tightened_vs_pruned/overall_comparison.csv`
  - `out/strategy_search_compare/universe_14_regime_pruned_tightened_vs_pruned/family_comparison.csv`
  - `out/strategy_search_compare/universe_14_regime_pruned_tightened_vs_pruned/comparison.md`

### Runtime
- observed runtime: about `274.1s` (`4.6` minutes)
- still comfortably inside the requested budget

### Pruned vs Tightened

| metric | pruned regime | tightened regime | delta |
|---|---:|---:|---:|
| candidate count | 116 | 116 | 0 |
| hard_gate_pass_count | 42 | 37 | -5 |
| best OOS total return mean | 0.0048 | 0.0037 | -0.0011 |
| best OOS sharpe mean | 0.1439 | 0.2382 | +0.0943 |
| best OOS max drawdown mean | -0.0162 | -0.0112 | +0.0050 |
| best positive symbols | 9 | 10 | +1 |
| best symbol_return_std | 0.0145 | 0.0085 | -0.0060 |
| best trade_count_mean | 10.86 | 15.57 | +4.71 |
| best fee_cost_total | 151.7073 | 218.6388 | +66.9315 |
| best regime_coverage_ratio | 0.4751 | 0.3649 | -0.1102 |

### Main Research Answers
- candidate count:
  - unchanged at `116`
  - interpretation:
    - coverage floor was implemented, but it did not bind on the remaining trend families
    - the effect of tightening came through candidate quality, not candidate-count truncation
- hard-gate passes:
  - fell from `42 -> 37`
  - this is a real reduction, but only a modest one
- lead candidate:
  - pruned regime lead by ranking: `macd`
  - tightened regime lead: `donchian_breakout`
  - interpretation:
    - stricter regime requirements favored the higher-breadth `donchian` branch over the lower-turnover `macd` branch
- low-coverage survivor issue:
  - still absent
  - tightened result coverage range stayed around `0.365 - 0.427`
  - no candidate returned to the ultra-low-coverage behavior seen before pruning

### Family-Level Read
- `donchian_breakout`
  - became the new rank-1 family
  - best branch:
    - `1h`
    - `entry_period=40`
    - `exit_period=5`
  - metrics:
    - `oos_total_return_mean=0.0037`
    - `oos_sharpe_mean=0.2382`
    - positive symbols `10/14`
    - `trade_count_mean=15.57`
    - `fee_cost_total=218.64`
    - `regime_coverage_ratio=0.3649`
  - interpretation:
    - return weakened versus the prior best `donchian`
    - but sharpe, drawdown, dispersion, and breadth together now make it the cleanest lead candidate
- `macd`
  - best family rank dropped to `10`
  - best tightened branch:
    - `4h`
    - `fast_period=12`
    - `slow_period=26`
    - `signal_period=9`
    - `adx_filter=false`
  - metrics:
    - `oos_total_return_mean=0.0053`
    - `oos_sharpe_mean=0.1585`
    - positive symbols `8/14`
    - `trade_count_mean=11.50`
    - `fee_cost_total=160.67`
    - `regime_coverage_ratio=0.4265`
  - interpretation:
    - within-family, `macd` improved slightly on return/sharpe
    - but it lost breadth relative to `donchian` and no longer won overall ranking
- `price_adx_breakout`
  - best family rank improved to `2`, but quality weakened
  - metrics:
    - `oos_total_return_mean=0.0020`
    - `oos_sharpe_mean=0.0205`
    - positive symbols `11/14`
    - `trade_count_mean=19.57`
    - `fee_cost_total=274.42`
  - interpretation:
    - breadth increased
    - but return quality degraded too much to keep it as a top conviction branch

### Major vs Alt
- tightened top candidate (`donchian_breakout @ 1h`):
  - majors mean OOS return: `0.0030`
  - alts mean OOS return: `0.0040`
  - majors mean OOS sharpe: higher than alts
- interpretation:
  - the tightened winner remained positive in both buckets
  - robustness is reasonably balanced; no single bucket dominates the result

### Interpretation
- result status: `PARTIAL`
- what improved:
  - pass count came down
  - top-candidate sharpe improved
  - top-candidate drawdown and dispersion improved
  - low-coverage survivor issue remained absent
- what did not improve:
  - best absolute return weakened
  - turnover and fee for the new top candidate increased relative to the pruned `macd` winner
  - pass count is still high enough (`37`) to remain skeptical
- practical read:
  - `donchian_breakout` is now the clearest primary candidate again
  - `macd` remains the best secondary branch
  - `price_adx_breakout` still survives, but now more as a weak backup than a co-lead

### Verification
- broad sweep smoke:
  - the requested tightened broad sweep command completed and wrote all required outputs
- full suite:
  - `uv run --active pytest -q`: PASS

### Next Lever (1 only)
- `final showdown: donchian_breakout vs macd`
- reason:
  - tightening re-established `donchian` as the lead and preserved `macd` as the strongest alternative
  - `price_adx_breakout` lost too much quality to justify staying in the next search surface

## 2026-03-14 - Final Showdown: Donchian vs MACD

### Scope
- changed lever only: comparison method
- fixed across both families and both scenarios:
  - symbols: `BTCUSDT ETHUSDT BNBUSDT SOLUSDT XRPUSDT ADAUSDT DOGEUSDT TRXUSDT AVAXUSDT LINKUSDT DOTUSDT LTCUSDT ATOMUSDT UNIUSDT`
  - intervals: `1h`, `4h`
  - historical Binance USDT-M Futures candles only
  - latest 1-year window
  - walk-forward: `180d train / 60d test / 60d step`
  - tightened regime definition unchanged
  - cost model unchanged in baseline; strongest prior stress reused as `mixed_2x`
- family finalists only:
  - `donchian_breakout`
  - `macd`
- important:
  - this was **not** a new broad sweep
  - only small neighborhoods around the currently best pockets were evaluated

### Why This Was the Right Final Step
- after `final2`, the research question changed:
  - not “which family scores highest on one row”
  - but “which family generates the more reproducible candidate set while still offering the stronger real edge”
- `donchian_breakout` had the best lead candidate
- `macd` had the stronger family-level reproducibility profile
- the final decision therefore required a narrow pocket-vs-pocket showdown, not another wide search

### Neighborhoods Evaluated
- Donchian pocket neighborhood:
  - `(entry_period=30, exit_period=5)`
  - `(entry_period=30, exit_period=10)`
  - `(entry_period=40, exit_period=5)`
  - `(entry_period=40, exit_period=10)`
- MACD pocket neighborhood:
  - `(fast=8, slow=21, signal=5)`
  - `(fast=10, slow=30, signal=7)`
  - `(fast=12, slow=26, signal=9)`
  - `(fast=16, slow=32, signal=9)`
- duplicate toggle suppression:
  - `use_histogram=false`
  - `adx_filter=false`
  - fixed to avoid counting identical behavior multiple times in family reproducibility metrics

### Run Command
- showdown runner:
  - `uv run --active python scripts/run_final_showdown.py --symbols BTCUSDT ETHUSDT BNBUSDT SOLUSDT XRPUSDT ADAUSDT DOGEUSDT TRXUSDT AVAXUSDT LINKUSDT DOTUSDT LTCUSDT ATOMUSDT UNIUSDT --intervals 1h 4h --out-root out/strategy_search_compare/final_showdown_donchian_vs_macd`

### Artifact Layout
- baseline:
  - `out/strategy_search_compare/final_showdown_donchian_vs_macd/baseline/summary.csv`
  - `out/strategy_search_compare/final_showdown_donchian_vs_macd/baseline/by_symbol.csv`
  - `out/strategy_search_compare/final_showdown_donchian_vs_macd/baseline/window_results.csv`
  - `out/strategy_search_compare/final_showdown_donchian_vs_macd/baseline/top_strategies.md`
  - `out/strategy_search_compare/final_showdown_donchian_vs_macd/baseline/strategy_family_summary.csv`
- mixed stress:
  - `out/strategy_search_compare/final_showdown_donchian_vs_macd/mixed_2x/summary.csv`
  - `out/strategy_search_compare/final_showdown_donchian_vs_macd/mixed_2x/by_symbol.csv`
  - `out/strategy_search_compare/final_showdown_donchian_vs_macd/mixed_2x/window_results.csv`
  - `out/strategy_search_compare/final_showdown_donchian_vs_macd/mixed_2x/top_strategies.md`
  - `out/strategy_search_compare/final_showdown_donchian_vs_macd/mixed_2x/strategy_family_summary.csv`
- showdown summary:
  - `out/strategy_search_compare/final_showdown_donchian_vs_macd/showdown_family_comparison.csv`
  - `out/strategy_search_compare/final_showdown_donchian_vs_macd/showdown.md`

### Runtime
- baseline + stress combined showdown runtime: about `627.6s` (`10.5` minutes)
- far inside the available budget

### Final Showdown Table

| metric | donchian_breakout | macd |
|---|---:|---:|
| candidate count | 8 | 8 |
| hard_gate_pass_count | 3 | 4 |
| neighborhood pass rate | 0.375 | 0.500 |
| neighborhood median return | -0.0003 | 0.0006 |
| neighborhood median sharpe | -0.1962 | -0.1489 |
| best OOS total return mean | 0.0037 | 0.0053 |
| best OOS sharpe mean | 0.2382 | 0.1585 |
| best OOS max drawdown mean | -0.0112 | -0.0169 |
| best positive symbols | 10 | 8 |
| best symbol_return_std | 0.0085 | 0.0111 |
| best trade_count_mean | 15.57 | 11.50 |
| best fee_cost_total | 218.6388 | 160.6727 |
| best regime_coverage_ratio | 0.3649 | 0.4265 |
| stress best-candidate survival | False | True |
| stress neighborhood pass rate | 0.125 | 0.500 |
| stress survival rate | 0.333 | 1.000 |

### Main Research Answers
- highest single-point strength:
  - on raw return: `macd` had the highest single neighborhood candidate (`0.0082`)
  - on selected best candidate quality: `donchian_breakout` still had the better deployable lead row because it combined:
    - higher sharpe
    - lower dispersion
    - broader positive-symbol support
- neighborhood stability:
  - `macd` clearly won:
    - pass rate `50.0%` vs `37.5%`
    - positive neighborhood median return vs slightly negative for `donchian`
- stress resilience:
  - `macd` clearly won:
    - baseline hard-gate candidates surviving under `mixed_2x`: `4/4`
    - `donchian`: `1/3`
- majors vs alts:
  - best Donchian candidate:
    - majors mean OOS return: `0.0030`
    - alts mean OOS return: `0.0040`
  - best MACD candidate:
    - majors mean OOS return: `0.0080`
    - alts mean OOS return: `0.0045`
  - interpretation:
    - `macd` leaned more major-robust
    - `donchian` was more balanced across the broader universe
- regime coverage:
  - `macd` best pocket had slightly higher coverage (`0.4265`)
  - `donchian` best pocket had slightly lower coverage (`0.3649`)
  - both remained well above the low-coverage survivor zone

### Final Decision
- result status: `SUCCESS`
- selected primary family:
  - `donchian_breakout`
- selected backup family:
  - `macd`
- rationale:
  - even after explicitly emphasizing reproducibility, `donchian_breakout` kept the stronger lead candidate on the metrics that matter most for an actual primary choice:
    - higher best-candidate sharpe
    - better breadth (`10` positive symbols)
    - lower symbol dispersion
  - `macd` did prove more reproducible as a family and much stronger under stress
  - therefore the correct final read is:
    - primary candidate: `donchian_breakout`
    - mandatory backup / hedge candidate: `macd`

### Verification
- showdown smoke:
  - `scripts/run_final_showdown.py` completed and wrote baseline, stress, and showdown summary outputs
- full suite:
  - `uv run --active pytest -q`: PASS

### Next Lever (1 only)
- `donchian winner holdout validation`
- reason:
  - family selection is now finished
  - the next clean step is no longer family search, but stricter holdout validation of the chosen `donchian_breakout` winner while retaining `macd` as the documented runner-up

## 2026-03-14 - Holdout Validation: Donchian Winner vs MACD Control

### Scope
- changed lever only: evaluation segment
- fixed across both pockets and both cost scenarios:
  - symbols: `BTCUSDT ETHUSDT BNBUSDT SOLUSDT XRPUSDT ADAUSDT DOGEUSDT TRXUSDT AVAXUSDT LINKUSDT DOTUSDT LTCUSDT ATOMUSDT UNIUSDT`
  - historical Binance USDT-M Futures candles only
  - tightened regime definition unchanged
  - no new search and no parameter retuning
- fixed candidates:
  - primary under test:
    - `donchian_breakout @ 1h`
    - `entry_period=40`
    - `exit_period=5`
    - `allow_short=false`
  - control:
    - `macd @ 4h`
    - `fast_period=12`
    - `slow_period=26`
    - `signal_period=9`
    - `use_histogram=false`
    - `adx_filter=false`

### Why This Was the Right Next Step
- family selection was already finished.
- the only remaining decision question was whether the selected Donchian pocket could survive on a segment not directly reused in the selection comparison.
- the strongest control under the same tightened regime was the best MACD pocket, so both were evaluated side by side on the same unseen trailing holdout.

### Holdout Definition
- holdout window:
  - `2025-11-13T20:00:00+00:00` to `2026-03-13T20:00:00+00:00`
- implementation:
  - trailing `120` days from each pocket's interval data
  - same strategy parameters, same regime rules, same cost model
- scenarios:
  - `baseline`
  - `mixed_2x`

### Run Command
- holdout runner:
  - `uv run --active python scripts/run_holdout_validation.py --symbols BTCUSDT ETHUSDT BNBUSDT SOLUSDT XRPUSDT ADAUSDT DOGEUSDT TRXUSDT AVAXUSDT LINKUSDT DOTUSDT LTCUSDT ATOMUSDT UNIUSDT --out-root out/strategy_search_compare/final_showdown_donchian_vs_macd_holdout`

### Artifact Layout
- baseline:
  - `out/strategy_search_compare/final_showdown_donchian_vs_macd_holdout/baseline/summary.csv`
  - `out/strategy_search_compare/final_showdown_donchian_vs_macd_holdout/baseline/by_symbol.csv`
- mixed stress:
  - `out/strategy_search_compare/final_showdown_donchian_vs_macd_holdout/mixed_2x/summary.csv`
  - `out/strategy_search_compare/final_showdown_donchian_vs_macd_holdout/mixed_2x/by_symbol.csv`
- comparison:
  - `out/strategy_search_compare/final_showdown_donchian_vs_macd_holdout/holdout_comparison.csv`
  - `out/strategy_search_compare/final_showdown_donchian_vs_macd_holdout/holdout_validation.md`

### Holdout Comparison

| metric | donchian_breakout | macd |
|---|---:|---:|
| baseline holdout total return | -0.0005 | 0.0019 |
| baseline holdout sharpe | -0.5031 | 0.2162 |
| baseline holdout max drawdown | -0.0104 | -0.0146 |
| baseline positive symbols | 7 | 7 |
| baseline symbol return std | 0.0064 | 0.0100 |
| baseline trade count mean | 10.00 | 6.64 |
| baseline fee cost total | 140.0378 | 92.6873 |
| baseline regime coverage ratio | 0.3369 | 0.3919 |
| mixed_2x holdout total return | -0.0019 | 0.0009 |
| mixed_2x holdout sharpe | -0.8026 | 0.0934 |
| mixed_2x holdout max drawdown | -0.0111 | -0.0150 |
| mixed_2x positive symbols | 6 | 7 |
| mixed_2x symbol return std | 0.0066 | 0.0100 |
| mixed_2x trade count mean | 10.00 | 6.64 |
| mixed_2x fee cost total | 280.0196 | 185.4005 |
| mixed_2x regime coverage ratio | 0.3369 | 0.3919 |

### Main Research Answers
- does Donchian hold up on holdout?
  - no
  - baseline and stressed holdout return are both negative
  - baseline and stressed holdout sharpe are both negative
- is MACD more stable on holdout?
  - yes
  - MACD stayed positive in both baseline and `mixed_2x`
  - positive-symbol count stayed flat at `7/14`
  - it also achieved this with lower turnover and lower total fee cost than Donchian
- baseline vs stress split:
  - baseline winner: `macd`
  - mixed stress winner: `macd`
  - the prior possible read of "Donchian in baseline, MACD in stress" did not materialize on the holdout segment
- majors vs alts:
  - Donchian holdout weakness was concentrated in majors:
    - baseline major mean return `-0.0058`
    - stress major mean return `-0.0076`
  - MACD remained major-led:
    - baseline major mean return `0.0087`
    - stress major mean return `0.0079`
  - MACD alt returns were near flat to slightly negative, but still materially stronger than Donchian's major failure profile

### Interpretation
- result status: `SUCCESS`
- key decision change:
  - the final-showdown primary choice does not survive stricter unseen-segment validation
  - `MACD` is therefore promoted from runner-up to current primary research candidate
- practical read:
  - `donchian_breakout` still had the strongest selected in-sweep candidate
  - but holdout validation is the stronger decision criterion than the prior selection comparison
  - once holdout is introduced, `macd` is the more credible live candidate because it remains positive under both baseline and stressed execution assumptions

### Verification
- holdout runner:
  - `scripts/run_holdout_validation.py` completed and wrote baseline, stress, and comparison outputs
- full suite:
  - `uv run --active pytest -q`: PASS

### Next Lever (1 only)
- `extended MACD holdout confirmation`
- reason:
  - family selection and head-to-head selection are finished
  - the next clean step is to keep the promoted MACD pocket frozen and test it on one more stricter holdout slice rather than reopening search

## 2026-03-14 - Extended Holdout Confirmation: MACD Primary Candidate

### Scope
- changed lever only: holdout window set
- fixed across all runs:
  - candidate: `macd @ 4h`
  - params:
    - `fast_period=12`
    - `slow_period=26`
    - `signal_period=9`
    - `use_histogram=false`
    - `adx_filter=false`
  - tightened regime definition unchanged
  - symbols: `BTCUSDT ETHUSDT BNBUSDT SOLUSDT XRPUSDT ADAUSDT DOGEUSDT TRXUSDT AVAXUSDT LINKUSDT DOTUSDT LTCUSDT ATOMUSDT UNIUSDT`
  - historical Binance USDT-M Futures candles only
  - no new sweep and no retuning
- scenarios:
  - `baseline`
  - `mixed_2x`

### Why This Was the Right Next Step
- the single trailing `120d` holdout already promoted `macd` over `donchian_breakout`.
- the remaining risk was that the `120d` result might be a lucky slice.
- the clean follow-up was therefore to keep the candidate frozen and test multiple unseen trailing holdout windows under the same two cost regimes.

### Holdout Design
- rolling trailing windows:
  - `60d`
  - `90d`
  - `120d`
- this satisfies the requirement to compare at least three unseen holdout windows without reopening the search surface.

### Run Command
- extended MACD holdout:
  - `uv run --active python scripts/run_holdout_validation.py --symbols BTCUSDT ETHUSDT BNBUSDT SOLUSDT XRPUSDT ADAUSDT DOGEUSDT TRXUSDT AVAXUSDT LINKUSDT DOTUSDT LTCUSDT ATOMUSDT UNIUSDT --mode extended-macd-confirmation --out-root out/strategy_search_compare/macd_extended_holdout_confirmation`

### Artifact Layout
- `out/strategy_search_compare/macd_extended_holdout_confirmation/baseline_summary.csv`
- `out/strategy_search_compare/macd_extended_holdout_confirmation/mixed_2x_summary.csv`
- `out/strategy_search_compare/macd_extended_holdout_confirmation/holdout_window_results.csv`
- `out/strategy_search_compare/macd_extended_holdout_confirmation/holdout_comparison.csv`
- `out/strategy_search_compare/macd_extended_holdout_confirmation/macd_extended_holdout_validation.md`

### Holdout-by-Holdout Results

| holdout | baseline return | baseline sharpe | baseline mdd | baseline pos symbols | baseline fee | mixed_2x return | mixed_2x sharpe | mixed_2x mdd | mixed_2x pos symbols | mixed_2x fee | coverage |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 60d | 0.0059 | 1.1465 | -0.0130 | 9 | 49.5346 | 0.0054 | 1.0303 | -0.0133 | 9 | 99.0857 | 0.4118 |
| 90d | 0.0014 | 0.1935 | -0.0138 | 6 | 70.6681 | 0.0007 | 0.0732 | -0.0141 | 6 | 141.3517 | 0.4061 |
| 120d | 0.0019 | 0.2162 | -0.0146 | 7 | 92.6873 | 0.0009 | 0.0934 | -0.0150 | 7 | 185.4005 | 0.3919 |

### Aggregate Summary
- baseline:
  - holdout success count: `3/3`
  - median return across holdouts: `0.0019`
  - median sharpe across holdouts: `0.2162`
  - majors mean return across holdouts: `0.0087`
  - alts mean return across holdouts: `0.0015`
- mixed_2x:
  - holdout success count: `3/3`
  - median return across holdouts: `0.0009`
  - median sharpe across holdouts: `0.0934`
  - majors mean return across holdouts: `0.0080`
  - alts mean return across holdouts: `0.0008`

### Main Research Answers
- does MACD stay positive beyond the original `120d` holdout?
  - yes
  - all three holdout windows stayed positive in baseline and in `mixed_2x`
- does it survive stronger costs?
  - yes, but with expected weakening
  - return and sharpe compress meaningfully from baseline to `mixed_2x`, especially in `90d` and `120d`
  - the key point is that they do not flip negative
- majors vs alts:
  - majors are clearly stronger and do most of the heavy lifting
  - alts are still positive on average across holdouts, so the branch is not purely a major-only artifact
- stability across windows:
  - strongest on `60d`
  - weaker but still positive on `90d` and `120d`
  - this is a moderate, not dominant, edge, but it is materially more stable than the earlier Donchian candidate
- operational-readiness question:
  - on current evidence, yes:
    - positive in `3/3` holdouts under baseline
    - positive in `3/3` holdouts under `mixed_2x`
    - positive-symbol breadth stays `6 - 9`
    - coverage remains around `0.39 - 0.41`

### Interpretation
- result status: `SUCCESS`
- final historical decision:
  - `MACD 유지`
  - `paper/testnet operational validation` 후보로 승격 가능
- caution:
  - this is still not a high-margin edge
  - the edge is major-led and weaker in longer holdouts
  - operational validation should therefore stay narrow and fixed-parameter, not reopen discovery logic

### Verification
- extended holdout runner:
  - `scripts/run_holdout_validation.py --mode extended-macd-confirmation` completed and wrote all requested outputs
- full suite:
  - `uv run --active pytest -q`: PASS

### Next Lever (1 only)
- `fixed MACD paper/testnet operational validation`
- reason:
  - the fixed MACD pocket has now survived sweep pruning, stress, head-to-head selection, single holdout, and extended holdout confirmation
  - the next useful question is execution stability, not more historical selection

## 2026-03-14 Fixed MACD Paper/Testnet Operational Validation

### Why This Step
- the historical research funnel is closed enough for now.
- the promoted candidate is fixed:
  - `macd @ 4h`
  - `fast=12`
  - `slow=26`
  - `signal=9`
  - tightened regime gating
- the goal here is runtime/execution validation only:
  - strategy-to-runtime wiring
  - parameter drift prevention
  - doctor/preflight
  - order/protective/state sync behavior

### Runtime Additions
- fixed runtime profile:
  - `macd_final_candidate`
- operational preset:
  - `config/presets/macd_final_candidate_ops.yaml`
- wrappers:
  - `scripts/run_macd_final_candidate_paper.ps1`
  - `scripts/run_macd_final_candidate_testnet.ps1`
- runtime safety fix:
  - live backfill signals are now suppressed before order handling, preventing stale bootstrap bars from placing testnet orders
- runtime wiring fix:
  - `trader run` now persists the actual fixed strategy params/profile instead of crashing on undefined `strategy_params`

### Commands Run
- doctor:
  - `uv run --active trader doctor --env testnet`
- paper:
  - `powershell -ExecutionPolicy Bypass -File scripts/run_macd_final_candidate_paper.ps1`
- testnet:
  - `powershell -ExecutionPolicy Bypass -File scripts/run_macd_final_candidate_testnet.ps1`
- tests:
  - `uv run --active pytest -q`

### Paper Result
- artifact root:
  - `out/operational_validation/macd_final_candidate_paper/`
- summary:
  - verdict: `FAIL`
  - fixed params OK: `true`
  - orders/fills/trades: `0/0/0`
  - halt reason: `volatility circuit breaker triggered`
- observed details:
  - `BTC/USDT` halted with `atr_pct=0.0646`
  - `ETH/USDT` halted with `atr_pct=0.0506`
  - `BNB/USDT` stayed active but remained `hold`
  - no order, no fill, and no protective-order lifecycle was exercised
- answer to the runtime questions:
  - fixed MACD params were injected without drift: yes
  - tightened regime was reflected in runtime state: yes
  - status/runtime_state persistence worked: yes
  - order/protective path was fully exercised: no

### Testnet Result
- artifact root:
  - `out/operational_validation/macd_final_candidate_testnet/`
- summary:
  - verdict: `FAIL`
  - fixed params OK: `true`
  - orders/fills/trades: `0/0/0`
  - halt reason: `preflight check failed`
- observed details:
  - `BTC/USDT` private preflight passed
  - `ETH/USDT` and `BNB/USDT` failed private preflight with Binance `-1021`
    - `Timestamp for this request was 1000ms ahead of the server's time.`
  - failing private endpoints included:
    - `fapiPrivateV2GetBalance`
    - `fapiPrivateV3GetPositionRisk`
  - runtime stdout also showed repeated user-stream disconnects:
    - `disconnected (no running event loop)`
- answer to the runtime questions:
  - fixed MACD params were injected without drift: yes
  - tightened regime was reflected in runtime state: yes
  - doctor/preflight was stable enough for live order validation: no
  - live order/protective/state sync path was exercised end to end: no

### Main Runtime Findings
- issue 1:
  - the new fixed profile path initially crashed because `strategy_params` was undefined in `trader run`
  - fixed in this step
- issue 2:
  - the validation wrapper initially misread multi-symbol runtime state and could attach an older run to the summary
  - fixed in this step
- issue 3:
  - volatility circuit breaker is currently binding too early for `BTC/USDT` and `ETH/USDT` on this 3-symbol paper validation
- issue 4:
  - live/testnet private preflight is not stable across all 3 symbols because of intermittent `-1021` timestamp-ahead failures
- issue 5:
  - user-stream startup still shows reconnect churn caused by `no running event loop`

### Decision
- result status: `PARTIAL`
- candidate decision:
  - keep `MACD` as the chosen operational candidate
- operational decision:
  - `3-symbol operational validation` is **not** passed yet
  - do **not** expand to 14 symbols
- reason:
  - candidate/profile wiring is now correct
  - but paper did not reach order-path validation and testnet did not clear stable preflight

### Verification
- runner smoke / fixed-param tests added
- `uv run --active pytest -q`: PASS

### Next Lever (1 only)
- stabilize `testnet preflight + runtime guards` and rerun the same fixed 3-symbol operational validation

## 2026-03-15 Fixed MACD Paper/Testnet Operational Validation Recovery Rerun

### Scope
- changed area: operational validation recovery only
- no new historical search
- no parameter retuning
- fixed candidate stayed frozen:
  - `macd @ 4h`
  - `fast=12`
  - `slow=26`
  - `signal=9`
  - tightened regime gating

### Recovery Read
- the interrupted working tree after `702b9c6` had already completed the important runtime wiring:
  - fixed strategy wrapper `macd_final_candidate`
  - preset `config/presets/macd_final_candidate_ops.yaml`
  - paper/testnet wrappers
  - CLI/runtime state persistence for fixed params/profile
- the remaining recovery work was:
  - rerun the wrappers sequentially to avoid mixed summaries
  - update the latest artifact record to reflect current runtime behavior
  - surface user-stream startup churn explicitly in the wrapper summary

### Wrapper Hardening
- `scripts/run_macd_final_candidate_validation.ps1` now scans stdout/stderr and writes:
  - `log_signals.user_stream_no_running_event_loop`
  - issue flag `user_stream_no_running_event_loop` when detected in live mode

### Commands Run
- `uv run --active pytest -q`
- `uv run --active trader doctor --env testnet`
- `powershell -ExecutionPolicy Bypass -File scripts/run_macd_final_candidate_paper.ps1`
- `powershell -ExecutionPolicy Bypass -File scripts/run_macd_final_candidate_testnet.ps1`

### Verification
- `pytest -q`: PASS
- `doctor --env testnet`: PASS

### Paper Result
- out dir:
  - `out/operational_validation/macd_final_candidate_paper/`
- run_id:
  - `9a98ced809544f1e84a6166bf3371ce2`
- verdict:
  - `FAIL`
- summary:
  - fixed params/regime injection: `PASS`
  - orders/fills/trades: `0/0/0`
  - halt reason: `volatility circuit breaker triggered`
- observed details:
  - `BTC/USDT` risk halt `atr_pct=0.06263718744039198`
  - `ETH/USDT` risk halt `atr_pct=0.053158928389347694`
  - `BNB/USDT` stayed active but remained `hold`

### Testnet Result
- out dir:
  - `out/operational_validation/macd_final_candidate_testnet/`
- run_id:
  - `5ca57ef900684e8d9e2895cd31ea7579`
- verdict:
  - `FAIL`
- summary:
  - fixed params/regime injection: `PASS`
  - private preflight across all 3 symbols: `PASS`
  - orders/fills/trades: `0/0/0`
  - halt reason: `volatility circuit breaker triggered`
- observed details:
  - `BTC/USDT` risk halt `atr_pct=0.06263718744039198`
  - `ETH/USDT` risk halt `atr_pct=0.053158928389347694`
  - `BNB/USDT` completed the full capped `240` bars and stayed `hold`
  - stdout still logged repeated `disconnected (no running event loop)` user-stream churn

### Decision
- result status:
  - `PARTIAL`
- candidate decision:
  - keep `MACD` as the fixed operational candidate
- operational decision:
  - `3-symbol operational validation` is still not passed
- current blockers:
  - volatility circuit breaker binds before entry/order-path validation
  - user-stream startup churn remains unresolved

### Next Lever (1 only)
- stabilize `volatility circuit breaker + user-stream event loop startup` and rerun the same fixed 3-symbol wrappers
## 2026-03-15 - Fixed MACD Execution Blocker Recovery Completion

### Scope
- changed area only: runtime / broker / validation wrappers
- no strategy retuning
- fixed candidate stayed frozen:
  - `macd @ 4h`
  - `fast=12`
  - `slow=26`
  - `signal=9`
  - tightened regime unchanged

### Root Cause Summary
- paper/testnet volatility blocker:
  - runtime `max_atr_pct=0.05` was too tight for the fixed 4h validation window
  - observed `atr_pct` around `0.053 - 0.063` on `BTC/USDT` and `ETH/USDT`, so runtime halted before entry
- user-stream failure:
  - `no running event loop` came from creating `aiohttp.ClientSession()` outside the loop in `trader/data/binance_user_stream.py`
- live execution path blocker:
  - live backfill suppression prevented the fixed 4h candidate from exercising a testnet order path during bootstrap bars
- protective order blocker:
  - live broker incorrectly waited for a terminal user-stream status even for fresh `STOP_MARKET` / `TAKE_PROFIT_MARKET` protective orders that should remain `NEW`

### Implementation
- moved user-stream websocket session creation fully inside the event loop
- close `aiohttp` session on failed websocket connect to stop `Unclosed client session` leakage
- added validation-only probe/override path in runtime:
  - force one controlled entry
  - create protective orders
  - force one controlled exit
- added validation-only live backfill execution allowance so the probe can run during capped bootstrap validation
- changed MACD validation wrappers to default `MaxBars=60`
  - reason: probe entry starts at bar `40`, so `60` bars is sufficient for entry -> protective -> exit -> cleanup
- kept ATR relaxation wrapper-scoped only:
  - `MAX_ATR_PCT=1.0`
- fixed live broker so protective trigger orders do not wait for terminal user-stream status on creation

### Commands Run
- `uv run --active pytest -q`
- `uv run --active trader doctor --env testnet`
- `powershell -ExecutionPolicy Bypass -File scripts/run_macd_final_candidate_paper.ps1`
- `powershell -ExecutionPolicy Bypass -File scripts/run_macd_final_candidate_testnet.ps1`

### Verification
- `pytest -q`: PASS (`63 passed`)
- `doctor --env testnet`: PASS

### Paper Result
- out dir:
  - `out/operational_validation/macd_final_candidate_paper/`
- run_id:
  - `02cb93cac04b4cc292efd8477a72d029`
- verdict:
  - `PASS`
- summary:
  - fixed params loaded: `true`
  - volatility breaker trigger count: `0`
  - user-stream no-running-event-loop count: `0`
  - orders/fills/trades: `15/6/3`
  - protective orders created: `3`
  - symbols halted: `0`

### Testnet Result
- out dir:
  - `out/operational_validation/macd_final_candidate_testnet/`
- run_id:
  - `34a511fc12dc4261b2db899c9ceaf97b`
- verdict:
  - `PASS`
- summary:
  - fixed params loaded: `true`
  - volatility breaker trigger count: `0`
  - user-stream no-running-event-loop count: `0`
  - orders/fills/trades: `18/0/3`
  - protective orders created: `3`
  - symbols halted: `0`
- note:
  - raw stdout still shows Binance testnet user-stream DNS reconnect churn (`Could not contact DNS servers`)
  - order/protective/state sync still completed despite that churn

### Decision
- result status:
  - `SUCCESS`
- operational decision:
  - `3-symbol operational validation` now passes for execution-path coverage
- remaining follow-up:
  - live/testnet `fills` remain `0` in DB because fill persistence still depends on user-stream delivery under DNS degradation

## 2026-03-15 Reconciliation Accounting Validation Completed

### Scope
- strategy remained frozen:
  - `macd_final_candidate`
  - `4h`
  - `fast=12`
  - `slow=26`
  - `signal=9`
  - tightened regime unchanged
- objective:
  - verify actual rerun recovery after reconciliation changes
  - confirm 3-symbol paper/testnet accounting consistency

### Additional Runtime/Broker Fixes Needed During Validation
- live order submission:
  - removed duplicate `clientOrderId` parameter usage and kept Binance futures `newClientOrderId`
- 3-symbol live preflight:
  - cached the futures permission check to avoid repeated `fapi/v2/balance` hits during symbol startup
- multi-symbol accounting:
  - made trade ids unique per symbol within a shared `run_id`
- wrapper summary:
  - switched summary counts to distinct `order_id` / `fill_id`
  - added `fills_accounted_count`
  - added `fills_reconciled_count`
  - added `fills_from_user_stream_count`
  - added `fills_from_rest_reconcile_count`
  - added `fills_from_aggregated_fallback_count`
  - added `partial_fills_count`
  - added `reconciled_missing_ws_fill_count`
  - added `trade_query_unavailable_count`
  - added `fill_provenance_breakdown`
  - added `partial_fill_audit_summary`
  - added `accounting_consistency_pass`
  - added user-stream disconnect / DNS reconnect counts

### Commands Run
- `uv run --active pytest -q`
- `uv run --active trader doctor --env testnet`
- `powershell -ExecutionPolicy Bypass -File scripts/run_macd_final_candidate_paper.ps1`
- `powershell -ExecutionPolicy Bypass -File scripts/run_macd_final_candidate_testnet.ps1`

### Final Usable Runs
- paper:
  - `run_id=185d99c7961f4681a49b8f18fe7442fa`
  - verdict `PASS`
  - orders/fills/trades `11/6/3`
  - `fills_accounted_count=6`
  - `fill_provenance_breakdown={"by_source":{"direct_runtime":6},"fills_reconciled_count":0,"fills_with_source_history_count":0}`
  - `accounting_consistency_pass=true`
  - protective orders created `3`
- testnet:
  - `run_id=392e3990ee3547ee8c30a98f7f0356b8`
  - verdict `PASS`
  - orders/fills/trades `12/8/3`
  - `fills_accounted_count=8`
  - `fills_reconciled_count=8`
  - `fills_from_rest_reconcile_count=8`
  - `fills_from_user_stream_count=0`
  - `fills_from_aggregated_fallback_count=0`
  - `partial_fills_count=4`
  - `reconciled_missing_ws_fill_count=8`
  - `trade_query_unavailable_count=0`
  - `fill_provenance_breakdown={"by_source":{"rest_trade_reconcile":8},"fills_reconciled_count":8,"fills_with_source_history_count":0}`
  - `partial_fill_audit_summary={"partial_fill_groups_count":2,"partial_fill_rows_count":4,"aggregated_fallback_fill_count":0,"reconciled_missing_ws_fill_count":8,"trade_query_unavailable_count":0,"fills_with_multiple_source_history_count":0}`
  - `accounting_consistency_pass=true`
  - protective orders created `3`
  - `user_stream_disconnect_count=14`
  - `user_stream_dns_reconnect_count=14`
  - `accounting_degraded_mode_used=true`

### Validation Read
- paper and testnet both completed with:
  - fixed params loaded
  - entry observed
  - protective orders created and canceled
  - exit observed
  - final state flat with open orders `0`
- latest testnet rerun proves the prior `DB fills=0` failure mode is no longer the live result under degraded user-stream conditions
- testnet fills were fully recovered through REST reconciliation on this run
- status/summary/DB now answer:
  - how many fills were WS vs REST vs aggregate fallback
  - which fills were partial groups
  - whether missing user-stream delivery was recovered by reconciliation

### Residual Risk
- aggregate fallback still exists as a degraded-path precision limit when trade queries are unavailable
- classification:
  - no blocker for the current fixed-candidate paper/testnet operational validation gate
  - fallback precision loss is now observable instead of silent

### Next Single Step
- keep using the same fixed candidate and validation wrapper
- if a future degraded testnet rerun shows `fills_from_aggregated_fallback_count > 0`, inspect the affected fill rows and decide whether higher-fidelity post-run trade hydration is worth the extra complexity
  - aggregated fallback fills
