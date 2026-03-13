# Notes

Date: 2026-03-11

## Guardrails

- **Historical evaluation must use real Binance USDT-M Futures mainnet candles only.**
- Testnet/demo candle data is out of scope for strategy discovery.
- Live/testnet execution code path is for operational validation only, not strategy edge discovery.
- Strategy ranking must not be based on in-sample performance alone (OOS-first ranking is mandatory).
- If results are weak or negative, keep them in the report (transparency over wishful thinking).

## Repo Direction Clarity (2026-03-14)

**This repo serves two distinct objectives:**

1. **Strategy Discovery** (primary objective)
   - Use real historical Binance USDT-M Futures data
   - Walk-forward OOS evaluation with realistic fees and slippage
   - Multi-family broad sweep to find edge
   - Output: ranked strategies by OOS performance with hard-gate flags
   - Location: `trader/research/`, `scripts/run_strategy_search.py`

2. **Operational Validation** (secondary objective, only after strategy discovery)
   - Testnet/live-forward execution to verify order flow, budget guards, protective orders
   - Does NOT prove strategy edge (use historical research for that)
   - Output: runtime stability metrics, order/fill logs, budget diagnostics
   - Location: `trader/runtime.py`, `trader/broker/`, `scripts/run_live_forward_*.ps1`

**Critical distinction:**
- Testnet/live demo data may differ from mainnet historical data
- Long-duration testnet runs prove operational stability, not strategy profitability
- Strategy selection MUST be based on historical OOS results, not live demo performance

## Implementation notes

- Existing `trader/data/futures_data.py` is a useful reference, but this task wants a narrower file layout and a simpler entry point.
- Existing `trader/backtest/engine.py` already covers the right fee/slippage execution semantics.
- Existing `trader/optimize.py` and `trader/experiments/runner.py` provide useful ranking/export patterns, but the new workflow should avoid their broader complexity.
- `EMA`, `Donchian`, and `RSI mean-reversion` should be compared under one common runner and one common cost model.
- Use local saved candle files as the source of truth for strategy search; do not fetch live candles inside the search loop.

## Walk-forward defaults

- train window: `180` days
- test window: `60` days
- step: `60` days
- default timeframe: `1h`

## Cost-model defaults to wire explicitly

- order type: `MARKET`
- fee side: taker
- slippage: fixed bps
- round-trip cost is implicit through entry fee/slippage plus exit fee/slippage

## Output expectations

- `out/strategy_search/summary.csv`
- `out/strategy_search/by_symbol.csv`
- `out/strategy_search/top_strategies.md`

## Repro commands to support

- `uv run --active python scripts/fetch_futures_historical.py --symbols BTCUSDT ETHUSDT XRPUSDT TRXUSDT ADAUSDT SOLUSDT --interval 1h --days 365`
- `uv run --active python scripts/run_strategy_search.py --symbols BTCUSDT ETHUSDT XRPUSDT TRXUSDT ADAUSDT SOLUSDT --interval 1h`
- `uv run --active python scripts/run_strategy_search.py --symbols BTCUSDT ETHUSDT XRPUSDT TRXUSDT ADAUSDT SOLUSDT --interval 1h --strategies donchian_breakout donchian_breakout_adx`

## 2026-03-12 Donchian + ADX follow-up

- `donchian_breakout` stayed the next candidate because the first 1-year `1h` walk-forward search ranked it highest among the three simple baselines even though all failed the hard gate.
- The added lever is only an `ADX` regime filter on entry. Breakout/exit logic, symbols, interval, fee, slippage, train/test split, and historical futures data source remain unchanged.
- Variant summary vs baseline on the same 6-symbol OOS run:
  - `oos_total_return_mean`: `-0.0230 -> -0.0168`
  - `oos_sharpe_mean`: `-1.3444 -> -1.1932`
  - `oos_max_drawdown_mean`: `-0.0380 -> -0.0323`
  - `trade_count_mean`: `44.0 -> 36.5`
  - `fee_cost_total`: `262.0895 -> 216.5757`
  - positive symbols: `1/6 -> 2/6`
  - symbol return std: `0.0273 -> 0.0205`
- Interpretation:
  - `donchian_breakout_adx` is less bad than baseline and reduces drawdown dispersion, but both strategies still fail the OOS hard gate and keep negative mean OOS return.
- Next single lever:
  - keep `donchian_breakout_adx` fixed and test only the timeframe lever (`1h -> 4h`) to see whether the filter works better on less noisy trend structure.

## 2026-03-12 Donchian + ADX timeframe follow-up

