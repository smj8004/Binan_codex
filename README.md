# binance-trader

Python 3.11+ Binance USDT-M trading toolkit with:
- Backtest / Optimize / Replay
- Runtime `paper` / `live`
- SQLite persistence for runs/orders/fills/trades/events/runtime state

## Install

```bash
uv sync
cp .env.example .env
```

Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

Note:
- Runtime now auto-loads `.env` (and falls back to `.env.example` if `.env` is missing).

## Project Structure

```text
trader/
  cli.py
  config.py
  runtime.py
  storage.py
  data/
  broker/
  backtest/
  strategy/
  risk/
tests/
scripts/
```

## Environment

Important:
- `LIVE_TRADING=false` by default
- `BINANCE_ENV=testnet` by default (recommended)

Core live/runtime env:

```env
LIVE_TRADING=false
BINANCE_ENV=testnet
USE_USER_STREAM=true
LISTENKEY_RENEW_SECS=1800
API_ERROR_HALT_THRESHOLD=3
PREFLIGHT_MAX_TIME_DRIFT_MS=5000
REQUIRE_PROTECTIVE_ORDERS=true
PROTECTIVE_MISSING_POLICY=halt
```

`BINANCE_ENV` values:
- `testnet`: Binance Futures testnet REST/WS
- `mainnet`: Binance Futures mainnet REST/WS

API key selection precedence:
- When `BINANCE_ENV=testnet`:
  `BINANCE_TESTNET_API_KEY/SECRET` -> fallback `BINANCE_API_KEY/SECRET`
- When `BINANCE_ENV=mainnet`:
  `BINANCE_MAINNET_API_KEY/SECRET` -> fallback `BINANCE_API_KEY/SECRET`
- Recommended: set testnet and mainnet keys separately to avoid `-2015` mix-ups.

## Backtest

PowerShell/Windows ?섍꼍?먯꽌??媛?곹솚寃??쇱꽑??以꾩씠湲??꾪빐 `uv run --active ...` ?뺥깭瑜?沅뚯옣?⑸땲??

```bash
uv run trader backtest --symbol BTC/USDT --timeframe 1h --limit 500
```

## Paper Example

Windows 沅뚯옣:

```powershell
uv run --active trader run --mode paper --symbol BTC/USDT --timeframe 1m --strategy ema_cross --max-bars 200
```

硫???щ낵(?숈떆 媛먯떆/二쇰Ц):

```powershell
uv run --active trader run --mode paper --data-mode websocket --symbols BTC/USDT,ETH/USDT --timeframe 1m --strategy ema_cross --max-bars 200
```

```bash
uv run trader run --mode paper --symbol BTC/USDT --timeframe 1m --strategy ema_cross --max-bars 200
```

Auto protective orders:

```bash
uv run trader run --mode paper --symbol BTC/USDT --timeframe 1m \
  --auto-protective --run-stop-loss-pct 0.01 --run-take-profit-pct 0.02
```

## Optimize / Walk-forward / Replay

```bash
uv run trader optimize --strategy ema_cross --symbols BTC/USDT,ETH/USDT --timeframe 1h \
  --start 2023-01-01 --end 2025-01-01 \
  --search grid --grid config/grids/ema_cross.yaml \
  --metric sharpe_like --top 20 --export out/opt_results.csv
```

```bash
uv run trader optimize --strategy ema_cross --symbols BTC/USDT --timeframe 1h \
  --start 2021-01-01 --end 2025-01-01 \
  --walk-forward --train-days 180 --test-days 60 --top-per-train 10 \
  --metric sharpe_like --export out/wfo.csv
```

```bash
uv run trader replay --run-id <id> --export out/replay/
uv run trader replay --from-opt out/opt_results.csv --top 20 --export out/replay_report.csv
```

## Edge Validation Experiments (Cost / Walk-forward / Regime)

Run the unified scientific validation suite:

```bash
uv run trader experiments \
  --suite all \
  --symbol BTC/USDT \
  --timeframe 15m \
  --start 2023-01-01 --end 2025-01-01 \
  --strategy ema_cross \
  --walk-grid config/grids/ema_cross.yaml \
  --seed 42
```

Quick smoke run with synthetic data (offline):

```bash
uv run trader experiments \
  --suite all \
  --data-source synthetic \
  --symbol BTC/USDT \
  --timeframe 1h \
  --start 2025-01-01 --end 2025-02-01 \
  --strategy ema_cross \
  --walk-grid config/grids/ema_cross.yaml \
  --seed 123
```

Cost stress only:

```bash
uv run trader experiments \
  --suite cost \
  --symbol BTC/USDT \
  --timeframe 5m \
  --start 2024-01-01 --end 2025-01-01 \
  --fee-multipliers 1.0,1.5,2.0,3.0 \
  --fixed-slippage-bps 1,3,5,10 \
  --atr-slippage-mults 0.02,0.05,0.1,0.2 \
  --latency-bars 0,1,3 \
  --order-models market,limit
```

