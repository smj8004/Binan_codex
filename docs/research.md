# Research: Live-Forward Testnet Trading and Budget Guard

Date: 2026-03-08
Scope: Phase A (read-only)

## 1) Where `--mode paper/live` splits

- CLI `run` enforces mode `paper|live`: `trader/cli.py:899`, `trader/cli.py:933`.
- Runtime receives mode via `RuntimeConfig(mode=mode)`: `trader/cli.py:985-986`.
- Broker choice in CLI:
  - `paper` -> `PaperBroker(...)`: `trader/cli.py:1066`.
  - `live` -> `LiveBinanceBroker(...)`: `trader/cli.py:1075`.
- Runtime preflight is only executed for live mode:
  - `RuntimeEngine.start_session()` -> `if self.config.mode == "live": self._run_preflight_checks()`: `trader/runtime.py:1330-1331`.

Important gap:
- `mode=live` does not guarantee real order submission.
- `LiveBinanceBroker.place_order()` rejects when `LIVE_TRADING=false`: `trader/broker/live_binance.py:1217-1221`.

## 2) Testnet futures endpoint and demo UI linkage (`demo-fapi` / env split)

Env split:
- `_is_testnet(cfg)` uses `cfg.binance_env == "testnet"`: `trader/cli.py:94-95`.
- `BINANCE_ENV` parsing and env-specific key selection:
  - parse: `trader/config.py:324-328`
  - key precedence: `trader/config.py:332-341`.

Testnet endpoint wiring:
- Live broker maps futures REST to `https://testnet.binancefuture.com`: `trader/broker/live_binance.py:86-95`.
- Live broker maps futures WS to `wss://stream.binancefuture.com/ws`: `trader/broker/live_binance.py:110-112`.
- Data client uses same testnet futures base: `trader/data/binance.py:20-29`.
- Market data WS uses `stream.binancefuture.com`: `trader/data/binance_live.py:320-323`.
- User stream listenKey base also uses testnet futures host: `trader/data/binance_user_stream.py:24`, `trader/data/binance_user_stream.py:90`.

Finding:
- No explicit `demo-fapi` string/domain exists in repo code.
- The code consistently targets Binance USD-M futures testnet hosts. Demo UI visibility therefore depends on successful testnet account trading with matching keys/account context.

## 3) Where orders are actually sent (`create_order` / `place_order`)

Call chain:
- Strategy signal handler calls runtime `_place_order(...)`: `trader/runtime.py:1052-1088`.
- Runtime submits through broker interface: `self.broker.place_order(req)`: `trader/runtime.py:768`.
- Live broker entrypoint: `LiveBinanceBroker.place_order()`: `trader/broker/live_binance.py:1213`.
- Exchange call: `_retry_create_order(...)` -> `self.exchange.create_order(...)`: `trader/broker/live_binance.py:731`, `trader/broker/live_binance.py:745`.

## 4) Current sizing inputs (budget/allocation/fixed notional/leverage)

Base size:
- Entry qty defaults to `fixed_notional_usdt / price`: `trader/runtime.py:662-663`.

Pre-order risk clamp:
- Runtime uses `risk_guard.suggest_entry_notional(...)` before non-reduce-only entry: `trader/runtime.py:696`.
- Risk guard applies:
  - `max_order_notional`
  - `max_position_notional`
  - `account_allocation_pct` and optional `capital_limit_usdt`
  - `risk_per_trade_pct` (optionally with SL distance)
  - daily loss limit
  - logic: `trader/risk/guards.py:28-84`.

Important gap:
- This is based on runtime internal equity (`cash + unrealized`) and not exchange account available margin:
  - runtime equity: `trader/runtime.py:354`.
- `LiveBinanceBroker.get_balance()` currently returns only `total` balance map (no normalized available-margin field):
  - `trader/broker/live_binance.py:1337-1340`.

## 5) Where protective orders (TP/SL) are created/canceled

Create:
- `_maybe_create_protective_orders()` creates reduce-only `STOP_MARKET`/`TAKE_PROFIT_MARKET`: `trader/runtime.py:843-918`.

Cancel:
- `_cancel_open_order()`: `trader/runtime.py:815-837`.
- `_cancel_all_protective_orders()`: `trader/runtime.py:838-841`.

Integrity policy:
- `_enforce_protective_integrity()` checks required protective kinds and applies `halt` or `recreate`: `trader/runtime.py:483-516`.

Protective fill handling:
- `_handle_trigger_fills_from_broker()` processes trigger fills (if broker supports polling), updates position, and cancels paired order: `trader/runtime.py:996-1041`.

## Additional implementation-relevant gaps

- No pre-order exchange `available balance` guard exists today.
- In multi-symbol run, all engines share one broker instance (`trader/cli.py:1103`), and bars are processed by a single consumer loop (`trader/runtime.py:1565-1632`), but there is no account-level pre-order budget snapshot/reservation layer.
