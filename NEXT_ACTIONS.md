# Next Actions (2026-03-14)

## Current Situation
- ✓ Historical research infrastructure: STRONG
- ✓ Operational validation infrastructure: STRONG
- ✗ Strategy edge discovery: WEAK (zero hard-gate winners)

## Priority 1: Find Profitable Strategy (REQUIRED before any testnet/live work)

### Option A: 15m Interval Exploration (RECOMMENDED - fastest)
```bash
# Fetch 15m data
uv run --active python scripts/fetch_futures_historical.py \
  --symbols BTCUSDT ETHUSDT XRPUSDT TRXUSDT ADAUSDT SOLUSDT \
  --interval 15m --days 365

# Run broad sweep on 15m
uv run --active python scripts/run_strategy_search.py \
  --symbols BTCUSDT ETHUSDT XRPUSDT TRXUSDT ADAUSDT SOLUSDT \
  --intervals 15m \
  --mode broad-sweep \
  --time-budget-hours 6
```

**Why:** Higher frequency may reveal scalping edge not visible on 1h/4h. Lowest implementation cost.

### Option B: Expanded Universe (15-20 symbols)
```bash
# Fetch expanded universe
uv run --active python scripts/fetch_futures_historical.py \
  --symbols BTCUSDT ETHUSDT BNBUSDT SOLUSDT XRPUSDT ADAUSDT DOGEUSDT TRXUSDT MATICUSDT DOTUSDT AVAXUSDT LINKUSDT UNIUSDT ATOMUSDT LTCUSDT \
  --interval 1h --days 365

# Run broad sweep on expanded universe
uv run --active python scripts/run_strategy_search.py \
  --symbols BTCUSDT ETHUSDT BNBUSDT SOLUSDT XRPUSDT ADAUSDT DOGEUSDT TRXUSDT MATICUSDT DOTUSDT AVAXUSDT LINKUSDT UNIUSDT ATOMUSDT LTCUSDT \
  --intervals 1h 4h \
  --mode broad-sweep \
  --time-budget-hours 6
```

**Why:** Edge may be concentrated in specific symbols not in current 6-symbol set.

### Option C: 2h/8h/1d Timeframe Exploration
```bash
# Fetch alternative timeframes
uv run --active python scripts/fetch_futures_historical.py \
  --symbols BTCUSDT ETHUSDT XRPUSDT TRXUSDT ADAUSDT SOLUSDT \
  --interval 2h --days 365

uv run --active python scripts/fetch_futures_historical.py \
  --symbols BTCUSDT ETHUSDT XRPUSDT TRXUSDT ADAUSDT SOLUSDT \
  --interval 1d --days 365

# Run broad sweep
uv run --active python scripts/run_strategy_search.py \
  --symbols BTCUSDT ETHUSDT XRPUSDT TRXUSDT ADAUSDT SOLUSDT \
  --intervals 2h 1d \
  --mode broad-sweep \
  --time-budget-hours 6
```

**Why:** Lower-frequency signals may have better signal-to-noise ratio.

## Priority 2: ONLY After Hard-Gate Winner Found

Once you have at least ONE strategy with:
- `oos_total_return_mean > 0`
- `oos_sharpe_mean > 0.5`
- `positive_symbols >= 4/6`
- Passing all 5 hard-gate criteria

THEN proceed to operational validation:

```bash
# Short testnet live-forward (2h)
powershell -ExecutionPolicy Bypass -File scripts/run_live_forward_2h.ps1
```

## What NOT to Do

❌ DO NOT run testnet/live-forward without first finding hard-gate winner
❌ DO NOT assume testnet performance validates strategy edge
❌ DO NOT invest time in operational improvements before solving discovery problem
❌ DO NOT add more operational features (more runners, more guards) until strategy edge exists

## Quick Status Check

```bash
# Run tests
uv run --active pytest -q

# Check latest broad sweep results
cat out/strategy_search_matrix/top_strategies.md

# Check hard-gate pass count in summary
head -20 out/strategy_search_matrix/summary.csv
```

## Remember

**Testnet proves order execution quality, NOT strategy profitability.**

**Historical OOS performance is the ONLY valid criterion for strategy selection.**
