param(
    [string]$Symbols = "BTC/USDT,ETH/USDT,BNB/USDT",
    [string]$Timeframe = "1m",
    [string]$Strategy = "ema_cross",
    [int]$MaxBars = 120,
    [int]$SnapshotEverySec = 300,
    [switch]$RealtimeOnly = $true,
    [string]$FixedNotionalUsdt = "250",
    [string]$MinEntryNotionalUsdt = "250"
)

$ErrorActionPreference = "Stop"

function Write-Header([string]$Text) {
    Write-Host ""
    Write-Host "==== $Text ===="
}

Write-Header "Prepare Environment"
$fixedNotionalValue = [double]$FixedNotionalUsdt
$minEntryNotionalValue = [double]$MinEntryNotionalUsdt
if ($fixedNotionalValue -lt $minEntryNotionalValue) {
    throw "FixedNotionalUsdt ($fixedNotionalValue) must be >= MinEntryNotionalUsdt ($minEntryNotionalValue)"
}
$env:BINANCE_TESTNET_API_KEY = $null
$env:BINANCE_TESTNET_API_SECRET = $null
$env:BINANCE_API_KEY = $null
$env:BINANCE_API_SECRET = $null
$env:LIVE_TRADING = "true"
$env:LEVERAGE = "20"
$env:RUN_FIXED_NOTIONAL_USDT = $FixedNotionalUsdt
$env:MIN_ENTRY_NOTIONAL_USDT = $MinEntryNotionalUsdt

$startUtc = (Get-Date).ToUniversalTime()
$stamp = $startUtc.ToString("yyyyMMdd_HHmmss")
$outDir = Join-Path "out/experiments" ("live_forward_2h_" + $stamp)
New-Item -ItemType Directory -Path $outDir -Force | Out-Null

Write-Header "Doctor Gate"
$doctorFile = Join-Path $outDir "doctor_preflight.txt"
uv run --active trader doctor --env testnet | Tee-Object -FilePath $doctorFile
if ($LASTEXITCODE -ne 0) {
    throw "Doctor failed. Aborting 2h run."
}

$runStdOut = Join-Path $outDir "run_stdout.log"
$runStdErr = Join-Path $outDir "run_stderr.log"
$statusDir = Join-Path $outDir "status_snapshots"
New-Item -ItemType Directory -Path $statusDir -Force | Out-Null

$rtOpt = ""
if ($RealtimeOnly) {
    $rtOpt = "--realtime-only"
}

$runCmd = @(
    "uv run --active trader run",
    "--mode live --env testnet --data-mode websocket",
    "--symbols $Symbols --timeframe $Timeframe --strategy $Strategy",
    "--max-bars $MaxBars $rtOpt --halt-on-error --yes-i-understand-live-risk"
) -join " "

Write-Header "Run Command"
Write-Host "fixed_notional_usdt=$FixedNotionalUsdt"
Write-Host "min_entry_notional_usdt=$MinEntryNotionalUsdt"
Write-Host $runCmd

$proc = Start-Process `
    -FilePath "C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe" `
    -ArgumentList "-NoProfile", "-Command", $runCmd `
    -PassThru `
    -RedirectStandardOutput $runStdOut `
    -RedirectStandardError $runStdErr

$snapshots = 0
Write-Header "Snapshot Loop"
while (-not $proc.HasExited) {
    Start-Sleep -Seconds $SnapshotEverySec
    $now = (Get-Date).ToUniversalTime().ToString("yyyyMMdd_HHmmss")
    $statusFile = Join-Path $statusDir ("status_" + $now + ".txt")
    uv run --active trader status --latest *> $statusFile
    $snapshots++
    Write-Host "snapshot[$snapshots] -> $statusFile"
    $proc.Refresh()
}

$proc.Refresh()
$exitCode = $proc.ExitCode

Write-Header "Finalize"
$finalStatus = Join-Path $outDir "status_final.txt"
uv run --active trader status --latest *> $finalStatus

if (Test-Path "logs/trader.log") {
    Copy-Item "logs/trader.log" (Join-Path $outDir "trader.log") -Force
}

$runText = if (Test-Path $runStdOut) { Get-Content $runStdOut -Raw } else { "" }
$runId = ""
$m = [regex]::Match($runText, "'run_id'\s*:\s*'([0-9a-f]{32})'")
if ($m.Success) {
    $runId = $m.Groups[1].Value
}

$endUtc = (Get-Date).ToUniversalTime()
$summary = [ordered]@{
    out_dir = $outDir
    run_id = $runId
    start_utc = $startUtc.ToString("o")
    end_utc = $endUtc.ToString("o")
    duration_minutes = [math]::Round(($endUtc - $startUtc).TotalMinutes, 2)
    symbols = $Symbols
    timeframe = $Timeframe
    strategy = $Strategy
    max_bars = $MaxBars
    realtime_only = [bool]$RealtimeOnly
    status_snapshots = $snapshots
    run_exit_code = $exitCode
}
$summaryPath = Join-Path $outDir "summary.json"
$summary | ConvertTo-Json -Depth 8 | Out-File -FilePath $summaryPath -Encoding utf8

Write-Host "OUT_DIR=$outDir"
Write-Host "RUN_ID=$runId"
Write-Host "EXIT_CODE=$exitCode"
Write-Host "SUMMARY=$summaryPath"
