# Incident: Binance testnet `-2015` (2026-03-08)

## Summary
- Symptom: `uv run --active trader doctor --env testnet` fails at `futures_permission`.
- Error: `{"code":-2015,"msg":"Invalid API-key, IP, or permissions for action"}`
- Scope: private Futures endpoint only (`GET /fapi/v2/balance`); public endpoints are healthy.

## Reproduction
1. `set TRADER_LOG_LEVEL=DEBUG` (or PowerShell `$env:TRADER_LOG_LEVEL='DEBUG'`)
2. Run: `uv run --active trader doctor --env testnet`
3. Repeat loop (5 tries):
   `for($i=1; $i -le 5; $i++){ uv run --active trader doctor --env testnet; Start-Sleep 2 }`

Observed stable pattern across retries:
- `server_time_sync`: YES (drift ~1.7s)
- `symbol_filters`: YES (`BTC/USDT` exchange info OK)
- `futures_permission`: NO (`-2015`, method `fapiPrivateV2GetBalance`)
- Cross-check evidence: `uv run --active trader doctor --env mainnet` passes on the same key pair.

## Root Cause Analysis
- Most likely external causes (Binance-side):
  - Testnet/mainnet key mismatch (highest confidence; mainnet doctor passes while testnet doctor fails)
  - API key IP whitelist mismatch
  - Futures permission disabled on that key
  - Stale key/secret pair
- Local config cause contributing to mistakes:
  - Prior code only consumed `BINANCE_API_KEY/SECRET`, making testnet/mainnet key separation easy to misconfigure.

## Fix Applied In Repo
- Added env-specific key selection in `AppConfig.from_env()`:
  - `testnet`: `BINANCE_TESTNET_API_KEY/SECRET` first, fallback to `BINANCE_API_KEY/SECRET`
  - `mainnet`: `BINANCE_MAINNET_API_KEY/SECRET` first, fallback to `BINANCE_API_KEY/SECRET`
- Updated `.env.example` with dedicated key variables.
- Updated `README.md` with key precedence guidance.
- Added regression tests for key precedence:
  - `tests/test_config_key_selection.py`

## External Actions Required (Binance Console)
1. Verify testnet key pair is generated from Binance Futures testnet.
2. If IP restriction is ON, add current public IP: `222.98.77.168`.
3. Ensure Futures permission is enabled for the key.
4. Re-run: `uv run --active trader doctor --env testnet`.

## Prevention
- Keep separate credentials:
  - `BINANCE_TESTNET_API_KEY/SECRET`
  - `BINANCE_MAINNET_API_KEY/SECRET`
- Leave generic `BINANCE_API_KEY/SECRET` empty unless intentionally used as fallback.
- Run `doctor --env testnet` before any websocket paper daemon/session.
