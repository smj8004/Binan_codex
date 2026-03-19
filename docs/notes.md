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

## 2026-03-14 Expanded Universe Broad Sweep (6 vs 15 symbols)

- why this was the next lever:
  - the repo is now explicitly historical-data-first, and the open question after the earlier broad sweep was whether the lack of hard-gate winners came from weak strategy families or from a universe that was too narrow.
  - this follow-up changed only the symbol universe. Strategy families, cost model, intervals (`1h`, `4h`), walk-forward windows, ranking logic, and hard-gate logic stayed fixed.
- baseline universe:
  - `BTCUSDT ETHUSDT XRPUSDT TRXUSDT ADAUSDT SOLUSDT`
- expanded universe:
  - `BTCUSDT ETHUSDT BNBUSDT SOLUSDT XRPUSDT ADAUSDT DOGEUSDT TRXUSDT AVAXUSDT LINKUSDT DOTUSDT LTCUSDT ATOMUSDT UNIUSDT MATICUSDT`
- data notes:
  - `1h` and `4h` sync reruns returned `fetched_rows=0` for all populated files, confirming merge/reuse without duplicate rows.
  - `MATICUSDT` returned `rows=0` on both `1h` and `4h` for the latest 1-year Binance futures window. The symbol was kept in the variant universe as specified and recorded as a zero-window member rather than replaced.
- current rerun result snapshot on `2026-03-14`:

| metric | universe_6 | universe_15 |
|---|---:|---:|
| hard_gate_pass_count | 1 | 0 |
| best family | `ema_cross @ 4h` | `ema_cross @ 4h` |
| best OOS total return mean | 0.0009 | -0.0003 |
| best OOS sharpe mean | 0.3663 | 0.0177 |
| best OOS max drawdown mean | -0.0003 | -0.0021 |
| best positive symbols | 1 | 2 |
| best symbol return std | 0.0021 | 0.0028 |

- interpretation:
  - expanding to 15 symbols did **not** produce a new hard-gate winner; it actually removed the single winner that appeared in the refreshed 6-symbol rerun.
  - breadth improved in some places, but edge quality did not:
    - best-candidate positive symbols: `1 -> 2`
    - best-candidate fee cost total: `2.0330 -> 7.9888`
    - best-candidate OOS return mean: `0.0009 -> -0.0003`
    - best-candidate OOS sharpe mean: `0.3663 -> 0.0177`
  - major vs alt on the top candidate:
    - `universe_15` major basket (`BTC/ETH/BNB`) mean OOS return: `-0.0009`
    - `universe_15` alt basket mean OOS return: `-0.0001`
    - the only positive symbols on the top variant candidate were `TRXUSDT` and `AVAXUSDT`, so any residual edge looks more alt-concentrated than major-concentrated.
  - family-level read:
    - most improved on wider universe: `rsi_mean_reversion`, `bollinger`, `supertrend`
    - but all three still remained below hard gate and still kept negative best-candidate OOS return after fees
    - high-turnover families such as `macd` and `stoch_rsi` gained positive-symbol breadth but mostly translated the wider universe into much larger fee totals without enough return improvement
- next single lever:
  - `15m` interval broad sweep
  - rationale: the expanded universe did not unlock robust edge, so the next clean test should keep the 15-symbol universe and family set fixed and change only timeframe resolution.

## 2026-03-14 15m Broad Sweep (14 symbols, MATIC excluded)

- why `15m` was the next lever:
  - the 14-symbol `1h/4h` rerun had `0` hard-gate winners and the prior 15-symbol expansion already failed to improve best return / best sharpe.
  - the next clean question was whether a shorter timeframe could reveal edge that `1h/4h` missed.
- why `MATICUSDT` was excluded:
  - the latest 1-year Binance USDT-M futures sync returned `0 rows` for `MATICUSDT` on both `1h` and `4h`.
  - this experiment therefore fixed the universe at 14 populated symbols and changed only the interval lever.

## 2026-03-14 Fixed MACD Operational Validation

- why this was the next step:
  - historical research is complete enough for now.
  - the selected primary branch is fixed:
    - `macd @ 4h`
    - `fast=12`
    - `slow=26`
    - `signal=9`
    - tightened trend regime gating
  - this stage is about runtime safety and execution integrity, not about proving more edge.
- runtime wiring added:
  - new fixed runtime profile: `macd_final_candidate`
  - baked-in fixed params and baked-in tightened regime state
  - preset: `config/presets/macd_final_candidate_ops.yaml`
  - wrappers:
    - `scripts/run_macd_final_candidate_paper.ps1`
    - `scripts/run_macd_final_candidate_testnet.ps1`
- safety/runtime fixes made during this step:
  - fixed `trader run` bug where `strategy_params` was not defined for the fixed profile path
  - fixed operational wrapper summary parsing so multi-symbol `runtime_state` is read correctly instead of falling back to an older run
  - added live-mode backfill signal suppression so historical bootstrap bars do not place stale testnet orders

### Paper Result

- command:
  - `powershell -ExecutionPolicy Bypass -File scripts/run_macd_final_candidate_paper.ps1`
- artifact root:
  - `out/operational_validation/macd_final_candidate_paper/`
- result:
  - fixed params/regime injection: `PASS`
  - status/state persistence: `PASS`
  - orders/fills/trades: `0/0/0`
  - verdict: `FAIL`
- what happened:
  - `BTC/USDT` and `ETH/USDT` tripped the runtime volatility circuit breaker almost immediately:
    - `BTC atr_pct=0.0646`
    - `ETH atr_pct=0.0506`
  - `BNB/USDT` kept running through the capped bar budget but never left `hold`
  - no entry, no fill, and therefore no TP/SL lifecycle was exercised in paper
- interpretation:
  - the fixed candidate wiring is correct
  - but the current runtime guard stack is too restrictive to complete order-path validation on this short 3-symbol paper run

### Testnet Result

