param(
    [ValidateSet("paper", "live")]
    [string]$Mode = "paper",
    [string]$Symbols = "BTC/USDT,ETH/USDT,BNB/USDT",
    [int]$MaxBars = 60,
    [int]$SnapshotEverySec = 10,
    [string]$Preset = "macd_final_candidate_ops",
    [string]$FixedNotionalUsdt = "250",
    [string]$MinEntryNotionalUsdt = "250",
    [string]$Leverage = "20",
    [string]$OutDir = ""
)

$ErrorActionPreference = "Stop"

function Write-Header([string]$Text) {
    Write-Host ""
    Write-Host "==== $Text ===="
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

rows = conn.execute("SELECT event_type, payload FROM events ORDER BY id DESC LIMIT 1000").fetchall()
event_counts = Counter()
recent_errors = []
volatility_breaker_trigger_count = 0
protective_orders_created_count = 0
for row in rows:
    payload = parse(row["payload"])
    if str(payload.get("run_id", "")) != run_id:
        continue
    event_type = str(row["event_type"])
    event_counts[event_type] += 1
    if event_type == "protective_orders_created":
        protective_orders_created_count += 1
    if event_type == "risk_halt" and "volatility circuit breaker triggered" in str(payload.get("reason", "")):
        volatility_breaker_trigger_count += 1
    if any(token in event_type.lower() for token in ("error", "halt", "failed", "reject")) and len(recent_errors) < 10:
        recent_errors.append({"event_type": event_type, "payload": payload})

if isinstance(risk_state, dict) and "strategy" not in risk_state and run_id in risk_state:
    risk_state = risk_state.get(run_id, {})
if isinstance(strategy_state, dict) and "profile_name" not in strategy_state and run_id in strategy_state:
    strategy_state = strategy_state.get(run_id, {})

def as_symbol_map(raw):
    if not isinstance(raw, dict) or not raw:
        return {}
    if any(isinstance(v, dict) for v in raw.values()):
        return {str(k): v for k, v in raw.items() if isinstance(v, dict)}
    return {}

risk_state_by_symbol = as_symbol_map(risk_state)
strategy_state_by_symbol = as_symbol_map(strategy_state)
if risk_state_by_symbol:
    first_state = next(iter(risk_state_by_symbol.values()))
    risk_state_summary = dict(first_state)
    risk_state_summary["halted"] = any(bool(state.get("halted")) for state in risk_state_by_symbol.values())
    halt_reasons = sorted({str(state.get("halt_reason", "")).strip() for state in risk_state_by_symbol.values() if str(state.get("halt_reason", "")).strip()})
    risk_state_summary["halt_reason"] = "; ".join(halt_reasons)
    risk_state = risk_state_summary
if strategy_state_by_symbol:
    strategy_state = next(iter(strategy_state_by_symbol.values()))

is_live_runtime = bool(risk_state.get("live_trading")) if isinstance(risk_state, dict) else False

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
        and str(strategy_state.get("regime_name", "")) == "trend_tight_high_adx_extreme_vol_strict_trend"
        and fixed_params.get("fast_period") == 12
        and fixed_params.get("slow_period") == 26
        and fixed_params.get("signal_period") == 9
    )

protective_violation = False
open_position_symbols = 0
open_order_total = 0
if isinstance(open_positions, dict) and isinstance(open_orders, dict):
    for sym, pos in open_positions.items():
        if not isinstance(pos, dict):
            continue
        qty = float(pos.get("qty", 0.0) or 0.0)
        if abs(qty) <= 0:
            continue
        open_position_symbols += 1
        sym_orders = open_orders.get(sym, {}) if isinstance(open_orders.get(sym, {}), dict) else {}
        live_order_count = len([k for k in sym_orders.keys() if isinstance(k, str) and not k.startswith("_")])
        open_order_total += live_order_count
        if live_order_count != 2:
            protective_violation = True
            break
