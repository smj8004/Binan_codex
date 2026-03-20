[CmdletBinding()]
param(
    [string]$Symbols = "BTC/USDT,ETH/USDT,BNB/USDT",
    [double]$Hours = 12.0,
    [int]$SnapshotEverySec = 300,
    [string]$Timeframe = "4h",
    [string]$ValidationMode = "real_strategy",
    [string]$Preset = "macd_final_candidate_ops",
    [string]$FixedNotionalUsdt = "250",
    [string]$MinEntryNotionalUsdt = "250",
    [string]$Leverage = "20",
    [string]$OutDir = "out/operational_validation/macd_final_candidate_testnet_long",
    [int]$MaxBars = 0,
    [switch]$RealtimeOnly,
    [switch]$StopAfterFirstLiveBar,
    [int]$MaxWallBufferMinutes = 30,
    [int]$StartupRunIdTimeoutMinutes = 5
)

$ErrorActionPreference = "Stop"

function Write-Header([string]$Text) {
    Write-Host ""
    Write-Host "==== $Text ===="
}

function Get-TimeframeSeconds([string]$Value) {
    $text = [string]$Value
    if ($text -match "^\d+m$") {
        return [int]$text.TrimEnd("m") * 60
    }
    if ($text -match "^\d+h$") {
        return [int]$text.TrimEnd("h") * 3600
    }
    if ($text -match "^\d+d$") {
        return [int]$text.TrimEnd("d") * 86400
    }
    throw "Unsupported timeframe: $Value"
}

function Get-NextCloseInfo([string]$Value, [datetime]$FromUtc) {
    $spanSec = Get-TimeframeSeconds -Value $Value
    $fromOffset = [DateTimeOffset]::new($FromUtc.ToUniversalTime())
    $epochSec = [int64]$fromOffset.ToUnixTimeSeconds()
    $nextSec = ([int64][Math]::Floor($epochSec / $spanSec) + 1) * $spanSec
    $nextClose = [DateTimeOffset]::FromUnixTimeSeconds($nextSec).UtcDateTime
    return [pscustomobject]@{
        timeframe = $Value
        expected_next_close_utc = $nextClose.ToString("o")
        minutes_until_next_close = [Math]::Round(($nextClose - $FromUtc.ToUniversalTime()).TotalMinutes, 2)
        seconds_until_next_close = [Math]::Round(($nextClose - $FromUtc.ToUniversalTime()).TotalSeconds, 2)
    }
}

function Get-LatestRunId([string]$DbPath) {
    $env:OV_DB_PATH = $DbPath
    $raw = @'
import os
import sqlite3

db_path = os.environ["OV_DB_PATH"]
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
row = conn.execute("SELECT run_id FROM runtime_state ORDER BY updated_at DESC LIMIT 1").fetchone()
print("" if row is None else str(row["run_id"]))
'@ | uv run --active python -
    return ($raw | Out-String).Trim()
}

function Get-FreshRuntimeActivity([string]$DbPath, [string]$PreRunLatest, [string]$StartedAfterIso) {
    $env:OV_DB_PATH = $DbPath
    $env:OV_PRE_RUN_ID = $PreRunLatest
    $env:OV_STARTED_AFTER = $StartedAfterIso
    $raw = @'
import json
import os
import sqlite3

db_path = os.environ["OV_DB_PATH"]
pre_run_id = os.environ.get("OV_PRE_RUN_ID", "").strip()
started_after = os.environ.get("OV_STARTED_AFTER", "").strip()
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

def _row_to_dict(row):
    return {} if row is None else {k: row[k] for k in row.keys()}

state_row = conn.execute(
    """
    SELECT run_id, updated_at, last_bar_ts
    FROM runtime_state
    WHERE updated_at >= ?
    ORDER BY updated_at DESC
    LIMIT 20
    """,
    (started_after,),
).fetchall()
fresh_state = next(
    (row for row in state_row if str(row["run_id"] or "").strip() and str(row["run_id"]) != pre_run_id),
    None,
)

event_rows = conn.execute(
    """
    SELECT
      id,
      ts,
      event_type,
      json_extract(payload, '$.run_id') AS run_id
    FROM events
    WHERE ts >= ?
      AND event_type IN ('runtime_started', 'runtime_profile', 'preflight_check', 'preflight_endpoint', 'runtime_stopped')
    ORDER BY id ASC
    LIMIT 500
    """,
    (started_after,),
).fetchall()
fresh_event = next(
    (row for row in event_rows if str(row["run_id"] or "").strip() and str(row["run_id"]) != pre_run_id),
    None,
)

run_id = ""
run_id_source = ""
if fresh_state is not None:
    run_id = str(fresh_state["run_id"])
    run_id_source = "runtime_state"
elif fresh_event is not None:
    run_id = str(fresh_event["run_id"])
    run_id_source = "event"

result = {
    "run_id": run_id,
    "run_id_source": run_id_source,
    "runtime_state_seen": fresh_state is not None,
    "runtime_state_run_id": (str(fresh_state["run_id"]) if fresh_state is not None else ""),
    "runtime_state_updated_at": (str(fresh_state["updated_at"]) if fresh_state is not None else ""),
    "runtime_state_last_bar_ts": (str(fresh_state["last_bar_ts"]) if fresh_state is not None and fresh_state["last_bar_ts"] is not None else ""),
    "first_event_seen": fresh_event is not None,
    "first_event_type": (str(fresh_event["event_type"]) if fresh_event is not None else ""),
    "first_event_ts": (str(fresh_event["ts"]) if fresh_event is not None else ""),
    "first_event_run_id": (str(fresh_event["run_id"]) if fresh_event is not None else ""),
}
print(json.dumps(result, ensure_ascii=False))
'@ | uv run --active python -
    return (($raw | Out-String).Trim() | ConvertFrom-Json)
}

