<#
.SYNOPSIS
  Configures the protected GitHub deploy/recovery path for the Site Panel VPS.

.DESCRIPTION
  Generates separate unencrypted GitHub Actions keys, installs only their public
  counterparts through the existing forced-command bootstrap, stores the private
  keys and pinned host key in GitHub Environments, discovers the production API
  health URL without printing secrets, and dispatches the verified deploy.

  The generated action keys are intentionally unencrypted because GitHub Actions
  cannot unlock an interactive private-key passphrase. They are restricted by
  GitHub Environment protection and the VPS forced-command gateway.

.EXAMPLE
  .\scripts\setup-github-production.ps1

.EXAMPLE
  .\scripts\setup-github-production.ps1 -SkipDeploy
#>

[CmdletBinding()]
param(
  [string]$VpsHost = "212.22.82.252",
  [int]$VpsPort = 22,
  [string]$VpsUser = "root",
  [string]$Repository = "jasper32wolf/MegaPanel",
  [string]$InstallRoot = "/opt/site-panel",
  [string]$DeployUser = "sitepanel-deploy",
  [string]$AdminKeyPath = "",
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

function Write-Ok([string]$Message) {
  Write-Host ("OK: {0}" -f $Message) -ForegroundColor Green
}

function Test-RequiredCommand([string]$Name, [string]$Hint) {
  if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
    throw ("Required command '{0}' was not found. {1}" -f $Name, $Hint)
  }
}

function Invoke-Checked {
  param(
    [Parameter(Mandatory = $true)]
    [string]$FilePath,
    [string[]]$ArgumentList = @(),
    [string]$FailureMessage = "Command failed"
  )

  & $FilePath @ArgumentList
  if ($LASTEXITCODE -ne 0) {
    throw ("{0}: {1} (exit code {2})" -f $FailureMessage, $FilePath, $LASTEXITCODE)
  }
}

function Get-RepositorySlug {
  $remote = (& git remote get-url origin 2>$null | Select-Object -First 1)
  if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($remote)) {
    return $Repository
  }
  $remote = $remote.Trim()
  $match = [regex]::Match(
    $remote,
    '(?:github\.com[^:/]*[:/])(?<slug>[^/]+/[^/]+?)(?:\.git)?$'
  )
  if ($match.Success) {
    return $match.Groups["slug"].Value
  }
  return $Repository
}

