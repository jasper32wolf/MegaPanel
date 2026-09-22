<#
.SYNOPSIS
  Configure the Site Panel Restic repository and dispatch the verified deploy.

.DESCRIPTION
  Prompts locally for the S3 credentials, generates and stores a local Restic
  password copy, writes protected backup files on the VPS, initializes an empty
  Restic repository when needed, creates the first encrypted backup, and starts
  the verified GitHub deploy. Secrets are never placed in arguments or logs.
#>

[CmdletBinding()]
param(
  [string]$VpsHost = "212.22.82.252",
  [int]$VpsPort = 22,
  [string]$VpsUser = "root",
  [string]$InstallRoot = "/opt/site-panel",
  [string]$DeployUser = "sitepanel-deploy",
  [string]$Repository = "s3:https://s3.regru.cloud/my-site-panel-backups",
  [switch]$RotateResticPassword,
  [switch]$SkipDeploy
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $RepoRoot

function Write-Step([string]$Message) {
  Write-Host ""
  Write-Host ("==> {0}" -f $Message) -ForegroundColor Cyan
}

function Require-Command([string]$Name) {
  if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
    throw "Required command was not found: $Name"
  }
}

function Get-PinnedKnownHosts {
  param(
    [string]$HostName,
    [int]$Port,
    [string]$OutputPath
  )

  $globalPath = Join-Path $env:USERPROFILE ".ssh\known_hosts"
  $lines = @()
  if (Test-Path -LiteralPath $globalPath) {
    foreach ($line in (Get-Content -LiteralPath $globalPath)) {
      if ([string]::IsNullOrWhiteSpace($line) -or $line.StartsWith("#")) {
        continue
      }
      $hostField = ($line -split "\s+", 2)[0]
      $hostNames = $hostField.Split(",")
      if ($hostNames -contains $HostName -or $hostNames -contains "[$HostName]:$Port") {
        $lines += $line
      }
    }
  }

  if ($lines.Count -eq 0) {
    $lines = @(& ssh-keyscan -p $Port -T 10 $HostName 2>$null)
    if ($LASTEXITCODE -ne 0 -or $lines.Count -eq 0) {
      throw "Could not obtain a host key for $HostName`:$Port"
    }
    [System.IO.File]::WriteAllLines($OutputPath, $lines, [System.Text.Encoding]::ASCII)
    $fingerprint = (& ssh-keygen -lf $OutputPath -E sha256 2>$null | Out-String).Trim()
    Write-Host "Host key candidate: $fingerprint" -ForegroundColor Yellow
    if ((Read-Host "Verify the fingerprint and type YES") -cne "YES") {
      throw "Host key was not confirmed"
    }
  } else {
    [System.IO.File]::WriteAllLines($OutputPath, $lines, [System.Text.Encoding]::ASCII)
  }
}

function Convert-SecureStringToPlainText {
  param([Security.SecureString]$Value)
  $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Value)
  try {
    return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
  } finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
  }
}

function Get-OrCreateResticPassword {
  param([switch]$Rotate)
  $directory = Join-Path $env:USERPROFILE ".site-panel"
  $path = Join-Path $directory "restic-password.txt"
  New-Item -ItemType Directory -Force -Path $directory | Out-Null
  if (Test-Path -LiteralPath $path) {
    if ($Rotate) {
      $archive = "$path.compromised-$([DateTime]::UtcNow.ToString('yyyyMMddHHmmss'))"
      Move-Item -LiteralPath $path -Destination $archive
      Write-Host ("Moved the exposed Restic password copy to: {0}" -f $archive) -ForegroundColor Yellow
    } else {
      $existing = (Get-Content -LiteralPath $path -Raw).Trim()
      if ($existing.Length -ge 24) {
        Write-Host ("Using the existing local Restic password copy: {0}" -f $path) -ForegroundColor Yellow
        return @{ Value = $existing; Path = $path }
      }
      throw "The local Restic password file exists but is too short: $path"
    }
  }

  $bytes = New-Object byte[] 32
  $random = [Security.Cryptography.RandomNumberGenerator]::Create()
  try {
    $random.GetBytes($bytes)
  } finally {
    $random.Dispose()
  }
  $password = [Convert]::ToBase64String($bytes)
  [System.IO.File]::WriteAllText(
    $path,
    $password + [Environment]::NewLine,
    [System.Text.Encoding]::ASCII
  )
  Write-Host ("Generated a new Restic password and saved it locally: {0}" -f $path) -ForegroundColor Yellow
  Write-Host "Copy this file to a password manager before relying on the backup." -ForegroundColor Yellow
  return @{ Value = $password; Path = $path }
}

