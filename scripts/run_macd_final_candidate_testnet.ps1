param(
    [string]$Symbols = "BTC/USDT,ETH/USDT,BNB/USDT",
    [int]$MaxBars = 60,
    [int]$SnapshotEverySec = 10
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
& (Join-Path $scriptDir "run_macd_final_candidate_validation.ps1") `
    -Mode live `
    -Symbols $Symbols `
    -MaxBars $MaxBars `
    -SnapshotEverySec $SnapshotEverySec
