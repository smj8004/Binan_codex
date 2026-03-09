# Plan: Demo-Visible Live Forward Trading on Testnet with Budget Guard

Date: 2026-03-08
Status: Draft (waiting for `approved`)

## Problem statement

- `paper` mode never sends exchange orders, so Binance Demo Futures UI cannot show account changes.
- `live` mode can still reject all orders when `LIVE_TRADING=false`.
- There is no pre-order check against exchange account `available balance`.
- Goal: testnet-only live-forward flow (no mainnet), with mandatory budget check before order submission.

## Proposed approach

1. Live mode must be testnet-only
- Add a hard safety gate in CLI run path: reject `--mode live` unless env is `testnet`.
- Keep current futures testnet endpoint path in broker/data layers.

2. Add pre-order account budget guard (default ON)
- Introduce an account budget guard (name flexible) that pulls `available balance` from broker.
- Run this guard right before order submission for new entry/reverse-entry orders.
- If insufficient:
  - do not call `broker.place_order`
  - emit event/status reason `insufficient_budget`
  - skip that order only (no full runtime halt)

3. Multi-symbol consistency
- Reuse current single broker shared by engines.
- In orchestrator single consumer loop, maintain a per-bar account budget snapshot.
- After each accepted order, reserve budget in snapshot immediately (optimistic reserve) to avoid over-allocation on same bar.

4. Protective-order behavior
- Keep TP/SL protective flow intact.
- Reduce-only protective orders should not be blocked by entry-budget guard (or use a separate permissive policy), to avoid disabling risk protection.

5. Rollback switch
- Add `--no-budget-guard` option, default ON.

## File-level change list

1. `trader/runtime.py`
- Add pre-order budget-guard hook in `_place_order`.
- Record `insufficient_budget` events and skip reason.
- Add budget snapshot/reservation handling compatible with multi-symbol run loop.

2. `trader/broker/live_binance.py`
- Add normalized account budget snapshot reader (available balance and related fields).
- Keep existing `place_order/create_order` flow unchanged.

3. `trader/config.py`
- Add budget-guard config default and parsing.

4. `trader/cli.py`
- Add `--budget-guard/--no-budget-guard`.
- Enforce testnet-only live execution.
- Pass guard option into runtime config.

5. `tests/test_budget_guard.py`
- Case 1: insufficient budget -> no order submission (broker spy/mock).
- Case 2: sufficient budget -> order submission proceeds and protective-order flow remains.

6. `tests/test_live_testnet_order_path_smoke.py`
- Minimal smoke around live testnet order path plus budget guard integration.

7. Docs
- Update `README.md` with demo UI live-forward commands.
- Update `guide/BASELINE_STATE.md` and `guide/EXPERIMENT_LOG.md` with change log and verification gate records.

## Rollback

- `--no-budget-guard` available; default remains ON.
- No rollback path to mainnet live; live remains testnet-only by policy.

## Verification

1. `uv run --active pytest -q`
2. `uv run --active trader doctor --env testnet`
3. 1-symbol live testnet smoke (5-10 minutes)
4. 3-symbol live testnet smoke (30-60 minutes)
5. Demo UI checks:
- Positions update
- Open Orders update
- Assets/balance update
- Insufficient budget path logs `insufficient_budget` and sends no order

## Command set to include in README/guide

- 1 symbol, 10 minutes:
  - `uv run --active trader run --mode live --env testnet --data-mode websocket --symbols BTC/USDT --timeframe 1m --strategy ema_cross --max-bars 10 --halt-on-error --yes-i-understand-live-risk`
- 3 symbols, 60 minutes:
  - `uv run --active trader run --mode live --env testnet --data-mode websocket --symbols BTC/USDT,ETH/USDT,BNB/USDT --timeframe 1m --strategy ema_cross --max-bars 60 --halt-on-error --yes-i-understand-live-risk`