function Get-RunMetrics([string]$DbPath, [string]$RunId) {
    $env:OV_DB_PATH = $DbPath
    $env:OV_RUN_ID = $RunId
    $raw = @'
import json
import os
import sqlite3
from collections import Counter

from trader.storage import SQLiteStorage

db_path = os.environ["OV_DB_PATH"]
run_id = os.environ["OV_RUN_ID"].strip()
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
storage = SQLiteStorage(db_path)

state = conn.execute(
    "SELECT run_id,last_bar_ts,open_positions,open_orders,strategy_state,risk_state,updated_at FROM runtime_state WHERE run_id=? LIMIT 1",
    (run_id,),
).fetchone()
if state is None:
    print(json.dumps({"run_id": run_id, "missing": True}))
    raise SystemExit(0)

def parse(raw):
    try:
        return json.loads(raw or "{}")
    except Exception:
        return {}

open_positions = parse(state["open_positions"])
open_orders = parse(state["open_orders"])
strategy_state = parse(state["strategy_state"])
risk_state = parse(state["risk_state"])
status_summary = storage.get_run_status(run_id)

counts = conn.execute(
    """
    SELECT
      (SELECT COUNT(DISTINCT order_id) FROM orders WHERE run_id = ?) AS orders_count,
      (SELECT COUNT(*) FROM orders WHERE run_id = ?) AS orders_row_count,
      (SELECT COUNT(*) FROM fills WHERE run_id = ?) AS fills_row_count,
      (SELECT COUNT(DISTINCT trade_id) FROM trades WHERE run_id = ?) AS trades_count,
      (SELECT COALESCE(SUM(net_pnl), 0.0) FROM trades WHERE run_id = ?) AS trades_net_pnl,
      (
        SELECT COUNT(DISTINCT f.fill_id)
        FROM fills f
        WHERE f.run_id = ?
          AND EXISTS (
            SELECT 1
            FROM orders o
            WHERE o.run_id = f.run_id
              AND o.order_id = f.order_id
          )
      ) AS fills_accounted_count
    """,
    (run_id, run_id, run_id, run_id, run_id, run_id),
).fetchone()

rows = conn.execute("SELECT event_type, payload FROM events ORDER BY id DESC LIMIT 5000").fetchall()
event_counts = Counter()
recent_errors = []
volatility_breaker_trigger_count = 0
protective_orders_created_count = 0
protective_orders_canceled_count = 0
for row in rows:
    payload = parse(row["payload"])
    if str(payload.get("run_id", "")) != run_id:
        continue
    event_type = str(row["event_type"])
    event_counts[event_type] += 1
    if event_type == "protective_orders_created":
        protective_orders_created_count += 1
    if event_type == "protective_order_canceled":
        protective_orders_canceled_count += 1
    if event_type == "risk_halt" and "volatility circuit breaker triggered" in str(payload.get("reason", "")):
        volatility_breaker_trigger_count += 1
    if any(token in event_type.lower() for token in ("error", "exception", "halt", "failed", "reject")) and len(recent_errors) < 20:
        recent_errors.append({"event_type": event_type, "payload": payload})

if isinstance(risk_state, dict) and "strategy" not in risk_state and run_id in risk_state:
    risk_state = risk_state.get(run_id, {})
if isinstance(strategy_state, dict) and "profile_name" not in strategy_state and run_id in strategy_state:
    strategy_state = strategy_state.get(run_id, {})

def as_symbol_map(raw):
    if not isinstance(raw, dict) or not raw:
        return {}
    if "symbol" in raw and isinstance(raw.get("symbol"), str):
        return {str(raw["symbol"]): raw}
    if any("/" in str(k) and isinstance(v, dict) for k, v in raw.items()):
        return {str(k): v for k, v in raw.items() if isinstance(v, dict)}
    return {}

risk_state_by_symbol = as_symbol_map(risk_state)
strategy_state_by_symbol = as_symbol_map(strategy_state)
open_positions_by_symbol = as_symbol_map(open_positions)
open_orders_by_symbol = as_symbol_map(open_orders)

if risk_state_by_symbol:
    first_state = next(iter(risk_state_by_symbol.values()))
    risk_state_summary = dict(first_state)
    risk_state_summary["halted"] = any(bool(state.get("halted")) for state in risk_state_by_symbol.values())
    halt_reasons = sorted(
        {
            str(state.get("halt_reason", "")).strip()
            for state in risk_state_by_symbol.values()
            if str(state.get("halt_reason", "")).strip()
        }
    )
    risk_state_summary["halt_reason"] = "; ".join(halt_reasons)
    risk_state = risk_state_summary
if strategy_state_by_symbol:
    strategy_state = next(iter(strategy_state_by_symbol.values()))

symbols_halted = sum(1 for state in risk_state_by_symbol.values() if bool(state.get("halted"))) if risk_state_by_symbol else (1 if bool(risk_state.get("halted")) else 0)
halt_reason_summary = str((risk_state.get("halt_reason", "") if isinstance(risk_state, dict) else "") or "")

def state_matches(state):
    if not isinstance(state, dict):
        return False
    fixed = state.get("fixed_params", {})
    return (
        str(state.get("regime_name", "")) == "trend_tight_high_adx_extreme_vol_strict_trend"
        and isinstance(fixed, dict)
        and fixed.get("fast_period") == 12
        and fixed.get("slow_period") == 26
        and fixed.get("signal_period") == 9
    )

if risk_state_by_symbol:
    fixed_params_ok = (
        all(str(state.get("strategy", "")) == "macd_final_candidate" for state in risk_state_by_symbol.values())
        and all(str(state.get("candidate_profile", "")) == "macd_final_candidate" for state in risk_state_by_symbol.values())
        and all(state_matches(state) for state in strategy_state_by_symbol.values())
    )
else:
    fixed_params = strategy_state.get("fixed_params", {}) if isinstance(strategy_state, dict) else {}
    fixed_params_ok = (
        isinstance(risk_state, dict)
        and str(risk_state.get("strategy", "")) == "macd_final_candidate"
        and str(risk_state.get("candidate_profile", "")) == "macd_final_candidate"
        and isinstance(strategy_state, dict)
        and state_matches(strategy_state)
        and isinstance(fixed_params, dict)
    )

open_position_symbols = 0
open_order_total = 0
protective_violation = False
processed_total = 0
budget_guard_triggered_count = int(event_counts.get("insufficient_budget", 0))
min_notional_block_count = 0
drawdown_halt = False
daily_loss_halt = False
per_symbol = {}

symbol_names = sorted(set(list(open_positions_by_symbol.keys()) + list(open_orders_by_symbol.keys()) + list(risk_state_by_symbol.keys())))
for sym in symbol_names:
    pos = open_positions_by_symbol.get(sym, {}) if isinstance(open_positions_by_symbol.get(sym), dict) else {}
    oo = open_orders_by_symbol.get(sym, {}) if isinstance(open_orders_by_symbol.get(sym), dict) else {}
    rs = risk_state_by_symbol.get(sym, {}) if isinstance(risk_state_by_symbol.get(sym), dict) else {}
    qty = float(pos.get("qty", 0.0) or 0.0)
    processed = int(rs.get("processed_bars", 0) or 0)
    halted = bool(rs.get("halted", False))
    halt_reason = str(rs.get("halt_reason", "") or "")
    open_count = len([k for k, v in oo.items() if not str(k).startswith("_") and isinstance(v, dict)])
    if abs(qty) > 0:
        open_position_symbols += 1
        if open_count != 2:
            protective_violation = True
    open_order_total += open_count
    processed_total += processed
    min_notional_block_count += int(rs.get("min_entry_notional_block_count", 0) or 0)
    if "drawdown" in halt_reason.lower():
        drawdown_halt = True
    if "daily" in halt_reason.lower():
        daily_loss_halt = True
    per_symbol[sym] = {
        "qty": qty,
        "entry_price": float(pos.get("entry_price", 0.0) or 0.0),
        "open_orders": open_count,
        "processed_bars": processed,
        "halted": halted,
        "halt_reason": halt_reason,
        "last_signal": rs.get("last_signal", ""),
    }

protective_orders_missing_detected = int(event_counts.get("protective_orders_missing", 0)) > 0
protective_lifecycle_anomaly_count = (
    int(event_counts.get("protective_orders_missing", 0))
    + int(status_summary.get("risk_state", {}).get("protective_fail_count", 0) or 0 if isinstance(status_summary.get("risk_state"), dict) else 0)
    + (1 if protective_violation else 0)
)
halt_on_error_triggered = "halt_on_error enabled" in halt_reason_summary.lower() or any(
    "halt_on_error enabled" in json.dumps(row, ensure_ascii=False).lower() for row in recent_errors
)

fills_count = int(status_summary.get("fills_count", 0) or 0)
fills_accounted_count = int(counts["fills_accounted_count"] or 0)
fills_reconciled_count = int(status_summary.get("fills_reconciled_count", 0) or 0)
fills_from_user_stream_count = int(status_summary.get("fills_from_user_stream_count", 0) or 0)
fills_from_rest_reconcile_count = int(status_summary.get("fills_from_rest_reconcile_count", 0) or 0)
fills_from_aggregated_fallback_count = int(status_summary.get("fills_from_aggregated_fallback_count", 0) or 0)
aggregated_fallback_fill_count = int(status_summary.get("aggregated_fallback_fill_count", 0) or 0)
partial_fills_count = int(status_summary.get("partial_fills_count", 0) or 0)
reconciled_missing_ws_fill_count = int(status_summary.get("reconciled_missing_ws_fill_count", 0) or 0)
trade_query_unavailable_count = int(status_summary.get("trade_query_unavailable_count", 0) or 0)
fill_provenance_consistency_pass = bool(status_summary.get("fill_provenance_consistency_pass", False))
fill_provenance_breakdown = status_summary.get("fill_provenance_breakdown", {})
partial_fill_audit_summary = status_summary.get("partial_fill_audit_summary", {})
accounting_consistency_pass = (
    fills_count == fills_accounted_count
    and int(counts["orders_count"] or 0) >= int(counts["trades_count"] or 0)
    and fills_count >= int(counts["trades_count"] or 0)
)

result = {
    "run_id": run_id,
    "updated_at": state["updated_at"],
    "last_bar_ts": state["last_bar_ts"],
    "orders_count": int(counts["orders_count"] or 0),
    "orders_row_count": int(counts["orders_row_count"] or 0),
    "fills_count": fills_count,
    "fills_row_count": int(counts["fills_row_count"] or 0),
    "trades_count": int(counts["trades_count"] or 0),
    "trades_net_pnl": float(counts["trades_net_pnl"] or 0.0),
    "fills_accounted_count": fills_accounted_count,
    "fills_reconciled_count": fills_reconciled_count,
    "fills_from_user_stream_count": fills_from_user_stream_count,
    "fills_from_rest_reconcile_count": fills_from_rest_reconcile_count,
    "fills_from_aggregated_fallback_count": fills_from_aggregated_fallback_count,
    "aggregated_fallback_fill_count": aggregated_fallback_fill_count,
    "partial_fills_count": partial_fills_count,
    "reconciled_missing_ws_fill_count": reconciled_missing_ws_fill_count,
    "trade_query_unavailable_count": trade_query_unavailable_count,
    "accounting_consistency_pass": bool(accounting_consistency_pass),
    "fill_provenance_consistency_pass": bool(fill_provenance_consistency_pass),
    "fill_provenance_breakdown": fill_provenance_breakdown,
    "partial_fill_audit_summary": partial_fill_audit_summary,
    "open_position_symbols": int(open_position_symbols),
    "open_order_total": int(open_order_total),
    "open_positions_final_state": open_positions_by_symbol,
    "open_orders_final_state": open_orders_by_symbol,
    "risk_state": risk_state,
    "strategy_state": strategy_state,
    "risk_state_by_symbol": risk_state_by_symbol,
    "strategy_state_by_symbol": strategy_state_by_symbol,
    "per_symbol": per_symbol,
    "event_counts": dict(event_counts),
    "recent_errors": recent_errors,
    "fixed_params_ok": fixed_params_ok,
    "whether_fixed_params_loaded": fixed_params_ok,
    "protective_orders_created_count": int(protective_orders_created_count),
    "protective_orders_canceled_count": int(protective_orders_canceled_count),
    "protective_lifecycle_anomaly_count": int(protective_lifecycle_anomaly_count),
    "protective_orders_missing_detected": bool(protective_orders_missing_detected),
    "protective_violation": bool(protective_violation),
    "volatility_breaker_trigger_count": int(volatility_breaker_trigger_count),
    "budget_guard_triggered_count": int(budget_guard_triggered_count),
    "min_notional_block_count": int(min_notional_block_count),
    "symbols_halted": int(symbols_halted),
    "halt_reason_summary": halt_reason_summary,
    "processed_total": int(processed_total),
    "drawdown_halt": bool(drawdown_halt),
    "daily_loss_halt": bool(daily_loss_halt),
    "halt_on_error_triggered": bool(halt_on_error_triggered),
}
print(json.dumps(result, ensure_ascii=False))
storage.close()
'@ | uv run --active python -
    return (($raw | Out-String).Trim() | ConvertFrom-Json)
}

