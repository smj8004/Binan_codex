# Baseline State

## Last Updated
- date: 2026-03-03
- experiment: rank_buffer sweep (`rank_buffer` in {0, 1, 2})
- comparison file: `out/experiments/rank_buffer_sweep_20260303_141548/rank_buffer_sweep_comparison.csv`

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
- lever name: `rank_buffer` (ranking hysteresis)
- tested candidates: `0`, `1`, `2`
- implementation location: `trader/experiments/runner.py` (`_portfolio_target_weights`)

## Current Recommendation
- recommended rank_buffer: `2`
- selected run id: `portfolio_20260303_142053_4c986473`
- reason (rule-based): hard gate passed; highest `net_pnl` among candidates; lower turnover and fee than `0/1`.