if isinstance(open_orders, dict):
    for sym, payload in open_orders.items():
        if not isinstance(sym, str) or sym.startswith("_"):
            continue
        if not isinstance(payload, dict):
            continue
        if "order_id" in payload:
            open_order_total += 1
            continue
        open_order_total += len([k for k in payload.keys() if isinstance(k, str) and not k.startswith("_")])

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
    and not protective_violation
    and open_position_symbols == 0
    and open_order_total == 0
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
    "fills_from_rest_reconcile_count": int(fills_from_rest_reconcile_count),
    "fills_from_aggregated_fallback_count": fills_from_aggregated_fallback_count,
    "aggregated_fallback_fill_count": aggregated_fallback_fill_count,
    "partial_fills_count": partial_fills_count,
    "reconciled_missing_ws_fill_count": reconciled_missing_ws_fill_count,
    "trade_query_unavailable_count": trade_query_unavailable_count,
    "accounting_consistency_pass": bool(accounting_consistency_pass),
    "fill_provenance_consistency_pass": fill_provenance_consistency_pass,
    "fill_provenance_breakdown": fill_provenance_breakdown,
    "partial_fill_audit_summary": partial_fill_audit_summary,
    "open_position_symbols": int(open_position_symbols),
    "open_order_total": int(open_order_total),
    "risk_state": risk_state,
    "strategy_state": strategy_state,
    "event_counts": dict(event_counts),
    "recent_errors": recent_errors,
    "fixed_params_ok": fixed_params_ok,
    "whether_fixed_params_loaded": fixed_params_ok,
    "protective_violation": protective_violation,
    "volatility_breaker_trigger_count": int(volatility_breaker_trigger_count),
    "protective_orders_created_count": int(protective_orders_created_count),
    "symbols_halted": int(symbols_halted),
    "halt_reason_summary": halt_reason_summary,
}
print(json.dumps(result, ensure_ascii=False))
storage.close()
'@ | uv run --active python -
    return (($raw | Out-String).Trim() | ConvertFrom-Json)
}

