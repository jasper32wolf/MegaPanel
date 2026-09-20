#!/usr/bin/env bash
# Immutable release activation and bounded code recovery for Site Panel.
# This script runs on the VPS. It never restores PostgreSQL automatically.
set -Eeuo pipefail

SITE_PANEL_ROOT="${SITE_PANEL_ROOT:-/opt/site-panel}"
COMPOSE_PROJECT="${COMPOSE_PROJECT:-site-panel}"
RELEASE_RETENTION="${RELEASE_RETENTION:-5}"
HEALTH_TIMEOUT_SECONDS="${HEALTH_TIMEOUT_SECONDS:-120}"
AUTO_RECOVERY_COOLDOWN_SECONDS="${AUTO_RECOVERY_COOLDOWN_SECONDS:-900}"

RELEASES_DIR="$SITE_PANEL_ROOT/releases"
INCOMING_DIR="$SITE_PANEL_ROOT/incoming"
SHARED_DIR="$SITE_PANEL_ROOT/shared"
STATE_DIR="$SHARED_DIR/release-state"
STATE_FILE="$STATE_DIR/state.env"
AUDIT_FILE="$STATE_DIR/audit.log"
LOCK_FILE="$STATE_DIR/release-manager.lock"
CURRENT_LINK="$SITE_PANEL_ROOT/current"
PREVIOUS_LINK="$SITE_PANEL_ROOT/previous"
BIN_DIR="$SITE_PANEL_ROOT/bin"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VALIDATOR="$SCRIPT_DIR/validate_production_env.py"

usage() {
  cat <<'EOF'
Usage:
  release-manager.sh install-archive <commit-sha>
  release-manager.sh activate <commit-sha>
  release-manager.sh deploy <commit-sha>
  release-manager.sh status
  release-manager.sh restart
  release-manager.sh rollback
  release-manager.sh auto-recover
  release-manager.sh backup [reason]
  release-manager.sh restore <restic-snapshot> --confirm-restore

The release archive must be at:
  $SITE_PANEL_ROOT/incoming/<commit-sha>.tar.gz

Safety properties:
  * release IDs are Git object IDs (40 or 64 lowercase hexadecimal characters);
  * deploy snapshots the current service only before an update;
  * a failed health check may roll back code to the previous release;
  * no command here restores a database automatically.
EOF
}

emit() {
  printf '%s\n' "$*"
}

now_utc() {
  date -u +%Y-%m-%dT%H:%M:%SZ
}

ensure_layout() {
  mkdir -p "$RELEASES_DIR" "$INCOMING_DIR" "$SHARED_DIR" "$STATE_DIR" "$BIN_DIR"
  chmod 0750 "$RELEASES_DIR" "$INCOMING_DIR" "$SHARED_DIR" "$STATE_DIR" "$BIN_DIR" 2>/dev/null || true
  touch "$AUDIT_FILE"
  chmod 0640 "$AUDIT_FILE" 2>/dev/null || true
}

audit() {
  local action="$1"
  local result="$2"
  local detail="${3:-}"
  printf '%s action=%s result=%s %s\n' "$(now_utc)" "$action" "$result" "$detail" >>"$AUDIT_FILE"
}

die() {
  emit "result=error"
  emit "error=$1"
  audit "${ACTION:-unknown}" error "message=$1"
  exit 1
}

is_release_id() {
  [[ "$1" =~ ^[0-9a-f]{40}([0-9a-f]{24})?$ ]]
}

require_release_id() {
  is_release_id "$1" || die "invalid_release_id"
}

release_dir() {
  printf '%s/%s' "$RELEASES_DIR" "$1"
}

release_id_for_link() {
  local link="$1"
  [[ -L "$link" ]] || return 0
  basename "$(readlink -f "$link")"
}

atomic_link() {
  local link="$1"
  local target="$2"
  local tmp="${link}.next.$$"
  ln -s "$target" "$tmp"
  mv -Tf "$tmp" "$link"
}

