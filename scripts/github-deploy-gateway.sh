#!/usr/bin/env bash
# Forced-command gateway for the GitHub recovery/deploy SSH key.
# Install outside mutable releases: /opt/site-panel/bin/github-deploy-gateway.sh
set -Eeuo pipefail

ROLE="${1:-}"
SITE_PANEL_ROOT="${SITE_PANEL_ROOT:-/opt/site-panel}"
MANAGER="$SITE_PANEL_ROOT/bin/release-manager.sh"
AUDIT_FILE="$SITE_PANEL_ROOT/shared/release-state/audit.log"
ORIGINAL="${SSH_ORIGINAL_COMMAND:-}"

[[ "$ROLE" == "deploy" || "$ROLE" == "recovery" ]] || {
  printf '%s\n' 'result=error' 'error=invalid_gateway_role'
  exit 126
}

log_reject() {
  install -d -m 0750 "$(dirname "$AUDIT_FILE")"
  printf '%s action=gateway result=rejected\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"$AUDIT_FILE" 2>/dev/null || true
}

reject() {
  log_reject
  printf '%s\n' 'result=error' 'error=command_not_allowed'
  exit 126
}

[[ -x "$MANAGER" ]] || reject
[[ "$ORIGINAL" != *$'\n'* && "$ORIGINAL" != *$'\r'* ]] || reject

case "$ORIGINAL" in
  'site-panel-release status')
    exec "$MANAGER" status
    ;;
  'site-panel-release restart')
    [[ "$ROLE" == "recovery" ]] || reject
    exec "$MANAGER" restart
    ;;
  'site-panel-release rollback')
    [[ "$ROLE" == "recovery" ]] || reject
    exec "$MANAGER" rollback
    ;;
  'site-panel-release auto-recover')
    [[ "$ROLE" == "recovery" ]] || reject
    exec "$MANAGER" auto-recover
    ;;
  'site-panel-release backup')
    [[ "$ROLE" == "recovery" ]] || reject
    exec "$MANAGER" backup github
    ;;
esac

if [[ "$ROLE" == "deploy" && "$ORIGINAL" =~ ^site-panel-release[[:space:]]+(deploy|install-archive|activate)[[:space:]]+([0-9a-f]{40}([0-9a-f]{24})?)$ ]]; then
  action="${BASH_REMATCH[1]}"
  release="${BASH_REMATCH[2]}"
  exec "$MANAGER" "$action" "$release"
fi

if [[ "$ROLE" == "recovery" && "$ORIGINAL" =~ ^site-panel-release[[:space:]]+restore[[:space:]]+([0-9a-f]{8,64})[[:space:]]+--confirm-restore$ ]]; then
  exec "$MANAGER" restore "${BASH_REMATCH[1]}" --confirm-restore
fi

# GitHub Actions sends a release archive with scp. Accept only the single
# expected target file and deny arbitrary SCP/SFTP filesystem access.
if [[ "$ROLE" == "deploy" && "$ORIGINAL" =~ ^scp[[:space:]]+-t[[:space:]]+(${SITE_PANEL_ROOT}/incoming/([0-9a-f]{40}([0-9a-f]{24})?)\.tar\.gz)$ ]]; then
  exec /usr/bin/scp -t "${BASH_REMATCH[1]}"
fi

reject
