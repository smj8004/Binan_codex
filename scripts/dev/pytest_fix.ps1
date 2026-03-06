# pytest_fix.ps1 - Pytest stabilization script for Windows
# Usage: .\scripts\dev\pytest_fix.ps1 [pytest args...]

param([switch]$Clean, [switch]$Verbose)

Write-Host "=== Pytest Stabilization ===" -ForegroundColor Cyan

# 1. Force UTF-8 encoding
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
Write-Host "[1/5] UTF-8 encoding set" -ForegroundColor Green

# 2. Reset VIRTUAL_ENV
$env:VIRTUAL_ENV = $null
Write-Host "[2/5] VIRTUAL_ENV reset" -ForegroundColor Green

# 3. Clean old Python processes (older than 10 minutes)
$oldProcs = Get-Process python* -ErrorAction SilentlyContinue | Where-Object { $_.StartTime -lt (Get-Date).AddMinutes(-10) }
if ($oldProcs) {
    Write-Host "[3/5] Stopping old Python processes: $($oldProcs.Count)" -ForegroundColor Yellow
    $oldProcs | Stop-Process -Force -ErrorAction SilentlyContinue
}
else {
    Write-Host "[3/5] No old processes found" -ForegroundColor Green
}

# 4. Clean artifacts
if ($Clean) {
    Write-Host "[4/5] Cleaning all artifacts..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force .pytest_cache, out -ErrorAction SilentlyContinue
    Get-ChildItem _tmp -Directory -Filter "pytest_*" -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "[4/5] Cleanup complete" -ForegroundColor Green
}
else {
    $oldDirs = Get-ChildItem _tmp -Directory -Filter "pytest_*" -ErrorAction SilentlyContinue | Where-Object { $_.CreationTime -lt (Get-Date).AddDays(-7) }
    if ($oldDirs) {
        Write-Host "[4/5] Cleaning old temp folders: $($oldDirs.Count)" -ForegroundColor Yellow
        $oldDirs | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    }
    else {
        Write-Host "[4/5] No old temp folders" -ForegroundColor Green
    }
}

# 5. Run pytest with unique basetemp
Write-Host "[5/5] Running pytest..." -ForegroundColor Cyan

# Ensure we're in project root
$scriptDir = Split-Path -Parent $PSCommandPath
$projectRoot = Split-Path -Parent (Split-Path -Parent $scriptDir)
Set-Location $projectRoot

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$basetemp = "_tmp/pytest_$timestamp"
New-Item -ItemType Directory -Path $basetemp -Force | Out-Null

$pytestArgs = if ($Verbose) { @("-v") } else { @("-q") }
$pytestArgs += $args
$pytestArgs += "--basetemp=$basetemp"

uv run --active pytest @pytestArgs
$exitCode = $LASTEXITCODE

Write-Host ""
if ($exitCode -eq 0) {
    Write-Host "=== SUCCESS ===" -ForegroundColor Green
}
else {
    Write-Host "=== FAILED (exit: $exitCode) ===" -ForegroundColor Red
}

# Check git status
$gitArtifacts = git status --porcelain 2>$null | Select-String "pytest|_tmp|out/"
if ($gitArtifacts) {
    Write-Host ""
    Write-Host "WARNING: Pytest artifacts in git:" -ForegroundColor Yellow
    Write-Host $gitArtifacts
}

exit $exitCode
