#!/usr/bin/env bash
# One-time VPS bootstrap for GitHub-managed releases and bounded recovery.
# Run as root or with sudo on the VPS. It never receives private keys/secrets.
set -Eeuo pipefail

SITE_PANEL_ROOT="/opt/site-panel"
DEPLOY_USER="sitepanel-deploy"
PUBLIC_KEY_FILE=""
RECOVERY_PUBLIC_KEY_FILE=""
INSTALL_TIMER=1

usage() {
  cat <<'EOF'
Usage:
  sudo scripts/bootstrap-github-deploy.sh \
    --github-public-key-file /secure/path/github-actions-deploy.pub \
    --github-recovery-public-key-file /secure/path/github-actions-recovery.pub \
    [--root /opt/site-panel] [--user sitepanel-deploy] [--no-backup-timer]

The public keys are the counterparts of separate GitHub Environment secrets.
This script configures a forced command gateway, so neither key can open an
interactive shell or forward ports.
EOF
}

die() {
  printf 'ERROR: %s\n' "$1" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root)
      [[ $# -ge 2 ]] || die "--root requires a value"
      SITE_PANEL_ROOT="$2"
      shift 2
      ;;
    --user)
      [[ $# -ge 2 ]] || die "--user requires a value"
      DEPLOY_USER="$2"
      shift 2
      ;;
    --github-public-key-file)
      [[ $# -ge 2 ]] || die "--github-public-key-file requires a value"
      PUBLIC_KEY_FILE="$2"
      shift 2
      ;;
    --github-recovery-public-key-file)
      [[ $# -ge 2 ]] || die "--github-recovery-public-key-file requires a value"
      RECOVERY_PUBLIC_KEY_FILE="$2"
      shift 2
      ;;
    --no-backup-timer)
      INSTALL_TIMER=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

[[ $EUID -eq 0 ]] || die "run with sudo/root"
[[ "$SITE_PANEL_ROOT" == /* ]] || die "--root must be absolute"
[[ "$DEPLOY_USER" =~ ^[a-z_][a-z0-9_-]*$ ]] || die "invalid deploy user"
[[ -n "$PUBLIC_KEY_FILE" && -r "$PUBLIC_KEY_FILE" ]] || die "deploy GitHub public key file is required"
[[ -n "$RECOVERY_PUBLIC_KEY_FILE" && -r "$RECOVERY_PUBLIC_KEY_FILE" ]] || die "recovery GitHub public key file is required"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
for file in release-manager.sh backup-production.sh restore-production.sh github-deploy-gateway.sh validate_production_env.py; do
  [[ -f "$ROOT_DIR/scripts/$file" ]] || die "missing scripts/$file"
done

if ! getent passwd "$DEPLOY_USER" >/dev/null; then
  useradd --create-home --shell /bin/bash "$DEPLOY_USER"
fi
usermod -aG docker "$DEPLOY_USER"

install -d -o "$DEPLOY_USER" -g "$DEPLOY_USER" -m 0750 \
  "$SITE_PANEL_ROOT" \
  "$SITE_PANEL_ROOT/releases" \
  "$SITE_PANEL_ROOT/incoming" \
  "$SITE_PANEL_ROOT/shared" \
  "$SITE_PANEL_ROOT/shared/release-state" \
  "$SITE_PANEL_ROOT/bin"

install -o "$DEPLOY_USER" -g "$DEPLOY_USER" -m 0750 \
  "$ROOT_DIR/scripts/release-manager.sh" "$SITE_PANEL_ROOT/bin/release-manager.sh"
install -o "$DEPLOY_USER" -g "$DEPLOY_USER" -m 0750 \
  "$ROOT_DIR/scripts/backup-production.sh" "$SITE_PANEL_ROOT/bin/backup-production.sh"
install -o "$DEPLOY_USER" -g "$DEPLOY_USER" -m 0750 \
  "$ROOT_DIR/scripts/restore-production.sh" "$SITE_PANEL_ROOT/bin/restore-production.sh"
install -o root -g root -m 0755 \
  "$ROOT_DIR/scripts/github-deploy-gateway.sh" "$SITE_PANEL_ROOT/bin/github-deploy-gateway.sh"
install -o root -g root -m 0755 \
  "$ROOT_DIR/scripts/validate_production_env.py" "$SITE_PANEL_ROOT/bin/validate_production_env.py"

if [[ ! -e "$SITE_PANEL_ROOT/shared/.env" ]]; then
  install -o "$DEPLOY_USER" -g "$DEPLOY_USER" -m 0600 /dev/null "$SITE_PANEL_ROOT/shared/.env"
fi
if [[ ! -e "$SITE_PANEL_ROOT/shared/backup.env" ]]; then
  install -o "$DEPLOY_USER" -g "$DEPLOY_USER" -m 0600 /dev/null "$SITE_PANEL_ROOT/shared/backup.env"
fi

HOME_DIR="$(getent passwd "$DEPLOY_USER" | cut -d: -f6)"
SSH_DIR="$HOME_DIR/.ssh"
AUTHORIZED_KEYS="$SSH_DIR/authorized_keys"
install -d -o "$DEPLOY_USER" -g "$DEPLOY_USER" -m 0700 "$SSH_DIR"
touch "$AUTHORIZED_KEYS"
chown "$DEPLOY_USER:$DEPLOY_USER" "$AUTHORIZED_KEYS"
chmod 0600 "$AUTHORIZED_KEYS"

DEPLOY_PUBLIC_KEY="$(tr -d '\r\n' <"$PUBLIC_KEY_FILE")"
RECOVERY_PUBLIC_KEY="$(tr -d '\r\n' <"$RECOVERY_PUBLIC_KEY_FILE")"
[[ "$DEPLOY_PUBLIC_KEY" =~ ^ssh-ed25519[[:space:]] ]] || die "deploy key must be ssh-ed25519"
[[ "$RECOVERY_PUBLIC_KEY" =~ ^ssh-ed25519[[:space:]] ]] || die "recovery key must be ssh-ed25519"
for key_role in deploy recovery; do
  if [[ "$key_role" == "deploy" ]]; then
    PUBLIC_KEY="$DEPLOY_PUBLIC_KEY"
  else
    PUBLIC_KEY="$RECOVERY_PUBLIC_KEY"
  fi
  FORCED_KEY="command=\"$SITE_PANEL_ROOT/bin/github-deploy-gateway.sh $key_role\",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty $PUBLIC_KEY"
  grep -Fqx "$FORCED_KEY" "$AUTHORIZED_KEYS" || printf '%s\n' "$FORCED_KEY" >>"$AUTHORIZED_KEYS"
done

if [[ "$INSTALL_TIMER" == "1" ]] && command -v systemctl >/dev/null; then
  cat > /etc/systemd/system/site-panel-backup.service <<EOF
[Unit]
Description=Encrypted Site Panel restic backup
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
User=$DEPLOY_USER
Group=$DEPLOY_USER
Environment=SITE_PANEL_ROOT=$SITE_PANEL_ROOT
Environment=COMPOSE_PROJECT=site-panel
ExecStart=$SITE_PANEL_ROOT/bin/release-manager.sh backup scheduled
EOF
  cat > /etc/systemd/system/site-panel-backup.timer <<'EOF'
[Unit]
Description=Run Site Panel encrypted backup daily

[Timer]
OnCalendar=*-*-* 02:15:00 UTC
RandomizedDelaySec=15m
Persistent=true

[Install]
WantedBy=timers.target
EOF
  systemctl daemon-reload
  systemctl enable --now site-panel-backup.timer
fi

cat <<EOF
Bootstrap complete.

Required operator actions before any deployment:
1. Write real production variables to $SITE_PANEL_ROOT/shared/.env and chmod 600 it.
2. Install restic and write RESTIC_REPOSITORY / RESTIC_PASSWORD_FILE to
   $SITE_PANEL_ROOT/shared/backup.env (chmod 600).
3. Store the matching private key, host and pinned known_hosts in GitHub
   Environment secrets. GitHub SCP must use legacy mode: scp -O.
4. Verify: sudo -u $DEPLOY_USER SITE_PANEL_ROOT=$SITE_PANEL_ROOT \\
   $SITE_PANEL_ROOT/bin/release-manager.sh status
5. Run the staging drill in docs/runbooks/github-deploy-recovery.md before
   enabling a production environment or scheduled recovery.
EOF