- command:
  - `powershell -ExecutionPolicy Bypass -File scripts/run_macd_final_candidate_testnet.ps1`
- artifact root:
  - `out/operational_validation/macd_final_candidate_testnet/`
- result:
  - fixed params/regime injection: `PASS`
  - testnet preflight: `PARTIAL`
  - orders/fills/trades: `0/0/0`
  - verdict: `FAIL`
- what happened:
  - `BTC/USDT` passed private preflight
  - `ETH/USDT` and `BNB/USDT` failed private preflight with Binance `-1021`
    - `Timestamp for this request was 1000ms ahead of the server's time.`
  - the failing endpoints were private calls such as:
    - `fapiPrivateV2GetBalance`
    - `fapiPrivateV3GetPositionRisk`
  - websocket user-stream setup also repeatedly logged:
    - `disconnected (no running event loop)`
- interpretation:
  - runtime startup profile is correct and persisted in DB/status
  - but live testnet execution did not reach the bar-processing or order-protection path because private preflight was not stable across all 3 symbols

### Current Decision

- do we keep `MACD` as the selected operational candidate?
  - yes
  - this step did not invalidate the historical choice; it surfaced runtime issues
- do we have 3-symbol operational validation pass yet?
  - no
  - paper/testnet both need another pass after runtime guard/preflight stabilization
- can we expand to 14 symbols now?
  - no
  - stay on `BTC/USDT,ETH/USDT,BNB/USDT` until:
    - testnet private preflight is stable across all three
    - at least one controlled order/fill/protective-order cycle is observed end to end
- 15m data note:
  - reruns showed `fetched_rows=1` on many symbols, but total rows stayed fixed at `35040`.
  - this is expected rolling-window behavior on `15m`: the newest closed candle advanced while the oldest candle rolled off, so reuse/dedup worked without duplicate growth.
- headline comparison on the same 14-symbol universe:

| metric | 14 symbols @ 1h/4h | 14 symbols @ 15m |
|---|---:|---:|
| hard_gate_pass_count | 0 | 0 |
| best family | `ema_cross @ 4h` | `donchian_breakout @ 15m` |
| best OOS total return mean | -0.0003 | -0.0294 |
| best OOS sharpe mean | 0.0189 | -1.4658 |
| best OOS max drawdown mean | -0.0023 | -0.0521 |
| best positive symbols | 2 | 1 |
| best symbol return std | 0.0029 | 0.0179 |
| best trade count mean | 0.57 | 135.50 |
| best fee cost total | 7.9888 | 1881.1342 |

- interpretation:
  - `15m` produced **no** hard-gate winner and no family improved versus `1h/4h`.
  - the top `15m` branch was much worse on every key robustness metric:
    - return: `-0.0003 -> -0.0294`
    - sharpe: `0.0189 -> -1.4658`
    - drawdown: `-0.0023 -> -0.0521`
    - positive symbols: `2 -> 1`
    - fee cost total: `7.9888 -> 1881.1342`
  - this looks like classic short-timeframe fee/noise domination, not hidden edge discovery.
  - because `15m` is more brittle by construction, the correct read is conservative: these are weak OOS results with clear false-positive risk, not near-miss candidates.
- major vs alt:
  - `1h/4h` top candidate already leaned slightly away from majors.
  - `15m` worsened both buckets:
    - major (`BTC/ETH/BNB`) mean OOS return: `-0.0312`
    - alt mean OOS return: `-0.0289`
  - there is no convincing symbol-cluster edge on `15m`; alt is only less bad than majors.
- next single lever:
  - `regime-conditional broad sweep`
  - rationale: interval expansion to `15m` clearly increased turnover and cost drag, so the next hypothesis should be conditional edge concentration, not even more raw frequency.

## 2026-03-15 Fixed MACD Operational Validation Recovery Rerun

- recovery read of the interrupted working tree:
  - the runtime profile, preset, wrapper scripts, tests, and status persistence path were already mostly implemented after commit `702b9c6`
  - the unfinished part was not candidate wiring itself, but safely closing the loop with reproducible operational artifacts and an accurate latest run record
  - the broad-sweep / holdout research edits were historical context only; this recovery kept them untouched and focused on the fixed runtime path
- fixed candidate kept frozen:
  - `macd @ 4h`
  - `fast=12`
  - `slow=26`
  - `signal=9`
  - tightened regime gating via `macd_final_candidate`
- wrapper hardening completed:
  - `scripts/run_macd_final_candidate_validation.ps1` now records `user_stream_no_running_event_loop` in `summary.json` when stdout/stderr shows repeated user-stream reconnect churn
  - this keeps the operational artifact aligned with the actual runtime symptom instead of hiding it in raw logs only

### Commands Re-run

- `uv run --active pytest -q`
- `uv run --active trader doctor --env testnet`
- `powershell -ExecutionPolicy Bypass -File scripts/run_macd_final_candidate_paper.ps1`
- `powershell -ExecutionPolicy Bypass -File scripts/run_macd_final_candidate_testnet.ps1`

### Paper Rerun Result

- artifact root:
  - `out/operational_validation/macd_final_candidate_paper/`
- summary:
  - verdict: `FAIL`
  - fixed params OK: `true`
  - orders/fills/trades: `0/0/0`
  - halt reason: `volatility circuit breaker triggered`
- observed details:
  - `BTC/USDT` halted with `atr_pct=0.0626`
  - `ETH/USDT` halted with `atr_pct=0.0532`
  - `BNB/USDT` remained active but stayed `hold`
  - no entry, no fill, and no protective-order lifecycle was exercised

### Testnet Rerun Result

- artifact root:
  - `out/operational_validation/macd_final_candidate_testnet/`
- summary:
  - verdict: `FAIL`
  - fixed params OK: `true`
  - orders/fills/trades: `0/0/0`
  - halt reason: `volatility circuit breaker triggered`
- what changed versus the earlier interrupted attempt:
  - private preflight stabilized on the rerun; the previous `-1021` failure did not reproduce
  - this means the current blocking issue is no longer preflight drift but runtime guard behavior plus user-stream instability
