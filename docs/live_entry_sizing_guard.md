# Live Entry Sizing Guard (Testnet/Live)

This document defines the execution-path sizing constraints added without changing `ema_cross` strategy rules.

## Definitions

- `max_position_notional_usdt` (cap): `4000.0`
- `min_entry_notional_usdt` (hard floor): `250.0` (default)

## Scope

- Hard floor applies only to non-reduce-only entries (`reduce_only=False`).
- Reduce-only orders (manual exits, SL/TP protective orders, emergency close) always bypass the floor.
- Strategy entry/exit signal rules are unchanged.

## Intent

- Reduce frequent tiny-entry skips and exchange rejects caused by under-sized notionals.
- Avoid excessive micro-fills and fee churn from very small entry sizes.

## Runtime behavior order

For non-reduce-only orders:

1. Strategy target notional/qty.
2. Risk-based sizing clamp.
3. Hard floor check (`min_entry_notional_usdt`).
4. Exchange filter guard/rounding (`minNotional/minQty/stepSize/tickSize`).
5. Broker order placement.

When blocked by floor:

- No order is sent.
- Event `entry_notional_below_floor` is recorded.
- Diagnostics update:
  - `min_entry_notional_block_count`
  - `min_entry_notional_block_samples` (last 5)