function Get-GitHubCli {
  $command = Get-Command gh -ErrorAction SilentlyContinue
  if ($command) {
    return $command.Source
  }

  $knownPath = Join-Path ${env:ProgramFiles} "GitHub CLI\gh.exe"
  if (Test-Path -LiteralPath $knownPath) {
    return $knownPath
  }

  $winget = Get-Command winget -ErrorAction SilentlyContinue
  if ($winget) {
    Write-Step "Installing GitHub CLI"
    & $winget.Source install --id GitHub.cli --exact --source winget `
      --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -eq 0) {
      if (Test-Path -LiteralPath $knownPath) {
        return $knownPath
      }
      $command = Get-Command gh -ErrorAction SilentlyContinue
      if ($command) {
        return $command.Source
      }
    }
  }

  Write-Step "Downloading portable GitHub CLI"
  try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $headers = @{
      Accept = "application/vnd.github+json"
      "User-Agent" = "site-panel-production-setup"
    }
    $release = Invoke-RestMethod `
      -Uri "https://api.github.com/repos/cli/cli/releases/latest" `
      -Headers $headers
    $asset = $release.assets |
      Where-Object { $_.name -match "windows_amd64\.zip$" } |
      Select-Object -First 1
    if (-not $asset) {
      throw "No Windows amd64 GitHub CLI archive was found"
    }
    $toolRoot = Join-Path $env:LOCALAPPDATA "SitePanel\tools\gh"
    $zipPath = Join-Path $env:TEMP $asset.name
    New-Item -ItemType Directory -Force -Path $toolRoot | Out-Null
    Invoke-WebRequest -UseBasicParsing -Uri $asset.browser_download_url -OutFile $zipPath
    Expand-Archive -LiteralPath $zipPath -DestinationPath $toolRoot -Force
    Remove-Item -LiteralPath $zipPath -Force -ErrorAction SilentlyContinue
    $portable = Get-ChildItem -LiteralPath $toolRoot -Filter "gh.exe" -File -Recurse |
      Select-Object -First 1
    if (-not $portable) {
      throw "Downloaded GitHub CLI archive did not contain gh.exe"
    }
    return $portable.FullName
  } catch {
    throw ("Could not install or download GitHub CLI: {0}" -f $_.Exception.Message)
  }
}

function Invoke-GitHub {
  param(
    [Parameter(Mandatory = $true)]
    [string[]]$Arguments,
    [string]$FailureMessage = "GitHub CLI command failed"
  )

  $output = & $script:GitHubCli @Arguments 2>&1
  $code = $LASTEXITCODE
  if ($code -ne 0) {
    $safeOutput = ($output | ForEach-Object { [string]$_ }) -join "\n"
    throw ("{0}: {1}" -f $FailureMessage, $safeOutput.Trim())
  }
  return $output
}

function Connect-GitHubCli {
  & $script:GitHubCli auth status --hostname github.com 2>$null 1>$null
  if ($LASTEXITCODE -eq 0) {
    Write-Ok "GitHub CLI is authenticated"
    return
  }

  Write-Host "A browser window will open for GitHub CLI authentication. Do not paste a token into chat." -ForegroundColor Yellow
  Invoke-Checked $script:GitHubCli @("auth", "login", "--hostname", "github.com", "--git-protocol", "ssh", "--web") `
    "GitHub CLI authentication failed"
  Write-Ok "GitHub CLI authenticated"
}

function New-ActionKeyPairIfMissing {
  param(
    [Parameter(Mandatory = $true)]
    [string]$PrivatePath,
    [Parameter(Mandatory = $true)]
    [string]$Comment
  )

  $publicPath = "$PrivatePath.pub"
  if (Test-Path -LiteralPath $PrivatePath -and Test-Path -LiteralPath $publicPath) {
    return $publicPath
  }
  if (Test-Path -LiteralPath $PrivatePath -or Test-Path -LiteralPath $publicPath) {
    throw "Incomplete action key pair exists at $PrivatePath; rename it and rerun."
  }

  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $PrivatePath) | Out-Null
  Invoke-Checked "ssh-keygen" @(
    "-t", "ed25519", "-a", "100", "-N", "", "-f", $PrivatePath, "-C", $Comment
  ) "Could not generate the GitHub Actions key pair"
  return $publicPath
}

function Write-AsciiFile {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Path,
    [Parameter(Mandatory = $true)]
    [string[]]$Lines
  )
  [System.IO.File]::WriteAllLines($Path, $Lines, [System.Text.Encoding]::ASCII)
}

function Get-PinnedKnownHosts {
  param(
    [Parameter(Mandatory = $true)]
    [string]$HostName,
    [Parameter(Mandatory = $true)]
    [int]$Port,
    [Parameter(Mandatory = $true)]
    [string]$OutputPath
  )

  $globalPath = Join-Path $env:USERPROFILE ".ssh\known_hosts"
  $hostKeyLines = @()
  if (Test-Path -LiteralPath $globalPath) {
    foreach ($line in (Get-Content -LiteralPath $globalPath)) {
      if ([string]::IsNullOrWhiteSpace($line) -or $line.StartsWith("#")) {
        continue
      }
      $hostField = ($line -split "\s+", 2)[0]
      $hostNames = $hostField.Split(",")
      if ($hostNames -contains $HostName -or $hostNames -contains "[$HostName]:$Port") {
        $hostKeyLines += $line
      }
    }
  }

  if ($hostKeyLines.Count -eq 0) {
    $scan = @(& ssh-keyscan -p $Port -T 10 $HostName 2>$null)
    if ($LASTEXITCODE -ne 0 -or $scan.Count -eq 0) {
      throw "Could not obtain a VPS host key from $HostName`:$Port"
    }
    Write-AsciiFile -Path $OutputPath -Lines $scan
    $fingerprint = (& ssh-keygen -lf $OutputPath -E sha256 2>$null | Out-String).Trim()
    Write-Host "VPS host-key fingerprint candidate: $fingerprint" -ForegroundColor Yellow
    $confirmation = Read-Host "Verify the fingerprint in the provider console, then type YES"
    if ($confirmation -cne "YES") {
      throw "VPS host key was not confirmed"
    }
  } else {
    Write-AsciiFile -Path $OutputPath -Lines $hostKeyLines
    $fingerprint = (& ssh-keygen -lf $OutputPath -E sha256 2>$null | Out-String).Trim()
    Write-Host "Using the previously pinned VPS host key: $fingerprint" -ForegroundColor Yellow
  }

  return $OutputPath
}

