#!/usr/bin/env bash
# Destructive manual restore from an encrypted restic snapshot.
# This script is intentionally impossible to call from release-manager auto-recover.
set -Eeuo pipefail

SITE_PANEL_ROOT="${SITE_PANEL_ROOT:-/opt/site-panel}"
COMPOSE_PROJECT="${COMPOSE_PROJECT:-site-panel}"
SHARED_DIR="$SITE_PANEL_ROOT/shared"
STATE_DIR="$SHARED_DIR/release-state"
STATE_FILE="$STATE_DIR/state.env"
AUDIT_FILE="$STATE_DIR/audit.log"
LOCK_FILE="$STATE_DIR/restore.lock"
CURRENT_LINK="$SITE_PANEL_ROOT/current"
BACKUP_ENV_FILE="${BACKUP_ENV_FILE:-$SHARED_DIR/backup.env}"
HEALTH_TIMEOUT_SECONDS="${HEALTH_TIMEOUT_SECONDS:-180}"
BACKUP_MANIFEST_VERSION="2"
BACKUP_POLICY="production-volumes"
BACKUP_POLICY_VERSION="1"
BACKUP_INCLUDED_VOLUMES="sites_data,uploads_data,caddy_data,caddy_config"
BACKUP_EXCLUDED_VOLUMES="dsar_data"
DSAR_RESTORE_ACTION="clear"

usage() {
  cat <<'EOF'
Usage:
  restore-production.sh --snapshot <restic-snapshot-id> --confirm-restore

This command stops application traffic, replaces PostgreSQL plus the sites,
uploads, and Caddy data/config volumes, clears DSAR exports, restores the shared
.env from the selected encrypted snapshot, and then runs migrations. It does not
accept "latest" and cannot run without the literal --confirm-restore acknowledgement.
EOF
}

emit() {
  printf '%s\n' "$*"
}

now_utc() {
  date -u +%Y-%m-%dT%H:%M:%SZ
}

die() {
  emit "result=error"
  emit "error=$1"
  printf '%s action=restore result=error message=%s\n' "$(now_utc)" "$1" >>"$AUDIT_FILE" 2>/dev/null || true
  exit 1
}

state_set() {
  local key="$1"
  local value="$2"
  local tmp="${STATE_FILE}.next.$$"
  if [[ -f "$STATE_FILE" ]]; then
    grep -Ev "^${key}=" "$STATE_FILE" >"$tmp" || true
  else
    : >"$tmp"
  fi
  printf '%s=%s\n' "$key" "$value" >>"$tmp"
  mv -f "$tmp" "$STATE_FILE"
  chmod 0640 "$STATE_FILE"
}

compose() {
  local release
  [[ -L "$CURRENT_LINK" ]] || die "current_release_missing"
  release="$(readlink -f "$CURRENT_LINK")"
  [[ -f "$release/infra/docker/docker-compose.production.yml" ]] || die "production_compose_missing"
  docker compose \
    --project-name "$COMPOSE_PROJECT" \
    --env-file "$release/.env" \
    -f "$release/infra/docker/docker-compose.production.yml" \
    "$@"
}

dotenv_value() {
  local key="$1"
  sed -n "s/^${key}=//p" "$SHARED_DIR/.env" | tail -n 1
}

load_backup_env() {
  local line key value seen_keys=" "

  [[ -f "$BACKUP_ENV_FILE" ]] || die "backup_env_missing"
  [[ -r "$BACKUP_ENV_FILE" ]] || die "backup_env_unreadable"

  # Treat backup.env as data, not shell code. Only these literal KEY=value
  # entries are accepted; substitutions, commands, exports, and other keys are
  # never evaluated or imported.
  unset RESTIC_REPOSITORY RESTIC_PASSWORD_FILE AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY
  unset BACKUP_KEEP_DAILY BACKUP_KEEP_WEEKLY BACKUP_KEEP_MONTHLY BACKUP_RUN_CHECK
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" =~ ^([A-Z_][A-Z0-9_]*)=(.*)$ ]] || die "backup_env_invalid_line"
    key="${BASH_REMATCH[1]}"
    value="${BASH_REMATCH[2]}"
    case "$key" in
      RESTIC_REPOSITORY|RESTIC_PASSWORD_FILE|AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY|BACKUP_KEEP_DAILY|BACKUP_KEEP_WEEKLY|BACKUP_KEEP_MONTHLY|BACKUP_RUN_CHECK)
        ;;
      *)
        die "backup_env_key_not_allowed"
        ;;
    esac
    [[ "$seen_keys" != *" $key "* ]] || die "backup_env_duplicate_key"
    seen_keys+="$key "
    case "$key" in
      RESTIC_REPOSITORY) RESTIC_REPOSITORY="$value" ;;
      RESTIC_PASSWORD_FILE) RESTIC_PASSWORD_FILE="$value" ;;
      AWS_ACCESS_KEY_ID) AWS_ACCESS_KEY_ID="$value" ;;
      AWS_SECRET_ACCESS_KEY) AWS_SECRET_ACCESS_KEY="$value" ;;
      BACKUP_KEEP_DAILY) BACKUP_KEEP_DAILY="$value" ;;
      BACKUP_KEEP_WEEKLY) BACKUP_KEEP_WEEKLY="$value" ;;
      BACKUP_KEEP_MONTHLY) BACKUP_KEEP_MONTHLY="$value" ;;
      BACKUP_RUN_CHECK) BACKUP_RUN_CHECK="$value" ;;
    esac
  done <"$BACKUP_ENV_FILE"

  [[ -n "${RESTIC_REPOSITORY:-}" ]] || die "restic_repository_missing"
  [[ -n "${RESTIC_PASSWORD_FILE:-}" ]] || die "restic_password_file_missing"
  [[ -r "$RESTIC_PASSWORD_FILE" ]] || die "restic_password_file_unreadable"
  export RESTIC_REPOSITORY RESTIC_PASSWORD_FILE
  [[ -n "${AWS_ACCESS_KEY_ID:-}" ]] && export AWS_ACCESS_KEY_ID
  [[ -n "${AWS_SECRET_ACCESS_KEY:-}" ]] && export AWS_SECRET_ACCESS_KEY
}

