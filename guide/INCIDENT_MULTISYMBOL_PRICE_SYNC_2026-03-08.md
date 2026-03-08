# Incident: Multi-Symbol Price/State Sync in Paper Websocket (2026-03-08)

## Symptoms
- In multi-symbol paper websocket (`BTC/USDT,ETH/USDT,BNB/USDT`), ETH/BNB `entry_price` could appear in BTC price range.
- Some symbols created a position but did not always retain required protective orders (SL/TP) reliably under concurrent fills.
- Account drawdown stop could be triggered by corrupted cross-symbol state, not by normal strategy losses.

## Reproduction
1. Run with shared account and multi-symbol websocket:
   `uv run --active trader run --mode paper --env testnet --data-mode websocket --symbols BTC/USDT,ETH/USDT,BNB/USDT --timeframe 1m --strategy ema_cross --max-bars 60 --halt-on-error --feed-stall-seconds 90 --bar-staleness-halt --bar-staleness-halt-seconds 120 --api-error-halt-threshold 3`
2. Observe execution/protective events and per-symbol status.

## Root Cause (Confirmed)
- `RuntimeEngine` generated `client_order_id` without symbol context.
- Under multi-symbol + shared `run_id`, different symbols could emit identical `client_order_id` in the same timestamp/intent window.
- `PaperBroker` deduplication reused the first matching order result, causing cross-symbol fill/entry state contamination.
- Secondary impact: trigger fill polling was global (`poll_filled_orders()`), so one engine could consume another symbol's trigger events.

## Fix
- [`trader/runtime.py`](C:/Users/smjan/Desktop/code/Binance_codex/trader/runtime.py)
  - `_make_client_order_id()` now includes symbol: `run_id:symbol:timestamp:intent`.
  - Added event-scoped execution snapshots (`fill_applied`, `protective_orders_created`, trigger fill handling) with:
    - `symbol`, `price_source`, `last_price`, `entry_price`, `position_qty`
    - protective order list (`order_id`, `type`, `price`, `reduce_only`, `status`)
  - Trigger-fill polling now uses symbol-scoped polling when supported.
- [`trader/broker/paper.py`](C:/Users/smjan/Desktop/code/Binance_codex/trader/broker/paper.py)
  - `poll_filled_orders(symbol=None)` supports symbol filtering and preserves unmatched events in queue.
- [`trader/data/binance_live.py`](C:/Users/smjan/Desktop/code/Binance_codex/trader/data/binance_live.py)
  - Added websocket payload symbol mismatch guard (`ws_symbol_mismatch`) to drop mismatched bar payloads.

## Tests Added
- [`tests/test_multisymbol_runtime_sync.py`](C:/Users/smjan/Desktop/code/Binance_codex/tests/test_multisymbol_runtime_sync.py)
  - `test_poll_filled_orders_symbol_scoped`:
    - validates per-symbol trigger fill polling isolation.
  - `test_multisymbol_entry_price_protective_and_bnb_bars`:
    - validates per-symbol entry price sanity,
    - validates protective orders exist for symbols with open positions,
    - validates BNB receives bars (`processed_bars > 0`).

## Validation Results
- Unit/regression tests: `uv run --active pytest -q` -> `8 passed`.
- Multi-symbol websocket run (max-bars=60): completed, `halted=False`, all symbols `processed_bars=60`.
- Multi-symbol websocket run (max-bars=360): completed, `halted=False`, all symbols `processed_bars=360`, per-symbol entry/protective state 정상.

## Recurrence Prevention
- Keep per-symbol identity in all order/fill state keys and IDs.
- Preserve symbol isolation in async event queues/pollers.
- Keep regression coverage for:
  - entry-price sanity by symbol,
  - per-symbol protective order guarantees,
  - non-zero bars on each subscribed symbol.