function New-RemoteBackupScript {
  param(
    [string]$StagePath,
    [string]$AccessKey,
    [string]$SecretKey,
    [string]$ResticPassword
  )

  $encode = {
    param([string]$Value)
    [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($Value))
  }
  $repositoryB64 = & $encode $Repository
  $accessB64 = & $encode $AccessKey
  $secretB64 = & $encode $SecretKey
  $passwordB64 = & $encode $ResticPassword

  $template = @'
set -Eeuo pipefail
root="__INSTALL_ROOT__"
user="__DEPLOY_USER__"
tmp="$(mktemp -d /tmp/site-panel-restic.XXXXXX)"
trap 'rm -rf "$tmp"' EXIT
decode() {
  local path="$1" data="$2"
  printf '%s' "$data" | base64 -d >"$path"
  chmod 0600 "$path"
}
decode "$tmp/repository" "__REPOSITORY_B64__"
decode "$tmp/access" "__ACCESS_B64__"
decode "$tmp/secret" "__SECRET_B64__"
decode "$tmp/password" "__PASSWORD_B64__"
repository="$(cat "$tmp/repository")"
access_key="$(cat "$tmp/access")"
secret_key="$(cat "$tmp/secret")"
password="$(cat "$tmp/password")"
[[ "$repository" != *$'\n'* && "$access_key" != *$'\n'* && "$secret_key" != *$'\n'* && "$password" != *$'\n'* ]]
command -v restic >/dev/null 2>&1 || {
  printf '%s\n' 'BACKUP_ERROR=restic_missing'
  exit 20
}
install -d -o "$user" -g "$user" -m 0750 "$root/shared"
printf '%s\n' "$password" >"$root/shared/restic-password"
chmod 0600 "$root/shared/restic-password"
chown "$user:$user" "$root/shared/restic-password"
{
  printf 'RESTIC_REPOSITORY=%s\n' "$repository"
  printf 'RESTIC_PASSWORD_FILE=%s\n' "$root/shared/restic-password"
  printf 'AWS_ACCESS_KEY_ID=%s\n' "$access_key"
  printf 'AWS_SECRET_ACCESS_KEY=%s\n' "$secret_key"
} >"$root/shared/backup.env"
chown "$user:$user" "$root/shared/backup.env"
chmod 0600 "$root/shared/backup.env"
restic_env=(RESTIC_REPOSITORY="$repository" RESTIC_PASSWORD_FILE="$root/shared/restic-password" AWS_ACCESS_KEY_ID="$access_key" AWS_SECRET_ACCESS_KEY="$secret_key")
if ! runuser -u "$user" -- env "${restic_env[@]}" restic snapshots --latest 1 --json >/dev/null 2>&1; then
  runuser -u "$user" -- env "${restic_env[@]}" restic init >/dev/null 2>&1 || {
    printf '%s\n' 'BACKUP_ERROR=restic_repository_unavailable_or_password_mismatch'
    exit 21
  }
fi
runuser -u "$user" -- env "${restic_env[@]}" restic snapshots --latest 1 --json >/dev/null 2>&1 || {
  printf '%s\n' 'BACKUP_ERROR=restic_repository_check_failed'
  exit 22
}
[[ -x "$root/bin/release-manager.sh" ]] || {
  printf '%s\n' 'BACKUP_ERROR=release_manager_missing'
  exit 23
}
output="$(runuser -u "$user" -- env SITE_PANEL_ROOT="$root" COMPOSE_PROJECT=site-panel "$root/bin/release-manager.sh" backup setup 2>&1)" || {
  printf '%s\n' "$output"
  printf '%s\n' 'BACKUP_ERROR=first_backup_failed'
  exit 24
}
printf '%s\n' "$output"
printf '%s\n' 'BACKUP_STATUS=ready'
'@

  $replacements = @{
    "__INSTALL_ROOT__" = $InstallRoot
    "__DEPLOY_USER__" = $DeployUser
    "__REPOSITORY_B64__" = $repositoryB64
    "__ACCESS_B64__" = $accessB64
    "__SECRET_B64__" = $secretB64
    "__PASSWORD_B64__" = $passwordB64
  }
  foreach ($key in $replacements.Keys) {
    $template = $template.Replace($key, $replacements[$key])
  }
  $path = Join-Path $StagePath "restic-setup.sh"
  $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($path, $template, $utf8NoBom)
  return $path
}