function Get-SshArguments {
  param(
    [Parameter(Mandatory = $true)]
    [string]$KnownHostsPath
  )

  $args = @(
    "-p", $VpsPort,
    "-o", "ConnectTimeout=20",
    "-o", "StrictHostKeyChecking=yes",
    "-o", "UserKnownHostsFile=$KnownHostsPath",
    "-o", "PreferredAuthentications=publickey,password",
    "-o", "PubkeyAuthentication=yes",
    "-o", "PasswordAuthentication=yes"
  )
  if (-not [string]::IsNullOrWhiteSpace($AdminKeyPath)) {
    $args += @("-i", $AdminKeyPath)
  }
  return $args
}

function New-RemoteSetupScript {
  param(
    [Parameter(Mandatory = $true)]
    [string]$StagePath,
    [Parameter(Mandatory = $true)]
    [string]$DeployPublicKey,
    [Parameter(Mandatory = $true)]
    [string]$RecoveryPublicKey
  )

  $sourceNames = @(
    "bootstrap-github-deploy.sh",
    "release-manager.sh",
    "backup-production.sh",
    "restore-production.sh",
    "github-deploy-gateway.sh",
    "validate_production_env.py"
  )
  $encoded = @{}
  foreach ($name in $sourceNames) {
    $path = Join-Path $RepoRoot "scripts\$name"
    if (-not (Test-Path -LiteralPath $path)) {
      throw "Required source file is missing: $path"
    }
    $encoded[$name] = [Convert]::ToBase64String([System.IO.File]::ReadAllBytes($path))
  }

  $deployPubB64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($DeployPublicKey))
  $recoveryPubB64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($RecoveryPublicKey))

  $template = @'
set -Eeuo pipefail
root="__INSTALL_ROOT__"
user="__DEPLOY_USER__"
tmp="$(mktemp -d /tmp/site-panel-actions.XXXXXX)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/scripts" "$tmp/keys"
decode() {
  local path="$1" data="$2" mode="$3"
  printf '%s' "$data" | base64 -d >"$path"
  chmod "$mode" "$path"
}
decode "$tmp/scripts/bootstrap-github-deploy.sh" "__bootstrap-github-deploy.sh__" 0750
decode "$tmp/scripts/release-manager.sh" "__release-manager.sh__" 0750
decode "$tmp/scripts/backup-production.sh" "__backup-production.sh__" 0750
decode "$tmp/scripts/restore-production.sh" "__restore-production.sh__" 0750
decode "$tmp/scripts/github-deploy-gateway.sh" "__github-deploy-gateway.sh__" 0750
decode "$tmp/scripts/validate_production_env.py" "__validate_production_env.py__" 0755
decode "$tmp/keys/deploy.pub" "__deploy_pub__" 0644
decode "$tmp/keys/recovery.pub" "__recovery_pub__" 0644
bash "$tmp/scripts/bootstrap-github-deploy.sh" \
  --root "$root" \
  --user "$user" \
  --github-public-key-file "$tmp/keys/deploy.pub" \
  --github-recovery-public-key-file "$tmp/keys/recovery.pub"
