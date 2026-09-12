<#
.SYNOPSIS
  Start Site Panel locally (API + worker + Vite panel).
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

$runtimeDir = Join-Path $Root "data\runtime"
New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null

$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
  throw "Missing .venv — run .\scripts\install.ps1 first"
}

if (Get-Command docker -ErrorAction SilentlyContinue) {
  docker info 1>$null 2>$null
  if ($LASTEXITCODE -eq 0) {
    Write-Host "Ensuring Postgres/Redis containers..."
    docker compose -f infra/docker/docker-compose.deps.yml up -d | Out-Null
  }
}

$env:PYTHONPATH = "$Root\apps\api;$Root\apps\worker;$Root\packages\shared\src;$Root\packages\security\src;$Root\packages\ssg\src"
$env:HTTP_PROXY = ""; $env:HTTPS_PROXY = ""; $env:NO_PROXY = "*"

function Start-LoggedProcess([string]$Name, [string]$FilePath, [string[]]$ArgumentList, [string]$WorkDir) {
  $outLog = Join-Path $runtimeDir "$Name.out.log"
  $errLog = Join-Path $runtimeDir "$Name.err.log"
  $pidFile = Join-Path $runtimeDir "$Name.pid"
  if (Test-Path $pidFile) {
    $old = Get-Content $pidFile | Select-Object -First 1
    if ($old) {
      try {
        Get-Process -Id ([int]$old) -ErrorAction Stop | Out-Null
        Write-Host "$Name already running (pid $old)"
        return
      } catch { }
    }
  }
  $p = Start-Process -FilePath $FilePath -ArgumentList $ArgumentList `
    -WorkingDirectory $WorkDir `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError $errLog `
    -WindowStyle Hidden `
    -PassThru
  Set-Content -Path $pidFile -Value $p.Id
  Write-Host "Started $Name (pid $($p.Id)) — logs: data/runtime/$Name.*.log"
}

$uv = Join-Path $Root ".venv\Scripts\uvicorn.exe"
$arq = Join-Path $Root ".venv\Scripts\arq.exe"

Start-LoggedProcess "api" $uv @(
  "app.main:app", "--app-dir", "apps/api", "--host", "127.0.0.1", "--port", "8000", "--reload"
) $Root

# Worker package lives under apps/worker; PYTHONPATH includes api for drip/dsar
Start-LoggedProcess "worker" $arq @("app.worker.WorkerSettings") (Join-Path $Root "apps\worker")

Start-LoggedProcess "panel" "cmd.exe" @(
  "/c", "npm", "run", "dev", "--", "--host", "127.0.0.1", "--port", "5173"
) (Join-Path $Root "apps\panel")

$ok = $false
for ($i = 0; $i -lt 45; $i++) {
  try {
    $r = Invoke-RestMethod "http://127.0.0.1:8000/api/v1/health" -TimeoutSec 2
    if ($r.status) { $ok = $true; break }
  } catch { Start-Sleep -Seconds 1 }
}
if ($ok) {
  Write-Host "API health OK" -ForegroundColor Green
} else {
  Write-Host "WARN: API not healthy yet — check data/runtime/api.err.log" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Panel: http://127.0.0.1:5173"
Write-Host "API:   http://127.0.0.1:8000/docs"
Write-Host "Login: admin@demo.local / DemoPass123!"
Write-Host "Stop:  .\scripts\stop.ps1"