- observed details:
  - `BTC/USDT` halted with `atr_pct=0.0626`
  - `ETH/USDT` halted with `atr_pct=0.0532`
  - `BNB/USDT` processed the full `240` bars without entry
  - stdout still showed repeated `disconnected (no running event loop)` user-stream churn

### Current Operational Read

- fixed MACD params were injected without drift: yes
- tightened regime state persisted into status/runtime_state: yes
- doctor/preflight is currently stable enough to start the run: yes on the latest rerun
- order/fill/protective-order path was exercised end to end: no
- current blockers:
  - volatility circuit breaker halts `BTC/USDT` and `ETH/USDT` before any controlled entry cycle
  - user-stream startup still reconnects with `no running event loop`

## 2026-03-14 Regime-Conditional Broad Sweep (14 symbols, 1h/4h)

- why move from `15m` to regime-conditional:
  - the `15m` run failed cleanly: no hard-gate winner, worse return/sharpe, much larger fee drag, and weaker positive-symbol breadth.
  - the next defensible lever was therefore not more frequency, but turning families on only in the environments that match their intended behavior.
- what changed:
  - universe stayed fixed at the same 14 populated symbols
  - intervals stayed fixed at `1h`, `4h`
  - fee/slippage, walk-forward OOS, ranking, and hard-gate rules stayed fixed
  - only regime gating was added
- regime implementation used:
  - trend-following families (`ema_cross`, `donchian_breakout`, `supertrend`, `price_adx_breakout`, `macd`):
    - `high_adx`
    - `not low_vol`
    - trend-aligned (`uptrend` for long, `downtrend` for short)
  - mean-reversion families (`rsi_mean_reversion`, `bollinger`, `stoch_rsi`):
    - `low_adx`
    - `low_vol`
    - `flat`
- result snapshot vs ungated `1h/4h` baseline:

| metric | 14 symbols @ 1h/4h | 14 symbols @ regime |
|---|---:|---:|
| hard_gate_pass_count | 0 | 70 |
| best family | `ema_cross @ 4h` | `donchian_breakout @ 1h` |
| best OOS total return mean | -0.0003 | 0.0062 |
| best OOS sharpe mean | 0.0189 | 0.3712 |
| best OOS max drawdown mean | -0.0023 | -0.0135 |
| best positive symbols | 2 | 10 |
| best trade count mean | 0.57 | 20.64 |
| best fee cost total | 7.9888 | 289.8043 |
| best regime coverage ratio | 1.0000 | 0.4097 |

- interpretation:
  - regime conditioning did create hard-gate candidates.
  - the strongest improvements came from:
    - `donchian_breakout`: `-0.0093 -> 0.0062`, positive symbols `2 -> 10`
    - `macd`: `-0.0103 -> 0.0078`, positive symbols `4 -> 8`
    - `price_adx_breakout`: `-0.0151 -> 0.0050`
  - mean-reversion families also became less bad or slightly positive under narrow low-vol / flat coverage.
- but the result should be treated cautiously:
  - the best candidate only trades in about `41%` of bars
  - hard-gate pass count jumped to `70/192`, which is too large to treat uncritically
  - this suggests gating is materially helping, but also means the next question is whether these candidates survive harsher cost assumptions
- major vs alt:
  - top regime candidate had positive performance in both buckets
  - majors were actually stronger than alts on the top branch:
    - major mean OOS return: `0.0090`
    - alt mean OOS return: `0.0055`
- next single lever:
  - `fee/slippage stress`
  - rationale: regime gating produced promising candidates, so the next validation should test whether the improvement survives more conservative execution assumptions rather than relaxing the gate further.

## 2026-03-14 Regime Fee/Slippage Stress (14 symbols, 1h/4h)

- why this was the next lever:
  - regime gating finally produced many hard-gate candidates, but the jump to `70/192` and the best-candidate `regime_coverage_ratio=0.4097` made the result vulnerable to multiple-testing and cost-assumption skepticism.
  - the clean next question was whether the same regime-conditioned candidate set survives more conservative execution costs without changing family logic, regime logic, universe, timeframe, or walk-forward structure.
- what changed:
  - baseline remained `14 symbols`, `1h/4h`, `broad-sweep`, `regime-mode=family-default`
  - only cost assumptions changed through CLI multipliers:
    - `baseline`
    - `fee_1p5x`
    - `fee_2x`
    - `slip_2x`
    - `slip_3x`
    - `mixed_2x`
- headline comparison:

| scenario | hard_gate_pass_count | best family | best OOS total return mean | best OOS sharpe mean | best positive symbols | best fee cost total | best regime coverage ratio |
|---|---:|---|---:|---:|---:|---:|---:|
| baseline | 70 | `donchian_breakout @ 1h` | 0.0062 | 0.3712 | 10 | 289.8043 | 0.4097 |
| fee_1p5x | 57 | `macd @ 4h` | 0.0071 | 0.3181 | 8 | 266.8588 | 0.4751 |
| fee_2x | 52 | `rsi_mean_reversion @ 4h` | 0.0001 | 0.2670 | 5 | 15.9114 | 0.0038 |
| slip_2x | 60 | `macd @ 4h` | 0.0073 | 0.3283 | 8 | 177.9397 | 0.4751 |
| slip_3x | 54 | `macd @ 4h` | 0.0068 | 0.2878 | 8 | 177.9364 | 0.4751 |
| mixed_2x | 50 | `rsi_mean_reversion @ 4h` | 0.0001 | 0.2544 | 5 | 15.9121 | 0.0038 |