api_url=""
if [[ -r "$root/shared/.env" ]]; then
  api_url="$(sed -n 's/^API_PUBLIC_URL=//p' "$root/shared/.env" | tail -n 1)"
  if [[ -z "$api_url" ]]; then
    api_domain="$(sed -n 's/^API_DOMAIN=//p' "$root/shared/.env" | tail -n 1)"
    [[ -n "$api_domain" ]] && api_url="https://$api_domain"
  fi
fi
printf 'SETUP_API_URL=%s\n' "$api_url"
if [[ -r "$root/shared/.env" && -x "$root/bin/validate_production_env.py" ]] && \
   python3 "$root/bin/validate_production_env.py" --env-file "$root/shared/.env" --require-secure-permissions >/dev/null 2>&1; then
  printf '%s\n' 'SETUP_ENV=ready'
else
  printf '%s\n' 'SETUP_ENV=not-ready'
fi
backup_status="not-ready"
backup_env="$root/shared/backup.env"
if [[ -r "$backup_env" && -r "$root/shared/.env" && -x "$root/bin/backup-production.sh" ]]; then
  repository="$(sed -n 's/^RESTIC_REPOSITORY=//p' "$backup_env" | tail -n 1)"
  password_file="$(sed -n 's/^RESTIC_PASSWORD_FILE=//p' "$backup_env" | tail -n 1)"
  if [[ -n "$repository" && -n "$password_file" && -r "$password_file" ]] && command -v restic >/dev/null 2>&1; then
    if runuser -u "$user" -- env RESTIC_REPOSITORY="$repository" RESTIC_PASSWORD_FILE="$password_file" restic snapshots --latest 1 --json >/dev/null 2>&1; then
      backup_status="ready"
    fi
  fi
fi
printf 'SETUP_BACKUP=%s\n' "$backup_status"
'@

  $replacements = @{
    "__INSTALL_ROOT__" = $InstallRoot
    "__DEPLOY_USER__" = $DeployUser
    "__deploy_pub__" = $deployPubB64
    "__recovery_pub__" = $recoveryPubB64
  }
  foreach ($name in $sourceNames) {
    $replacements["__$name__"] = $encoded[$name]
  }
  foreach ($key in $replacements.Keys) {
    $template = $template.Replace($key, $replacements[$key])
  }

  $remotePath = Join-Path $StagePath "remote-setup.sh"
  $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($remotePath, $template, $utf8NoBom)
  return $remotePath
}

function Invoke-RemoteSetup {
  param(
    [Parameter(Mandatory = $true)]
    [string]$ScriptPath,
    [Parameter(Mandatory = $true)]
    [string]$KnownHostsPath
  )

  $sshArgs = Get-SshArguments -KnownHostsPath $KnownHostsPath
  $sshArgs += @("$VpsUser@$VpsHost", "bash -s")
  Write-Host "SSH may ask for the root password locally. The password is never stored." -ForegroundColor Yellow
  $output = Get-Content -LiteralPath $ScriptPath -Raw | & ssh @sshArgs 2>&1
  $code = $LASTEXITCODE
  $output | ForEach-Object { Write-Host $_ }
  if ($code -ne 0) {
    throw "VPS bootstrap failed (exit code $code)"
  }
  return @($output | ForEach-Object { [string]$_ })
}

