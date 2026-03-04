# Baseline State

## Last Updated
- date: 2026-03-05
- experiment: k A/B (`k=4` vs `k=5`) with extreme/delever stack fixed
- comparison file: `out/experiments/extreme_no_trade_ab_20260304_150839/baseline_vs_variant.csv`

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
- lever name: `k` (portfolio top-k)
- tested candidates: `A=4`, `B=5` (fixed: `rank_buffer=2`, `lookback_score_mode=median_3`, `extreme_no_trade=ON`, `extreme_high_vol_percentile=0.90`, `extreme_non_trend_logic=OR`, `trend_slope_threshold=0.0015`, `extreme_regime_mode=delever`, `extreme_gross_mult=0.5`)
- implementation location: `trader/experiments/runner.py` (`_build_extreme_no_trade_map`, `_simulate_portfolio`)

## Current Recommendation
- recommended rank_buffer: `2` (unchanged)
- recommended k: `4` (keep)
- recommended extreme_no_trade: `ON` (unchanged)
- recommended extreme_high_vol_percentile: `0.90` (keep)
- recommended extreme_non_trend_logic: `OR` (keep)
- recommended trend_slope_threshold: `0.0015` (keep)
- recommended extreme_regime_mode: `delever` (use `extreme_gross_mult=0.5`)
- recommended extreme_gross_mult: `0.5` (keep)
- selected run id (baseline anchor, unchanged): `portfolio_20260303_142053_4c986473`
- reason (rule-based): both passed hard gate, but `k=5` degraded `net_pnl` (`13192.04 -> 5293.63`) despite better MDD (`-0.164491 -> -0.145177`), so `k=4` is finalized.
- selected run id unchanged reason: baseline anchor is kept fixed for reproducibility; this round compares only `k` (`4` vs `5`) while keeping all other fixed values unchanged (`data_source=binance`, `testnet=False`, `rank_buffer=2`, `lookback_score_mode=median_3`, extreme/delever stack fixed).