Output per run is saved under `out/experiments/<run_id>/`:
- `config.json`: exact run config (reproducible)
- `summary.csv`, `summary.json`: verdict and key metrics
- `cost_stress.csv`, `cost_sensitivity.csv`
- `walk_forward_windows.csv`, `walk_forward_candidates.csv`
- `regime_scenarios.csv`, `regime_table.csv`
- `report.md`
- `plots/*.png` (cost sensitivity, walk-forward distribution, regime performance)

Core interpretation:
- `NO EDGE`: out-of-sample win ratio low and/or performance collapses under realistic costs
- `UNCERTAIN`: mixed out-of-sample stability, requires narrower hypothesis and re-test
- `HAS EDGE`: out-of-sample consistency + cost robustness + regime selectivity are all positive

## Portfolio Cross-Section Suite (Dollar-neutral Long/Short)

Run multi-symbol cross-sectional portfolio validation:

```bash
uv run trader experiments \
  --suite portfolio \
  --symbols BTC/USDT,ETH/USDT,BNB/USDT,SOL/USDT,XRP/USDT,ADA/USDT,DOGE/USDT,AVAX/USDT,LINK/USDT,TRX/USDT \
  --timeframe 1h \
  --start 2021-01-01 --end 2026-01-01 \
  --lookbacks 7d,14d,28d \
  --rebalance 4h,1d \
  --k 3,4 \
  --gross 1.0,1.5 \
  --signal-models momentum,mean_reversion \
  --rank-buffer 0,1 \
  --walk-train-days 240 --walk-test-days 60 --walk-step-days 30 \
  --walk-top-pct 0.15 --walk-max-candidates 120 \
  --fee-multipliers 1.0,1.5,2.0,3.0 \
  --latency-bars 0,1,3 \
  --slippage-mode mixed \
  --fixed-slippage-bps 3 \
  --atr-slippage-mults 0.05 \
  --order-models market,limit \
  --high-vol-gross-mult 0.5 \
  --turnover-threshold-low-vol 0.05 \
  --turnover-threshold-high-vol 0.20 \
  --cap-mode adaptive \
  --base-cap 0.25 --cap-min 0.20 --cap-max 0.40 \
  --backlog-thresholds 0.25,0.50,0.75 \
  --cap-steps 0.25,0.30,0.35,0.40 \
  --high-vol-cap-max 0.30 \
  --dd-controller \
  --dd-thresholds 0.10,0.20,0.30,0.40 \
  --dd-gross-mults 1.0,0.7,0.5,0.3,0.0 \
  --dd-recover-thresholds 0.08,0.16,0.24,0.32 \
  --kill-cooldown-bars 168 \
  --disable-new-entry-when-dd \
  --enable-liquidation \
  --equity-floor-ratio 0.01 \
  --trading-halt-bars 168 \
  --skip-trades-if-cost-exceeds-equity-ratio 0.02 \
  --transition-smoother \
  --gross-step-up 0.10 \
  --gross-step-down 0.25 \
  --post-halt-cooldown-bars 168 \
  --post-halt-max-gross 0.15 \
  --liquidation-lookback-bars 720 \
  --liquidation-lookback-max-gross 0.15 \
  --max-turnover-notional-to-equity 0.25 \
  --drift-threshold 0.35 \
  --gross-decay-steps 3 \
  --debug-mode \
  --seed 42
```

