param(
    [string]$Symbols = "BTC/USDT,ETH/USDT,BNB/USDT,SOL/USDT,XRP/USDT,ADA/USDT,DOGE/USDT,AVAX/USDT,LINK/USDT,TRX/USDT",
    [string]$Timeframe = "1m",
    [string]$Strategy = "ema_cross",
    [double]$Hours = 12.0,
    [int]$MaxBars = 0,
    [int]$SnapshotEverySec = 300,
    [string]$Leverage = "20",
    [string]$LiveTrading = "true",
    [string]$FixedNotionalUsdt = "250",
    [string]$MinEntryNotionalUsdt = "250"
)

$ErrorActionPreference = "Stop"

function Write-Header([string]$Text) {
    Write-Host ""
    Write-Host "==== $Text ===="
}

function Get-LatestRunId([string]$DbPath) {
    $env:LF_DB_PATH = $DbPath
    $raw = @'
import os
import sqlite3

db_path = os.environ.get("LF_DB_PATH", "data/trader.db")
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
row = conn.execute("SELECT run_id FROM runtime_state ORDER BY updated_at DESC LIMIT 1").fetchone()
print("" if row is None else str(row["run_id"]))
'@ | uv run --active python -
    return ($raw | Out-String).Trim()
}

function Get-RunMetrics([string]$DbPath, [string]$RunId) {
    $env:LF_DB_PATH = $DbPath
    $env:LF_RUN_ID = $RunId
    $raw = @'
import json
import os
import sqlite3

db_path = os.environ["LF_DB_PATH"]
run_id = os.environ["LF_RUN_ID"].strip()
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

if not run_id:
    row = conn.execute("SELECT run_id FROM runtime_state ORDER BY updated_at DESC LIMIT 1").fetchone()
    run_id = "" if row is None else str(row["run_id"])

state_row = None
if run_id:
    state_row = conn.execute(
        "SELECT run_id,last_bar_ts,open_positions,open_orders,risk_state,updated_at FROM runtime_state WHERE run_id=? LIMIT 1",
        (run_id,),
    ).fetchone()

counts = {"trades": 0, "orders": 0, "fills": 0, "net_pnl": 0.0}
if run_id:
    c = conn.execute(
        """
        SELECT
          (SELECT COUNT(*) FROM trades WHERE run_id=?) AS trades_count,
          (SELECT COUNT(*) FROM orders WHERE run_id=?) AS orders_count,
          (SELECT COUNT(*) FROM fills WHERE run_id=?) AS fills_count,
          (SELECT COALESCE(SUM(net_pnl), 0.0) FROM trades WHERE run_id=?) AS net_pnl
        """,
        (run_id, run_id, run_id, run_id),
    ).fetchone()
    if c is not None:
        counts["trades"] = int(c["trades_count"] or 0)
        counts["orders"] = int(c["orders_count"] or 0)
        counts["fills"] = int(c["fills_count"] or 0)
        counts["net_pnl"] = float(c["net_pnl"] or 0.0)

positions = {}
open_orders = {}
risk_state = {}
updated_at = None
last_bar_ts = None
if state_row is not None:
    updated_at = state_row["updated_at"]
    last_bar_ts = state_row["last_bar_ts"]
    try:
        positions = json.loads(state_row["open_positions"] or "{}")
    except Exception:
        positions = {}
    try:
        open_orders = json.loads(state_row["open_orders"] or "{}")
    except Exception:
        open_orders = {}
    try:
        risk_state = json.loads(state_row["risk_state"] or "{}")
    except Exception:
        risk_state = {}

symbols = sorted(set(list(positions.keys()) + list(open_orders.keys()) + list(risk_state.keys())))
per_symbol = {}
symbols_halted = 0
drawdowns = []
halt_reason = ""
halted_any = False
processed_total = 0
protective_violations = []
rejected_by_min_notional_count = 0
protective_fail_count = 0

for sym in symbols:
    pos = positions.get(sym, {}) if isinstance(positions.get(sym, {}), dict) else {}
    oo = open_orders.get(sym, {}) if isinstance(open_orders.get(sym, {}), dict) else {}
    rs = risk_state.get(sym, {}) if isinstance(risk_state.get(sym, {}), dict) else {}
    qty = float(pos.get("qty", 0.0) or 0.0)
    entry_price = float(pos.get("entry_price", 0.0) or 0.0)
    order_count = len([k for k in oo.keys() if isinstance(k, str) and not k.startswith("_")])
    halted = bool(rs.get("halted", False))
    symbol_halt_reason = str(rs.get("halt_reason", "") or "")
    processed_bars = int(rs.get("processed_bars", 0) or 0)
    rejected_by_min_notional_count += int(rs.get("rejected_by_min_notional_count", 0) or 0)
    protective_fail_count += int(rs.get("protective_fail_count", 0) or 0)
    drawdown_pct = float(rs.get("drawdown_pct", 0.0) or 0.0)
    drawdowns.append(drawdown_pct)
    processed_total += processed_bars
    if halted:
        symbols_halted += 1
        halted_any = True
    if symbol_halt_reason and not halt_reason:
        halt_reason = symbol_halt_reason
    position_open = abs(qty) > 0
    protective_ok = (not position_open) or (order_count == 2)
    if position_open and order_count != 2:
        protective_violations.append({"symbol": sym, "qty": qty, "open_orders": order_count})
    per_symbol[sym] = {
        "qty": qty,
        "entry_price": entry_price,
        "open_orders": order_count,
        "processed_bars": processed_bars,
        "halted": halted,
        "halt_reason": symbol_halt_reason,
        "position_open": position_open,
        "protective_ok": protective_ok,
    }

result = {
    "run_id": run_id,
    "updated_at": updated_at,
    "last_bar_ts": last_bar_ts,
    "trades": counts["trades"],
    "orders": counts["orders"],
    "fills": counts["fills"],
    "net_pnl": counts["net_pnl"],
    "drawdown_pct": max(drawdowns) if drawdowns else 0.0,
    "symbols_halted": symbols_halted,
    "halted": halted_any,
    "halt_reason": halt_reason,
    "processed_total": processed_total,
    "rejected_by_min_notional_count": rejected_by_min_notional_count,
    "protective_fail_count": protective_fail_count,
    "per_symbol": per_symbol,
    "protective_violations": protective_violations,
}
print(json.dumps(result, ensure_ascii=False))
'@ | uv run --active python -
    return (($raw | Out-String).Trim() | ConvertFrom-Json)
}