function New-EmptyMetrics([string]$Mode) {
    return [pscustomobject]@{
        run_id = ""
        updated_at = ""
        last_bar_ts = ""
        orders_count = 0
        fills_count = 0
        trades_count = 0
        trades_net_pnl = 0.0
        risk_state = [pscustomobject]@{
            halted = $false
            halt_reason = ""
            strategy = ""
            candidate_profile = ""
            broker = ""
            env = "testnet"
            live_trading = ($Mode -eq "live")
            account_total_usdt = 0.0
            account_available_usdt = 0.0
        }
        strategy_state = @{}
        event_counts = @{}
        recent_errors = @()
        fixed_params_ok = $false
        protective_violation = $false
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

if (-not $OutDir) {
    $OutDir = if ($Mode -eq "paper") {
        "out/operational_validation/macd_final_candidate_paper"
    } else {
        "out/operational_validation/macd_final_candidate_testnet"
    }
}

New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
$statusDir = Join-Path $OutDir "status_snapshots"
New-Item -ItemType Directory -Path $statusDir -Force | Out-Null

$fixedNotionalValue = [double]$FixedNotionalUsdt
$minEntryNotionalValue = [double]$MinEntryNotionalUsdt
if ($fixedNotionalValue -lt $minEntryNotionalValue) {
    throw "FixedNotionalUsdt ($fixedNotionalValue) must be >= MinEntryNotionalUsdt ($minEntryNotionalValue)"
}

Write-Header "Prepare Environment"
$env:RUN_FIXED_NOTIONAL_USDT = $FixedNotionalUsdt
$env:MIN_ENTRY_NOTIONAL_USDT = $MinEntryNotionalUsdt
$env:LIVE_TRADING = if ($Mode -eq "live") { "true" } else { "false" }
$env:LEVERAGE = $Leverage
$env:USE_USER_STREAM = if ($Mode -eq "live") { "true" } else { "false" }
$env:MAX_ATR_PCT = "1.0"
$env:VALIDATION_PROBE_ENABLED = "true"
$env:VALIDATION_PROBE_ENTRY_AFTER_BARS = "40"
$env:VALIDATION_PROBE_EXIT_AFTER_BARS = "2"
$env:VALIDATION_ALLOW_LIVE_BACKFILL_EXECUTION = if ($Mode -eq "live") { "true" } else { "false" }

$dbPath = (Resolve-Path "data/trader.db").Path
$preRunLatest = Get-LatestRunId -DbPath $dbPath
$startUtc = (Get-Date).ToUniversalTime()

Write-Header "Doctor Gate"
$doctorFile = Join-Path $OutDir "doctor_preflight.txt"
uv run --active trader doctor --env testnet | Tee-Object -FilePath $doctorFile
if ($LASTEXITCODE -ne 0) {
    throw "Doctor failed. Aborting operational validation."
}

$runStdOut = Join-Path $OutDir "run_stdout.log"
$runStdErr = Join-Path $OutDir "run_stderr.log"
$statusFinal = Join-Path $OutDir "status_final.txt"
$summaryPath = Join-Path $OutDir "summary.json"

$modeArgs = if ($Mode -eq "live") {
    "--mode live --env testnet --data-mode websocket --symbols $Symbols --timeframe 4h --strategy macd_final_candidate --preset $Preset --max-bars $MaxBars --halt-on-error --yes-i-understand-live-risk"
} else {
    "--mode paper --env testnet --data-mode websocket --symbols $Symbols --timeframe 4h --strategy macd_final_candidate --preset $Preset --max-bars $MaxBars --halt-on-error"
}
$runCmd = "uv run --active trader run $modeArgs"

Write-Header "Run Command"
Write-Host $runCmd

$proc = Start-Process `
    -FilePath "C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe" `
    -ArgumentList "-NoProfile", "-Command", $runCmd `
    -PassThru `
    -RedirectStandardOutput $runStdOut `
    -RedirectStandardError $runStdErr

$runId = ""
$snapshots = 0
Write-Header "Snapshot Loop"
while (-not $proc.HasExited) {
    Start-Sleep -Seconds $SnapshotEverySec
    $statusFile = Join-Path $statusDir ("status_" + (Get-Date).ToUniversalTime().ToString("yyyyMMdd_HHmmss") + ".txt")
    uv run --active trader status --latest *> $statusFile
    $snapshots++
    if (-not $runId) {
        $statusText = Get-Content $statusFile -Raw
        $m = [regex]::Match($statusText, "Runtime Status:\s*([0-9a-f]{32})")
        if ($m.Success) {
            $cand = $m.Groups[1].Value
            if ($cand -and ($cand -ne $preRunLatest)) {
                $runId = $cand
            }
        }
    }
    $proc.Refresh()
}

$proc.Refresh()
$exitCode = [int]$proc.ExitCode
uv run --active trader status --latest *> $statusFinal

if (-not $runId) {
    $statusText = Get-Content $statusFinal -Raw
    $m = [regex]::Match($statusText, "Runtime Status:\s*([0-9a-f]{32})")
    if ($m.Success) {
        $cand = $m.Groups[1].Value
        if ($cand -and ($cand -ne $preRunLatest)) {
            $runId = $cand
        }
    }
}
if (-not $runId) {
    $runId = Get-LatestRunId -DbPath $dbPath
}

$metrics = if ($exitCode -eq 0 -and $runId) {
    Get-RunMetrics -DbPath $dbPath -RunId $runId
} else {
    New-EmptyMetrics -Mode $Mode
}
$logSignals = Get-RunLogSignals -StdOutPath $runStdOut -StdErrPath $runStdErr
$endUtc = (Get-Date).ToUniversalTime()

$warnings = @()
$issues = @()
if ($exitCode -ne 0) {
    $issues += "run_process_failed"
}
if (-not [bool]$metrics.fixed_params_ok) {
    $issues += "fixed_params_or_regime_drift"
}
if ([int]$metrics.event_counts.protective_orders_missing -gt 0) {
    $issues += "protective_orders_missing"
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
if ([int]$metrics.event_counts.api_error_halt -gt 0 -or [bool]$metrics.risk_state.halted) {
    $issues += "runtime_halted"
}
if ([int]$metrics.trades_count -eq 0) {
    $warnings += "no_trades_observed"
}
if ($Mode -eq "live" -and [int]$metrics.event_counts.live_backfill_signal_suppressed -gt 0) {
    $warnings += "live_backfill_signals_safely_suppressed"
}
if ($Mode -eq "live" -and [int]$logSignals.user_stream_no_running_event_loop -gt 0) {
    $issues += "user_stream_no_running_event_loop"
}

$verdict = "PASS"
if ($issues.Count -gt 0) {
    $verdict = "FAIL"
} elseif ($warnings.Count -gt 0) {
    $verdict = "WARNING"
}

$summary = [ordered]@{
    verdict = $verdict
    mode = $Mode
    out_dir = $OutDir
    run_id = $runId
    start_utc = $startUtc.ToString("o")
    end_utc = $endUtc.ToString("o")
    duration_minutes = [Math]::Round(($endUtc - $startUtc).TotalMinutes, 2)
    command = $runCmd
    candidate = [ordered]@{
        strategy = "macd_final_candidate"
        timeframe = "4h"
        symbols = $Symbols
        preset = $Preset
        fixed_params_ok = [bool]$metrics.fixed_params_ok
    }
    whether_fixed_params_loaded = [bool]$metrics.whether_fixed_params_loaded
    volatility_breaker_trigger_count = [int]$metrics.volatility_breaker_trigger_count
    user_stream_no_running_event_loop_count = [int]$logSignals.user_stream_no_running_event_loop
    user_stream_disconnect_count = [int]$logSignals.user_stream_disconnect_count
    user_stream_dns_reconnect_count = [int]$logSignals.user_stream_dns_reconnect_count
    protective_orders_created_count = [int]$metrics.protective_orders_created_count
    symbols_halted = [int]$metrics.symbols_halted
    halt_reason_summary = [string]$metrics.halt_reason_summary
    fills_accounted_count = [int]$metrics.fills_accounted_count
    fills_reconciled_count = [int]$metrics.fills_reconciled_count
    fills_from_user_stream_count = [int]$metrics.fills_from_user_stream_count
    fills_from_rest_reconcile_count = [int]$metrics.fills_from_rest_reconcile_count
    fills_from_aggregated_fallback_count = [int]$metrics.fills_from_aggregated_fallback_count
    aggregated_fallback_fill_count = [int]$metrics.aggregated_fallback_fill_count
    partial_fills_count = [int]$metrics.partial_fills_count
    reconciled_missing_ws_fill_count = [int]$metrics.reconciled_missing_ws_fill_count
    trade_query_unavailable_count = [int]$metrics.trade_query_unavailable_count
    accounting_consistency_pass = [bool]$metrics.accounting_consistency_pass
    fill_provenance_consistency_pass = [bool]$metrics.fill_provenance_consistency_pass
    fill_provenance_breakdown = $metrics.fill_provenance_breakdown
    partial_fill_audit_summary = $metrics.partial_fill_audit_summary
    accounting_degraded_mode_used = [bool](($Mode -eq "live") -and (
        ([int]$metrics.fills_from_rest_reconcile_count -gt 0) -or
        ([int]$metrics.fills_from_aggregated_fallback_count -gt 0) -or
        ([int]$logSignals.user_stream_disconnect_count -gt 0)
    ))
    metrics = [ordered]@{
        orders = [int]$metrics.orders_count
        fills = [int]$metrics.fills_count
        trades = [int]$metrics.trades_count
        net_pnl = [double]$metrics.trades_net_pnl
        last_bar_ts = [string]$metrics.last_bar_ts
        updated_at = [string]$metrics.updated_at
        orders_row_count = [int]$metrics.orders_row_count
        fills_row_count = [int]$metrics.fills_row_count
    }
    runtime = [ordered]@{
        halted = [bool]$metrics.risk_state.halted
        halt_reason = [string]$metrics.risk_state.halt_reason
        strategy = [string]$metrics.risk_state.strategy
        candidate_profile = [string]$metrics.risk_state.candidate_profile
        broker = [string]$metrics.risk_state.broker
        env = [string]$metrics.risk_state.env
        live_trading = [bool]$metrics.risk_state.live_trading
        account_total_usdt = [double]$metrics.risk_state.account_total_usdt
        account_available_usdt = [double]$metrics.risk_state.account_available_usdt
    }
    strategy_state = $metrics.strategy_state
    event_counts = $metrics.event_counts
    log_signals = [ordered]@{
        user_stream_no_running_event_loop = [int]$logSignals.user_stream_no_running_event_loop
        user_stream_disconnect_count = [int]$logSignals.user_stream_disconnect_count
        user_stream_dns_reconnect_count = [int]$logSignals.user_stream_dns_reconnect_count
    }
    warnings = $warnings
    issues = $issues
    recent_errors = $metrics.recent_errors
    artifacts = [ordered]@{
        doctor_preflight = $doctorFile
        run_stdout = $runStdOut
        run_stderr = $runStdErr
        status_final = $statusFinal
        status_snapshots = $statusDir
    }
}

$summary | ConvertTo-Json -Depth 12 | Out-File -FilePath $summaryPath -Encoding utf8

Write-Header "Result"
Write-Host "verdict=$verdict"
Write-Host "run_id=$runId"
Write-Host "orders=$($summary.metrics.orders) fills=$($summary.metrics.fills) trades=$($summary.metrics.trades)"
Write-Host "fill_provenance=$(([string]($summary.fill_provenance_breakdown | ConvertTo-Json -Compress)))"
Write-Host "issues=$($issues -join ',')"
Write-Host "warnings=$($warnings -join ',')"
Write-Host "summary=$summaryPath"