manifest_value() {
  local key="$1"
  local manifest="$2"
  sed -n "s/^${key}=//p" "$manifest" | tail -n 1
}

validate_manifest() {
  local manifest="$1"

  [[ -s "$manifest" ]] || die "restore_manifest_missing"
  [[ "$(manifest_value manifest_version "$manifest")" == "$BACKUP_MANIFEST_VERSION" ]] || die "restore_manifest_version_unsupported"
  [[ "$(manifest_value backup_policy "$manifest")" == "$BACKUP_POLICY" ]] || die "restore_manifest_policy_unsupported"
  [[ "$(manifest_value backup_policy_version "$manifest")" == "$BACKUP_POLICY_VERSION" ]] || die "restore_manifest_policy_version_unsupported"
  [[ "$(manifest_value included_volumes "$manifest")" == "$BACKUP_INCLUDED_VOLUMES" ]] || die "restore_manifest_volumes_unsupported"
  [[ "$(manifest_value excluded_volumes "$manifest")" == "$BACKUP_EXCLUDED_VOLUMES" ]] || die "restore_manifest_exclusions_unsupported"
  [[ "$(manifest_value excluded_volume_restore_action "$manifest")" == "$DSAR_RESTORE_ACTION" ]] || die "restore_manifest_dsar_action_unsupported"
}

restore_volume() {
  local volume="$1"
  local archive_name="$2"
  local payload="$3"

  docker run --rm \
    -v "${volume}:/data" \
    -v "$payload:/restore:ro" \
    alpine:3.20 \
    sh -eu -c "find /data -mindepth 1 -maxdepth 1 -exec rm -rf {} +; tar xzf /restore/$archive_name -C /data"
}

clear_volume() {
  local volume="$1"

  docker run --rm \
    -v "${volume}:/data" \
    alpine:3.20 \
    sh -eu -c 'find /data -mindepth 1 -maxdepth 1 -exec rm -rf {} +'
}

parse_args() {
  SNAPSHOT=""
  CONFIRMED=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --snapshot)
        [[ $# -ge 2 ]] || die "snapshot_missing"
        SNAPSHOT="$2"
        shift 2
        ;;
      --confirm-restore)
        CONFIRMED=1
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        die "unknown_argument"
        ;;
    esac
  done
  [[ "$SNAPSHOT" =~ ^[0-9a-f]{8,64}$ ]] || die "invalid_snapshot_id"
  (( CONFIRMED == 1 )) || die "restore_confirmation_required"
}

wait_for_postgres() {
  local deadline=$(( $(date +%s) + HEALTH_TIMEOUT_SECONDS ))
  while (( $(date +%s) < deadline )); do
    if compose exec -T postgres pg_isready -U "$1" >/dev/null 2>&1; then
      return 0
    fi
    sleep 3
  done
  return 1
}

wait_for_api() {
  local deadline=$(( $(date +%s) + HEALTH_TIMEOUT_SECONDS ))
  while (( $(date +%s) < deadline )); do
    if compose exec -T api python -c '
from urllib.request import urlopen
response = urlopen("http://127.0.0.1:8000/api/v1/health", timeout=5)
raise SystemExit(0 if response.status == 200 else 1)
' >/dev/null 2>&1; then
      return 0
    fi
    sleep 3
  done
  return 1
}