# Ensure repo root
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..")
Set-Location $repoRoot

$targetBars = if ($MaxBars -gt 0) { $MaxBars } else { [Math]::Max(1, [int][Math]::Round($Hours * 60.0)) }
if ($targetBars -le 0) {
    throw "MaxBars must be > 0"
}
if ($SnapshotEverySec -lt 60) {
    throw "SnapshotEverySec must be >= 60"
}
$fixedNotionalValue = [double]$FixedNotionalUsdt
$minEntryNotionalValue = [double]$MinEntryNotionalUsdt
if ($fixedNotionalValue -lt $minEntryNotionalValue) {
    throw "FixedNotionalUsdt ($fixedNotionalValue) must be >= MinEntryNotionalUsdt ($minEntryNotionalValue)"
}

Write-Header "Prepare Environment"
$env:BINANCE_TESTNET_API_KEY = $null
$env:BINANCE_TESTNET_API_SECRET = $null
$env:BINANCE_API_KEY = $null
$env:BINANCE_API_SECRET = $null
$env:LIVE_TRADING = $LiveTrading
$env:LEVERAGE = $Leverage
$env:RUN_FIXED_NOTIONAL_USDT = $FixedNotionalUsdt
$env:MIN_ENTRY_NOTIONAL_USDT = $MinEntryNotionalUsdt

$startUtc = (Get-Date).ToUniversalTime()
$stamp = $startUtc.ToString("yyyyMMdd_HHmmss")
$outDir = Join-Path "out/experiments" ("live_forward_12h_" + $stamp)
$statusDir = Join-Path $outDir "status_snapshots"
New-Item -ItemType Directory -Path $statusDir -Force | Out-Null