write_state() {
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

read_state() {
  local key="$1"
  [[ -f "$STATE_FILE" ]] || return 0
  sed -n "s/^${key}=//p" "$STATE_FILE" | tail -n 1
}

compose_for_release() {
  local release="$1"
  local dir
  dir="$(release_dir "$release")"
  [[ -d "$dir" ]] || die "release_not_found"
  [[ -f "$dir/infra/docker/docker-compose.production.yml" ]] || die "production_compose_missing"
  [[ -f "$dir/.env" ]] || die "release_env_link_missing"
  shift
  docker compose \
    --project-name "$COMPOSE_PROJECT" \
    --env-file "$dir/.env" \
    -f "$dir/infra/docker/docker-compose.production.yml" \
    "$@"
}

link_shared_env() {
  local release="$1"
  local dir
  dir="$(release_dir "$release")"
  [[ -f "$SHARED_DIR/.env" ]] || die "shared_env_missing"
  rm -f "$dir/.env"
  ln -s "$SHARED_DIR/.env" "$dir/.env"
}

shared_env_value() {
  local key="$1"
  sed -n "s/^${key}=//p" "$SHARED_DIR/.env" | tail -n 1
}

validate_production_env() {
  local value key
  [[ "$(shared_env_value APP_ENV)" == "production" ]] || die "app_env_must_be_production"

  for key in PANEL_DOMAIN API_DOMAIN CADDY_EMAIL POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD \
    APP_SECRET_KEY APP_PEPPER BLIND_INDEX_PEPPER FIELD_ENCRYPTION_KEY; do
    value="$(shared_env_value "$key")"
    [[ -n "$value" ]] || die "production_env_missing_${key}"
    [[ "$value" != *change-me* ]] || die "production_env_placeholder_${key}"
  done

  for key in PANEL_DOMAIN API_DOMAIN; do
    value="$(shared_env_value "$key")"
    [[ "$value" =~ ^[A-Za-z0-9.-]+$ && "$value" == *.* && "$value" != *.example.com && "$value" != *.example.test && "$value" != *.invalid ]] || die "invalid_${key}"
  done

  value="$(shared_env_value CADDY_EMAIL)"
  [[ "$value" == *@* && "$value" != *@example.com && "$value" != *@example.test && "$value" != *@invalid ]] || die "invalid_CADDY_EMAIL"

  [[ -f "$VALIDATOR" ]] || die "production_env_validator_missing"
  python3 "$VALIDATOR" --env-file "$SHARED_DIR/.env" --require-secure-permissions >/dev/null \
    || die "production_env_validation_failed"

  [[ -f "$SHARED_DIR/backup.env" ]] || die "backup_env_missing"
  [[ -n "$(sed -n 's/^RESTIC_REPOSITORY=//p' "$SHARED_DIR/backup.env" | tail -n 1)" ]] || die "restic_repository_missing"
  value="$(sed -n 's/^RESTIC_PASSWORD_FILE=//p' "$SHARED_DIR/backup.env" | tail -n 1)"
  [[ -n "$value" && -r "$value" ]] || die "restic_password_file_unreadable"
}

archive_path() {
  printf '%s/%s.tar.gz' "$INCOMING_DIR" "$1"
}

validate_archive_paths() {
  local archive="$1"
  local path
  while IFS= read -r path; do
    case "$path" in
      /*|../*|*/../*|..)
        die "unsafe_archive_path"
        ;;
    esac
  done < <(tar -tzf "$archive")
}

install_archive() {
  local release="$1"
  local archive target tmp manifest
  require_release_id "$release"
  archive="$(archive_path "$release")"
  target="$(release_dir "$release")"

  [[ -f "$archive" ]] || die "release_archive_missing"
  [[ ! -e "$target" ]] || die "release_already_installed"
  validate_archive_paths "$archive"

  tmp="${RELEASES_DIR}/.${release}.unpack.$$"
  mkdir -p "$tmp"
  trap 'rm -rf "$tmp"' RETURN
  tar -xzf "$archive" -C "$tmp"

  manifest="$tmp/.site-panel-release"
  [[ -f "$manifest" ]] || die "release_manifest_missing"
  grep -qx "commit=$release" "$manifest" || die "release_manifest_mismatch"
  [[ -f "$tmp/infra/docker/docker-compose.production.yml" ]] || die "production_compose_missing"
  [[ -f "$tmp/scripts/release-manager.sh" ]] || die "release_manager_missing"
  [[ -f "$tmp/scripts/backup-production.sh" ]] || die "backup_script_missing"
  [[ -f "$tmp/scripts/restore-production.sh" ]] || die "restore_script_missing"
  [[ -f "$tmp/scripts/validate_production_env.py" ]] || die "production_env_validator_missing"

  chmod -R go-w "$tmp"
  mv "$tmp" "$target"
  trap - RETURN
  link_shared_env "$release"

  # The stable bin directory is the entry point used by the restricted SSH gateway.
  install -m 0750 "$target/scripts/release-manager.sh" "$BIN_DIR/release-manager.sh"
  install -m 0750 "$target/scripts/backup-production.sh" "$BIN_DIR/backup-production.sh"
  install -m 0750 "$target/scripts/restore-production.sh" "$BIN_DIR/restore-production.sh"
  install -m 0755 \
    "$target/scripts/validate_production_env.py" "$BIN_DIR/validate_production_env.py"

  emit "release=$release"
  emit "archive=installed"
  audit install-archive ok "release=$release"
}

running_services_are_present() {
  local release="$1"
  local running service
  running="$(compose_for_release "$release" ps --services --status running 2>/dev/null || true)"
  for service in postgres redis api worker caddy panel; do
    grep -qx "$service" <<<"$running" || return 1
  done
}

api_health_is_ok() {
  local release="$1"
  compose_for_release "$release" exec -T api python -c '
from urllib.request import urlopen
response = urlopen("http://127.0.0.1:8000/api/v1/health", timeout=5)
raise SystemExit(0 if response.status == 200 else 1)
' >/dev/null 2>&1
}

wait_for_health() {
  local release="$1"
  local deadline
  deadline=$(( $(date +%s) + HEALTH_TIMEOUT_SECONDS ))
  while (( $(date +%s) < deadline )); do
    if running_services_are_present "$release" && api_health_is_ok "$release"; then
      return 0
    fi
    sleep 3
  done
  return 1
}

start_release() {
  local release="$1"
  compose_for_release "$release" up -d --build --remove-orphans
}

run_predeploy_backup() {
  local target_release="$1"
  local current
  current="$(release_id_for_link "$CURRENT_LINK")"
  [[ -n "$current" ]] || return 0
  [[ -x "$BIN_DIR/backup-production.sh" ]] || die "backup_manager_missing"
  SITE_PANEL_ROOT="$SITE_PANEL_ROOT" COMPOSE_PROJECT="$COMPOSE_PROJECT" \
    "$BIN_DIR/backup-production.sh" --reason "pre-deploy-$target_release"
}

cleanup_old_releases() {
  local current previous path count=0
  current="$(release_id_for_link "$CURRENT_LINK")"
  previous="$(release_id_for_link "$PREVIOUS_LINK")"
  while IFS= read -r path; do
    local id
    id="$(basename "$path")"
    if [[ "$id" == "$current" || "$id" == "$previous" ]]; then
      continue
    fi
    count=$((count + 1))
    if (( count > RELEASE_RETENTION )); then
      rm -rf "$path"
      audit cleanup ok "release=$id"
    fi
  done < <(find "$RELEASES_DIR" -mindepth 1 -maxdepth 1 -type d -regextype posix-extended -regex '.*/[0-9a-f]{40}([0-9a-f]{24})?' -printf '%T@ %p\n' | sort -nr | cut -d' ' -f2-)
}

activate_release() {
  local release="$1"
  local target previous_path previous_id
  require_release_id "$release"
  target="$(release_dir "$release")"
  [[ -d "$target" ]] || die "release_not_found"
  validate_production_env
  link_shared_env "$release"

  previous_path=""
  previous_id="$(release_id_for_link "$CURRENT_LINK")"
  if [[ -n "$previous_id" ]]; then
    previous_path="$(readlink -f "$CURRENT_LINK")"
  fi

  run_predeploy_backup "$release"

  # Build before traffic points at the new release. It does not change running containers.
  compose_for_release "$release" build
  atomic_link "$CURRENT_LINK" "$target"

  if start_release "$release" && wait_for_health "$release"; then
    if [[ -n "$previous_path" ]]; then
      atomic_link "$PREVIOUS_LINK" "$previous_path"
    fi
    write_state current_release "$release"
    write_state last_good_release "$release"
    write_state last_deploy_at "$(now_utc)"
    cleanup_old_releases
    emit "release=$release"
    emit "health=ok"
    emit "rollback=not_needed"
    audit activate ok "release=$release previous=${previous_id:-none}"
    return 0
  fi

  audit activate failed "release=$release previous=${previous_id:-none}"
  if [[ -z "$previous_path" ]]; then
    write_state current_release "$release"
    die "health_failed_no_previous_release"
  fi

  atomic_link "$CURRENT_LINK" "$previous_path"
  if start_release "$previous_id" && wait_for_health "$previous_id"; then
    write_state current_release "$previous_id"
    write_state last_rollback_at "$(now_utc)"
    emit "release=$release"
    emit "health=failed"
    emit "rollback=ok"
    emit "active_release=$previous_id"
    audit rollback ok "failed_release=$release active_release=$previous_id"
    return 1
  fi

  write_state current_release "$previous_id"
  die "health_failed_after_rollback"
}

rollback_release() {
  local current current_path previous previous_path
  current="$(release_id_for_link "$CURRENT_LINK")"
  previous="$(release_id_for_link "$PREVIOUS_LINK")"
  [[ -n "$current" && -n "$previous" ]] || return 1
  current_path="$(readlink -f "$CURRENT_LINK")"
  previous_path="$(readlink -f "$PREVIOUS_LINK")"

  atomic_link "$CURRENT_LINK" "$previous_path"
  atomic_link "$PREVIOUS_LINK" "$current_path"
  if start_release "$previous" && wait_for_health "$previous"; then
    write_state current_release "$previous"
    write_state last_rollback_at "$(now_utc)"
    emit "rollback=ok"
    emit "active_release=$previous"
    audit rollback ok "from=$current to=$previous"
    return 0
  fi

  atomic_link "$CURRENT_LINK" "$current_path"
  atomic_link "$PREVIOUS_LINK" "$previous_path"
  start_release "$current" || true
  return 1
}

restart_current() {
  local current
  current="$(release_id_for_link "$CURRENT_LINK")"
  [[ -n "$current" ]] || return 1
  compose_for_release "$current" restart api worker panel caddy || return 1
  if wait_for_health "$current"; then
    write_state last_restart_at "$(now_utc)"
    emit "restart=ok"
    emit "active_release=$current"
    audit restart ok "release=$current"
    return 0
  fi
  return 1
}

auto_recover() {
  local current last_recovery now
  current="$(release_id_for_link "$CURRENT_LINK")"
  [[ -n "$current" ]] || die "current_release_missing"

  if wait_for_health "$current"; then
    emit "health=ok"
    emit "recovery=no_action"
    return 0
  fi

  now="$(date +%s)"
  last_recovery="$(read_state last_auto_recovery_epoch)"
  if [[ "$last_recovery" =~ ^[0-9]+$ ]] && (( now - last_recovery < AUTO_RECOVERY_COOLDOWN_SECONDS )); then
    emit "health=failed"
    emit "recovery=cooldown"
    audit auto-recover skipped "release=$current reason=cooldown"
    return 1
  fi
  write_state last_auto_recovery_epoch "$now"

  if restart_current; then
    emit "recovery=restart"
    audit auto-recover ok "release=$current action=restart"
    return 0
  fi

  if rollback_release; then
    emit "recovery=rollback"
    audit auto-recover ok "release=$current action=rollback"
    return 0
  fi

  die "auto_recovery_failed"
}

show_status() {
  local current previous health=unknown
  current="$(release_id_for_link "$CURRENT_LINK")"
  previous="$(release_id_for_link "$PREVIOUS_LINK")"
  if [[ -n "$current" ]] && wait_for_health "$current"; then
    health=ok
  elif [[ -n "$current" ]]; then
    health=failed
  fi
  emit "current_release=${current:-none}"
  emit "previous_release=${previous:-none}"
  emit "health=$health"
  emit "last_good_release=$(read_state last_good_release)"
  emit "last_backup_snapshot=$(read_state last_backup_snapshot)"
}

run_backup() {
  local reason="${1:-manual}"
  [[ -x "$BIN_DIR/backup-production.sh" ]] || die "backup_manager_missing"
  SITE_PANEL_ROOT="$SITE_PANEL_ROOT" COMPOSE_PROJECT="$COMPOSE_PROJECT" \
    "$BIN_DIR/backup-production.sh" --reason "$reason"
}

run_restore() {
  local snapshot="$1"
  local confirmation="$2"
  [[ "$confirmation" == "--confirm-restore" ]] || die "restore_confirmation_required"
  [[ -x "$BIN_DIR/restore-production.sh" ]] || die "restore_manager_missing"
  SITE_PANEL_ROOT="$SITE_PANEL_ROOT" COMPOSE_PROJECT="$COMPOSE_PROJECT" \
    "$BIN_DIR/restore-production.sh" --snapshot "$snapshot" --confirm-restore
}

main() {
  ACTION="${1:-}"
  ensure_layout
  exec 9>"$LOCK_FILE"
  flock -n 9 || die "release_manager_locked"

  case "$ACTION" in
    install-archive)
      [[ $# -eq 2 ]] || { usage; exit 2; }
      install_archive "$2"
      ;;
    activate)
      [[ $# -eq 2 ]] || { usage; exit 2; }
      activate_release "$2"
      ;;
    deploy)
      [[ $# -eq 2 ]] || { usage; exit 2; }
      install_archive "$2"
      activate_release "$2"
      ;;
    status)
      [[ $# -eq 1 ]] || { usage; exit 2; }
      show_status
      ;;
    restart)
      [[ $# -eq 1 ]] || { usage; exit 2; }
      restart_current || die "restart_health_failed"
      ;;
    rollback)
      [[ $# -eq 1 ]] || { usage; exit 2; }
      rollback_release || die "rollback_health_failed_or_missing"
      ;;
    auto-recover)
      [[ $# -eq 1 ]] || { usage; exit 2; }
      auto_recover
      ;;
    backup)
      [[ $# -le 2 ]] || { usage; exit 2; }
      run_backup "${2:-manual}"
      ;;
    restore)
      [[ $# -eq 3 ]] || { usage; exit 2; }
      run_restore "$2" "$3"
      ;;
    -h|--help|help|"")
      usage
      ;;
    *)
      usage
      exit 2
      ;;
  esac
}

main "$@"
