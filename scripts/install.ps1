<#
.SYNOPSIS
  One-shot installer for Site Panel (Windows).

.PARAMETER Mode
  local  — host API/panel + Docker Postgres/Redis (default)
  docker — full docker compose stack
  deps   — packages + .env only (external Postgres/Redis)

.EXAMPLE
  .\scripts\install.ps1
  .\scripts\install.ps1 -Mode docker
#>

[CmdletBinding()]
param(
  [ValidateSet("local", "docker", "deps")]
  [string]$Mode = "local",
  [switch]$SkipTests,
  [switch]$SkipSeed,
  [switch]$SkipStart,
  [switch]$Start,
  [switch]$NoDocker
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

function Write-Step([string]$msg) {
  Write-Host ""
  Write-Host "==> $msg" -ForegroundColor Cyan
}

function Assert-Cmd([string]$name, [string]$hint) {
  if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
    throw "Required command not found: $name. $hint"
  }
}

function Clear-ProxyEnv {
  $env:HTTP_PROXY = ""; $env:HTTPS_PROXY = ""; $env:ALL_PROXY = ""
  $env:http_proxy = ""; $env:https_proxy = ""; $env:all_proxy = ""
  $env:NO_PROXY = "*"
}

function Wait-Tcp([string]$TargetHost, [int]$Port, [int]$TimeoutSec = 90) {
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    try {
      $client = New-Object System.Net.Sockets.TcpClient
      $iar = $client.BeginConnect($TargetHost, $Port, $null, $null)
      $ok = $iar.AsyncWaitHandle.WaitOne(1000, $false)
      if ($ok -and $client.Connected) {
        $client.EndConnect($iar) | Out-Null
        $client.Close()
        return
      }
      $client.Close()
    } catch { }
    Start-Sleep -Seconds 2
  }
  throw "Timeout waiting for ${TargetHost}:${Port}"
}

function Invoke-HostPython {
  param([Parameter(ValueFromRemainingArguments = $true)][string[]]$PyArgs)
  if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3.12 @PyArgs
    if ($LASTEXITCODE -eq 0) { return $LASTEXITCODE }
    & py -3 @PyArgs
    if ($LASTEXITCODE -eq 0) { return $LASTEXITCODE }
  }
  Assert-Cmd "python" "Install Python 3.12+ from https://www.python.org/downloads/"
  & python @PyArgs
  return $LASTEXITCODE
}

if (-not $PSBoundParameters.ContainsKey("Start") -and -not $SkipStart -and $Mode -eq "local") {
  $Start = $true
}
if ($SkipStart) { $Start = $false }

Write-Host "Site Panel installer (Windows) — mode=$Mode" -ForegroundColor Green
Clear-ProxyEnv

Write-Step "Checking prerequisites"
$code = Invoke-HostPython -c "import sys; assert sys.version_info >= (3, 12), sys.version; print(sys.version)"
if ($code -ne 0) { throw "Python 3.12+ required" }
Assert-Cmd "npm" "Install Node.js 20+ from https://nodejs.org/"

$hasDocker = [bool](Get-Command docker -ErrorAction SilentlyContinue)
$dockerOk = $false
if ($hasDocker -and -not $NoDocker) {
  docker info 1>$null 2>$null
  $dockerOk = ($LASTEXITCODE -eq 0)
}

if ($Mode -eq "docker" -and -not $dockerOk) {
  throw "Docker is required for -Mode docker. Install/start Docker Desktop."
}
if ($Mode -eq "local" -and -not $dockerOk -and -not $NoDocker) {
  Write-Host "WARN: Docker not available. Expect Postgres :5432 and Redis :6379 on localhost." -ForegroundColor Yellow
}

Write-Step "Preparing .env"
$envMode = if ($Mode -eq "docker") { "docker" } else { "local" }
$code = Invoke-HostPython scripts/prepare_env.py --mode $envMode
if ($code -ne 0) { throw "prepare_env.py failed" }

$composeDeps = "infra/docker/docker-compose.deps.yml"
$composeFull = "infra/docker/docker-compose.yml"

if ($Mode -eq "docker") {
  Write-Step "Starting full Docker Compose stack"
  docker compose --env-file .env -f $composeFull up -d --build
  if ($LASTEXITCODE -ne 0) { throw "docker compose failed" }
  Write-Step "Waiting for API :8000"
  Wait-Tcp "127.0.0.1" 8000 180
  Write-Host ""
  Write-Host "DONE (docker mode)" -ForegroundColor Green
  Write-Host "  API:   http://127.0.0.1:8000/docs"
  Write-Host "  Panel: http://127.0.0.1:5173"
  Write-Host "  Stop:  docker compose -f infra/docker/docker-compose.yml down"
  exit 0
}

if ($Mode -eq "local" -and $dockerOk) {
  Write-Step "Starting Postgres + Redis (Docker deps)"
  docker compose -f $composeDeps up -d
  if ($LASTEXITCODE -ne 0) { throw "docker compose deps failed" }
  Write-Step "Waiting for Postgres :5432 and Redis :6379"
  Wait-Tcp "127.0.0.1" 5432 90
  Wait-Tcp "127.0.0.1" 6379 90
}

Write-Step "Python venv + packages"
if (-not (Test-Path ".venv")) {
  $code = Invoke-HostPython -m venv .venv
  if ($code -ne 0) { throw "venv failed" }
}
$py = Join-Path $Root ".venv\Scripts\python.exe"
& $py -m pip install --upgrade pip
& $py -m pip install hatchling editables
& $py -m pip install --no-build-isolation `
  -e packages/shared `
  -e packages/security `
  -e packages/ssg `
  -e packages/block-library `
  -e apps/api `
  -e apps/worker `
  pytest pytest-asyncio ruff
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

$env:PYTHONPATH = "$Root\apps\api;$Root\apps\worker;$Root\packages\shared\src;$Root\packages\security\src;$Root\packages\ssg\src"

if (-not $SkipTests) {
  Write-Step "Running unit tests"
  & $py -m pytest apps/api/tests -q
  if ($LASTEXITCODE -ne 0) { throw "tests failed" }
}

Write-Step "Alembic migrations"
& $py -m alembic -c apps/api/alembic.ini upgrade head
if ($LASTEXITCODE -ne 0) { throw "alembic failed — is Postgres up on :5432?" }

if (-not $SkipSeed) {
  Write-Step "Seeding demo tenant"
  & $py scripts/seed_demo.py
  if ($LASTEXITCODE -ne 0) { throw "seed_demo failed" }
}

Write-Step "Panel npm install"
Push-Location (Join-Path $Root "apps\panel")
if (-not (Test-Path "node_modules")) {
  npm install
  if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
} else {
  Write-Host "node_modules present — skip (delete to force reinstall)"
}
Pop-Location

New-Item -ItemType Directory -Force -Path (Join-Path $Root "data\runtime") | Out-Null

if ($Start) {
  Write-Step "Starting API, worker, panel"
  & (Join-Path $PSScriptRoot "start.ps1")
} else {
  Write-Host ""
  Write-Host "Install complete. Start with: .\scripts\start.ps1" -ForegroundColor Green
}

Write-Host ""
Write-Host "DONE" -ForegroundColor Green
Write-Host "  Login:  admin@demo.local / DemoPass123!"
Write-Host "  API:    http://127.0.0.1:8000/docs"
Write-Host "  Panel:  http://127.0.0.1:5173"
Write-Host "  Stop:   .\scripts\stop.ps1"
Write-Host "  Stop+DB:.\scripts\stop.ps1 -Deps"
