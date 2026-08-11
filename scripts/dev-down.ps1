<#
.SYNOPSIS
    Stop everything dev-up.ps1 started.

.DESCRIPTION
    Closes the three component windows and stops the containers.

    `podman compose down` without `-v` on purpose: the volumes hold the audit trail, the
    registered applications and their encrypted credentials. Losing those to a routine stop
    would mean re-entering every token, and an audit trail a stop script can delete is not an
    audit trail. Pass -Volumes when you actually want a clean slate.

.PARAMETER Volumes
    Also destroy the database volume. Everything registered, every stored credential and the
    whole audit trail go with it.
#>
[CmdletBinding()]
param(
    [switch]$Volumes
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

# Matched on the window titles dev-up sets, so this cannot take down an unrelated pwsh the
# operator happens to have open.
$titles = @("kuwarden worker", "kuwarden api", "kuwarden workbench")
$stopped = 0
foreach ($process in Get-Process pwsh -ErrorAction SilentlyContinue) {
    if ($titles -contains $process.MainWindowTitle) {
        Stop-Process -Id $process.Id -Force
        $stopped++
    }
}
Write-Host "stopped $stopped component window(s)" -ForegroundColor Green

if ($Volumes) {
    Write-Host "destroying volumes - every credential and the whole audit trail" -ForegroundColor Red
    podman compose down -v
} else {
    podman compose down
}
Write-Host "containers stopped" -ForegroundColor Green
