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
