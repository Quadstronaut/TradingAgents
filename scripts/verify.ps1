#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Wrapper around tests.verify.matrix for Windows.

.DESCRIPTION
    Runs the full verification matrix. With -Reps 1 (default) runs one pass.
    With -UntilGreen N runs until N consecutive clean passes (the "3x with 0
    errors" acceptance gate from the spec).

.PARAMETER Reps
    Number of passes to run when not in until-green mode. Default 1.

.PARAMETER UntilGreen
    Run until this many consecutive clean passes; overrides -Reps.

.PARAMETER MaxAttempts
    Abort the green-streak loop after this many tries. Default 10.

.PARAMETER Only
    Run a single intent (e.g. specific, news_scan). Useful for debugging.

.PARAMETER Quick
    Run only fast intents (skip full deep pipeline). Smoke mode.

.EXAMPLE
    ./scripts/verify.ps1
    ./scripts/verify.ps1 -Reps 3
    ./scripts/verify.ps1 -UntilGreen 3 -MaxAttempts 5
    ./scripts/verify.ps1 -Only news_scan
    ./scripts/verify.ps1 -Quick
#>
[CmdletBinding()]
param(
    [int]$Reps = 1,
    [int]$UntilGreen = 0,
    [int]$MaxAttempts = 10,
    [string]$Only = "",
    [switch]$Quick
)

$ErrorActionPreference = "Stop"

# Resolve repo root from this script's location so it works no matter where
# the user invoked it from.
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $repoRoot

# Verify Ollama before spending an hour on something that'll fail in 30 sec.
try {
    $null = Invoke-RestMethod -Uri "http://localhost:11434/api/version" -Method GET -TimeoutSec 5
} catch {
    Write-Host "[fatal] Ollama not reachable at http://localhost:11434" -ForegroundColor Red
    Write-Host "        See steps-to-production.md Phase 2 to start it." -ForegroundColor Red
    exit 2
}

# -u forces unbuffered stdout/stderr so progress is visible when output
# is being piped to Tee-Object or a file. Without this, a long-running
# deep run can sit in the OS pipe buffer for minutes before flushing.
$env:PYTHONUNBUFFERED = "1"
$cmdArgs = @("run", "python", "-u", "-m", "tests.verify.matrix")
if ($UntilGreen -gt 0) {
    $cmdArgs += @("--until-green", $UntilGreen, "--max-attempts", $MaxAttempts)
} else {
    $cmdArgs += @("--reps", $Reps)
}
if ($Only) { $cmdArgs += @("--only", $Only) }
if ($Quick) { $cmdArgs += @("--quick") }

Write-Host "[verify] uv $($cmdArgs -join ' ')" -ForegroundColor Cyan
# Run directly. If the user wants to tee, they can: `./verify.ps1 ... |
# Tee-Object -FilePath x.txt -Encoding utf8` (default PS 5.1 encoding is
# UTF-16 LE which downstream tools misread as mojibake).
& uv @cmdArgs
exit $LASTEXITCODE
