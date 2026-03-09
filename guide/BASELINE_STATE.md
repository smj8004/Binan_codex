# Baseline State

## 2026-03-08 - Live Testnet Runtime Safety Baseline

- scope: operational runtime hardening (not strategy parameter tuning)
- enforcement:
  - live execution is testnet-only (`--mode live` with non-testnet env is rejected)
  - pre-order account budget guard defaults to ON (`BUDGET_GUARD_ENABLED=true`)
- expected behavior:
  - on insufficient available balance, entry order is skipped
  - reason `insufficient_budget` is recorded in events/runtime state
  - protective reduce-only orders remain in normal flow when budget is sufficient
- gate snapshot (2026-03-08):
  - `uv run --active pytest -q` -> PASS (`22 passed`)
  - `uv run --active trader doctor --env testnet` -> PASS (masked diagnostics show `key_source_origin=merged_defaults`)
  - negative doctor check with injected bad key/secret -> FAIL with clear `-2014` hint and `key_source_origin=process_env`
- live-forward evidence (testnet/demo only):
  - `run_id=91471b8aa4e74d578cbd9add56580e8d` (3 symbols, 60 bars, `LIVE_TRADING=true`, `LEVERAGE=20`)
  - DB orders/fills confirm real submissions:
    - `BNB/USDT` market short `filled` + `STOP_MARKET`/`TAKE_PROFIT_MARKET` protective orders `new`
    - `ETH/USDT` market short `filled` + `STOP_MARKET`/`TAKE_PROFIT_MARKET` protective orders `new`
  - runtime logs show `open_protective=2` after entries
- demo UI validation commands:
  - `uv run --active trader run --mode live --env testnet --data-mode websocket --symbols BTC/USDT --timeframe 1m --strategy ema_cross --max-bars 10 --halt-on-error --yes-i-understand-live-risk`
  - `uv run --active trader run --mode live --env testnet --data-mode websocket --symbols BTC/USDT,ETH/USDT,BNB/USDT --timeframe 1m --strategy ema_cross --max-bars 60 --halt-on-error --yes-i-understand-live-risk`
  - account alignment required on this machine: set `LEVERAGE=20` (or adjust exchange-side leverage to match config)
  - real order submission required for UI visibility: set `LIVE_TRADING=true`
  - note: testnet/demo only, mainnet live is blocked by runtime CLI guard
- 2h wall-clock baseline status:
  - codex in-session attempt (`run_id=5db848d01bec4045b4b74b153489decb`) recorded only ~9 minutes due environment runtime cap (~10 minutes per long command)
  - full 2h unattended verification should be executed via `scripts/run_live_forward_2h.ps1` on a normal terminal

## Last Updated
- date: 2026-03-05
- experiment: shock cooldown bars A/B (`48` vs `36`) with all other fixed values unchanged
- comparison file: `out/experiments/shock_cooldown_48_vs_36_ab_20260305_142036/baseline_vs_variant.csv`

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
- lever name: `shock_cooldown_bars` (shock freeze cooldown bars only)
- tested candidates: `A=48`, `B=36` (fixed: `testnet=False`, `k=4`, `rank_buffer=2`, `lookback_score_mode=median_3`, `shock_mode=downweight`, `shock_freeze_min_fraction=0.40`, `atr_shock_threshold=2.7`, `gap_shock_threshold=0.12`, `shock_weight_mult_gap=0.15`, `shock_weight_mult_atr=0.30`, `extreme_no_trade=ON`, `extreme_high_vol_percentile=0.90`, `extreme_non_trend_logic=OR`, `trend_slope_threshold=0.0015`, `extreme_regime_mode=delever`, `extreme_gross_mult=0.5`)
- implementation location: `trader/experiments/runner.py` (`_build_extreme_no_trade_map`, `_simulate_portfolio`)

## Current Recommendation
- recommended rank_buffer: `2` (unchanged)
- recommended k: `4` (keep)
- recommended lookback_score_mode: `median_3` (keep)
- recommended lookback_bars for `median_3`: `168` (7d base => 7/14/28)
- recommended shock_cooldown_bars: `48` (update from 72)
- recommended atr_shock_threshold: `2.7` (update from 2.5)
- recommended gap_shock_threshold: `0.12` (update from 0.10)
- recommended shock_weight_mult_gap: `0.15` (keep)
- recommended shock_weight_mult_atr: `0.30` (keep)
- recommended extreme_no_trade: `ON` (unchanged)
- recommended extreme_high_vol_percentile: `0.90` (keep)
- recommended extreme_non_trend_logic: `OR` (keep)
- recommended trend_slope_threshold: `0.0015` (keep)
- recommended extreme_regime_mode: `delever` (use `extreme_gross_mult=0.5`)
- recommended extreme_gross_mult: `0.5` (keep)
- selected run id (baseline anchor, unchanged): `portfolio_20260303_142053_4c986473`
- reason (rule-based): both runs passed hard gate, but all decision metrics were identical (`net_pnl=13880.39`, `max_drawdown=-0.156128`, `fee=989.49`), so baseline `shock_cooldown_bars=48` is retained.
- selected run id unchanged reason: baseline anchor is kept fixed for reproducibility; this round compares only `shock_cooldown_bars` while keeping all fixed values unchanged (`data_source=binance`, `testnet=False`, `k=4`, `rank_buffer=2`, `lookback_score_mode=median_3`, `atr_shock_threshold=2.7`, `gap_shock_threshold=0.12`, `shock_weight_mult_gap=0.15`, `shock_weight_mult_atr=0.30`, shock/extreme stack fixed).
