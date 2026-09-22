#!/usr/bin/env bash
# Isolated behaviour tests for scripts/release-manager.sh.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# Git for Windows maps directory symlinks differently from Linux and cannot
# exercise mv -T atomic link replacement. The full state-transition suite runs
# in the Ubuntu GitHub Actions runner; local Windows runs stay non-destructive.
case "$(uname -s)" in
  MINGW*|MSYS*)
    printf 'SKIP: release-manager symlink transition test requires Linux\n'
    exit 0
    ;;
esac

MANAGER="$ROOT/scripts/release-manager.sh"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/site-panel-release-test.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

SITE_ROOT="$TMP/site-panel"
FAKE_BIN="$TMP/bin"
LOG="$TMP/docker.log"
RESTIC_PAYLOAD="$TMP/restic-payload"
mkdir -p "$SITE_ROOT/releases" "$SITE_ROOT/incoming" "$SITE_ROOT/shared/release-state" "$SITE_ROOT/bin" "$FAKE_BIN" "$RESTIC_PAYLOAD"
printf '%s\n' \
  'APP_ENV=production' \
  'POSTGRES_DB=site_panel' \
  'POSTGRES_USER=site_panel' \
  'POSTGRES_PASSWORD=test-postgres-password' \
  'APP_SECRET_KEY=test-app-secret-that-is-long-enough' \
  'APP_PEPPER=test-app-pepper-that-is-long-enough' \
  'BLIND_INDEX_PEPPER=test-blind-index-pepper-that-is-long-enough' \
  'FIELD_ENCRYPTION_KEY=test-field-encryption-key-that-is-long-enough' \
  'PANEL_DOMAIN=panel.test' \
  'API_DOMAIN=api.test' \
  'CADDY_EMAIL=ops@ops.test' \
  'PANEL_PUBLIC_URL=https://panel.test' \
  'API_PUBLIC_URL=https://api.test' \
  'CORS_ORIGINS=https://panel.test' >"$SITE_ROOT/shared/.env"
chmod 0600 "$SITE_ROOT/shared/.env"
printf '%s\n' 'RESTIC_REPOSITORY=s3:test' "RESTIC_PASSWORD_FILE=$TMP/restic-password" >"$SITE_ROOT/shared/backup.env"
printf '%s\n' 'test-password' >"$TMP/restic-password"

cat >"$FAKE_BIN/docker" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
printf '%s\n' "$*" >>"${FAKE_DOCKER_LOG:?}"
if [[ "${1:-}" == "volume" && "${2:-}" == "inspect" ]]; then
  exit 0
