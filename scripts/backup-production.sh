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

The script saves a PostgreSQL custom dump, static sites volume, shared .env and
release state to restic. It does not store credentials in the Git repository.
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
  [[ -f "$SHARED_DIR/.env" ]] || die "shared_env_missing"
  [[ -f "$BACKUP_ENV_FILE" ]] || die "backup_env_missing"
  [[ -r "$BACKUP_ENV_FILE" ]] || die "backup_env_unreadable"
  # This is a VPS-owned root/service configuration file, never a Git-tracked input.
  # shellcheck disable=SC1090
  source "$BACKUP_ENV_FILE"
  : "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY is required}"
  : "${RESTIC_PASSWORD_FILE:?RESTIC_PASSWORD_FILE is required}"
  [[ -r "$RESTIC_PASSWORD_FILE" ]] || die "restic_password_file_unreadable"
  BACKUP_KEEP_DAILY="${BACKUP_KEEP_DAILY:-7}"
  BACKUP_KEEP_WEEKLY="${BACKUP_KEEP_WEEKLY:-4}"
  BACKUP_KEEP_MONTHLY="${BACKUP_KEEP_MONTHLY:-12}"
  BACKUP_RUN_CHECK="${BACKUP_RUN_CHECK:-1}"
  require_uint backup_keep_daily "$BACKUP_KEEP_DAILY"
  require_uint backup_keep_weekly "$BACKUP_KEEP_WEEKLY"
  require_uint backup_keep_monthly "$BACKUP_KEEP_MONTHLY"
  [[ "$BACKUP_RUN_CHECK" == "0" || "$BACKUP_RUN_CHECK" == "1" ]] || die "invalid_backup_run_check"
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

  local db_name db_user sites_volume stage payload dump sites_archive current_release snapshot
  db_name="$(dotenv_value POSTGRES_DB)"
  db_user="$(dotenv_value POSTGRES_USER)"
  [[ -n "$db_name" && -n "$db_user" ]] || die "postgres_settings_missing"
  sites_volume="${COMPOSE_PROJECT}_sites_data"
  docker volume inspect "$sites_volume" >/dev/null 2>&1 || die "sites_volume_missing"

  stage="$(mktemp -d "${TMPDIR:-/tmp}/site-panel-backup.XXXXXX")"
  trap 'rm -rf "$stage"' EXIT
  payload="$stage/site-panel"
  dump="$payload/postgres.dump"
  sites_archive="$payload/sites.tar.gz"
  mkdir -p "$payload"

  compose exec -T postgres pg_dump -U "$db_user" -Fc "$db_name" >"$dump"
  [[ -s "$dump" ]] || die "postgres_dump_empty"

  docker run --rm \
    -v "${sites_volume}:/data:ro" \
    -v "$payload:/backup" \
    alpine:3.20 \
    tar czf /backup/sites.tar.gz -C /data .
  [[ -s "$sites_archive" ]] || die "sites_archive_empty"

  install -m 0600 "$SHARED_DIR/.env" "$payload/shared.env"
  if [[ -f "$STATE_FILE" ]]; then
    install -m 0600 "$STATE_FILE" "$payload/release-state.env"
  fi
  current_release="$(basename "$(readlink -f "$CURRENT_LINK")")"
  cat >"$payload/manifest.env" <<EOF
created_at=$(now_utc)
reason=$BACKUP_REASON
release=$current_release
postgres_db=$db_name
sites_volume=$sites_volume
EOF
  chmod 0600 "$payload/manifest.env"

  restic backup \
    --tag site-panel \
    --tag "reason-$BACKUP_REASON" \
    --tag "release-$current_release" \
    "$payload" >/dev/null
  restic forget --prune \
    --keep-daily "$BACKUP_KEEP_DAILY" \
    --keep-weekly "$BACKUP_KEEP_WEEKLY" \
    --keep-monthly "$BACKUP_KEEP_MONTHLY" >/dev/null
  if [[ "$BACKUP_RUN_CHECK" == "1" ]]; then
    restic check --read-data-subset=1/50 >/dev/null
  fi

  snapshot="$(snapshot_id)"
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
