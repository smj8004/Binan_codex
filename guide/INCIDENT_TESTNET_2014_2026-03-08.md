# Incident: Binance testnet `-2014` key format invalid (2026-03-08)

## Summary

- Symptom: `uv run --active trader doctor --env testnet` failed.
- Error: `{"code":-2014,"msg":"API-key format invalid."}`
- Scope: private futures permission endpoint call (`/fapi/v2/balance`) failed, while public checks passed.

## Masked diagnostics snapshot

- env: `testnet`
- env_file_used: project root `.env`
- key_source: `BINANCE_TESTNET_API_KEY`
- key_source_origin: `process_env`
- key_len: `13`
- key_prefix: `(tes`
- secret_source: `BINANCE_TESTNET_API_SECRET`
- secret_source_origin: `process_env`
- secret_len: `16`
- has_whitespace: `False`
- contains_newline: `False`
- looks_like_hmac: `unknown`

## Confirmed cause on this machine

The currently loaded credentials came from process environment overrides, not from root `.env`.
Those override values are malformed/placeholder-grade, so Binance rejected them with `-2014`.

Evidence:
- doctor reports `key_source_origin=process_env` and `secret_source_origin=process_env`.
- `key_len=13` and `secret_len=16` are far shorter than normal Binance API credentials.
- `key_prefix=(tes` suggests placeholder-like injected value.
- doctor successfully reached public endpoints (`server_time_sync`, `symbol_filters`), isolating failure to private auth payload.

## Top-3 common causes of `-2014`

1. Placeholder or malformed key/secret in `.env` (wrong length/characters, quoted text, copied label).
2. Wrong credential family (spot/demo key instead of USD-M futures testnet key).
3. Process environment overrides (`BINANCE_TESTNET_API_KEY/SECRET`) shadow root `.env`.

## Recovery checklist

1. Re-issue Binance Futures testnet API key/secret.
2. Clear process env overrides before running:
   - PowerShell: `Remove-Item Env:BINANCE_TESTNET_API_KEY -ErrorAction SilentlyContinue`
   - PowerShell: `Remove-Item Env:BINANCE_TESTNET_API_SECRET -ErrorAction SilentlyContinue`
3. Save only these two lines in root `.env` without quotes:
   - `BINANCE_TESTNET_API_KEY=...`
   - `BINANCE_TESTNET_API_SECRET=...`
4. Re-run:
   - `uv run --active trader doctor --env testnet`
5. Confirm doctor diagnostics show:
   - `key_source=BINANCE_TESTNET_API_KEY`
   - `key_source_origin=merged_defaults` (or expected source)
   - realistic key/secret lengths
   - no whitespace/newline warnings
   - final status `Doctor passed`

## Validation snapshots (same day)

- Negative check (forced bad process env):
  - `key_source_origin=process_env`
  - `key_len=6`, `secret_len=9`
  - endpoint auth failed with `-2014`
  - doctor printed dedicated `Doctor Hint -2014` table
- Positive check (process overrides cleared):
  - `key_source_origin=merged_defaults`
  - `key_len=64`, `secret_len=64`
  - auth/time/symbol checks all `yes`
  - final: `Doctor passed. No orders were sent.`
