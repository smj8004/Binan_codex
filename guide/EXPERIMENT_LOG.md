# Experiment Log

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

