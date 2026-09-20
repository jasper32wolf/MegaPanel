#!/usr/bin/env bash
# Idempotent bootstrap for a single-operator Site Panel production VPS.
# It intentionally does not accept secret values on the command line.
set -Eeuo pipefail

DEPLOY_USER="sitepanel-deploy"
INSTALL_ROOT="/opt/site-panel"
SOURCE_DIR=""
CONFIG_FILE=""
DEPLOY_PUBLIC_KEY_FILE=""
RECOVERY_PUBLIC_KEY_FILE=""
RUN_PHASE="all"
RESUME=0
ENABLE_FIREWALL=0
HARDEN_SSH=0

PANEL_DOMAIN=""
API_DOMAIN=""
CADDY_EMAIL=""
SSH_PORT="22"
TRUSTED_SSH_CIDR=""
SOURCE_SHA=""

usage() {
  cat <<'EOF'
Usage:
  sudo bash scripts/install-production-vps.sh --source-dir /absolute/checkout [options]

Options:
  --config-file FILE                Root-readable 0600 file with public settings only.
  --phase NAME                      Run one phase: preflight, packages, layout, env,
                                    backup, volumes, github, release, verify, operator,
                                    firewall, ssh-hardening.
  --resume                          Resume from the persistent non-secret state record.
  --enable-firewall                 Configure UFW only after SSH lockout checks.
  --harden-ssh                      Prepare a validated SSH hardening change with rollback.
  --deploy-public-key-file FILE     GitHub deploy public key (never a private key).
  --recovery-public-key-file FILE   GitHub recovery public key (never a private key).
  -h, --help                        Show this help.

The config file permits only INSTALL_ROOT, PANEL_DOMAIN, API_DOMAIN, CADDY_EMAIL,
SSH_PORT and TRUSTED_SSH_CIDR. Secrets are prompted on a TTY or read from secure
VPS-local files; they are never accepted as command-line arguments.
EOF
}

die() { printf 'ERROR: %s\n' "$1" >&2; exit 1; }
step() { printf '\n==> %s\n' "$1"; }
need() { command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source-dir) SOURCE_DIR="${2:-}"; shift 2 ;;
    --config-file) CONFIG_FILE="${2:-}"; shift 2 ;;
    --phase) RUN_PHASE="${2:-}"; shift 2 ;;
    --resume) RESUME=1; shift ;;
    --enable-firewall) ENABLE_FIREWALL=1; shift ;;
    --harden-ssh) HARDEN_SSH=1; shift ;;
    --deploy-public-key-file) DEPLOY_PUBLIC_KEY_FILE="${2:-}"; shift 2 ;;
    --recovery-public-key-file) RECOVERY_PUBLIC_KEY_FILE="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown argument: $1" ;;
  esac
done