- `4h` was the next lever because the `1h` `donchian_breakout_adx` branch improved over raw Donchian but still looked noisy: too many trades, still negative OOS mean return, and no hard-gate pass.
- This experiment changed only the timeframe from `1h` to `4h`. Strategy logic, `ADX` filter, symbols, cost model, and walk-forward structure stayed fixed.
- Comparison on the same latest 1-year Binance USDT-M history:
  - `oos_total_return_mean`: `-0.0160 -> -0.0129`
  - `oos_sharpe_mean`: `-1.0283 -> -0.9962`
  - `oos_max_drawdown_mean`: `-0.0316 -> -0.0254`
  - `trade_count_mean`: `39.5 -> 10.7`
  - `fee_cost_total`: `234.2254 -> 62.9543`
  - positive symbols: `2/6 -> 0/6`
  - `symbol_return_std`: `0.0208 -> 0.0093`
  - `hard_gate_count`: `3/5 -> 2/5`
- Interpretation:
  - `4h` captured cleaner, lower-dispersion trades and reduced cost drag substantially.
  - But the gains were not broad enough across symbols; aggregate OOS return is still negative, positive-symbol count fell to zero, and hard-gate distance worsened.
- Next single lever:
  - keep `donchian_breakout_adx @ 4h` fixed and change only the symbol universe lever by dropping the weakest tail symbol first (`SOLUSDT`) to test whether the edge is concentrated rather than broad.

## 2026-03-12 Donchian + ADX 4h universe follow-up

- `SOLUSDT` was the exclusion candidate because it was the weakest symbol in the prior `4h` run:
  - `oos_total_return=-0.0297`
  - `oos_sharpe=-1.2627`
  - `oos_max_drawdown=-0.0519`
  - `trade_count=19`
  - it contributed the worst loss while not adding any positive-symbol support
- This experiment changed only the symbol universe:
  - baseline: `BTCUSDT ETHUSDT XRPUSDT TRXUSDT ADAUSDT SOLUSDT`
  - variant: `BTCUSDT ETHUSDT XRPUSDT TRXUSDT ADAUSDT`
- With SOL vs without SOL on `donchian_breakout_adx @ 4h`:
  - `oos_total_return_mean`: `-0.0129 -> -0.0096`
  - `oos_sharpe_mean`: `-0.9962 -> -0.9428`
  - `oos_max_drawdown_mean`: `-0.0254 -> -0.0201`
  - `trade_count_mean`: `10.7 -> 9.0`
  - `fee_cost_total`: `62.9543 -> 44.5202`
  - positive symbols: `0/6 -> 0/5`
  - `symbol_return_std`: `0.0093 -> 0.0060`
  - `hard_gate_count`: `2/5 -> 2/5`
- Interpretation:
  - removing `SOLUSDT` improved average quality metrics and reduced dispersion/cost drag.
  - but robustness did not materially improve because positive symbols stayed at zero and hard-gate progress was flat.
- Next single lever:
  - keep `donchian_breakout_adx @ 4h` and the reduced universe fixed, then change only the exit-speed lever by tightening `exit_period` around the winning branch to test whether losses can be cut faster without changing entries.

## 2026-03-12 Broad Sweep Discovery Run

- broad sweep is the right next step because the sequential single-lever runs improved the least-bad branch but still produced no OOS hard-gate winner.
- this run stays historical-data-first:
  - real Binance USDT-M Futures candles only
  - no live/testnet candles
  - same fee and slippage model across every family
  - rolling OOS ranking, not in-sample ranking
- included families:
  - `ema_cross`
  - `donchian_breakout`
  - `supertrend`
  - `price_adx_breakout`
  - `rsi_mean_reversion`
  - `bollinger`
  - `macd`
  - `stoch_rsi`
- matrix scale:
  - raw definitions: `234` combos
  - executed default cap: `96` combos
  - intervals: `1h`, `4h`
  - symbols: `6`
  - estimated backtests: `6912`
  - observed runtime: about `8.25` minutes with `jobs=8`
- actual result snapshot:
  - hard-gate pass count: `0`
  - best overall candidate: `donchian_breakout @ 4h`
  - best family candidates in rank order:
    - `donchian_breakout @ 4h`: `oos_total_return_mean=-0.0035`, `oos_sharpe_mean=-0.5540`
    - `ema_cross @ 4h`: `oos_total_return_mean=-0.0003`, `oos_sharpe_mean=-0.2491`
    - `price_adx_breakout @ 1h`: `oos_total_return_mean=-0.0002`, `oos_sharpe_mean=-0.1241`
    - `macd @ 4h`: `oos_total_return_mean=-0.0081`, `oos_sharpe_mean=-0.2471`
    - `stoch_rsi @ 4h`: `oos_total_return_mean=-0.0078`, `oos_sharpe_mean=-0.8326`
- interpretation:
  - `4h` trend-following branches still dominate the top of the table, even though none cleared the hard gate.
  - mean-reversion families were broadly weaker on this 1-year fee-inclusive futures set.
  - the sweep is useful because it narrowed the follow-up space to a smaller set of less-bad families instead of continuing blind parameter tweaking.
- next single lever:
  - keep the winning `donchian_breakout @ 4h` branch fixed and add only an `ADX` regime filter around the `entry_period=40`, `exit_period=5` pocket.