function Set-GitHubEnvironment {
  param(
    [Parameter(Mandatory = $true)]
    [string]$EnvironmentName
  )
  Invoke-GitHub @("api", "--method", "PUT", "repos/$Repository/environments/$EnvironmentName") `
    "Could not create GitHub Environment $EnvironmentName" | Out-Null
}

function Set-GitHubVariable {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Name,
    [Parameter(Mandatory = $true)]
    [string]$Value,
    [string]$EnvironmentName = ""
  )
  $args = @("variable", "set", $Name, "--repo", $Repository, "--body", $Value)
  if ($EnvironmentName) {
    $args += @("--env", $EnvironmentName)
  }
  Invoke-GitHub $args "Could not set GitHub variable $Name" | Out-Null
}

function Set-GitHubSecretFile {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Name,
    [Parameter(Mandatory = $true)]
    [string]$Path,
    [Parameter(Mandatory = $true)]
    [string]$EnvironmentName
  )
  $content = Get-Content -LiteralPath $Path -Raw
  $args = @("secret", "set", $Name, "--repo", $Repository, "--env", $EnvironmentName)
  $content | & $script:GitHubCli @args 2>&1 | Out-Null
  if ($LASTEXITCODE -ne 0) {
    throw "Could not set GitHub secret $Name"
  }
}

function Wait-ForCi {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Sha
  )
  Write-Step "Waiting for successful CI"
  for ($i = 0; $i -lt 60; $i++) {
    $json = Invoke-GitHub @(
      "run", "list", "--repo", $Repository, "--workflow", "ci.yml",
      "--commit", $Sha, "--limit", "20", "--json", "status,conclusion,databaseId,url"
    ) "Could not inspect CI status"
    $runs = @($json -join "`n" | ConvertFrom-Json)
    $successful = $runs | Where-Object { $_.status -eq "completed" -and $_.conclusion -eq "success" }
    if ($successful) {
      Write-Ok "CI is green for $Sha"
      return
    }
    $failed = $runs | Where-Object { $_.status -eq "completed" -and $_.conclusion -notin @("success", "neutral", "skipped") }
    if ($failed) {
      throw "CI failed for $Sha. Open the failed GitHub Actions run before retrying."
    }
    Start-Sleep -Seconds 10
  }
  throw "Timed out waiting for CI for $Sha"
}

function Start-Deploy {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Sha
  )
  Write-Step "Dispatching verified production deploy"
  Invoke-GitHub @(
    "workflow", "run", "deploy-production.yml", "--repo", $Repository,
    "--ref", "main", "--field", "release_sha=$Sha"
  ) "Could not dispatch production deploy" | Out-Null
  Start-Sleep -Seconds 5
  $json = Invoke-GitHub @(
    "run", "list", "--repo", $Repository, "--workflow", "deploy-production.yml",
    "--limit", "10", "--json", "status,conclusion,headSha,url,databaseId"
  ) "Could not inspect production deploy status"
  $runs = @($json -join "`n" | ConvertFrom-Json)
  $run = $runs | Where-Object { $_.headSha -eq $Sha } | Select-Object -First 1
  if ($run) {
    Write-Host ("Deploy run: {0}" -f $run.url) -ForegroundColor Green
  } else {
    Write-Host "Deploy dispatched; open GitHub Actions to monitor the run." -ForegroundColor Yellow
  }
}

Write-Host "Site Panel GitHub production setup" -ForegroundColor Green
Write-Host ("VPS: {0}@{1}:{2}" -f $VpsUser, $VpsHost, $VpsPort)

Test-RequiredCommand "ssh" "OpenSSH client is required"
Test-RequiredCommand "ssh-keygen" "OpenSSH client is required"
Test-RequiredCommand "git" "Git is required"
$Repository = Get-RepositorySlug
$script:GitHubCli = Get-GitHubCli
Connect-GitHubCli

$actionKeyDir = Join-Path $env:USERPROFILE ".ssh"
$deployKey = Join-Path $actionKeyDir "site-panel-actions-deploy"
$recoveryKey = Join-Path $actionKeyDir "site-panel-actions-recovery"
$deployPublic = New-ActionKeyPairIfMissing $deployKey "site-panel-actions-deploy"
$recoveryPublic = New-ActionKeyPairIfMissing $recoveryKey "site-panel-actions-recovery"