[[ $EUID -eq 0 ]] || die "Run with sudo/root"
[[ -n "$SOURCE_DIR" && "$SOURCE_DIR" == /* && -d "$SOURCE_DIR" ]] || die "--source-dir must be an existing absolute checkout path"
[[ "$INSTALL_ROOT" == /* ]] || die "INSTALL_ROOT must be absolute"

load_public_config() {
  [[ -n "$CONFIG_FILE" ]] || return 0
  [[ -f "$CONFIG_FILE" ]] || die "Config file does not exist"
  [[ "$(stat -c '%a' "$CONFIG_FILE")" =~ ^[0-7][0-6]0$ ]] || die "Config file must not be readable by group/others"
  local key value
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" || "$line" == \#* ]] && continue
    [[ "$line" == *=* ]] || die "Invalid config line"
    key="${line%%=*}"
    value="${line#*=}"
    case "$key" in
      INSTALL_ROOT) INSTALL_ROOT="$value" ;;
      PANEL_DOMAIN) PANEL_DOMAIN="$value" ;;
      API_DOMAIN) API_DOMAIN="$value" ;;
      CADDY_EMAIL) CADDY_EMAIL="$value" ;;
      SSH_PORT) SSH_PORT="$value" ;;
      TRUSTED_SSH_CIDR) TRUSTED_SSH_CIDR="$value" ;;
      *PASSWORD*|*SECRET*|*KEY*|*TOKEN*|*RESTIC*|*AWS*) die "Secret-like config key is forbidden: $key" ;;
      *) die "Unsupported config key: $key" ;;
    esac
  done <"$CONFIG_FILE"
}

state_path() { printf '%s/shared/installer-state.json' "$INSTALL_ROOT"; }
state_dir() { printf '%s/shared' "$INSTALL_ROOT"; }

write_state() {
  local phase="$1" status="$2" detail="${3:-}"
  install -d -m 0750 "$(state_dir)"
  python3 - "$(state_path)" "$phase" "$status" "$detail" <<'PY'
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

path = Path(sys.argv[1])
phase, status, detail = sys.argv[2:]
try:
    state = json.loads(path.read_text(encoding="utf-8"))
except FileNotFoundError:
    state = {"schema_version": 1, "completed": {}, "pending": {}, "security": {}}
state.setdefault("schema_version", 1)
state.setdefault("completed", {})
state.setdefault("pending", {})
state.setdefault("security", {})
record = {"at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
if detail:
    record["detail"] = detail
if status == "complete":
    state["completed"][phase] = record
    state["pending"].pop(phase, None)
else:
    state["pending"][phase] = record | {"status": status}
tmp = path.with_suffix(".next")
tmp.write_text(json.dumps(state, sort_keys=True, indent=2) + "\n", encoding="utf-8")
os.chmod(tmp, 0o600)
os.replace(tmp, path)
PY
  chmod 0600 "$(state_path)"
}

phase_complete() {
  python3 - "$(state_path)" "$1" <<'PY'
import json
import sys
from pathlib import Path
try:
    state = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
except FileNotFoundError:
    raise SystemExit(1)
raise SystemExit(0 if sys.argv[2] in state.get("completed", {}) else 1)
PY
}

run_phase() {
  local name="$1" function="$2"
  if [[ "$RUN_PHASE" != "all" && "$RUN_PHASE" != "$name" ]]; then
    return 0
  fi
  if [[ "$RESUME" == 1 ]] && phase_complete "$name"; then
    printf '==> %s already completed; validating current evidence\n' "$name"
  fi
  "$function"
}

validate_hostname() {
  local hostname="${1,,}"
  [[ "$hostname" =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$ ]] || return 1
  [[ "$hostname" != *localhost* && "$hostname" != *.example.com && "$hostname" != *.example.test && "$hostname" != *.example.invalid ]]
}

validate_inputs() {
  [[ "$SSH_PORT" =~ ^[0-9]{1,5}$ ]] && (( SSH_PORT >= 1 && SSH_PORT <= 65535 )) || die "Invalid SSH_PORT"
  validate_hostname "$PANEL_DOMAIN" || die "Invalid PANEL_DOMAIN"
  validate_hostname "$API_DOMAIN" || die "Invalid API_DOMAIN"
  [[ "$PANEL_DOMAIN" != "$API_DOMAIN" ]] || die "Panel and API domains must differ"
  [[ "$CADDY_EMAIL" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]] || die "Invalid CADDY_EMAIL"
}

preflight() {
  step "Preflight"
  [[ "$(uname -s)" == "Linux" ]] || die "This installer supports Linux VPS hosts only"
  [[ -d /run/systemd/system ]] || die "systemd is required"
  [[ -r /etc/os-release ]] || die "Cannot determine Linux distribution"
  # shellcheck disable=SC1091
  . /etc/os-release
  case "${ID:-}" in
    debian) [[ "${VERSION_ID:-}" == "12" ]] || die "Only Debian 12 is supported" ;;
    ubuntu) [[ "${VERSION_ID:-}" =~ ^(22\.04|24\.04)$ ]] || die "Ubuntu 22.04 or 24.04 is required" ;;
    *) die "Supported distributions: Debian 12, Ubuntu 22.04+" ;;
  esac
  need python3
  if [[ -n "$CONFIG_FILE" ]]; then
    load_public_config
  fi
  [[ "$INSTALL_ROOT" == /* ]] || die "INSTALL_ROOT must be absolute"
  if [[ -z "$PANEL_DOMAIN" && -t 0 ]]; then read -r -p 'Panel domain: ' PANEL_DOMAIN; fi
  if [[ -z "$API_DOMAIN" && -t 0 ]]; then read -r -p 'API domain: ' API_DOMAIN; fi
  if [[ -z "$CADDY_EMAIL" && -t 0 ]]; then read -r -p 'Caddy ACME email: ' CADDY_EMAIL; fi
  validate_inputs
  if [[ "$RUN_PHASE" == "all" && "$RESUME" == 0 ]]; then
    [[ -z "$(ss -ltnH '( sport = :80 or sport = :443 )' 2>/dev/null || true)" ]] || die "Host port 80 or 443 is already in use"
  fi
  install -d -m 0750 "$(state_dir)"
  exec 9>"$(state_dir)/installer.lock"
  flock -n 9 || die "Another installer run is active"
  write_state preflight complete
}

validate_source_checkout() {
  need git
  need tar
  [[ -d "$SOURCE_DIR/.git" ]] || die "--source-dir must be a Git checkout"
  git -C "$SOURCE_DIR" diff --quiet || die "Source checkout has tracked changes"
  git -C "$SOURCE_DIR" diff --cached --quiet || die "Source checkout has staged changes"
  SOURCE_SHA="$(git -C "$SOURCE_DIR" rev-parse --verify HEAD^{commit})"
  [[ "$SOURCE_SHA" =~ ^[0-9a-f]{40}([0-9a-f]{24})?$ ]] || die "Cannot resolve immutable source SHA"
}

install_docker() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    return 0
  fi
  # shellcheck disable=SC1091
  . /etc/os-release
  install -d -m 0755 /etc/apt/keyrings
  apt-get update
  apt-get install -y ca-certificates curl gnupg
  curl -fsSL "https://download.docker.com/linux/$ID/gpg" | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  local codename="${VERSION_CODENAME:-$UBUNTU_CODENAME}"
  printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/%s %s stable\n' \
    "$(dpkg --print-architecture)" "$ID" "$codename" > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
}

packages() {
  step "Install host dependencies"
  apt-get update
  apt-get install -y \
    git curl ca-certificates python3 restic tar gzip util-linux openssh-server ufw \
    fail2ban unattended-upgrades
  install_docker
  systemctl enable --now docker
  systemctl enable --now fail2ban
  systemctl enable --now apt-daily-upgrade.timer 2>/dev/null || true
  docker info >/dev/null
  docker compose version >/dev/null
  restic version >/dev/null
  write_state packages complete
}

layout() {
  step "Create deployment account and durable layout"
  if ! getent passwd "$DEPLOY_USER" >/dev/null; then
    useradd --create-home --shell /bin/bash "$DEPLOY_USER"
  fi
  usermod -aG docker "$DEPLOY_USER"
  install -d -o "$DEPLOY_USER" -g "$DEPLOY_USER" -m 0750 \
    "$INSTALL_ROOT" "$INSTALL_ROOT/bin" "$INSTALL_ROOT/incoming" "$INSTALL_ROOT/releases" \
    "$INSTALL_ROOT/shared" "$INSTALL_ROOT/shared/release-state"
  write_state layout complete
}

production_env() {
  step "Create or validate production environment"
  python3 "$SOURCE_DIR/scripts/prepare_env.py" --production \
    --output "$INSTALL_ROOT/shared/.env" \
    --panel-domain "$PANEL_DOMAIN" \
    --api-domain "$API_DOMAIN" \
    --caddy-email "$CADDY_EMAIL"
  chown "$DEPLOY_USER:$DEPLOY_USER" "$INSTALL_ROOT/shared/.env"
  chmod 0600 "$INSTALL_ROOT/shared/.env"
  python3 "$SOURCE_DIR/scripts/validate_production_env.py" \
    --env-file "$INSTALL_ROOT/shared/.env" --require-secure-permissions
  write_state env complete
}

restic_repository_ready() {
  runuser -u "$DEPLOY_USER" -- python3 - "$1" <<'PY'
import os
import re
import subprocess
import sys
from pathlib import Path

allowed = {
    "RESTIC_REPOSITORY",
    "RESTIC_PASSWORD_FILE",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "BACKUP_KEEP_DAILY",
    "BACKUP_KEEP_WEEKLY",
    "BACKUP_KEEP_MONTHLY",
    "BACKUP_RUN_CHECK",
}
values = {}
for number, raw_line in enumerate(Path(sys.argv[1]).read_text(encoding="utf-8").splitlines(), 1):
    line = raw_line.strip()
    if not line or line.startswith("#"):
        continue
    if "=" not in line:
        raise SystemExit(f"invalid backup.env line {number}")
    key, value = line.split("=", 1)
    if key not in allowed or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        raise SystemExit(f"unsupported backup.env key {key}")
    values[key] = value
for key in ("RESTIC_REPOSITORY", "RESTIC_PASSWORD_FILE"):
    if not values.get(key):
        raise SystemExit(f"missing {key}")
if not Path(values["RESTIC_PASSWORD_FILE"]).is_file():
    raise SystemExit("restic password file is missing")
env = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR") if key in os.environ}
env.update(values)
if subprocess.run(["restic", "snapshots"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
    subprocess.run(["restic", "init"], env=env, check=True)
PY
}

backup_gate() {
  step "Configure encrypted off-host restic backup"
  local backup_env="$INSTALL_ROOT/shared/backup.env" password_file="$INSTALL_ROOT/shared/restic-password"
  if [[ ! -f "$backup_env" ]]; then
    [[ -t 0 ]] || die "Create secure backup.env and restic password files, then re-run with --resume"
    local repository password access_key secret_key
    read -r -p 'Restic repository: ' repository
    [[ -n "$repository" ]] || die "Restic repository is required"
    read -r -s -p 'New/existing restic password: ' password; printf '\n'
    [[ ${#password} -ge 16 ]] || die "Restic password must be at least 16 characters"
    install -o "$DEPLOY_USER" -g "$DEPLOY_USER" -m 0600 /dev/null "$password_file"
    printf '%s\n' "$password" >"$password_file"
    unset password
    {
      printf 'RESTIC_REPOSITORY=%s\n' "$repository"
      printf 'RESTIC_PASSWORD_FILE=%s\n' "$password_file"
      if [[ "$repository" == s3:* ]]; then
        read -r -p 'S3 access key ID: ' access_key
        read -r -s -p 'S3 secret access key: ' secret_key; printf '\n'
        [[ -n "$access_key" && -n "$secret_key" ]] || die "S3 credentials are required for an S3 repository"
        printf 'AWS_ACCESS_KEY_ID=%s\nAWS_SECRET_ACCESS_KEY=%s\n' "$access_key" "$secret_key"
        unset secret_key
      fi
      printf 'BACKUP_KEEP_DAILY=7\nBACKUP_KEEP_WEEKLY=4\nBACKUP_KEEP_MONTHLY=12\nBACKUP_RUN_CHECK=1\n'
    } >"$backup_env"
    chown "$DEPLOY_USER:$DEPLOY_USER" "$backup_env"
    chmod 0600 "$backup_env"
  fi
  [[ "$(stat -c '%a' "$backup_env")" == "600" ]] || die "backup.env must have mode 0600"
  restic_repository_ready "$backup_env"
  write_state backup pending_restore_drill
}

initialize_volumes() {
  step "Initialize persistent-volume ownership"
  local volume
  for volume in sites_data uploads_data dsar_data caddy_data caddy_config; do
    docker volume create "site-panel_${volume}" >/dev/null
    docker run --rm -v "site-panel_${volume}:/data" alpine:3.20 sh -eu -c 'chown -R 10001:10001 /data'
    docker run --rm --user 10001:10001 -v "site-panel_${volume}:/data" alpine:3.20 \
      sh -eu -c 'touch /data/.site-panel-write-probe; rm /data/.site-panel-write-probe'
  done
  write_state volumes complete
}

bootstrap_github() {
  step "Configure optional restricted GitHub deployment gateway"
  if [[ -z "$DEPLOY_PUBLIC_KEY_FILE$RECOVERY_PUBLIC_KEY_FILE" ]]; then
    write_state github pending_external_environment
    return 0
  fi
  [[ -r "$DEPLOY_PUBLIC_KEY_FILE" && -r "$RECOVERY_PUBLIC_KEY_FILE" ]] || die "Both readable GitHub public-key files are required"
  bash "$SOURCE_DIR/scripts/bootstrap-github-deploy.sh" \
    --root "$INSTALL_ROOT" --user "$DEPLOY_USER" \
    --github-public-key-file "$DEPLOY_PUBLIC_KEY_FILE" \
    --github-recovery-public-key-file "$RECOVERY_PUBLIC_KEY_FILE"
  write_state github complete
}

create_initial_archive() {
  local staging archive
  staging="$(mktemp -d)"
  trap 'rm -rf "$staging"' RETURN
  git -C "$SOURCE_DIR" archive "$SOURCE_SHA" | tar -x -C "$staging"
  printf 'commit=%s\n' "$SOURCE_SHA" >"$staging/.site-panel-release"
  archive="$INSTALL_ROOT/incoming/$SOURCE_SHA.tar.gz"
  [[ -e "$archive" ]] || tar -C "$staging" -czf "$archive" .
  chown "$DEPLOY_USER:$DEPLOY_USER" "$archive"
  chmod 0640 "$archive"
  trap - RETURN
  rm -rf "$staging"
}

initial_release() {
  step "Install initial immutable release"
  validate_source_checkout
  create_initial_archive
  if [[ -d "$INSTALL_ROOT/releases/$SOURCE_SHA" ]]; then
    runuser -u "$DEPLOY_USER" -- env SITE_PANEL_ROOT="$INSTALL_ROOT" COMPOSE_PROJECT=site-panel \
      bash "$SOURCE_DIR/scripts/release-manager.sh" activate "$SOURCE_SHA"
  else
    runuser -u "$DEPLOY_USER" -- env SITE_PANEL_ROOT="$INSTALL_ROOT" COMPOSE_PROJECT=site-panel \
      bash "$SOURCE_DIR/scripts/release-manager.sh" deploy "$SOURCE_SHA"
  fi
  runuser -u "$DEPLOY_USER" -- env SITE_PANEL_ROOT="$INSTALL_ROOT" COMPOSE_PROJECT=site-panel \
    "$INSTALL_ROOT/bin/release-manager.sh" backup initial-install
  write_state release complete "source_sha=$SOURCE_SHA"
}

mark_pending() { write_state "$1" pending "$2"; }

verify_runtime() {
  step "Verify host-specific runtime evidence"
  local release="$INSTALL_ROOT/current"
  [[ -L "$release" ]] || die "No current release after activation"
  runuser -u "$DEPLOY_USER" -- env SITE_PANEL_ROOT="$INSTALL_ROOT" COMPOSE_PROJECT=site-panel \
    "$INSTALL_ROOT/bin/release-manager.sh" status
  if ! getent ahostsv4 "$PANEL_DOMAIN" >/dev/null || ! getent ahostsv4 "$API_DOMAIN" >/dev/null; then
    mark_pending verify "dns_not_ready"
    printf 'PENDING: DNS for panel/API does not yet resolve from this VPS. Re-run --resume after DNS propagation.\n'
    return 2
  fi
  if ! curl --fail --silent --show-error --max-time 20 "https://$PANEL_DOMAIN/" >/dev/null || \
     ! curl --fail --silent --show-error --max-time 20 "https://$PANEL_DOMAIN/api/v1/health" >/dev/null; then
    mark_pending verify "public_tls_or_proxy_not_ready"
    printf 'PENDING: public TLS or same-origin API proxy is not ready. Re-run --resume after DNS/ACME/firewall investigation.\n'
    return 2
  fi
  write_state verify complete "public_https_checked"
}

bootstrap_operator() {
  step "Create or verify the single operator"
  [[ -t 0 ]] || { mark_pending operator "interactive_operator_required"; return 2; }
  local release
  release="$(readlink -f "$INSTALL_ROOT/current")"
  runuser -u "$DEPLOY_USER" -- docker compose --project-name site-panel --env-file "$release/.env" \
    -f "$release/infra/docker/docker-compose.production.yml" run --rm api \
    python /app/scripts/bootstrap_operator.py --interactive
  printf 'Open the panel over HTTPS, enroll TOTP, then type YES to record your operator attestation.\n'
  local mfa
  read -r -p 'TOTP confirmed in an authenticator? [YES/no] ' mfa
  if [[ "$mfa" != "YES" ]]; then
    mark_pending operator "totp_attestation_required"
    return 2
  fi
  write_state operator complete "totp_attested"
}

configure_firewall() {
  [[ "$ENABLE_FIREWALL" == 1 ]] || return 0
  step "Configure UFW without removing existing rules"
  [[ -n "${SSH_CONNECTION:-}" ]] || die "--enable-firewall requires an active SSH session"
  ufw allow "$SSH_PORT/tcp"
  if [[ -n "$TRUSTED_SSH_CIDR" ]]; then
    ufw allow from "$TRUSTED_SSH_CIDR" to any port "$SSH_PORT" proto tcp
  fi
  ufw allow 80/tcp
  ufw allow 443/tcp
  ufw default deny incoming
  ufw default allow outgoing
  ufw --force enable
  ufw status verbose
  write_state firewall complete
}

prepare_ssh_hardening() {
  [[ "$HARDEN_SSH" == 1 ]] || return 0
  step "Prepare reversible SSH hardening"
  [[ -n "${SSH_CONNECTION:-}" ]] || die "--harden-ssh requires an active SSH session"
  local dropin=/etc/ssh/sshd_config.d/90-site-panel-hardening.conf backup=/root/90-site-panel-hardening.conf.before-site-panel
  [[ ! -e "$dropin" ]] && : >"$backup" || cp "$dropin" "$backup"
  cat >"$dropin" <<EOF
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
EOF
  sshd -t || { mv "$backup" "$dropin"; die "Generated SSH configuration is invalid"; }
  systemd-run --unit=site-panel-ssh-hardening-rollback --on-active=10m \
    /bin/sh -c "cp '$backup' '$dropin'; systemctl reload ssh || systemctl reload sshd" >/dev/null
  systemctl reload ssh || systemctl reload sshd
  cat <<EOF
SSH hardening is active with a 10-minute automatic rollback.
Open and verify a new key-based SSH session, then cancel rollback with:
  sudo systemctl cancel site-panel-ssh-hardening-rollback
EOF
  mark_pending ssh_hardening "awaiting_second_key_session"
}

main() {
  load_public_config
  preflight
  if [[ "$RUN_PHASE" == "preflight" ]]; then
    return 0
  fi
  run_phase packages packages
  run_phase layout layout
  run_phase env production_env
  run_phase backup backup_gate
  run_phase volumes initialize_volumes
  run_phase github bootstrap_github
  run_phase release initial_release
  run_phase verify verify_runtime
  run_phase operator bootstrap_operator
  run_phase firewall configure_firewall
  run_phase ssh-hardening prepare_ssh_hardening
  printf '\nBootstrap state: %s\n' "$(state_path)"
  printf 'The installer never proves a restore drill, client-domain lead flow, webhook delivery, or GitHub Environment custody.\n'
}

main
