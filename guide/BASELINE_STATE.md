# Baseline State

## Last Updated
- date: 2026-03-05
- experiment: shock weight mult gap A/B (`0.10` vs `0.15`) with all other fixed values unchanged
- comparison file: `out/experiments/shock_weight_mult_gap_ab_20260305_134817/baseline_vs_variant.csv`

## Fixed Configuration (Do Not Change)
- period: `2021-01-01` to `2026-01-01`
- timeframe: `1h`
- rebalance: `1d` (`rebalance_bars=24`)
- data source: `binance` (mainnet historical, `testnet=False`)
- symbols: `BTC/USDT, ETH/USDT, BNB/USDT, SOL/USDT, XRP/USDT, ADA/USDT, DOGE/USDT, AVAX/USDT, LINK/USDT, TRX/USDT`
- signal: `momentum`
- lookback score mode: `median_3` on `(7d,14d,28d)`
- k: `4`
- shock mode: `downweight` (`atr_mult=0.25`, `gap_mult=0.10`)
- shock freeze rebalance: `ON`
- shock freeze min fraction: `0.40`
- transition smoother: `ON`
- churn gate and cooldown overlays: `ON` (unchanged)
- account hardening and liquidation model: `ON`
- cost model: unchanged (same baseline fee/slippage/latency/order settings)

## Lever Under Test
- lever name: `shock_weight_mult_gap` (gap shock downweight multiplier only)
- tested candidates: `A=0.10`, `B=0.15` (fixed: `testnet=False`, `k=4`, `rank_buffer=2`, `lookback_score_mode=median_3`, `shock_mode=downweight`, `shock_freeze_min_fraction=0.40`, `shock_cooldown_bars=48`, `atr_shock_threshold=2.7`, `gap_shock_threshold=0.12`, `extreme_no_trade=ON`, `extreme_high_vol_percentile=0.90`, `extreme_non_trend_logic=OR`, `trend_slope_threshold=0.0015`, `extreme_regime_mode=delever`, `extreme_gross_mult=0.5`)
- implementation location: `trader/experiments/runner.py` (`_build_extreme_no_trade_map`, `_simulate_portfolio`)

## Current Recommendation
- recommended rank_buffer: `2` (unchanged)
- recommended k: `4` (keep)
- recommended lookback_score_mode: `median_3` (keep)
- recommended lookback_bars for `median_3`: `168` (7d base => 7/14/28)
- recommended shock_cooldown_bars: `48` (update from 72)
- recommended atr_shock_threshold: `2.7` (update from 2.5)
- recommended gap_shock_threshold: `0.12` (update from 0.10)
- recommended shock_weight_mult_gap: `0.15` (candidate winner; fixed config section remains unchanged)
- recommended extreme_no_trade: `ON` (unchanged)
- recommended extreme_high_vol_percentile: `0.90` (keep)
- recommended extreme_non_trend_logic: `OR` (keep)
- recommended trend_slope_threshold: `0.0015` (keep)
- recommended extreme_regime_mode: `delever` (use `extreme_gross_mult=0.5`)
- recommended extreme_gross_mult: `0.5` (keep)
- selected run id (baseline anchor, unchanged): `portfolio_20260303_142053_4c986473`
- reason (rule-based): both runs passed hard gate, and `net_pnl` was within 5% (`13778.12 -> 13799.00`), so tie-break used MDD where `0.15` was marginally less severe (effectively tied at `-0.156239`); therefore `shock_weight_mult_gap=0.15` is selected.
- selected run id unchanged reason: baseline anchor is kept fixed for reproducibility; this round compares only `shock_weight_mult_gap` while keeping all fixed values unchanged (`data_source=binance`, `testnet=False`, `k=4`, `rank_buffer=2`, `lookback_score_mode=median_3`, `shock_cooldown_bars=48`, `atr_shock_threshold=2.7`, `gap_shock_threshold=0.12`, shock/extreme stack fixed).