Portfolio outputs are saved under `out/experiments/<portfolio_run_id>/`:
- `report.md`, `summary.csv`, `summary.json`
- `diagnostics.json` (rebalance_attempts/execs, skip reasons, safety events)
- `debug_dump.json` (equity crash/anomaly 吏곸쟾 ?대깽??
- `portfolio_equity_curve.csv`
- `dd_timeline.csv` (timestamp/equity/peak/drawdown/dd_stage/effective_gross)
- `gross_target_vs_applied.csv` (target gross vs applied gross transition series)
- `portfolio_positions.csv` (timestamp/symbol weights and holdings)
- `turnover.csv`
- `liquidation_events.csv` (liquidation timestamp, equity before/after, costs, reason)
- `rate_limit_comparison.csv` (same config cap off vs cap on comparison)
- `cost_breakdown.csv`
- `cost_stress.csv`, `cost_sensitivity.csv`
- `walk_forward_windows.csv`, `walk_forward_candidates.csv`
- `regime_scenarios.csv`, `regime_table.csv`
- `plots/*.png`

Metric definitions:
- `rebalance_attempt_count`: warmup ?댄썑 ?ㅼ?以꾩긽 由щ갭?곗뒪 "寃?? ?잛닔
- `rebalance_exec_count`: ?ㅼ젣 二쇰Ц/?ъ???蹂寃쎌씠 諛쒖깮???잛닔
- `avg_turnover_ratio`: executions 湲곗? ?됯퇏 turnover
- `avg_turnover_ratio_attempts`: attempts 湲곗? ?됯퇏 turnover
- `turnover_cap_notional`: rebalance 1?뚮떦 ?덉슜 turnover notional 罹?(`-1` means cap off)
- `turnover_executed_fraction`: 紐⑺몴 turnover ?鍮??ㅼ젣 吏묓뻾 鍮꾩쑉
- `backlog_notional`: cap/鍮꾩슜 李⑤떒?쇰줈 誘몄쭛?됰맂 紐⑺몴 ?붾웾 notional
- `backlog_ratio`: `backlog_notional / equity`
- `cap_used`: ?대떦 由щ갭?곗뒪 諛붿뿉???ㅼ젣 ?ъ슜??cap 媛?- `diagnostics.cap_histogram`: cap ?ъ슜 鍮덈룄 遺꾪룷
- `diagnostics.dd_trigger_counts`: DD stage 吏꾩엯 ?잛닔
- `diagnostics.time_in_dd_stage`: DD stage 泥대쪟 bar ??
Liquidation diagnostics:
- `diagnostics.liquidation_events`: liquidation count + first timestamp
- `diagnostics.negative_equity_cause_counts`: root-cause counts (`fee/slippage/penalty/price_gap/gross_transition/backlog_execution`)
- `liquidation_events.csv`: per-event details (`ts,equity_before,equity_after,gross,dd_stage,regime,turnover_notional,fee,slippage,penalty,reason`)
- `transition_smoother_comparison.csv`: smoother off vs on (`liquidation_count`, `gross_transition_cause_count`, `max_drawdown`, `fee_cost_total`)
## Candidate Systems Batch (Track A/B/C)

Run 3 system candidates with hard-gate evaluation:

```bash
uv run trader system-batch \
  --symbols BTC/USDT,ETH/USDT,SOL/USDT \
  --timeframe 1h \
  --start 2021-01-01 --end 2026-01-01 \
  --seed 42
```

Output:
- `out/experiments/<batch_run_id>/batch_summary.csv`
- `out/experiments/<batch_run_id>/<candidate_id>/report.md`
- candidate蹂?`symbols/<symbol>/<run_id>/` ?섏쐞??`report.md`, `summary.csv/json`, `plots/*` ?앹꽦

Candidate definitions:
- [guide/SYSTEM_CANDIDATES.md](/mnt/c/Users/smjan/Desktop/code/Binance_codex/guide/SYSTEM_CANDIDATES.md)

## Live Runtime (Safety First)

Live mode requires explicit flag:

```bash
uv run trader run --mode live --symbol BTC/USDT --timeframe 1m \
  --strategy ema_cross --params-from <run_id> \
  --yes-i-understand-live-risk
```

Recommended first step:

```bash
uv run trader run --mode live --dry-run --symbol BTC/USDT --timeframe 1m \
  --strategy ema_cross --params-from <run_id> \
  --yes-i-understand-live-risk
```

硫???щ낵 ?쒕씪?대윴:

```powershell
$env:BINANCE_ENV="mainnet"
$env:LIVE_TRADING="true"
uv run --active trader run --mode live --dry-run --data-mode websocket --symbols BTC/USDT,ETH/USDT --timeframe 1m --strategy ema_cross --yes-i-understand-live-risk
```

Data source:

```bash
uv run trader run --mode live --data-mode rest --symbol BTC/USDT --timeframe 1m --yes-i-understand-live-risk
uv run trader run --mode live --data-mode websocket --symbol BTC/USDT --timeframe 1m --yes-i-understand-live-risk
```

Production options:

```bash
uv run trader run --mode live --halt-on-error --symbol BTC/USDT --timeframe 1m --yes-i-understand-live-risk
uv run trader run --mode live --one-shot --symbol BTC/USDT --timeframe 1m --yes-i-understand-live-risk
uv run trader run --mode live --resume --resume-run-id <id> --symbol BTC/USDT --timeframe 1m --yes-i-understand-live-risk
```

## Sleep Mode ?댁쁺 媛?대뱶

Sleep Mode??臾닿컧???먭??? ?섍꼍?먯꽌 ?섏씡蹂대떎 怨꾩쥖 ?앹〈???곗꽑?섎룄濡??ㅺ퀎??蹂댁닔???⑦궎吏?낅땲??

沅뚯옣 ?④퀎:
1. `paper` 2二?2. `testnet` 1二?3. `mainnet` ?뚯븸

沅뚯옣 湲곕낯媛?
- 諛곕텇(`ACCOUNT_ALLOCATION_PCT`) 10~20%
- ?덈쾭由ъ?(`LEVERAGE`) 1~2
- ?쇱넀???쒕룄(`DAILY_LOSS_LIMIT_PCT`) 1~2%
- 理쒕? ?숉룺(`MAX_DRAWDOWN_PCT`) 5~10%

?꾨━??
- `config/presets/sleep_mode.yaml`
- `config/presets/conservative.yaml`
- `config/presets/aggressive.yaml` (寃쎄퀬?? 湲곕낯 鍮꾪솢??沅뚯옣)

?꾨━???곸슜:

```bash
uv run trader arm-sleep --preset sleep_mode
uv run trader run --mode paper --sleep-mode --symbol BTC/USDT --timeframe 1m --max-bars 200
uv run trader run --mode live --dry-run --sleep-mode --env testnet --symbol BTC/USDT --timeframe 1m --yes-i-understand-live-risk
```

二쇱쓽:
- `LIVE_TRADING`? ?먮룞?쇰줈 `true`濡?諛붾뚯? ?딆뒿?덈떎.
- ?ㅼ＜臾몄? `LIVE_TRADING=true` + `--yes-i-understand-live-risk`???뚮쭔 媛?ν빀?덈떎.

?덈? ?쇳빐?????ㅼ젙:
- `LEVERAGE > 2`瑜?臾닿컧?쒕줈 ?댁쁺
- `DAILY_LOSS_LIMIT_PCT > 2%`
- `ACCOUNT_ALLOCATION_PCT > 30%`
- `LIVE_TRADING=true` + `BINANCE_ENV=mainnet`瑜??ъ쟾 寃利??놁씠 諛붾줈 ?ъ슜

## Pre-flight Checks (Live Start)

At live runtime start, the engine performs preflight checks and halts on failure:
- API key/secret presence and futures account access
- server time drift check
- symbol tradability + filters load (`tickSize/stepSize/minNotional` when available)
- leverage/margin-mode alignment check (when endpoint available)

Preflight also runs with `--dry-run`.

Preflight now stores separated event rows in SQLite:
- `preflight_environment`: `BINANCE_ENV`, `base_url`, `ws_url`
- `preflight_credentials`: key presence + key length only (no key value output)
- `preflight_endpoint`: called endpoint and HTTP status
- `preflight_auth_guidance`: detailed hints when Binance error code is `-2015`

If `-2015` is detected, guidance includes:
- possible testnet/mainnet key mix-up
- possible IP whitelist restriction
- possible Futures permission not enabled
- possible API key/secret mismatch

## Doctor Command

Run pre-trade diagnostics only (no order send):

```bash
uv run trader doctor --env testnet
uv run trader doctor --env mainnet
```

`doctor` checks only:
- account authentication
- server time sync
- symbol filters

## Status Command

Check runtime status directly from SQLite:

```bash
uv run trader status --latest
uv run trader status --run-id <id>
```

Shows:
- position / open orders / last bar / halted reason
- trades/orders/fills counts and net PnL
- recent events and recent error events

## Backtest DB Inspect Script

```bash
uv run python scripts/inspect_backtest.py --latest
uv run python scripts/inspect_backtest.py --run-id <id> --export-csv out/
```

## Order Types and Protective Orders

Supported futures order types in broker/runtime:
- `MARKET`
- `LIMIT`
- `STOP_MARKET`
- `TAKE_PROFIT_MARKET`
- `reduce_only`

Protective orders are strongly recommended for live:
- Runtime can auto-create SL/TP (both `reduce_only=true`) after entry
- When one protective order fills, paired order is canceled
- If position exists but protective orders are missing, runtime can `halt` (default) or `recreate`

## Testnet Setup Notes

1. Keep `BINANCE_ENV=testnet` and `LIVE_TRADING=false` initially.
2. Validate strategy and risk behavior for at least 1-2 weeks in paper/testnet.
3. Confirm user-stream updates and DB persistence before mainnet.
4. Switch to `BINANCE_ENV=mainnet` only after checks pass.

## Operational Checklist (10 lines)

1. Start with `--dry-run` and verify preflight/events in DB.
2. Keep `BINANCE_ENV=testnet` until full checklist is passed.
3. Enable `--halt-on-error` for unattended live sessions.
4. Confirm `runtime_state` snapshots are updating.
5. Confirm user-stream keepalive/reconnect events are healthy.
6. Verify protective SL/TP are present for every open position.
7. Set conservative limits (`MAX_POSITION_NOTIONAL`, `MAX_DAILY_LOSS`, `MAX_DRAWDOWN_PCT`).
8. Configure Telegram/Discord alerts and test halt notifications.
9. Use `trader status --latest` during operations and after restart.
10. Move to mainnet with minimal notional first, then scale gradually.