- interpretation:
  - the regime edge did **not** collapse under cost stress:
    - hard-gate passes fell from `70` to `50` in the harshest `mixed_2x` case, but never fell to zero
    - the original baseline top candidate (`donchian_breakout @ 1h`) stayed positive and hard-gate-pass in all six scenarios
    - under `mixed_2x`, that same baseline winner still showed `oos_total_return_mean=0.0033`, `oos_sharpe_mean=0.0225`, positive symbols `7/14`
  - however, the ranking became more fragile under stronger stress:
    - best-family leadership rotated from `donchian_breakout` to `macd`, then to ultra-low-turnover `rsi_mean_reversion`
    - the `fee_2x` and `mixed_2x` top candidates survived largely by trading almost nothing (`trade_count_mean=0.57`) with near-zero coverage (`0.0038`)
    - that is statistically interesting but operationally weak; it looks more like a narrow pocket than a broad, scalable edge
  - practical cost-resilience read by family:
    - economically meaningful resilience: `macd`, `donchian_breakout`
    - partial but weak/marginal resilience: `rsi_mean_reversion`, `bollinger`
    - still unconvincing after stress: `supertrend`, `ema_cross`
- majors vs alts on the original baseline winner:
  - majors (`BTC/ETH/BNB`) stayed more resilient than alts across all stress scenarios
  - under `mixed_2x`, major mean OOS return stayed `0.0062`, while alt mean OOS return fell to `0.0025`
  - the stress did not reveal a hidden alt-only robust edge
- key caveat:
  - some stress scenarios showed a slightly better top-line best return than baseline because ranking switched to lower-turnover candidates, not because higher cost improved economics
  - the correct read is therefore conservative: regime gating appears directionally real, but the current candidate set is still too broad and too coverage-skewed to trust as-is
- next single lever:
  - `family pruning`
  - rationale: fee/slippage stress suggests the most credible signal is concentrated in a smaller subset of families (`donchian_breakout`, `macd`, `price_adx_breakout`, with marginal low-coverage mean-reversion branches). The next clean test is to reduce the multiple-testing surface without changing regime logic again.

## 2026-03-14 Regime Family Pruning (14 symbols, 1h/4h)

- why family pruning was the next lever:
  - the regime-conditioned full sweep showed real edge, but `70/192` hard-gate passes still looked too inflated.
  - fee/slippage stress suggested the only economically meaningful resilient families were `donchian_breakout`, `macd`, and secondarily `price_adx_breakout`.
  - the clean next step was therefore to change only the family set and leave regime gating, universe, interval, cost model, and walk-forward structure unchanged.
- pruned family set:
  - included: `donchian_breakout`, `macd`, `price_adx_breakout`
  - excluded: `ema_cross`, `rsi_mean_reversion`, `bollinger`, `supertrend`, `stoch_rsi`
- headline comparison vs full regime sweep:

| metric | full regime | pruned regime |
|---|---:|---:|
| candidate count | 192 | 116 |
| hard_gate_pass_count | 70 | 42 |
| best family | `donchian_breakout @ 1h` | `macd @ 4h` |
| best OOS total return mean | 0.0062 | 0.0048 |
| best OOS sharpe mean | 0.3712 | 0.1439 |
| best OOS max drawdown mean | -0.0135 | -0.0162 |
| best positive symbols | 10 | 9 |
| best trade count mean | 20.64 | 10.86 |
| best fee cost total | 289.8043 | 151.7073 |
| best regime coverage ratio | 0.4097 | 0.4751 |

- interpretation:
  - pruning worked in the narrow sense:
    - candidate count fell from `192 -> 116`
    - hard-gate passes fell from `70 -> 42`
    - this also moved below the prior strongest cost-stress count (`50`) and removed the low-coverage mean-reversion survivors from the top of the table
  - but pruning did **not** improve the best overall candidate quality:
    - best return fell `0.0062 -> 0.0048`
    - best sharpe fell `0.3712 -> 0.1439`
    - drawdown worsened slightly
  - the positive part is that the remaining top branches are now all medium-coverage trend branches rather than ultra-low-coverage anomalies.
- family read after pruning:
  - `donchian_breakout`:
    - the strongest economically meaningful branch still looks like the same baseline winner:
      - `1h`
      - `entry_period=30`
      - `exit_period=5`
      - `regime_coverage_ratio=0.4097`
      - `oos_total_return_mean=0.0062`
      - positive symbols `10/14`
    - pruning did not weaken this branch at all; it stayed exactly intact
  - `macd`:
    - pruning promoted `macd @ 4h` to rank 1 because it offered lower trade count and lower fee cost
    - but its best pruned winner (`fast=16`, `slow=32`, `adx_filter=true`) had lower quality than the earlier best `macd` pocket:
      - return `0.0078 -> 0.0048`
      - sharpe `0.3690 -> 0.1439`
    - this makes `macd` the table leader by score, not necessarily the best absolute edge
  - `price_adx_breakout`:
    - modestly improved under pruning:
      - return `0.0050 -> 0.0056`
      - sharpe `0.2684 -> 0.3148`
      - fee decreased slightly
    - still looks like a valid backup candidate, but not the primary branch
- majors vs alts:
  - pruned top candidate (`macd @ 4h`):
    - majors mean OOS return: `0.0042`
    - alts mean OOS return: `0.0050`
  - unlike the full regime `donchian` winner, the pruned `macd` winner did not show major-led strength; its edge was spread slightly more toward alts.
  - this argues for keeping `donchian_breakout` as the more stable primary branch and `macd` as the lower-turnover alternative branch.
- low coverage survivor check:
  - in the full regime/stress runs, some top branches migrated into `coverage ~ 0.0038` low-frequency mean-reversion pockets.
  - after pruning, the top family coverage stayed in the `0.41 - 0.48` range.
  - that is a meaningful improvement in interpretability and removes the worst low-coverage survivor problem from the top of the ranking.
- next single lever:
  - `regime parameter tightening`
  - rationale: pruning reduced the multiple-testing surface and removed the worst low-coverage distortions, but `42` hard-gate passes are still too many. The next clean step is to keep the pruned family set fixed and tighten only regime thresholds to see if the surviving edge becomes narrower but more trustworthy.

## 2026-03-14 Regime Parameter Tightening (14 symbols, 1h/4h, pruned families)

