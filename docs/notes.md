# Notes

Date: 2026-03-08

## Current behavior

- `mode=live` and `LIVE_TRADING=true` are separate controls.
- Live order submission is rejected when `LIVE_TRADING=false`.
- Current runtime sizing/risk checks use internal equity, not exchange available balance.
- Testnet endpoints are consistently wired to futures testnet hosts.

## Implementation guardrails

- No mainnet live trading path.
- Reuse existing architecture in `trader/runtime.py`, `trader/broker/*`, and `trader/config.py`.
- Keep strategy logic unchanged; scope is operations/broker/budget layers.
- Always emit structured skip reason (`insufficient_budget`) for observability.

## Test notes

- Insufficient budget test must prove `place_order` is never called.
- Sufficient budget test must prove:
  - entry order is sent
  - normal fill path remains
  - protective-order creation path remains

## Useful commands

- `uv run --active pytest -q`
- `uv run --active trader doctor --env testnet`
- `uv run --active trader run --mode live --env testnet --data-mode websocket --symbols BTC/USDT --timeframe 1m --strategy ema_cross --max-bars 10 --halt-on-error --yes-i-understand-live-risk`