function Invoke-RemoteScript {
  param(
    [string]$ScriptPath,
    [string]$KnownHostsPath
  )
  $sshOptions = @(
    "-p", $VpsPort,
    "-o", "ConnectTimeout=20",
    "-o", "StrictHostKeyChecking=yes",
    "-o", "UserKnownHostsFile=$KnownHostsPath",
    "-o", "PreferredAuthentications=publickey,password",
    "-o", "PubkeyAuthentication=yes",
    "-o", "PasswordAuthentication=yes",
    "$VpsUser@$VpsHost",
    "bash -s"
  )
  Write-Host "SSH will ask for the root password locally. It is never stored." -ForegroundColor Yellow
  $previousPreference = $ErrorActionPreference
  try {
    $ErrorActionPreference = "Continue"
    $output = Get-Content -LiteralPath $ScriptPath -Raw | & ssh @sshOptions 2>&1
    $exitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previousPreference
  }
  $output | ForEach-Object { Write-Host $_ }
  if ($exitCode -ne 0) {
    throw "Remote Restic setup failed (exit code $exitCode)"
  }
  return @($output | ForEach-Object { [string]$_ })
}

function Get-GitHubCliPath {
  $command = Get-Command gh -ErrorAction SilentlyContinue
  if ($command) { return $command.Source }
  $portableRoot = Join-Path $env:LOCALAPPDATA "SitePanel\tools\gh"
  $portable = Get-ChildItem -LiteralPath $portableRoot -Filter "gh.exe" -File -Recurse -ErrorAction SilentlyContinue |
    Select-Object -First 1
  if ($portable) { return $portable.FullName }
  throw "GitHub CLI was not found"
}

function Start-VerifiedDeploy {
  $gh = Get-GitHubCliPath
  $repoRemote = (& git remote get-url origin 2>$null | Select-Object -First 1).Trim()
  $match = [regex]::Match($repoRemote, '(?:github\.com[^:/]*[:/])(?<slug>[^/]+/[^/]+?)(?:\.git)?$')
  $repo = if ($match.Success) { $match.Groups["slug"].Value } else { "jasper32wolf/MegaPanel" }
  $sha = (& git ls-remote origin refs/heads/main 2>$null | Select-Object -First 1).Split("`t")[0]
  if ([string]::IsNullOrWhiteSpace($sha)) { throw "Could not resolve origin/main" }
  $runs = & $gh run list --repo $repo --workflow ci.yml --commit $sha --limit 20 --json status,conclusion,url 2>&1
  if ($LASTEXITCODE -ne 0) { throw "Could not inspect CI status" }
  $ci = @($runs -join "`n" | ConvertFrom-Json)
  if (-not ($ci | Where-Object { $_.status -eq "completed" -and $_.conclusion -eq "success" })) {
    throw "CI is not green for $sha; deploy was not dispatched"
  }
  & $gh workflow run deploy-production.yml --repo $repo --ref main --field "release_sha=$sha"
  if ($LASTEXITCODE -ne 0) { throw "Could not dispatch production deploy" }
  Write-Host "Deploy dispatched for verified SHA $sha" -ForegroundColor Green
}

Write-Host "Site Panel Restic backup setup" -ForegroundColor Green
Require-Command "ssh"
Require-Command "ssh-keyscan"
Require-Command "ssh-keygen"
$stage = Join-Path $env:TEMP ("site-panel-restic-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $stage | Out-Null
try {
  $knownHosts = Join-Path $stage "known_hosts"
  Get-PinnedKnownHosts -HostName $VpsHost -Port $VpsPort -OutputPath $knownHosts
  $accessKey = Read-Host "Enter S3 access key ID (local only)"
  if ([string]::IsNullOrWhiteSpace($accessKey)) { throw "S3 access key ID is required" }
  $secretSecure = Read-Host "Enter S3 secret access key (local only)" -AsSecureString
  $secretKey = Convert-SecureStringToPlainText $secretSecure
  if ([string]::IsNullOrWhiteSpace($secretKey)) { throw "S3 secret access key is required" }
  $password = Get-OrCreateResticPassword -Rotate:$RotateResticPassword
  $remoteScript = New-RemoteBackupScript -StagePath $stage -AccessKey $accessKey -SecretKey $secretKey -ResticPassword $password.Value
  $output = Invoke-RemoteScript -ScriptPath $remoteScript -KnownHostsPath $knownHosts
  if (-not ($output -contains "BACKUP_STATUS=ready")) {
    $errorLine = $output | Where-Object { $_ -like "BACKUP_ERROR=*" } | Select-Object -Last 1
    if ($errorLine) {
      throw ([string]$errorLine)
    }
    throw "Backup setup did not report ready"
  }
  Write-Host "Encrypted backup is ready." -ForegroundColor Green
  Write-Host ("Local Restic password copy: {0}" -f $password.Path) -ForegroundColor Yellow
  if (-not $SkipDeploy) {
    Start-VerifiedDeploy
  }
} finally {
  Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
}
Write-Host "Done." -ForegroundColor Green