- why regime parameter tightening was the next lever:
  - family pruning removed the worst low-coverage survivors from the top of the table, but `42/116` hard-gate passes were still too many.
  - the next clean step was to keep the same pruned family set and tighten only the regime definition so the remaining candidates had to survive stricter trend-strength and volatility conditions.
- fixed family set:
  - `donchian_breakout`
  - `macd`
  - `price_adx_breakout`
- tightening applied:
  - `high_adx_threshold: 25 -> 30`
  - `vol_percentile_window: 120 -> 160`
  - `low_vol_quantile/high_vol_quantile: 0.35/0.65 -> 0.20/0.80`
  - `trend_ema_span: 80 -> 100`
  - `trend_slope_lookback: 12 -> 16`
  - `trend_slope_threshold: 0.0015 -> 0.0030`
  - `trend_distance_threshold: 0.0050` added
  - `min_coverage_ratio: 0.20` added
- headline comparison vs pruned regime baseline:

| metric | pruned regime | tightened regime |
|---|---:|---:|
| candidate count | 116 | 116 |
| hard_gate_pass_count | 42 | 37 |
| best family | `macd @ 4h` | `donchian_breakout @ 1h` |
| best OOS total return mean | 0.0048 | 0.0037 |
| best OOS sharpe mean | 0.1439 | 0.2382 |
| best OOS max drawdown mean | -0.0162 | -0.0112 |
| best positive symbols | 9 | 10 |
| best symbol return std | 0.0145 | 0.0085 |
| best trade count mean | 10.86 | 15.57 |
| best fee cost total | 151.7073 | 218.6388 |
| best regime coverage ratio | 0.4751 | 0.3649 |

- interpretation:
  - tightening did reduce pass inflation, but only modestly:
    - `42 -> 37`
  - candidate count stayed `116` because the new `coverage floor=0.20` did not bind on the surviving pruned trend families.
  - the more important effect was qualitative:
    - the rank-1 branch moved from lower-turnover `macd` back to `donchian_breakout`
    - top-candidate sharpe improved
    - drawdown and cross-symbol dispersion improved
    - but absolute mean return fell
- final family read:
  - `donchian_breakout`:
    - now the clearest lead candidate again
    - best branch:
      - `1h`
      - `entry_period=40`
      - `exit_period=5`
      - `oos_total_return_mean=0.0037`
      - `oos_sharpe_mean=0.2382`
      - positive symbols `10/14`
      - `regime_coverage_ratio=0.3649`
    - compared with pruned baseline, it traded less, paid less fee, and had tighter dispersion, but gave up some raw return
  - `macd`:
    - still robust and arguably improved within-family under tightening:
      - return `0.0048 -> 0.0053`
      - sharpe `0.1439 -> 0.1585`
    - but it lost one positive symbol and remained below `donchian` on breadth and overall score
    - this now looks like the strongest secondary branch, not the lead
  - `price_adx_breakout`:
    - remained viable but weakened materially:
      - return `0.0056 -> 0.0020`
      - sharpe `0.3148 -> 0.0205`
    - positive symbols increased, but the quality drop was too large to keep it near the top
- low coverage survivor check:
  - before pruning, stressed runs could surface candidates with `coverage ~ 0.0038`
  - after pruning, top families already sat around `0.41 - 0.48`
  - after tightening, all remaining candidates still sat around `0.365 - 0.427`
  - so the low-coverage survivor problem stayed removed; the new floor was codified, but the practical cleanup had already happened in pruning
- majors vs alts:
  - tightened top candidate (`donchian_breakout @ 1h`) remained positive on both:
    - majors mean OOS return: `0.0030`
    - alts mean OOS return: `0.0040`
  - majors showed higher mean sharpe, alts slightly higher mean return
  - the read is balanced rather than major-only or alt-only
- next single lever:
  - `final showdown: donchian_breakout vs macd`
  - rationale: tightening re-established `donchian` as the lead and kept `macd` as the strongest alternative, while `price_adx_breakout` weakened materially. The next clean comparison is now a head-to-head between the two credible finalists.

## 2026-03-14 Final Showdown: Donchian vs MACD

- why this was the next lever:
  - the `final2` run already showed that dropping `price_adx_breakout` did not change the top frontier.
  - the remaining open question was no longer broad search quality, but final selection:
    - `donchian_breakout` had the best single candidate
    - `macd` had the thicker reproducible candidate cluster
- comparison design:
  - this was **not** another broad sweep
  - only small neighborhoods around the current best pockets were evaluated
  - baseline and strongest stress (`mixed_2x`) were both run under the same tightened regime setup
- neighborhoods used:
  - Donchian:
    - `entry_period, exit_period` in
      - `(30,5)`
      - `(30,10)`
      - `(40,5)`
      - `(40,10)`
  - MACD:
    - `(fast, slow, signal)` in
      - `(8,21,5)`
      - `(10,30,7)`
      - `(12,26,9)`
      - `(16,32,9)`
    - `use_histogram=false`, `adx_filter=false` fixed to avoid duplicate-toggle inflation
- showdown result:

| metric | donchian_breakout | macd |
|---|---:|---:|
| candidate count | 8 | 8 |
| neighborhood hard-gate pass count | 3 | 4 |
| neighborhood pass rate | 37.5% | 50.0% |
| neighborhood median return | -0.0003 | 0.0006 |
| best OOS total return mean | 0.0037 | 0.0053 |
| best OOS sharpe mean | 0.2382 | 0.1585 |
| best positive symbols | 10 | 8 |
| best symbol return std | 0.0085 | 0.0111 |
| best trade count mean | 15.57 | 11.50 |
| best fee cost total | 218.6388 | 160.6727 |
| best regime coverage ratio | 0.3649 | 0.4265 |
| stress survival rate | 33.3% | 100.0% |

- final decision:
  - **primary winner: `donchian_breakout`**
  - **runner-up: `macd`**