function New-EmptyMetrics() {
    return [pscustomobject]@{
        run_id = ""
        updated_at = ""
        last_bar_ts = ""
        orders_count = 0
        orders_row_count = 0
        fills_count = 0
        fills_row_count = 0
        trades_count = 0
        trades_net_pnl = 0.0
        fills_accounted_count = 0
        fills_reconciled_count = 0
        fills_from_user_stream_count = 0
        fills_from_rest_reconcile_count = 0
        fills_from_aggregated_fallback_count = 0
        aggregated_fallback_fill_count = 0
        partial_fills_count = 0
        reconciled_missing_ws_fill_count = 0
        trade_query_unavailable_count = 0
        accounting_consistency_pass = $false
        fill_provenance_consistency_pass = $false
        fill_provenance_breakdown = @{ by_source = @{}; fills_with_source_history_count = 0; fills_reconciled_count = 0 }
        partial_fill_audit_summary = @{
            partial_fill_groups_count = 0
            partial_fill_rows_count = 0
            aggregated_fallback_fill_count = 0
            reconciled_missing_ws_fill_count = 0
            trade_query_unavailable_count = 0
            fills_with_multiple_source_history_count = 0
        }
        open_position_symbols = 0
        open_order_total = 0
        open_positions_final_state = @{}
        open_orders_final_state = @{}
        risk_state = [pscustomobject]@{
            halted = $false
            halt_reason = ""
            strategy = "macd_final_candidate"
            candidate_profile = "macd_final_candidate"
            broker = "live_binance"
            env = "testnet"
            live_trading = $true
            account_total_usdt = 0.0
            account_available_usdt = 0.0
        }
        strategy_state = @{}
        risk_state_by_symbol = @{}
        strategy_state_by_symbol = @{}
        per_symbol = @{}
        event_counts = @{}
        recent_errors = @()
        fixed_params_ok = $false
        whether_fixed_params_loaded = $false
        protective_orders_created_count = 0
        protective_orders_canceled_count = 0
        protective_lifecycle_anomaly_count = 0
        protective_orders_missing_detected = $false
        protective_violation = $false
        volatility_breaker_trigger_count = 0
        budget_guard_triggered_count = 0
        min_notional_block_count = 0
        symbols_halted = 0
        halt_reason_summary = ""
        processed_total = 0
        drawdown_halt = $false
        daily_loss_halt = $false
        halt_on_error_triggered = $false
    }
}

