<#
.SYNOPSIS
    Natural-language wrapper for TradingAgents runs.

.DESCRIPTION
    With no arguments, launches the interactive guided menu — the
    recommended entry point. Pass -Prompt "..." to skip the menu and
    use the free-form path (same as menu option 7).

.PARAMETER Prompt
    Optional. Free-form natural-language ask. When supplied, the menu is
    skipped and this prompt is parsed directly.

    Note: PowerShell interpolates "$variables" inside double-quoted
    strings, so a ticker-like "$100" will be eaten. Either use single
    quotes ('like this') or omit the parameter and use the menu.

.PARAMETER Budget
    Optional. Max per-share price in USD; only meaningful with -Prompt.

.EXAMPLE
    ./agent.ps1
    # launches the guided menu

.EXAMPLE
    ./agent.ps1 -Prompt 'should I buy NVDA'

.EXAMPLE
    ./agent.ps1 -Prompt 'tech under $100' -Budget 100
#>
[CmdletBinding()]
param(
    [string]$Prompt,
    [int]$Budget
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repoRoot

$pyArgs = @("scripts/agent_assist.py")
if ($PSBoundParameters.ContainsKey('Prompt') -and -not [string]::IsNullOrWhiteSpace($Prompt)) {
    $pyArgs += @("--prompt", $Prompt)
    if ($PSBoundParameters.ContainsKey('Budget')) {
        $pyArgs += @("--budget", $Budget)
    }
}

& uv run python @pyArgs
exit $LASTEXITCODE