- why `donchian_breakout` still wins:
  - it kept the stronger lead candidate on the actual “deployable” metrics:
    - higher sharpe
    - lower symbol dispersion
    - broader positive-symbol support (`10/14`)
  - it remained more balanced across cross-section quality, even though stress hurt it harder than `macd`
  - its best branch stayed the same robust family story the prior tightening run had already isolated:
    - `1h`
    - `entry_period=40`
    - `exit_period=5`
    - `allow_short=false`
- why `macd` did not win:
  - it was better on family-level reproducibility:
    - higher neighborhood pass rate
    - positive neighborhood median return
    - `100%` stress survival for baseline hard-gate candidates
  - but its best candidate still had weaker breadth and weaker cross-symbol stability than `donchian`
  - practical read:
    - `macd` is the safer backup family
    - `donchian` is still the stronger primary candidate
- major vs alt:
  - baseline best Donchian candidate:
    - majors mean OOS return: `0.0030`
    - alts mean OOS return: `0.0040`
  - baseline best MACD candidate:
    - majors mean OOS return: `0.0080`
    - alts mean OOS return: `0.0045`
  - interpretation:
    - `macd` leaned more major-robust
    - `donchian` was more balanced across the whole 14-symbol universe
- stress read:
  - under `mixed_2x`, `macd` degraded much less at the family level
  - under the same stress, `donchian` best-pocket return stayed positive but lost hard-gate status
  - this does not overturn the final winner, but it does make `macd` the mandatory backup candidate rather than a discarded branch
- next single lever:
  - `donchian winner holdout validation`
  - rationale: the research comparison is now narrow enough. The clean next step is to hold the chosen `donchian_breakout` pocket fixed and validate it on a stricter holdout procedure rather than reopening the family search.

## 2026-03-14 Holdout Validation: Donchian Winner vs MACD Control

- why holdout validation was the next lever:
  - family selection was already finished.
  - the remaining question was whether the chosen Donchian winner could survive on a genuinely unseen trailing segment.
- what stayed fixed:
  - symbols: `BTCUSDT ETHUSDT BNBUSDT SOLUSDT XRPUSDT ADAUSDT DOGEUSDT TRXUSDT AVAXUSDT LINKUSDT DOTUSDT LTCUSDT ATOMUSDT UNIUSDT`
  - historical Binance USDT-M Futures candles only
  - tightened regime rules unchanged
  - fixed strategy parameters:
    - Donchian primary: `1h`, `entry_period=40`, `exit_period=5`, `allow_short=false`
    - MACD control: `4h`, `fast=12`, `slow=26`, `signal=9`, `use_histogram=false`, `adx_filter=false`
  - only the evaluation segment changed:
    - holdout window: `2025-11-13T20:00:00+00:00` to `2026-03-13T20:00:00+00:00`
- scenarios:
  - `baseline`
  - `mixed_2x`

| metric | donchian_breakout | macd |
|---|---:|---:|
| baseline holdout total return | -0.0005 | 0.0019 |
| baseline holdout sharpe | -0.5031 | 0.2162 |
| baseline holdout max drawdown | -0.0104 | -0.0146 |
| baseline positive symbols | 7 | 7 |
| baseline symbol return std | 0.0064 | 0.0100 |
| baseline trade count mean | 10.00 | 6.64 |
| baseline fee cost total | 140.0378 | 92.6873 |
| baseline regime coverage ratio | 0.3369 | 0.3919 |
| mixed_2x holdout total return | -0.0019 | 0.0009 |
| mixed_2x holdout sharpe | -0.8026 | 0.0934 |
| mixed_2x holdout max drawdown | -0.0111 | -0.0150 |
| mixed_2x positive symbols | 6 | 7 |
| mixed_2x symbol return std | 0.0066 | 0.0100 |
| mixed_2x trade count mean | 10.00 | 6.64 |
| mixed_2x fee cost total | 280.0196 | 185.4005 |
| mixed_2x regime coverage ratio | 0.3369 | 0.3919 |

- interpretation:
  - the previously selected `donchian_breakout` winner did not hold up on the unseen holdout:
    - baseline holdout return and sharpe both turned negative
    - mixed stress pushed them further negative
  - the `macd` control remained positive in both scenarios:
    - baseline holdout return `0.0019`
    - mixed stress holdout return `0.0009`
    - positive symbols stayed `7/14`
  - cross-section read:
    - Donchian failed mainly in majors:
      - baseline major mean return `-0.0058`
      - mixed stress major mean return `-0.0076`
    - MACD remained stronger in majors:
      - baseline major mean return `0.0087`
      - mixed stress major mean return `0.0079`
    - MACD alt returns were near flat to mildly negative, but still compared better than Donchian's major failure profile.
- final read:
  - the prior primary choice is no longer supported by stricter holdout evidence.
  - `MACD` should be promoted from control/back-up to the current primary research candidate.
  - `Donchian` remains the documented comparison branch, but not the lead after holdout failure.
- next single lever:
  - `extended MACD holdout confirmation`
  - rationale: the next clean validation is to keep the promoted MACD pocket frozen and verify that the result is not just one favorable trailing segment.

## 2026-03-14 Extended Holdout Confirmation: MACD Primary Candidate

- why this was the next step:
  - the trailing `120d` holdout already demoted Donchian and promoted the fixed MACD pocket.
  - the remaining question was whether that MACD result was just one favorable slice or whether it stayed positive across multiple unseen trailing windows.
- what stayed fixed:
  - candidate: `macd @ 4h`, `fast=12`, `slow=26`, `signal=9`, `use_histogram=false`, `adx_filter=false`
  - tightened regime definition unchanged
  - universe unchanged:
    - `BTCUSDT ETHUSDT BNBUSDT SOLUSDT XRPUSDT ADAUSDT DOGEUSDT TRXUSDT AVAXUSDT LINKUSDT DOTUSDT LTCUSDT ATOMUSDT UNIUSDT`
  - only evaluation windows changed