$stage = Join-Path $env:TEMP ("site-panel-actions-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $stage | Out-Null
try {
  $knownHostsPath = Join-Path $stage "known_hosts"
  Get-PinnedKnownHosts -HostName $VpsHost -Port $VpsPort -OutputPath $knownHostsPath | Out-Null

  Write-Step "Checking root SSH access"
  $sshCheckArgs = Get-SshArguments -KnownHostsPath $knownHostsPath
  $sshCheckArgs += @("$VpsUser@$VpsHost", "true")
  & ssh @sshCheckArgs
  if ($LASTEXITCODE -ne 0) {
    throw "Cannot authenticate to the VPS as $VpsUser. The script needs an interactive root password or -AdminKeyPath."
  }
  Write-Ok "VPS SSH access works"

  Write-Step "Installing forced-command gateway and release tools on VPS"
  $deployPubText = (Get-Content -LiteralPath $deployPublic -Raw).Trim()
  $recoveryPubText = (Get-Content -LiteralPath $recoveryPublic -Raw).Trim()
  $remoteScript = New-RemoteSetupScript -StagePath $stage `
    -DeployPublicKey $deployPubText -RecoveryPublicKey $recoveryPubText
  $remoteOutput = Invoke-RemoteSetup -ScriptPath $remoteScript -KnownHostsPath $knownHostsPath

  $apiLine = $remoteOutput | Where-Object { $_ -like "SETUP_API_URL=*" } | Select-Object -Last 1
  $envLine = $remoteOutput | Where-Object { $_ -like "SETUP_ENV=*" } | Select-Object -Last 1
  $backupLine = $remoteOutput | Where-Object { $_ -like "SETUP_BACKUP=*" } | Select-Object -Last 1
  $apiUrl = if ($apiLine) { ([string]$apiLine).Substring("SETUP_API_URL=".Length).Trim() } else { "" }
  $envStatus = if ($envLine) { ([string]$envLine).Substring("SETUP_ENV=".Length).Trim() } else { "not-ready" }
  $backupStatus = if ($backupLine) { ([string]$backupLine).Substring("SETUP_BACKUP=".Length).Trim() } else { "not-ready" }

  Write-Step "Configuring protected GitHub Environments"
  Set-GitHubEnvironment "production-deploy"
  Set-GitHubEnvironment "production-recovery"
  Set-GitHubVariable "DEPLOY_HOST" $VpsHost "production-deploy"
  Set-GitHubVariable "DEPLOY_USER" $DeployUser "production-deploy"
  Set-GitHubVariable "DEPLOY_PORT" ([string]$VpsPort) "production-deploy"
  Set-GitHubVariable "DEPLOY_ROOT" $InstallRoot "production-deploy"
  Set-GitHubVariable "RECOVERY_HOST" $VpsHost "production-recovery"
  Set-GitHubVariable "RECOVERY_USER" $DeployUser "production-recovery"
  Set-GitHubVariable "RECOVERY_PORT" ([string]$VpsPort) "production-recovery"
  Set-GitHubSecretFile "DEPLOY_SSH_KEY" $deployKey "production-deploy"
  Set-GitHubSecretFile "DEPLOY_KNOWN_HOSTS" $knownHostsPath "production-deploy"
  Set-GitHubSecretFile "RECOVERY_SSH_KEY" $recoveryKey "production-recovery"
  Set-GitHubSecretFile "RECOVERY_KNOWN_HOSTS" $knownHostsPath "production-recovery"
  if ($apiUrl -match '^https://') {
    Set-GitHubVariable "PUBLIC_HEALTH_URL" ((($apiUrl.TrimEnd('/')) + "/api/v1/health"))
    Write-Ok "PUBLIC_HEALTH_URL discovered from VPS production configuration"
  } else {
    Write-Host "WARN: API public URL is not configured on VPS; scheduled recovery remains disabled." -ForegroundColor Yellow
  }

  Write-Ok "GitHub deploy/recovery secrets and variables configured"
  if ($envStatus -ne "ready") {
    throw "VPS production .env is not valid yet; no deploy was dispatched."
  }
  if ($backupStatus -ne "ready") {
    throw "VPS Restic backup configuration is not ready; no deploy was dispatched."
  }

  if (-not $SkipDeploy) {
    $sha = (& git rev-parse HEAD).Trim()
    if ((git status --porcelain)) {
      throw "Working tree is not clean; commit and push changes before deploying."
    }
    Wait-ForCi -Sha $sha
    Start-Deploy -Sha $sha
  } else {
    Write-Host "Setup complete. Deploy was skipped by -SkipDeploy." -ForegroundColor Yellow
  }
} finally {
  Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "Done. Private keys exist only on this computer and in GitHub Environment secrets." -ForegroundColor Green