$dbPath = (Resolve-Path "data/trader.db").Path
$preRunLatest = Get-LatestRunId -DbPath $dbPath

Write-Header "Doctor Gate"
$doctorFile = Join-Path $outDir "doctor_preflight.txt"
uv run --active trader doctor --env testnet | Tee-Object -FilePath $doctorFile
if ($LASTEXITCODE -ne 0) {
    throw "Doctor failed. Aborting run."
}

Write-Header "Run Configuration"
Write-Host "symbols=$Symbols"
Write-Host "timeframe=$Timeframe"
Write-Host "strategy=$Strategy"
Write-Host "hours=$Hours"
Write-Host "target_bars=$targetBars"
Write-Host "snapshot_every_sec=$SnapshotEverySec"
Write-Host "env=testnet"
Write-Host "realtime_only=true"
Write-Host "live_trading=$LiveTrading"
Write-Host "leverage=$Leverage"
Write-Host "fixed_notional_usdt=$FixedNotionalUsdt"
Write-Host "min_entry_notional_usdt=$MinEntryNotionalUsdt"

$runStdOut = Join-Path $outDir "run_stdout.log"
$runStdErr = Join-Path $outDir "run_stderr.log"
$runCmd = @(
    "uv run --active trader run",
    "--mode live --env testnet --data-mode websocket",
    "--symbols $Symbols --timeframe $Timeframe --strategy $Strategy",
    "--max-bars $targetBars --realtime-only --budget-usdt auto --halt-on-error --yes-i-understand-live-risk"
) -join " "

