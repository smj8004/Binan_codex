# Experiment Log

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
