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

## Backtest

PowerShell/Windows 환경에서는 가상환경 혼선을 줄이기 위해 `uv run --active ...` 형태를 권장합니다.

```bash
uv run trader backtest --symbol BTC/USDT --timeframe 1h --limit 500
```

## Paper Example

Windows 권장:

```powershell
uv run --active trader run --mode paper --symbol BTC/USDT --timeframe 1m --strategy ema_cross --max-bars 200
```

멀티 심볼(동시 감시/주문):

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
  --debug-mode \
  --seed 42
```

Portfolio outputs are saved under `out/experiments/<portfolio_run_id>/`:
- `report.md`, `summary.csv`, `summary.json`
- `diagnostics.json` (rebalance_attempts/execs, skip reasons, safety events)
- `debug_dump.json` (equity crash/anomaly 직전 이벤트)
- `portfolio_equity_curve.csv`
- `portfolio_positions.csv` (timestamp/symbol weights and holdings)
- `turnover.csv`
- `cost_breakdown.csv`
- `cost_stress.csv`, `cost_sensitivity.csv`
- `walk_forward_windows.csv`, `walk_forward_candidates.csv`
- `regime_scenarios.csv`, `regime_table.csv`
- `plots/*.png`

Metric definitions:
- `rebalance_attempt_count`: warmup 이후 스케줄상 리밸런스 "검토" 횟수
- `rebalance_exec_count`: 실제 주문/포지션 변경이 발생한 횟수
- `avg_turnover_ratio`: executions 기준 평균 turnover
- `avg_turnover_ratio_attempts`: attempts 기준 평균 turnover

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
- candidate별 `symbols/<symbol>/<run_id>/` 하위에 `report.md`, `summary.csv/json`, `plots/*` 생성

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

멀티 심볼 드라이런:

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

## Sleep Mode 운영 가이드

Sleep Mode는 무감시(자가동) 환경에서 수익보다 계좌 생존을 우선하도록 설계된 보수적 패키지입니다.

권장 단계:
1. `paper` 2주
2. `testnet` 1주
3. `mainnet` 소액

권장 기본값:
- 배분(`ACCOUNT_ALLOCATION_PCT`) 10~20%
- 레버리지(`LEVERAGE`) 1~2
- 일손실 한도(`DAILY_LOSS_LIMIT_PCT`) 1~2%
- 최대 낙폭(`MAX_DRAWDOWN_PCT`) 5~10%

프리셋:
- `config/presets/sleep_mode.yaml`
- `config/presets/conservative.yaml`
- `config/presets/aggressive.yaml` (경고용, 기본 비활성 권장)

프리셋 적용:

```bash
uv run trader arm-sleep --preset sleep_mode
uv run trader run --mode paper --sleep-mode --symbol BTC/USDT --timeframe 1m --max-bars 200
uv run trader run --mode live --dry-run --sleep-mode --env testnet --symbol BTC/USDT --timeframe 1m --yes-i-understand-live-risk
```

주의:
- `LIVE_TRADING`은 자동으로 `true`로 바뀌지 않습니다.
- 실주문은 `LIVE_TRADING=true` + `--yes-i-understand-live-risk`일 때만 가능합니다.

절대 피해야 할 설정:
- `LEVERAGE > 2`를 무감시로 운영
- `DAILY_LOSS_LIMIT_PCT > 2%`
- `ACCOUNT_ALLOCATION_PCT > 30%`
- `LIVE_TRADING=true` + `BINANCE_ENV=mainnet`를 사전 검증 없이 바로 사용

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