Write-Header "Launch Runtime"
Write-Host $runCmd
$proc = Start-Process `
    -FilePath "C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe" `
    -ArgumentList "-NoProfile", "-Command", $runCmd `
    -PassThru `
    -RedirectStandardOutput $runStdOut `
    -RedirectStandardError $runStdErr

$runId = ""
$snapshots = 0
$maxWallSec = [int]([Math]::Ceiling($Hours * 3600.0 + 1800.0))
$deadline = (Get-Date).AddSeconds($maxWallSec)

Write-Header "Snapshot Loop"
while (-not $proc.HasExited) {
    Start-Sleep -Seconds $SnapshotEverySec
    $nowUtc = (Get-Date).ToUniversalTime().ToString("yyyyMMdd_HHmmss")
    $statusFile = Join-Path $statusDir ("status_" + $nowUtc + ".txt")
    $statusText = uv run --active trader status --latest
    $statusText | Tee-Object -FilePath $statusFile
    $snapshots++

    if (-not $runId) {
        $m = [regex]::Match(($statusText | Out-String), "Runtime Status:\s*([0-9a-f]{32})")
        if ($m.Success) {
            $cand = $m.Groups[1].Value
            if ($cand -and ($cand -ne $preRunLatest)) {
                $runId = $cand
            }
        }
    }

    $proc.Refresh()
    if ((Get-Date) -ge $deadline) {
        Write-Warning "Wall-clock deadline reached. Stopping runtime process."
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
$finalStatusFile = Join-Path $outDir "status_final.txt"
$finalStatus = uv run --active trader status --latest
$finalStatus | Tee-Object -FilePath $finalStatusFile

if (-not $runId) {
    $mFinal = [regex]::Match(($finalStatus | Out-String), "Runtime Status:\s*([0-9a-f]{32})")
    if ($mFinal.Success) {
        $cand = $mFinal.Groups[1].Value
        if ($cand -and ($cand -ne $preRunLatest)) {
            $runId = $cand
        }
    }
}
if (-not $runId -and (Test-Path $runStdOut)) {
    $runText = Get-Content $runStdOut -Raw
    $mOut = [regex]::Match($runText, "'run_id'\s*:\s*'([0-9a-f]{32})'")
    if ($mOut.Success) {
        $runId = $mOut.Groups[1].Value
    }
}
if (-not $runId) {
    $runId = (Get-LatestRunId -DbPath $dbPath)
}

$metrics = Get-RunMetrics -DbPath $dbPath -RunId $runId

if (Test-Path "logs/trader.log") {
    Copy-Item "logs/trader.log" (Join-Path $outDir "trader.log") -Force
}

$endUtc = (Get-Date).ToUniversalTime()
$tradeWarn = $false
if ([int]$metrics.trades -eq 0) {
    $tradeWarn = $true
}
$warnings = @()
if ($tradeWarn) {
    $warnings += "no_trades_observed"
}

$protectiveViolations = @()
if ($metrics.protective_violations) {
    $protectiveViolations = @($metrics.protective_violations)
}

$pass = $true
$failReasons = @()
if ([bool]$metrics.halted) {
    $pass = $false
    $failReasons += "halted=true"
}
if ([int]$metrics.symbols_halted -gt 0) {
    $pass = $false
    $failReasons += "symbols_halted=$($metrics.symbols_halted)"
}
if ($protectiveViolations.Count -gt 0) {
    $pass = $false
    $failReasons += "protective_orders_not_2_for_open_positions"
}

$summary = [ordered]@{
    pass = $pass
    verdict = $(if ($pass) { "PASS" } else { "FAIL" })
    fail_reasons = $failReasons
    warnings = $warnings
    out_dir = $outDir
    run_id = $runId
    start_utc = $startUtc.ToString("o")
    end_utc = $endUtc.ToString("o")
    duration_minutes = [Math]::Round(($endUtc - $startUtc).TotalMinutes, 2)
    target = [ordered]@{
        hours = $Hours
        bars = $targetBars
        timeframe = $Timeframe
        symbols = $Symbols
        realtime_only = $true
        env = "testnet"
    }
    runtime = [ordered]@{
        exit_code = $exitCode
        halted = [bool]$metrics.halted
        halt_reason = [string]$metrics.halt_reason
        symbols_halted = [int]$metrics.symbols_halted
        last_bar_ts = [string]$metrics.last_bar_ts
        updated_at = [string]$metrics.updated_at
    }
    metrics = [ordered]@{
        processed_bars_total = [int]$metrics.processed_total
        trades = [int]$metrics.trades
        orders = [int]$metrics.orders
        fills = [int]$metrics.fills
        rejected_by_min_notional_count = [int]$metrics.rejected_by_min_notional_count
        protective_fail_count = [int]$metrics.protective_fail_count
        net_pnl = [double]$metrics.net_pnl
        drawdown_pct = [double]$metrics.drawdown_pct
    }
    per_symbol = $metrics.per_symbol
    protective_violations = $protectiveViolations
    snapshots = [ordered]@{
        interval_sec = $SnapshotEverySec
        count = $snapshots
        dir = $statusDir
    }
    artifacts = [ordered]@{
        doctor = $doctorFile
        run_stdout = $runStdOut
        run_stderr = $runStdErr
        status_final = $finalStatusFile
        trader_log = $(Join-Path $outDir "trader.log")
    }
}

$summaryPath = Join-Path $outDir "summary.json"
$summary | ConvertTo-Json -Depth 12 | Out-File -FilePath $summaryPath -Encoding utf8

Write-Header "Result"
Write-Host "verdict=$($summary.verdict)"
Write-Host "run_id=$runId"
Write-Host "duration_minutes=$($summary.duration_minutes)"
Write-Host "trades=$($summary.metrics.trades) orders=$($summary.metrics.orders) fills=$($summary.metrics.fills)"
Write-Host "rejected_by_min_notional_count=$($summary.metrics.rejected_by_min_notional_count) protective_fail_count=$($summary.metrics.protective_fail_count)"
Write-Host "net_pnl=$($summary.metrics.net_pnl) drawdown_pct=$($summary.metrics.drawdown_pct)"
Write-Host "symbols_halted=$($summary.runtime.symbols_halted) halted=$($summary.runtime.halted)"
if ($tradeWarn) {
    Write-Warning "No trades observed during run window."
}
if (-not $pass) {
    Write-Warning ("FAIL reasons: " + ($failReasons -join ", "))
}
Write-Host "summary=$summaryPath"
Write-Host "out_dir=$outDir"
