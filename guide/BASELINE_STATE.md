# Baseline State

## Last Updated
- date: 2026-03-05
- experiment: ATR shock threshold A/B (`2.5` vs `2.7`) with all other fixed values unchanged
- comparison file: `out/experiments/atr_shock_threshold_ab_20260305_131735/baseline_vs_variant.csv`

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
- lever name: `atr_shock_threshold` (ATR shock trigger threshold only)
- tested candidates: `A=2.5`, `B=2.7` (fixed: `testnet=False`, `k=4`, `rank_buffer=2`, `lookback_score_mode=median_3`, `shock_mode=downweight`, `shock_freeze_min_fraction=0.40`, `shock_cooldown_bars=48`, `extreme_no_trade=ON`, `extreme_high_vol_percentile=0.90`, `extreme_non_trend_logic=OR`, `trend_slope_threshold=0.0015`, `extreme_regime_mode=delever`, `extreme_gross_mult=0.5`)
- implementation location: `trader/experiments/runner.py` (`_build_extreme_no_trade_map`, `_simulate_portfolio`)

## Current Recommendation
- recommended rank_buffer: `2` (unchanged)
- recommended k: `4` (keep)
- recommended lookback_score_mode: `median_3` (keep)
- recommended lookback_bars for `median_3`: `168` (7d base => 7/14/28)
- recommended shock_cooldown_bars: `48` (update from 72)
- recommended atr_shock_threshold: `2.7` (update from 2.5)
- recommended extreme_no_trade: `ON` (unchanged)
- recommended extreme_high_vol_percentile: `0.90` (keep)
- recommended extreme_non_trend_logic: `OR` (keep)
- recommended trend_slope_threshold: `0.0015` (keep)
- recommended extreme_regime_mode: `delever` (use `extreme_gross_mult=0.5`)
- recommended extreme_gross_mult: `0.5` (keep)
- selected run id (baseline anchor, unchanged): `portfolio_20260303_142053_4c986473`
- reason (rule-based): both runs passed hard gate, and `net_pnl` was within 5% (`13274.72 -> 13917.81`), so tie-break used MDD where `2.7` was less severe (`-0.157939 -> -0.156239`); therefore `atr_shock_threshold=2.7` is selected.
- selected run id unchanged reason: baseline anchor is kept fixed for reproducibility; this round compares only `atr_shock_threshold` while keeping all fixed values unchanged (`data_source=binance`, `testnet=False`, `k=4`, `rank_buffer=2`, `lookback_score_mode=median_3`, `shock_cooldown_bars=48`, shock/extreme stack fixed).