- holdout design:
  - rolling trailing holdouts:
    - `60d`
    - `90d`
    - `120d`
  - each holdout was evaluated under:
    - `baseline`
    - `mixed_2x`

| holdout | baseline return | baseline sharpe | baseline positive symbols | mixed_2x return | mixed_2x sharpe | mixed_2x positive symbols | coverage |
|---|---:|---:|---:|---:|---:|---:|---:|
| 60d | 0.0059 | 1.1465 | 9 | 0.0054 | 1.0303 | 9 | 0.4118 |
| 90d | 0.0014 | 0.1935 | 6 | 0.0007 | 0.0732 | 6 | 0.4061 |
| 120d | 0.0019 | 0.2162 | 7 | 0.0009 | 0.0934 | 7 | 0.3919 |

- aggregate read:
  - baseline positive holdouts: `3/3`
  - `mixed_2x` positive holdouts: `3/3`
  - baseline median return across holdouts: `0.0019`
  - mixed median return across holdouts: `0.0009`
  - baseline median sharpe across holdouts: `0.2162`
  - mixed median sharpe across holdouts: `0.0934`
  - majors mean return across holdouts:
    - baseline: `0.0087`
    - mixed: `0.0080`
  - alts mean return across holdouts:
    - baseline: `0.0015`
    - mixed: `0.0008`
- interpretation:
  - the fixed MACD candidate stayed positive in every tested holdout window under both cost assumptions.
  - performance clearly weakened as the window widened from `60d` to `90d/120d`, so this is not a dominant edge, but it did not collapse.
  - majors were consistently stronger than alts, yet alts were positive on average across holdouts rather than contributing only a tiny handful of symbols.
  - regime coverage stayed stable around `0.39 - 0.41`, which is comfortably above the low-coverage survivor zone that invalidated earlier branches.
- final read:
  - `MACD 유지`
  - `paper/testnet operational validation` 후보로 승격 가능
  - Donchian is still useful as a historical comparison branch, but there is no longer a reason to keep it as the operational lead candidate.
- next single lever:
  - `fixed MACD paper/testnet operational validation`
  - rationale: strategy discovery is now narrow enough that the next useful question is operational behavior, not more historical search.
## 2026-03-15 Execution Blocker Recovery Completion

- fixed candidate stayed frozen:
  - `macd @ 4h`
  - `fast=12`
  - `slow=26`
  - `signal=9`
  - tightened regime unchanged
- blocker diagnosis:
  - volatility guard was binding at the default `max_atr_pct=0.05` because 4h `BTC/USDT` and `ETH/USDT` were opening around `atr_pct ~ 0.053 - 0.063`
  - `user_stream_no_running_event_loop` came from creating `aiohttp.ClientSession()` outside a running loop in `trader/data/binance_user_stream.py`
  - live validation also could not reach a real order path with the previous backfill suppression, because the fixed 4h probe was trapped inside bootstrap bars
- runtime-only recovery applied:
  - user-stream loop creation moved fully inside the event loop, removing the `no running event loop` failure mode
  - failed websocket connect now closes the temporary `aiohttp` session cleanly instead of leaking `Unclosed client session`
  - live broker no longer waits for a terminal user-stream status when placing protective trigger orders (`STOP_MARKET` / `TAKE_PROFIT_MARKET`); `NEW` is now accepted as the healthy creation state
  - validation-only probe/override path was added so the fixed candidate can exercise entry -> protective -> exit without changing strategy params
  - MACD final candidate wrappers now default to `60` bars; probe entry starts at bar `40`, so `60` is enough for a controlled cycle and keeps testnet validation bounded
  - validation-only ATR override stays wrapper-scoped via `MAX_ATR_PCT=1.0`
- rerun result:
  - `paper`: `PASS`
    - `run_id=02cb93cac04b4cc292efd8477a72d029`
    - `orders/fills/trades=15/6/3`
    - `protective_orders_created_count=3`
    - `symbols_halted=0`
  - `testnet`: `PASS`
    - `run_id=34a511fc12dc4261b2db899c9ceaf97b`
    - `orders/fills/trades=18/0/3`
    - `protective_orders_created_count=3`
    - `symbols_halted=0`
    - `user_stream_no_running_event_loop_count=0`
- current residual issue:
  - raw testnet logs still show Binance testnet user-stream DNS reconnect churn (`Could not contact DNS servers`)
  - runtime order/protective/state-sync validation now completes despite that churn
  - live/testnet DB `fills` are still `0` because fill persistence remains user-stream-dependent when websocket delivery never lands
- current decision:
  - `3-symbol operational validation`: passed for execution-path coverage
  - do not expand the universe yet; first clean up live/testnet fill accounting under user-stream degradation

## 2026-03-15 Reconciliation Rerun Validation

Scope stayed fixed:

- `macd_final_candidate`
- `4h`
- `fast=12 slow=26 signal=9`
- tightened regime unchanged
- no research/holdout code touched

What was added/fixed during this validation pass:

- REST fill reconciliation is now persisted into DB
- duplicate WS/REST fills are deduped by `fill_id`/alias handling
- validation summary now uses distinct `order_id` / `fill_id` counts
- live broker no longer sends duplicate `clientOrderId` params
- live broker caches the futures permission preflight briefly, which stops 3-symbol testnet starts from self-triggering `-1003` rate-limit bans
- multi-symbol shared runs now generate unique trade ids per symbol

Usable rerun results:

- paper:
  - `run_id=185d99c7961f4681a49b8f18fe7442fa`
  - verdict `PASS`
  - orders/fills/trades `11/6/3`
  - `fills_accounted_count=6`
  - `fills_from_rest_reconcile_count=0`
  - `fill_provenance_breakdown={"by_source":{"direct_runtime":6},"fills_reconciled_count":0,"fills_with_source_history_count":0}`
  - `accounting_consistency_pass=true`
