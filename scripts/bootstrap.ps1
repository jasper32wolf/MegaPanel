# Bootstrap local Python env and run unit tests (no Docker required).
# Prefer full automation: .\scripts\install.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "Tip: for one-shot setup use .\scripts\install.ps1" -ForegroundColor Cyan

# Clear broken SOCKS proxy inheritance that breaks pip on some Windows setups
$env:HTTP_PROXY = ""; $env:HTTPS_PROXY = ""; $env:ALL_PROXY = ""
$env:http_proxy = ""; $env:https_proxy = ""; $env:all_proxy = ""
$env:NO_PROXY = "*"

if (-not (Test-Path ".venv")) {
  python -m venv .venv
}
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install hatchling editables
& .\.venv\Scripts\python.exe -m pip install --no-build-isolation `
  -e packages/shared `
  -e packages/security `
  -e packages/ssg `
  -e packages/block-library `
  -e apps/api `
  pytest pytest-asyncio ruff

& .\.venv\Scripts\python.exe scripts\prepare_env.py --mode local

$env:PYTHONPATH = "$Root\apps\api;$Root\packages\shared\src;$Root\packages\security\src;$Root\packages\ssg\src"
& .\.venv\Scripts\python.exe -m pytest apps/api/tests -q
Write-Host "OK: packages installed, tests passed"
Write-Host "Next: .\scripts\install.ps1   (DB + migrate + seed + start)"