function Get-RunLogSignals([string]$StdOutPath, [string]$StdErrPath) {
    $stdoutText = if (Test-Path $StdOutPath) { Get-Content $StdOutPath -Raw } else { "" }
    $stderrText = if (Test-Path $StdErrPath) { Get-Content $StdErrPath -Raw } else { "" }
    $combined = "$stdoutText`n$stderrText"
    return [pscustomobject]@{
        user_stream_no_running_event_loop = ([regex]::Matches($combined, [regex]::Escape("disconnected (no running event loop)"))).Count
        user_stream_disconnect_count = ([regex]::Matches($combined, [regex]::Escape("[user-stream] disconnected"))).Count
        user_stream_dns_reconnect_count = ([regex]::Matches($combined, [regex]::Escape("Could not contact DNS servers"))).Count
    }
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..")
Set-Location $repoRoot

if ($Hours -le 0) {
    throw "Hours must be > 0"
}
if ($SnapshotEverySec -lt 60) {
    throw "SnapshotEverySec must be >= 60"
}
$ValidationMode = [string]$ValidationMode
if ($ValidationMode -notin @("real_strategy", "pipeline_proof")) {
    throw "ValidationMode must be one of: real_strategy, pipeline_proof"
}
$effectiveTimeframe = [string]$Timeframe
$effectiveRealtimeOnly = [bool]$RealtimeOnly
$effectiveMaxBars = [int]$MaxBars
if ($ValidationMode -eq "pipeline_proof") {
    if (-not $PSBoundParameters.ContainsKey("Timeframe")) {
        $effectiveTimeframe = "1m"
    }
    if (-not $PSBoundParameters.ContainsKey("RealtimeOnly")) {
        $effectiveRealtimeOnly = $true
    }
    if ($effectiveMaxBars -le 0) {
        $effectiveMaxBars = 1
    }
}
if ($StopAfterFirstLiveBar) {
    $effectiveRealtimeOnly = $true
    $effectiveMaxBars = 1
}
$fixedNotionalValue = [double]$FixedNotionalUsdt
$minEntryNotionalValue = [double]$MinEntryNotionalUsdt
if ($fixedNotionalValue -lt $minEntryNotionalValue) {
    throw "FixedNotionalUsdt ($fixedNotionalValue) must be >= MinEntryNotionalUsdt ($minEntryNotionalValue)"
}
if ($OutDir -notmatch "macd_final_candidate_testnet_long") {
    Write-Warning "OutDir does not match the default long-run artifact path. Using caller override."
}

New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
$statusDir = Join-Path $OutDir "status_snapshots"
New-Item -ItemType Directory -Path $statusDir -Force | Out-Null

Write-Header "Prepare Environment"
$env:RUN_FIXED_NOTIONAL_USDT = $FixedNotionalUsdt
$env:MIN_ENTRY_NOTIONAL_USDT = $MinEntryNotionalUsdt
$env:LIVE_TRADING = "true"
$env:LEVERAGE = $Leverage
$env:USE_USER_STREAM = "true"
$env:MAX_ATR_PCT = "1.0"
$env:VALIDATION_PROBE_ENABLED = "true"
$env:VALIDATION_PROBE_ENTRY_AFTER_BARS = "40"
$env:VALIDATION_PROBE_EXIT_AFTER_BARS = "2"
$env:VALIDATION_ALLOW_LIVE_BACKFILL_EXECUTION = "true"

$dbPath = (Resolve-Path "data/trader.db").Path
$preRunLatest = Get-LatestRunId -DbPath $dbPath
$startUtc = (Get-Date).ToUniversalTime()
$nextCloseInfo = Get-NextCloseInfo -Value $effectiveTimeframe -FromUtc $startUtc
$stopPolicy = if ($StopAfterFirstLiveBar) { "graceful_after_first_live_bar" } elseif ($effectiveMaxBars -gt 0) { "graceful_after_max_bars" } else { "wall_deadline" }
$strategyEvidenceAllowed = ($ValidationMode -eq "real_strategy")
$pipelineProofMode = ($ValidationMode -eq "pipeline_proof")
$forcedStopApplied = $false
$forcedStopReason = ""

Write-Header "Doctor Gate"
$doctorFile = Join-Path $OutDir "doctor_preflight.txt"
uv run --active trader doctor --env testnet | Tee-Object -FilePath $doctorFile
if ($LASTEXITCODE -ne 0) {
    throw "Doctor failed. Aborting long-run operational validation."
}

$runStdOut = Join-Path $OutDir "run_stdout.log"
$runStdErr = Join-Path $OutDir "run_stderr.log"
$statusFinal = Join-Path $OutDir "status_final.txt"
$summaryPath = Join-Path $OutDir "summary.json"
$diagnosticSummaryPath = Join-Path $OutDir "diagnostic_summary.json"
$diagnosticMarkdownPath = Join-Path $OutDir "diagnostic_summary.md"
$diagnosticContextPath = Join-Path $OutDir "diagnostic_context.json"
$reconcileAuditPath = Join-Path $OutDir "reconciliation_audit.json"

$runArgs = @(
    "run",
    "--active",
    "trader",
    "run",
    "--mode", "live",
    "--env", "testnet",
    "--data-mode", "websocket",
    "--symbols", $Symbols,
    "--timeframe", $effectiveTimeframe,
    "--strategy", "macd_final_candidate",
    "--preset", $Preset,
    "--halt-on-error",
    "--budget-usdt", "auto",
    "--yes-i-understand-live-risk"
)
if ($effectiveRealtimeOnly) {
    $runArgs += @("--realtime-only")
}
if ($effectiveMaxBars -gt 0) {
    $runArgs += @("--max-bars", [string]$effectiveMaxBars)
}
$runCmd = "uv run --active trader run --mode live --env testnet --data-mode websocket --symbols $Symbols --timeframe $effectiveTimeframe --strategy macd_final_candidate --preset $Preset --halt-on-error --budget-usdt auto --yes-i-understand-live-risk"
if ($effectiveRealtimeOnly) {
    $runCmd = "$runCmd --realtime-only"
}
if ($effectiveMaxBars -gt 0) {
    $runCmd = "$runCmd --max-bars $effectiveMaxBars"
}

Write-Header "Run Command"
Write-Host $runCmd
Write-Host "hours=$Hours"
Write-Host "snapshot_every_sec=$SnapshotEverySec"
Write-Host "timeframe=$effectiveTimeframe"
Write-Host "validation_mode=$ValidationMode"
Write-Host "pipeline_proof_mode=$pipelineProofMode"
Write-Host "strategy_evidence_allowed=$strategyEvidenceAllowed"
Write-Host "realtime_only=$effectiveRealtimeOnly"
Write-Host "stop_policy=$stopPolicy"
Write-Host "expected_next_close_utc=$($nextCloseInfo.expected_next_close_utc)"
Write-Host "minutes_until_next_close=$($nextCloseInfo.minutes_until_next_close)"
Write-Host "preset=$Preset"
Write-Host "validation_probe_enabled=$($env:VALIDATION_PROBE_ENABLED)"
Write-Host "validation_allow_live_backfill_execution=$($env:VALIDATION_ALLOW_LIVE_BACKFILL_EXECUTION)"

$proc = Start-Process `
    -FilePath "uv" `
    -ArgumentList $runArgs `
    -PassThru `
    -RedirectStandardOutput $runStdOut `
    -RedirectStandardError $runStdErr

$runId = ""
$runIdDetectionSource = ""
$snapshots = 0
$startupStalled = $false
$attemptedProcessStarted = ($null -ne $proc)
$freshRunIdDetected = $false
$firstStatusSeen = $false
$firstEventSeen = $false
$runtimeStateSeen = $false
$firstEventType = ""
$firstEventTs = ""
$startupPhase = "process_spawned"
$startupFailureReason = ""
$maxWallSec = [int]([Math]::Ceiling(($Hours * 3600.0) + ($MaxWallBufferMinutes * 60.0)))
$deadline = (Get-Date).AddSeconds($maxWallSec)
$runIdDeadline = (Get-Date).AddMinutes($StartupRunIdTimeoutMinutes)

Write-Header "Snapshot Loop"
while (-not $proc.HasExited) {
    Start-Sleep -Seconds $SnapshotEverySec
    $statusFile = Join-Path $statusDir ("status_" + (Get-Date).ToUniversalTime().ToString("yyyyMMdd_HHmmss") + ".txt")
    if ($runId) {
        uv run --active trader status --run-id $runId *> $statusFile
    } else {
        uv run --active trader status --latest *> $statusFile
    }
    $snapshots++
    $statusText = Get-Content $statusFile -Raw
    $m = [regex]::Match($statusText, "Runtime Status:\s*([0-9a-f]{32})")
    if ($m.Success) {
        $cand = $m.Groups[1].Value
        if ($cand -and ($cand -ne $preRunLatest)) {
            $runId = $cand
            $runIdDetectionSource = if ($runIdDetectionSource) { $runIdDetectionSource } else { "status" }
            $freshRunIdDetected = $true
            $firstStatusSeen = $true
        }
    }
    $startupSignal = Get-FreshRuntimeActivity -DbPath $dbPath -PreRunLatest $preRunLatest -StartedAfterIso $startUtc.ToString("o")
    if ([bool]$startupSignal.first_event_seen) {
        $firstEventSeen = $true
        if (-not $firstEventType) {
            $firstEventType = [string]$startupSignal.first_event_type
            $firstEventTs = [string]$startupSignal.first_event_ts
        }
        if ($startupPhase -eq "process_spawned") {
            $startupPhase = "runtime_started_event_seen"
        }
    }
    if ([bool]$startupSignal.runtime_state_seen) {
        $runtimeStateSeen = $true
        if ($startupPhase -in @("process_spawned", "runtime_started_event_seen")) {
            $startupPhase = "runtime_state_registered"
        }
    }
    if (-not $runId -and [string]$startupSignal.run_id) {
        $runId = [string]$startupSignal.run_id
        $runIdDetectionSource = [string]$startupSignal.run_id_source
        $freshRunIdDetected = $true
    }
    $proc.Refresh()
    if (-not $runId -and (Get-Date) -ge $runIdDeadline) {
        Write-Warning "No new runtime run_id detected before startup timeout. Stopping runtime process."
        $startupStalled = $true
        $forcedStopApplied = $true
        $forcedStopReason = "startup_timeout"
        if ($proc.HasExited) {
            $startupFailureReason = "run process exited before fresh run_id detection"
        } elseif ($firstEventSeen -and -not $runtimeStateSeen) {
            $startupFailureReason = "runtime_started event observed but runtime_state was not registered before timeout"
        } elseif (-not $firstEventSeen) {
            $startupFailureReason = "no runtime_started event or runtime_state observed before startup timeout"
        } else {
            $startupFailureReason = "fresh run_id detection timed out"
        }
        $startupPhase = "startup_timeout"
        try {
            Stop-Process -Id $proc.Id -Force
        } catch {
        }
        break
    }
    if ((Get-Date) -ge $deadline) {
        Write-Warning "Wall-clock deadline reached. Stopping runtime process."
        $forcedStopApplied = $true
        $forcedStopReason = "wall_deadline"
        try {
            Stop-Process -Id $proc.Id -Force
        } catch {
        }
        break
    }
}

$proc.Refresh()
$exitCode = if ($proc.HasExited) { [int]$proc.ExitCode } else { -999 }

Write-Header "Finalize"
$startupSignal = Get-FreshRuntimeActivity -DbPath $dbPath -PreRunLatest $preRunLatest -StartedAfterIso $startUtc.ToString("o")
if ([bool]$startupSignal.first_event_seen) {
    $firstEventSeen = $true
    if (-not $firstEventType) {
        $firstEventType = [string]$startupSignal.first_event_type
        $firstEventTs = [string]$startupSignal.first_event_ts
    }
}
if ([bool]$startupSignal.runtime_state_seen) {
    $runtimeStateSeen = $true
}
if (-not $runId -and [string]$startupSignal.run_id) {
    $runId = [string]$startupSignal.run_id
    $runIdDetectionSource = [string]$startupSignal.run_id_source
    $freshRunIdDetected = $true
}
if ($runId) {
    uv run --active trader status --run-id $runId *> $statusFinal
} else {
    uv run --active trader status --latest *> $statusFinal
}

$reportedRunId = if ($runId -and ($runId -ne $preRunLatest)) { $runId } else { "" }
$statusText = Get-Content $statusFinal -Raw
$m = [regex]::Match($statusText, "Runtime Status:\s*([0-9a-f]{32})")
if ($m.Success) {
    $cand = $m.Groups[1].Value
    if ($cand -and ($cand -eq $reportedRunId)) {
        $firstStatusSeen = $true
    }
}

$metrics = if ($reportedRunId) {
    Get-RunMetrics -DbPath $dbPath -RunId $runId
} else {
    New-EmptyMetrics
}
$logSignals = Get-RunLogSignals -StdOutPath $runStdOut -StdErrPath $runStdErr
$endUtc = (Get-Date).ToUniversalTime()
$firstBarSeen = (([int]$metrics.processed_total -gt 0) -or ([string]$metrics.last_bar_ts -ne ""))
$firstOrderSeen = ([int]$metrics.orders_count -gt 0)
if (-not $startupStalled) {
    if ($firstBarSeen) {
        $startupPhase = "first_bar_processed"
    } elseif ($firstStatusSeen) {
        $startupPhase = "status_written"
    } elseif ($runtimeStateSeen) {
        $startupPhase = "runtime_state_registered"
    } elseif ($firstEventSeen) {
        $startupPhase = "runtime_started_event_seen"
    }
}

$issues = @()
$warnings = @()

if ($exitCode -ne 0 -and $exitCode -ne -999) {
    $issues += "run_process_failed"
}
if ($startupStalled) {
    $issues += "startup_stalled_before_run_id"
}
if ((-not $startupStalled) -and (-not [bool]$metrics.fixed_params_ok)) {
    $issues += "fixed_params_or_regime_drift"
}
if ([bool]$metrics.protective_orders_missing_detected) {
    $issues += "protective_orders_missing_detected"
}
if ([int]$metrics.protective_lifecycle_anomaly_count -gt 0) {
    $issues += "protective_lifecycle_anomaly"
}
if ([bool]$metrics.protective_violation) {
    $issues += "open_position_without_two_protective_orders"
}
if ([int]$metrics.event_counts.runtime_exception -gt 0) {
    $issues += "runtime_exception"
}
if ([int]$metrics.event_counts.live_order_failed -gt 0) {
    $issues += "live_order_failed"
}
if ((-not $startupStalled) -and (-not [bool]$metrics.accounting_consistency_pass)) {
    $issues += "accounting_consistency_failed"
}
if ((-not $startupStalled) -and (-not [bool]$metrics.fill_provenance_consistency_pass)) {
    $issues += "fill_provenance_consistency_failed"
}
if ([int]$metrics.fills_count -eq 0) {
    $issues += "fills_missing"
}
if ([int]$metrics.symbols_halted -gt 0 -or [bool]$metrics.risk_state.halted) {
    $warnings += "symbols_halted_or_runtime_halted"
}
if ([bool]$metrics.drawdown_halt -or [bool]$metrics.daily_loss_halt -or [bool]$metrics.halt_on_error_triggered) {
    $warnings += "halt_condition_triggered"
}
if ([int]$metrics.protective_orders_created_count -eq 0 -or [int]$metrics.protective_orders_canceled_count -eq 0 -or [int]$metrics.trades_count -eq 0) {
    $warnings += "entry_protective_exit_cycle_not_observed"
}
if ([int]$logSignals.user_stream_no_running_event_loop -gt 0) {
    $issues += "user_stream_no_running_event_loop"
}

$lifecycleVerdict = "PASS"
if ($issues.Count -gt 0) {
    $lifecycleVerdict = "FAIL"
} elseif ($warnings.Count -gt 0) {
    $lifecycleVerdict = "WARNING"
}

$accountingDegradedModeUsed = [bool]((
    [int]$metrics.fills_from_rest_reconcile_count -gt 0
) -or (
    [int]$metrics.fills_from_aggregated_fallback_count -gt 0
) -or (
    [int]$logSignals.user_stream_disconnect_count -gt 0
))

$summary = [ordered]@{
    verdict = "PENDING"
    mode = "live"
    out_dir = $OutDir
    validation_mode = $ValidationMode
    pipeline_proof_mode = $pipelineProofMode
    evidence_scope = if ($pipelineProofMode) { "runtime_pipeline_only" } else { "real_strategy_runtime" }
    strategy_evidence_allowed = $strategyEvidenceAllowed
    strategy_lifecycle_validation_applicable = $strategyEvidenceAllowed
    primary_verdict_source = "diagnostic_summary.json"
    strategy_lifecycle_verdict = if ($strategyEvidenceAllowed) { $lifecycleVerdict } else { "not_applicable" }
    runtime_validation_verdict = ""
    runtime_chain_proof_advanced = $false
    feed_runtime_chain_proven = $false
    long_run_gap_closed = $false
    diagnostic_verdict = ""
    diagnostic_reason = ""
    summary_interpretation = ""
    run_id = $reportedRunId
    previous_latest_run_id = $preRunLatest
    attempted_process_started = $attemptedProcessStarted
    fresh_run_id_detected = $freshRunIdDetected
    fresh_run_id_detection_source = $runIdDetectionSource
    startup_phase = $startupPhase
    startup_failure_reason = $startupFailureReason
    first_status_seen = $firstStatusSeen
    first_event_seen = $firstEventSeen
    first_event_type = $firstEventType
    first_event_ts = $firstEventTs
    first_bar_seen = $firstBarSeen
    first_order_seen = $firstOrderSeen
    start_utc = $startUtc.ToString("o")
    end_utc = $endUtc.ToString("o")
    duration_minutes = [Math]::Round(($endUtc - $startUtc).TotalMinutes, 2)
    duration_hours = [Math]::Round(($endUtc - $startUtc).TotalHours, 2)
    timeframe = $effectiveTimeframe
    realtime_only = $effectiveRealtimeOnly
    stop_policy = $stopPolicy
    forced_stop_applied = $forcedStopApplied
    forced_stop_reason = $forcedStopReason
    expected_next_close_utc = [string]$nextCloseInfo.expected_next_close_utc
    minutes_until_next_close_at_start = [double]$nextCloseInfo.minutes_until_next_close
    seconds_until_next_close_at_start = [double]$nextCloseInfo.seconds_until_next_close
    command = $runCmd
    startup_stalled_before_run_id = $startupStalled
    candidate = [ordered]@{
        strategy = "macd_final_candidate"
        timeframe = $effectiveTimeframe
        symbols = $Symbols
        preset = $Preset
        fixed_params_ok = [bool]$metrics.fixed_params_ok
    }
    whether_fixed_params_loaded = [bool]$metrics.whether_fixed_params_loaded
    processed_bars_total = [int]$metrics.processed_total
    last_bar_ts = [string]$metrics.last_bar_ts
    updated_at = [string]$metrics.updated_at
    orders = [int]$metrics.orders_count
    fills = [int]$metrics.fills_count
    trades = [int]$metrics.trades_count
    open_positions_final_state = $metrics.open_positions_final_state
    open_orders_final_state = $metrics.open_orders_final_state
    open_position_symbols = [int]$metrics.open_position_symbols
    open_order_total = [int]$metrics.open_order_total
    protective_orders_created_count = [int]$metrics.protective_orders_created_count
    protective_orders_canceled_count = [int]$metrics.protective_orders_canceled_count
    protective_lifecycle_anomaly_count = [int]$metrics.protective_lifecycle_anomaly_count
    protective_orders_missing_detected = [bool]$metrics.protective_orders_missing_detected
    fills_accounted_count = [int]$metrics.fills_accounted_count
    fills_reconciled_count = [int]$metrics.fills_reconciled_count
    fills_from_user_stream_count = [int]$metrics.fills_from_user_stream_count
    fills_from_rest_reconcile_count = [int]$metrics.fills_from_rest_reconcile_count
    fills_from_aggregated_fallback_count = [int]$metrics.fills_from_aggregated_fallback_count
    accounting_consistency_pass = [bool]$metrics.accounting_consistency_pass
    fill_provenance_consistency_pass = [bool]$metrics.fill_provenance_consistency_pass
    fill_provenance_breakdown = $metrics.fill_provenance_breakdown
    partial_fill_audit_summary = $metrics.partial_fill_audit_summary
    user_stream_no_running_event_loop_count = [int]$logSignals.user_stream_no_running_event_loop
    user_stream_disconnect_count = [int]$logSignals.user_stream_disconnect_count
    user_stream_dns_reconnect_count = [int]$logSignals.user_stream_dns_reconnect_count
    websocket_reconnect_count = [int]$metrics.event_counts.ws_worker_reconnect
    accounting_degraded_mode_used = $accountingDegradedModeUsed
    symbols_halted = [int]$metrics.symbols_halted
    halt_reason_summary = [string]$metrics.halt_reason_summary
    budget_guard_triggered_count = [int]$metrics.budget_guard_triggered_count
    min_notional_block_count = [int]$metrics.min_notional_block_count
    volatility_breaker_trigger_count = [int]$metrics.volatility_breaker_trigger_count
    drawdown_halt = [bool]$metrics.drawdown_halt
    daily_loss_halt = [bool]$metrics.daily_loss_halt
    halt_on_error_triggered = [bool]$metrics.halt_on_error_triggered
    runtime = [ordered]@{
        exit_code = $exitCode
        halted = [bool]$metrics.risk_state.halted
        halt_reason = [string]$metrics.risk_state.halt_reason
        broker = [string]$metrics.risk_state.broker
        env = [string]$metrics.risk_state.env
        live_trading = [bool]$metrics.risk_state.live_trading
        account_total_usdt = [double]$metrics.risk_state.account_total_usdt
        account_available_usdt = [double]$metrics.risk_state.account_available_usdt
    }
    per_symbol = $metrics.per_symbol
    strategy_state = $metrics.strategy_state
    event_counts = $metrics.event_counts
    log_signals = [ordered]@{
        user_stream_no_running_event_loop = [int]$logSignals.user_stream_no_running_event_loop
        user_stream_disconnect_count = [int]$logSignals.user_stream_disconnect_count
        user_stream_dns_reconnect_count = [int]$logSignals.user_stream_dns_reconnect_count
    }
    warnings = @()
    issues = @()
    strategy_lifecycle_warnings = $warnings
    strategy_lifecycle_issues = $issues
    recent_errors = $metrics.recent_errors
    artifacts = [ordered]@{
        doctor_preflight = $doctorFile
        run_stdout = $runStdOut
        run_stderr = $runStdErr
        status_final = $statusFinal
        status_snapshots = $statusDir
        reconciliation_audit = $reconcileAuditPath
    }
}

$reconcileAudit = [ordered]@{
    run_id = $reportedRunId
    previous_latest_run_id = $preRunLatest
    startup_phase = $startupPhase
    startup_failure_reason = $startupFailureReason
    accounting_consistency_pass = [bool]$metrics.accounting_consistency_pass
    fill_provenance_consistency_pass = [bool]$metrics.fill_provenance_consistency_pass
    fills_accounted_count = [int]$metrics.fills_accounted_count
    fills_count = [int]$metrics.fills_count
    fills_reconciled_count = [int]$metrics.fills_reconciled_count
    fills_from_user_stream_count = [int]$metrics.fills_from_user_stream_count
    fills_from_rest_reconcile_count = [int]$metrics.fills_from_rest_reconcile_count
    fills_from_aggregated_fallback_count = [int]$metrics.fills_from_aggregated_fallback_count
    fill_provenance_breakdown = $metrics.fill_provenance_breakdown
    partial_fill_audit_summary = $metrics.partial_fill_audit_summary
    open_position_symbols = [int]$metrics.open_position_symbols
    open_order_total = [int]$metrics.open_order_total
    protective_orders_missing_detected = [bool]$metrics.protective_orders_missing_detected
    protective_lifecycle_anomaly_count = [int]$metrics.protective_lifecycle_anomaly_count
}

$processExitedBeforeRuntimeStopped = $false
if ($reportedRunId -and ([int]$metrics.event_counts.runtime_stopped -le 0) -and $proc.HasExited) {
    $processExitedBeforeRuntimeStopped = $true
}

$diagnosticContext = [ordered]@{
    db_path = $dbPath
    run_id = $reportedRunId
    previous_latest_run_id = $preRunLatest
    strategy = "macd_final_candidate"
    candidate_profile = "macd_final_candidate"
    mode = "live"
    env = "testnet"
    data_mode = "websocket"
    timeframe = $effectiveTimeframe
    validation_mode = $ValidationMode
    pipeline_proof_mode = $pipelineProofMode
    strategy_evidence_allowed = $strategyEvidenceAllowed
    realtime_only = $effectiveRealtimeOnly
    stop_policy = $stopPolicy
    forced_stop_applied = $forcedStopApplied
    forced_stop_reason = $forcedStopReason
    expected_next_close_utc = [string]$nextCloseInfo.expected_next_close_utc
    minutes_until_next_close_at_start = [double]$nextCloseInfo.minutes_until_next_close
    seconds_until_next_close_at_start = [double]$nextCloseInfo.seconds_until_next_close
    symbols = $Symbols
    preset = $Preset
    command = $runCmd
    start_utc = $startUtc.ToString("o")
    end_utc = $endUtc.ToString("o")
    startup_phase = $startupPhase
    startup_failure_reason = $startupFailureReason
    fresh_run_id_detected = $freshRunIdDetected
    run_id_detection_source = $runIdDetectionSource
    first_status_seen = $firstStatusSeen
    first_event_seen = $firstEventSeen
    first_event_type = $firstEventType
    first_event_ts = $firstEventTs
    exit_code = $exitCode
    process_exited_before_runtime_stopped = $processExitedBeforeRuntimeStopped
    doctor_preflight = $doctorFile
    run_stdout = $runStdOut
    run_stderr = $runStdErr
    status_final = $statusFinal
    status_snapshots = $statusDir
}
$diagnosticContext | ConvertTo-Json -Depth 8 | Out-File -FilePath $diagnosticContextPath -Encoding utf8
python -m trader.runtime_diagnostics --context-path $diagnosticContextPath --output-json $diagnosticSummaryPath --output-md $diagnosticMarkdownPath
if ($LASTEXITCODE -ne 0) {
    throw "Runtime diagnostic summary generation failed."
}

$diagnosticSummary = Get-Content $diagnosticSummaryPath -Raw | ConvertFrom-Json
$summary["verdict"] = [string]$diagnosticSummary.verdict
$summary["runtime_validation_verdict"] = [string]$diagnosticSummary.verdict
$summary["runtime_chain_proof_advanced"] = [bool]$diagnosticSummary.runtime_validation_confidence_advanced
$summary["feed_runtime_chain_proven"] = [bool]$diagnosticSummary.feed_runtime_chain_proven
$summary["long_run_gap_closed"] = [bool]$diagnosticSummary.long_run_gap_closed
$summary["diagnostic_verdict"] = [string]$diagnosticSummary.diagnostic_verdict
$summary["diagnostic_reason"] = [string]$diagnosticSummary.diagnostic_reason
$summary["issues"] = @($diagnosticSummary.issues)
$summary["warnings"] = @($diagnosticSummary.warnings)
$summary["summary_interpretation"] = if ($pipelineProofMode) {
    "Pipeline-proof runtime artifact. Top-level verdict reflects runtime-chain validation only; strategy lifecycle fields are informational and not gating."
} else {
    "Real-strategy runtime artifact. Top-level verdict reflects the runtime-validation objective for the incumbent path; strategy lifecycle fields remain secondary context."
}

$summary | ConvertTo-Json -Depth 12 | Out-File -FilePath $summaryPath -Encoding utf8
$reconcileAudit | ConvertTo-Json -Depth 12 | Out-File -FilePath $reconcileAuditPath -Encoding utf8

Write-Header "Result"
Write-Host "verdict=$($summary.verdict)"
Write-Host "strategy_lifecycle_verdict=$($summary.strategy_lifecycle_verdict)"
Write-Host "runtime_validation_verdict=$($summary.runtime_validation_verdict)"
Write-Host "run_id=$reportedRunId"
Write-Host "duration_hours=$($summary.duration_hours)"
Write-Host "orders=$($summary.orders) fills=$($summary.fills) trades=$($summary.trades)"
Write-Host "fill_provenance=$(([string]($summary.fill_provenance_breakdown | ConvertTo-Json -Compress)))"
Write-Host "issues=$(([string[]]$summary.issues) -join ',')"
Write-Host "warnings=$(([string[]]$summary.warnings) -join ',')"
Write-Host "summary=$summaryPath"
Write-Host "diagnostic_summary=$diagnosticSummaryPath"
Write-Host "diagnostic_markdown=$diagnosticMarkdownPath"
