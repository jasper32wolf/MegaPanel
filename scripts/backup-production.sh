#!/usr/bin/env bash
# Encrypted off-host backup for the Site Panel production release system.
# Requires restic and /opt/site-panel/shared/backup.env on the VPS.
set -Eeuo pipefail

SITE_PANEL_ROOT="${SITE_PANEL_ROOT:-/opt/site-panel}"
COMPOSE_PROJECT="${COMPOSE_PROJECT:-site-panel}"

SHARED_DIR="$SITE_PANEL_ROOT/shared"
STATE_DIR="$SHARED_DIR/release-state"
STATE_FILE="$STATE_DIR/state.env"
AUDIT_FILE="$STATE_DIR/audit.log"
LOCK_FILE="$STATE_DIR/backup.lock"
CURRENT_LINK="$SITE_PANEL_ROOT/current"
BACKUP_ENV_FILE="${BACKUP_ENV_FILE:-$SHARED_DIR/backup.env}"
BACKUP_MANIFEST_VERSION="2"
BACKUP_POLICY="production-volumes"
BACKUP_POLICY_VERSION="1"
BACKUP_INCLUDED_VOLUMES="sites_data,uploads_data,caddy_data,caddy_config"
BACKUP_EXCLUDED_VOLUMES="dsar_data"
DSAR_RESTORE_ACTION="clear"

usage() {
  cat <<'EOF'
Usage: backup-production.sh [--reason <label>]

Required VPS-only configuration in $SITE_PANEL_ROOT/shared/backup.env:
  RESTIC_REPOSITORY=...
  RESTIC_PASSWORD_FILE=/path/to/restic-password

Optional retention values:
  BACKUP_KEEP_DAILY=7
  BACKUP_KEEP_WEEKLY=4
  BACKUP_KEEP_MONTHLY=12
  BACKUP_RUN_CHECK=1

The script saves a PostgreSQL custom dump, sites, uploads, Caddy data/config,
shared .env and release state to restic. It deliberately excludes DSAR exports;
they are cleared during a restore. It does not store credentials in Git.
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
  printf '%s action=backup result=error message=%s\n' "$(now_utc)" "$1" >>"$AUDIT_FILE" 2>/dev/null || true
  exit 1
}

backup_unexpected_error() {
  local status="$?"
  emit "result=error"
  emit "error=unexpected_backup_failure_line_${BASH_LINENO[0]}_status_${status}"
  printf '%s action=backup result=error message=unexpected_failure line=%s status=%s\n' \
    "$(now_utc)" "${BASH_LINENO[0]}" "$status" >>"$AUDIT_FILE" 2>/dev/null || true
  exit "$status"
}

trap backup_unexpected_error ERR

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
  local value
  value="$(sed -n "s/^${key}=//p" "$SHARED_DIR/.env" | tail -n 1)"
  printf '%s' "$value"
}

require_uint() {
  [[ "$2" =~ ^[0-9]+$ ]] || die "invalid_${1}"
}

