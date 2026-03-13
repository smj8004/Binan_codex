param(
    [string]$Symbols = "BTC/USDT,ETH/USDT,BNB/USDT,SOL/USDT,XRP/USDT,ADA/USDT,DOGE/USDT,AVAX/USDT,LINK/USDT,TRX/USDT",
    [string]$Timeframe = "1m",
    [string]$Strategy = "ema_cross",
    [int]$MaxBars = 0,
    [int]$SnapshotEverySec = 300,
    [string]$Leverage = "20",
    [string]$LiveTrading = "true",
    [string]$FixedNotionalUsdt = "250",
    [string]$MinEntryNotionalUsdt = "250"
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$runner12h = Join-Path $scriptDir "run_live_forward_12h.ps1"
if (-not (Test-Path $runner12h)) {
    throw "Missing dependency script: $runner12h"
}

& $runner12h `
    -Symbols $Symbols `
    -Timeframe $Timeframe `
    -Strategy $Strategy `
    -Hours 16.0 `
    -MaxBars $MaxBars `
    -SnapshotEverySec $SnapshotEverySec `
    -Leverage $Leverage `
    -LiveTrading $LiveTrading `
    -FixedNotionalUsdt $FixedNotionalUsdt `
    -MinEntryNotionalUsdt $MinEntryNotionalUsdt
