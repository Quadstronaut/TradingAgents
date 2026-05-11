<#
.SYNOPSIS
    Natural-language wrapper for TradingAgents runs.

.PARAMETER Prompt
    Your natural-language ask. Examples:
      "should I buy NVDA"
      "tech companies I can get into this week"
      "should I sell NVDA or wait?"

.PARAMETER Budget
    Optional. Max per-share price in USD when the prompt is a screen.
    If omitted, the orchestrator will ask you interactively.

.EXAMPLE
    ./agent.ps1 "should I buy NVDA"

.EXAMPLE
    ./agent.ps1 "what tech can I get into" -Budget 100
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Prompt,

    [int]$Budget
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($Prompt)) {
    Write-Error "Prompt cannot be empty."
    exit 1
}

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repoRoot

$pyArgs = @("scripts/agent_assist.py", "--prompt", $Prompt)
if ($PSBoundParameters.ContainsKey('Budget')) {
    $pyArgs += @("--budget", $Budget)
}

& uv run python @pyArgs
exit $LASTEXITCODE