fi
if [[ "${1:-}" == "run" ]]; then
  args=("$@")
  backup_host=""
  archive=""
  for ((i=0; i<${#args[@]}; i++)); do
    if [[ "${args[$i]}" == *:/backup ]]; then
      backup_host="${args[$i]%:/backup}"
    elif [[ "${args[$i]}" == "czf" ]]; then
      archive="${args[$((i+1))]}"
    fi
  done
  if [[ -n "$backup_host" ]]; then
    [[ "$archive" == /backup/*.tar.gz ]] || exit 1
    printf 'volume-archive' >"$backup_host/${archive#/backup/}"
  fi
  exit 0
fi
if [[ "${1:-}" != "compose" ]]; then
  exit 0
fi
joined=" $* "
compose_file=""
args=("$@")
for ((i=0; i<${#args[@]}; i++)); do
  if [[ "${args[$i]}" == "-f" ]]; then
    compose_file="${args[$((i+1))]}"
  fi
done
if [[ "$joined" == *" ps --services --status running "* ]]; then
  if [[ -n "${FAKE_FAILED_RELEASE:-}" && "$compose_file" == *"/$FAKE_FAILED_RELEASE/"* ]]; then
    printf '%s\n' postgres redis worker caddy panel
  else
    printf '%s\n' postgres redis api worker caddy panel
  fi
  exit 0
fi
if [[ "$joined" == *" exec -T api python "* ]]; then
  if [[ -n "${FAKE_FAILED_RELEASE:-}" && "$compose_file" == *"/$FAKE_FAILED_RELEASE/"* ]]; then
    exit 1
  fi
  exit 0
fi
if [[ "$joined" == *" exec -T postgres pg_dump "* ]]; then
  printf 'postgres-dump'
  exit 0
fi
exit 0
EOF
chmod +x "$FAKE_BIN/docker"

cat >"$FAKE_BIN/restic" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
[[ "${RESTIC_REPOSITORY:-}" == "s3:test" ]]
[[ -r "${RESTIC_PASSWORD_FILE:-}" ]]
case "${1:-}" in
  snapshots)
    printf '[{"short_id":"deadbeef"}]\n'
    ;;
  backup)
    payload="${!#}"
    for file in manifest.env sites_data.tar.gz uploads_data.tar.gz caddy_data.tar.gz caddy_config.tar.gz; do
      cp "$payload/$file" "${FAKE_RESTIC_PAYLOAD:?}/$file"
    done
    ;;
  restore)
    target=""
    args=("$@")
    for ((i=0; i<${#args[@]}; i++)); do
      if [[ "${args[$i]}" == "--target" ]]; then
        target="${args[$((i+1))]}"
      fi
    done
    [[ -n "$target" ]] || exit 1
    payload="$target/site-panel"
    mkdir -p "$payload"
    printf 'postgres-dump' >"$payload/postgres.dump"
    for file in sites_data.tar.gz uploads_data.tar.gz caddy_data.tar.gz caddy_config.tar.gz; do
      printf 'volume-archive' >"$payload/$file"
    done
    cp "${FAKE_SHARED_ENV:?}" "$payload/shared.env"
    cat >"$payload/manifest.env" <<'MANIFEST'
manifest_version=2
backup_policy=production-volumes
backup_policy_version=1
included_volumes=sites_data,uploads_data,caddy_data,caddy_config
excluded_volumes=dsar_data
excluded_volume_restore_action=clear
MANIFEST
    ;;
  *)
    exit 0
    ;;
esac
EOF
chmod +x "$FAKE_BIN/restic"

cat >"$FAKE_BIN/flock" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$FAKE_BIN/flock"

assert_eq() {
  local actual="$1" expected="$2" label="$3"
  if [[ "$actual" != "$expected" ]]; then
    printf 'FAIL: %s (expected=%s actual=%s)\n' "$label" "$expected" "$actual" >&2
    exit 1
  fi
}

assert_fails() {
  if "$@" >/dev/null 2>&1; then
    printf 'FAIL: expected command to fail: %s\n' "$*" >&2
    exit 1
  fi
}

run_or_report() {
  local output="$1"
  shift
  if ! "$@" >"$output" 2>&1; then
    cat "$output" >&2
    exit 1
  fi
}

assert_line() {
  local expected="$1" file="$2" label="$3"
  if ! grep -qx -- "$expected" "$file"; then
    printf 'FAIL: %s (expected=%s file=%s)\n' "$label" "$expected" "$file" >&2
    printf '%s\n' 'Actual file contents:' >&2
    cat "$file" >&2
    exit 1
  fi
}

assert_not_contains() {
  local pattern="$1" file="$2" label="$3"
  if grep -Fq -- "$pattern" "$file"; then
    printf 'FAIL: unexpected content for %s: %s\n' "$label" "$pattern" >&2
    exit 1
  fi
}

# Explicit, valid deterministic IDs for archive/state assertions.
OLD=1111111111111111111111111111111111111111
GOOD=2222222222222222222222222222222222222222
BAD=3333333333333333333333333333333333333333

make_release_tree() {
  local id="$1"
  local dir="$SITE_ROOT/releases/$id"
  mkdir -p "$dir/infra/docker" "$dir/scripts"
  : >"$dir/infra/docker/docker-compose.production.yml"
  printf 'commit=%s\n' "$id" >"$dir/.site-panel-release"
  cp "$ROOT/scripts/release-manager.sh" "$dir/scripts/release-manager.sh"
  cp "$ROOT/scripts/backup-production.sh" "$dir/scripts/backup-production.sh"
  cp "$ROOT/scripts/restore-production.sh" "$dir/scripts/restore-production.sh"
  cp "$ROOT/scripts/validate_production_env.py" "$dir/scripts/validate_production_env.py"
  chmod +x "$dir/scripts/"*.sh "$dir/scripts/validate_production_env.py"
  ln -s "$SITE_ROOT/shared/.env" "$dir/.env"
}

make_archive() {
  local id="$1"
  local payload="$TMP/payload-$id"
  mkdir -p "$payload/infra/docker" "$payload/scripts"
  : >"$payload/infra/docker/docker-compose.production.yml"
  printf 'commit=%s\n' "$id" >"$payload/.site-panel-release"
  cp "$ROOT/scripts/release-manager.sh" "$payload/scripts/release-manager.sh"
  cp "$ROOT/scripts/backup-production.sh" "$payload/scripts/backup-production.sh"
  cp "$ROOT/scripts/restore-production.sh" "$payload/scripts/restore-production.sh"
  cp "$ROOT/scripts/validate_production_env.py" "$payload/scripts/validate_production_env.py"
  chmod +x "$payload/scripts/"*.sh
  tar -C "$payload" -czf "$SITE_ROOT/incoming/$id.tar.gz" .
}

make_release_tree "$OLD"
ln -s "$SITE_ROOT/releases/$OLD" "$SITE_ROOT/current"
make_archive "$GOOD"
make_archive "$BAD"

export PATH="$FAKE_BIN:$PATH"
export FAKE_DOCKER_LOG="$LOG"
export FAKE_RESTIC_PAYLOAD="$RESTIC_PAYLOAD"
export FAKE_SHARED_ENV="$SITE_ROOT/shared/.env"
export SITE_PANEL_ROOT="$SITE_ROOT"
export COMPOSE_PROJECT="site-panel"
export HEALTH_TIMEOUT_SECONDS=1
export AUTO_RECOVERY_COOLDOWN_SECONDS=3600

run_or_report "$TMP/good.out" bash "$MANAGER" deploy "$GOOD"
assert_eq "$(basename "$(readlink -f "$SITE_ROOT/current")")" "$GOOD" "good release becomes current"
assert_eq "$(basename "$(readlink -f "$SITE_ROOT/previous")")" "$OLD" "old release becomes previous"
assert_line 'health=ok' "$TMP/good.out" "good release health"
run_or_report "$TMP/backup.out" bash "$MANAGER" backup test
assert_line 'backup_snapshot=deadbeef' "$TMP/backup.out" "backup snapshot"
for archive in sites_data.tar.gz uploads_data.tar.gz caddy_data.tar.gz caddy_config.tar.gz; do
  [[ -s "$RESTIC_PAYLOAD/$archive" ]] || {
    printf 'FAIL: expected backup archive missing: %s\n' "$archive" >&2
    exit 1
  }
done
  assert_line 'manifest_version=2' "$RESTIC_PAYLOAD/manifest.env" "backup manifest version"
  assert_line 'backup_policy=production-volumes' "$RESTIC_PAYLOAD/manifest.env" "backup policy"
  assert_line 'backup_policy_version=1' "$RESTIC_PAYLOAD/manifest.env" "backup policy version"
  assert_line 'included_volumes=sites_data,uploads_data,caddy_data,caddy_config' "$RESTIC_PAYLOAD/manifest.env" "included volumes"
  assert_line 'excluded_volumes=dsar_data' "$RESTIC_PAYLOAD/manifest.env" "excluded volumes"
  assert_line 'excluded_volume_restore_action=clear' "$RESTIC_PAYLOAD/manifest.env" "excluded volume action"
assert_not_contains 'source "$BACKUP_ENV_FILE"' "$ROOT/scripts/backup-production.sh" "backup dotenv parser"
assert_not_contains 'source "$BACKUP_ENV_FILE"' "$ROOT/scripts/restore-production.sh" "restore dotenv parser"
assert_not_contains 'down -v' "$ROOT/scripts/restore-production.sh" "restore volume handling"

export FAKE_FAILED_RELEASE="$BAD"
assert_fails bash "$MANAGER" deploy "$BAD"
assert_eq "$(basename "$(readlink -f "$SITE_ROOT/current")")" "$GOOD" "failed deployment restores current release"
  assert_line 'last_good_release=2222222222222222222222222222222222222222' "$SITE_ROOT/shared/release-state/state.env" "last good release"

export FAKE_FAILED_RELEASE="$GOOD"
run_or_report "$TMP/recover.out" bash "$MANAGER" auto-recover
assert_eq "$(basename "$(readlink -f "$SITE_ROOT/current")")" "$OLD" "auto recovery rolls code back"
assert_line 'recovery=rollback' "$TMP/recover.out" "auto recovery result"
export FAKE_FAILED_RELEASE="$OLD"
assert_fails bash "$MANAGER" auto-recover
assert_fails bash "$MANAGER" install-archive not-a-release-id
assert_fails bash "$ROOT/scripts/restore-production.sh" --snapshot deadbeef

cat >"$SITE_ROOT/shared/backup.env" <<EOF
RESTIC_REPOSITORY=s3:test
RESTIC_PASSWORD_FILE=$TMP/restic-password
AWS_ACCESS_KEY_ID=\$(touch "$TMP/allowed-value-executed")
EOF
run_or_report "$TMP/dotenv-allowed-value.out" bash "$MANAGER" backup dotenv-allowed-value
[[ ! -e "$TMP/allowed-value-executed" ]] || {
  printf 'FAIL: allowlisted backup.env value executed shell code\n' >&2
  exit 1
}
cat >"$SITE_ROOT/shared/backup.env" <<EOF
RESTIC_REPOSITORY=s3:test
RESTIC_PASSWORD_FILE=$TMP/restic-password
UNSAFE=\$(touch "$TMP/dotenv-executed")
EOF
assert_fails bash "$MANAGER" backup dotenv-test
assert_fails bash "$ROOT/scripts/restore-production.sh" --snapshot deadbeef --confirm-restore
[[ ! -e "$TMP/dotenv-executed" ]] || {
  printf 'FAIL: backup.env executed shell code\n' >&2
  exit 1
}
printf '%s\n' 'RESTIC_REPOSITORY=s3:test' "RESTIC_PASSWORD_FILE=$TMP/restic-password" >"$SITE_ROOT/shared/backup.env"

unset FAKE_FAILED_RELEASE
run_or_report "$TMP/restore.out" bash "$ROOT/scripts/restore-production.sh" --snapshot deadbeef --confirm-restore
assert_line 'restore=ok' "$TMP/restore.out" "restore result"
for volume in sites_data uploads_data caddy_data caddy_config dsar_data; do
  grep -Fq "site-panel_${volume}:/data" "$LOG"
done
assert_not_contains 'site-panel_dsar_data:/data:ro' "$LOG" "DSAR backup exclusion"

printf 'PASS: release manager deploy, backup policy, rollback, dotenv parser and restore guard\n'