parse_args() {
  BACKUP_REASON="manual"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --reason)
        [[ $# -ge 2 ]] || die "backup_reason_missing"
        BACKUP_REASON="$2"
        shift 2
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
  [[ "$BACKUP_REASON" =~ ^[A-Za-z0-9._-]{1,80}$ ]] || die "invalid_backup_reason"
}

load_backup_env() {
  local line key value seen_keys=" "

  [[ -f "$SHARED_DIR/.env" ]] || die "shared_env_missing"
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
  BACKUP_KEEP_DAILY="${BACKUP_KEEP_DAILY:-7}"
  BACKUP_KEEP_WEEKLY="${BACKUP_KEEP_WEEKLY:-4}"
  BACKUP_KEEP_MONTHLY="${BACKUP_KEEP_MONTHLY:-12}"
  BACKUP_RUN_CHECK="${BACKUP_RUN_CHECK:-1}"
  require_uint backup_keep_daily "$BACKUP_KEEP_DAILY"
  require_uint backup_keep_weekly "$BACKUP_KEEP_WEEKLY"
  require_uint backup_keep_monthly "$BACKUP_KEEP_MONTHLY"
  [[ "$BACKUP_RUN_CHECK" == "0" || "$BACKUP_RUN_CHECK" == "1" ]] || die "invalid_backup_run_check"

  export RESTIC_REPOSITORY RESTIC_PASSWORD_FILE
  [[ -n "${AWS_ACCESS_KEY_ID:-}" ]] && export AWS_ACCESS_KEY_ID
  [[ -n "${AWS_SECRET_ACCESS_KEY:-}" ]] && export AWS_SECRET_ACCESS_KEY
}

archive_volume() {
  local volume="$1"
  local archive_name="$2"
  local payload="$3"

  docker volume inspect "$volume" >/dev/null 2>&1 || die "${archive_name%.tar.gz}_volume_missing"
  docker run --rm \
    -v "${volume}:/data:ro" \
    -v "$payload:/backup" \
    alpine:3.20 \
    tar czf "/backup/$archive_name" -C /data . \
    || die "${archive_name%.tar.gz}_archive_command_failed"
  [[ -s "$payload/$archive_name" ]] || die "${archive_name%.tar.gz}_archive_empty"
}

snapshot_id() {
  restic snapshots --latest 1 --json | python3 -c '
import json, sys
rows = json.load(sys.stdin)
print(rows[-1]["short_id"] if rows else "")
'
}

main() {
  parse_args "$@"
  umask 077
  install -d -m 0750 "$STATE_DIR"
  touch "$AUDIT_FILE"
  exec 9>"$LOCK_FILE"
  flock -n 9 || die "backup_locked"

  command -v docker >/dev/null || die "docker_missing"
  command -v restic >/dev/null || die "restic_missing"
  command -v python3 >/dev/null || die "python3_missing"
  load_backup_env

  local db_name db_user sites_volume uploads_volume caddy_data_volume caddy_config_volume
  local stage payload dump current_release snapshot
  db_name="$(dotenv_value POSTGRES_DB)"
  db_user="$(dotenv_value POSTGRES_USER)"
  [[ -n "$db_name" && -n "$db_user" ]] || die "postgres_settings_missing"
  sites_volume="${COMPOSE_PROJECT}_sites_data"
  uploads_volume="${COMPOSE_PROJECT}_uploads_data"
  caddy_data_volume="${COMPOSE_PROJECT}_caddy_data"
  caddy_config_volume="${COMPOSE_PROJECT}_caddy_config"

  stage="$(mktemp -d "${TMPDIR:-/tmp}/site-panel-backup.XXXXXX")"
  trap 'rm -rf "${stage:-}"' EXIT
  payload="$stage/site-panel"
  dump="$payload/postgres.dump"
  mkdir -p "$payload"

  compose exec -T postgres pg_dump -U "$db_user" -Fc "$db_name" >"$dump" \
    || die "postgres_dump_failed"
  [[ -s "$dump" ]] || die "postgres_dump_empty"

  archive_volume "$sites_volume" "sites_data.tar.gz" "$payload"
  archive_volume "$uploads_volume" "uploads_data.tar.gz" "$payload"
  archive_volume "$caddy_data_volume" "caddy_data.tar.gz" "$payload"
  archive_volume "$caddy_config_volume" "caddy_config.tar.gz" "$payload"

  install -m 0600 "$SHARED_DIR/.env" "$payload/shared.env"
  if [[ -f "$STATE_FILE" ]]; then
    install -m 0600 "$STATE_FILE" "$payload/release-state.env"
  fi
  current_release="$(basename "$(readlink -f "$CURRENT_LINK")")"
  cat >"$payload/manifest.env" <<EOF
manifest_version=$BACKUP_MANIFEST_VERSION
backup_policy=$BACKUP_POLICY
backup_policy_version=$BACKUP_POLICY_VERSION
included_volumes=$BACKUP_INCLUDED_VOLUMES
excluded_volumes=$BACKUP_EXCLUDED_VOLUMES
excluded_volume_restore_action=$DSAR_RESTORE_ACTION
created_at=$(now_utc)
reason=$BACKUP_REASON
release=$current_release
postgres_db=$db_name
sites_volume=$sites_volume
uploads_volume=$uploads_volume
caddy_data_volume=$caddy_data_volume
caddy_config_volume=$caddy_config_volume
EOF
  chmod 0600 "$payload/manifest.env"

  restic backup \
    --tag site-panel \
    --tag "reason-$BACKUP_REASON" \
    --tag "release-$current_release" \
    "$payload" >/dev/null \
    || die "restic_backup_failed"
  restic forget --prune \
    --keep-daily "$BACKUP_KEEP_DAILY" \
    --keep-weekly "$BACKUP_KEEP_WEEKLY" \
    --keep-monthly "$BACKUP_KEEP_MONTHLY" >/dev/null \
    || die "restic_prune_failed"
  if [[ "$BACKUP_RUN_CHECK" == "1" ]]; then
    restic check --read-data-subset=1/50 >/dev/null || die "restic_check_failed"
  fi

  snapshot="$(snapshot_id)" || die "restic_snapshot_query_failed"
  [[ -n "$snapshot" ]] || die "restic_snapshot_missing"
  state_set last_backup_snapshot "$snapshot"
  state_set last_backup_at "$(now_utc)"
  printf '%s action=backup result=ok snapshot=%s release=%s reason=%s\n' \
    "$(now_utc)" "$snapshot" "$current_release" "$BACKUP_REASON" >>"$AUDIT_FILE"
  emit "backup=ok"
  emit "backup_snapshot=$snapshot"
  emit "release=$current_release"
}

main "$@"
