<#
.SYNOPSIS
  Stop locally started Site Panel processes (from start.ps1 / install.ps1).
#>
[CmdletBinding()]
param(
  [switch]$Deps
)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
$runtimeDir = Join-Path $Root "data\runtime"

foreach ($name in @("api", "worker", "panel")) {
  $pidFile = Join-Path $runtimeDir "$name.pid"
  if (-not (Test-Path $pidFile)) { continue }
  $procId = Get-Content $pidFile | Select-Object -First 1
  if ($procId) {
    try {
      $proc = Get-Process -Id ([int]$procId) -ErrorAction Stop
      # Kill process tree on Windows
      taskkill /PID $procId /T /F 2>$null | Out-Null
      Write-Host "Stopped $name (pid $procId)"
    } catch {
      Write-Host "$name not running (stale pid $procId)"
    }
  }
  Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}

if ($Deps -and (Get-Command docker -ErrorAction SilentlyContinue)) {
  Set-Location -LiteralPath $Root
  docker compose -f infra/docker/docker-compose.deps.yml down
  Write-Host "Stopped Docker deps (postgres/redis)"
}

Write-Host "Done."