- testnet:
  - `run_id=392e3990ee3547ee8c30a98f7f0356b8`
  - verdict `PASS`
  - orders/fills/trades `12/8/3`
  - `fills_accounted_count=8`
  - `fills_reconciled_count=8`
  - `fills_from_rest_reconcile_count=8`
  - `fills_from_user_stream_count=0`
  - `fills_from_aggregated_fallback_count=0`
  - `partial_fills_count=4`
  - `reconciled_missing_ws_fill_count=8`
  - `trade_query_unavailable_count=0`
  - `fill_provenance_breakdown={"by_source":{"rest_trade_reconcile":8},"fills_reconciled_count":8,"fills_with_source_history_count":0}`
  - `partial_fill_audit_summary={"partial_fill_groups_count":2,"partial_fill_rows_count":4,"aggregated_fallback_fill_count":0,"reconciled_missing_ws_fill_count":8,"trade_query_unavailable_count":0,"fills_with_multiple_source_history_count":0}`
  - `accounting_consistency_pass=true`
  - `user_stream_disconnect_count=14`
  - `user_stream_dns_reconnect_count=14`
  - `accounting_degraded_mode_used=true`

Interpretation:

- the old `fills=0` testnet accounting failure is resolved on the latest usable rerun
- testnet completed entry -> protective create -> exit -> protective cancel -> flat state sync under degraded user-stream conditions
- summary, DB, and `trader status --latest` are now aligned on distinct order/fill/trade counts
- provenance is now explicit per fill row and in status/summary:
  - `user_stream`
  - `rest_trade_reconcile`
  - `aggregated_fallback`
  - `direct_runtime`
- partial-fill audit is no longer implicit:
  - latest usable testnet rerun exposed `partial_fill_groups_count=2`
  - both groups were recovered via exact REST trade rows, not aggregate fallback

Observed transient blockers during validation:

- duplicate `ClientOrderId` failures on live entry path
  - fixed by using Binance futures `newClientOrderId` only
- repeated testnet preflight `-1003` / `418` bans in 3-symbol live starts
  - fixed by caching the private futures permission check across symbol preflight
- one transient paper rerun ended with `0` processed bars / `no_trades_observed`
  - not used for final judgment because the immediately previous and subsequent validation path was already verified with a successful usable run

Remaining risk and priority:

- residual risk:
  - future degraded runs can still fall back to aggregate accounting when the trade query is unavailable
- priority:
  - observability hardening is complete for this validation lane; no new blocker opened
- reason:
  - the remaining edge case is now observable through `fills_from_aggregated_fallback_count` and `trade_query_unavailable_count` instead of being silent

## 2026-03-19 - 3-symbol fixed MACD long-run testnet pilot

- why this was the next step:
  - research is closed
  - short paper/testnet operational validation already passed
  - the next question is whether the fixed 3-symbol candidate is stable for longer unattended wall-clock testnet operation
- runner:
  - added `scripts/run_macd_final_candidate_testnet_long.ps1`
  - fixed inputs:
    - `strategy=macd_final_candidate`
    - `timeframe=4h`
    - `symbols=BTC/USDT,ETH/USDT,BNB/USDT`
    - preset `macd_final_candidate_ops`
    - `fast=12 slow=26 signal=9`
  - output dir:
    - `out/operational_validation/macd_final_candidate_testnet_long/`
- actual result:
  - verdict `FAIL`
  - runtime duration `5.18` minutes
  - `startup_stalled_before_run_id=true`
  - `user_stream_disconnect_count=18`
  - `user_stream_dns_reconnect_count=18`
  - `orders/fills/trades=0/0/0`
  - no new runtime `run_id` was created before the startup timeout, so there was no trustworthy long-run execution/accounting state to evaluate
- interpretation:
  - the fixed candidate remains valid for short controlled operational probes
  - it is not yet operationally trustworthy for a longer 3-symbol unattended testnet run in the current user-stream/DNS environment
  - 14-symbol expansion is premature until this startup stall is removed
- next step:
  - remove the long-run startup stall on the 3-symbol testnet path, then rerun the same long-run command before considering any symbol expansion

## 2026-03-20 - 3-symbol long-run startup stall root cause and recovery

- root cause:
  - this was primarily a detection bug, not a `macd_final_candidate` preset/profile bootstrap failure
  - `RuntimeEngine.start_session()` emitted `runtime_started` / `runtime_profile` events but did not persist `runtime_state` until the first bar or session finish
  - the long-run runner originally watched `runtime_state` / `trader status --latest`, so a 4h websocket session could be alive and preflighting while still looking like "no fresh run_id"
- fix:
  - `RuntimeEngine.start_session()` now saves an initial `runtime_state` row immediately
  - `scripts/run_macd_final_candidate_testnet_long.ps1` now distinguishes:
    - process spawn
    - fresh `runtime_started` event
    - fresh `runtime_state` registration
    - first status visibility
  - summary now records:
    - `attempted_process_started`
    - `fresh_run_id_detected`
    - `startup_phase`
    - `startup_failure_reason`
    - `first_status_seen`
    - `first_event_seen`
    - `first_bar_seen`
    - `first_order_seen`
- rerun result:
  - command lane: `powershell -ExecutionPolicy Bypass -File scripts/run_macd_final_candidate_testnet_long.ps1 -SnapshotEverySec 60`
  - fresh `run_id=301a138e8d1e49aa9462eea7b02507af`
  - `startup_stalled_before_run_id=false`
  - `whether_fixed_params_loaded=true`
  - `startup_phase=status_written`
  - `first_event_seen=true`
  - `first_status_seen=true`
  - `processed_bars_total=0`
  - `orders/fills/trades=0/0/0`
  - repeated user-stream DNS churn remained visible: `user_stream_disconnect_count=19`, `user_stream_dns_reconnect_count=19`
- interpretation:
  - the startup blocker is removed
  - the runner now produces authoritative fresh-run `status_final.txt` / `summary.json`
  - a full 12h unattended verdict still remains open because this recovery rerun only proved startup registration, not longer wall-clock bar/order lifecycle stability