main() {
  parse_args "$@"
  umask 077
  install -d -m 0750 "$STATE_DIR"
  touch "$AUDIT_FILE"
  exec 9>"$LOCK_FILE"
  flock -n 9 || die "restore_locked"

  command -v docker >/dev/null || die "docker_missing"
  command -v restic >/dev/null || die "restic_missing"
  load_backup_env
  [[ -f "$SHARED_DIR/.env" ]] || die "shared_env_missing"
  [[ -L "$CURRENT_LINK" ]] || die "current_release_missing"

  local stage payload db_name db_user sites_volume uploads_volume caddy_data_volume caddy_config_volume dsar_volume current_release
  stage="$(mktemp -d "${TMPDIR:-/tmp}/site-panel-restore.XXXXXX")"
  trap 'rm -rf "$stage"' EXIT
  restic restore "$SNAPSHOT" --target "$stage" >/dev/null
  payload="$stage/site-panel"
  [[ -s "$payload/postgres.dump" ]] || die "restore_postgres_dump_missing"
  [[ -s "$payload/sites_data.tar.gz" ]] || die "restore_sites_archive_missing"
  [[ -s "$payload/uploads_data.tar.gz" ]] || die "restore_uploads_archive_missing"
  [[ -s "$payload/caddy_data.tar.gz" ]] || die "restore_caddy_data_archive_missing"
  [[ -s "$payload/caddy_config.tar.gz" ]] || die "restore_caddy_config_archive_missing"
  [[ -s "$payload/shared.env" ]] || die "restore_shared_env_missing"
  validate_manifest "$payload/manifest.env"

  # Protect the currently running state before destructive operations. If this
  # pre-restore backup cannot be made, restoration stops rather than replacing data.
  SITE_PANEL_ROOT="$SITE_PANEL_ROOT" COMPOSE_PROJECT="$COMPOSE_PROJECT" \
    "$SITE_PANEL_ROOT/bin/backup-production.sh" --reason "pre-restore-$SNAPSHOT"

  db_name="$(dotenv_value POSTGRES_DB)"
  db_user="$(dotenv_value POSTGRES_USER)"
  [[ -n "$db_name" && -n "$db_user" ]] || die "postgres_settings_missing"
  sites_volume="${COMPOSE_PROJECT}_sites_data"
  uploads_volume="${COMPOSE_PROJECT}_uploads_data"
  caddy_data_volume="${COMPOSE_PROJECT}_caddy_data"
  caddy_config_volume="${COMPOSE_PROJECT}_caddy_config"
  dsar_volume="${COMPOSE_PROJECT}_dsar_data"
  docker volume inspect "$sites_volume" >/dev/null 2>&1 || die "sites_volume_missing"
  docker volume inspect "$uploads_volume" >/dev/null 2>&1 || die "uploads_volume_missing"
  docker volume inspect "$caddy_data_volume" >/dev/null 2>&1 || die "caddy_data_volume_missing"
  docker volume inspect "$caddy_config_volume" >/dev/null 2>&1 || die "caddy_config_volume_missing"
  docker volume inspect "$dsar_volume" >/dev/null 2>&1 || die "dsar_volume_missing"
  current_release="$(basename "$(readlink -f "$CURRENT_LINK")")"

  compose stop api worker panel caddy || true
  compose up -d postgres redis
  wait_for_postgres "$db_user" || die "postgres_not_ready"

  compose exec -T postgres pg_restore \
    -U "$db_user" \
    -d "$db_name" \
    --clean \
    --if-exists \
    --no-owner \
    --no-privileges <"$payload/postgres.dump"

  restore_volume "$sites_volume" "sites_data.tar.gz" "$payload"
  restore_volume "$uploads_volume" "uploads_data.tar.gz" "$payload"
  restore_volume "$caddy_data_volume" "caddy_data.tar.gz" "$payload"
  restore_volume "$caddy_config_volume" "caddy_config.tar.gz" "$payload"
  # DSAR exports are intentionally excluded from off-host backups because they
  # can contain sensitive one-time exports. Never carry them into a restore.
  clear_volume "$dsar_volume"

  install -m 0600 "$payload/shared.env" "$SHARED_DIR/.env"
  if [[ -f "$payload/release-state.env" ]]; then
    install -m 0600 "$payload/release-state.env" "$STATE_FILE"
  fi

  compose run --rm migrate
  compose up -d --build --remove-orphans
  wait_for_api || die "restore_api_health_failed"

  state_set last_restore_snapshot "$SNAPSHOT"
  state_set last_restore_at "$(now_utc)"
  printf '%s action=restore result=ok snapshot=%s release=%s\n' \
    "$(now_utc)" "$SNAPSHOT" "$current_release" >>"$AUDIT_FILE"
  emit "restore=ok"
  emit "snapshot=$SNAPSHOT"
  emit "active_release=$current_release"
}

main "$@"
