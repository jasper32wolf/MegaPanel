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
mkdir -p "$SITE_ROOT/releases" "$SITE_ROOT/incoming" "$SITE_ROOT/shared/release-state" "$SITE_ROOT/bin" "$FAKE_BIN"
printf '%s\n' \
  'APP_ENV=production' \
  'POSTGRES_DB=site_panel' \
  'POSTGRES_USER=site_panel' \
  'POSTGRES_PASSWORD=test-postgres-password' \
  'APP_SECRET_KEY=test-app-secret-that-is-long-enough' \
  'APP_PEPPER=test-app-pepper-that-is-long-enough' \
  'BLIND_INDEX_PEPPER=test-blind-index-pepper-that-is-long-enough' \
  'FIELD_ENCRYPTION_KEY=test-field-encryption-key-that-is-long-enough' \
  'PANEL_DOMAIN=panel.test.example' \
  'API_DOMAIN=api.test.example' \
  'CADDY_EMAIL=ops@test.example' >"$SITE_ROOT/shared/.env"
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
  for arg in "$@"; do
    if [[ "$arg" == *:/backup ]]; then
      host="${arg%:/backup}"
      printf 'sites' >"$host/sites.tar.gz"
      exit 0
    fi
  done
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
case "${1:-}" in
  snapshots) printf '[{"short_id":"deadbeef"}]\n' ;;
  *) exit 0 ;;
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
  chmod +x "$dir/scripts/"*.sh
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
  chmod +x "$payload/scripts/"*.sh
  tar -C "$payload" -czf "$SITE_ROOT/incoming/$id.tar.gz" .
}

make_release_tree "$OLD"
ln -s "$SITE_ROOT/releases/$OLD" "$SITE_ROOT/current"
make_archive "$GOOD"
make_archive "$BAD"

export PATH="$FAKE_BIN:$PATH"
export FAKE_DOCKER_LOG="$LOG"
export SITE_PANEL_ROOT="$SITE_ROOT"
export COMPOSE_PROJECT="site-panel"
export HEALTH_TIMEOUT_SECONDS=1
export AUTO_RECOVERY_COOLDOWN_SECONDS=3600

"$MANAGER" deploy "$GOOD" >"$TMP/good.out"
assert_eq "$(basename "$(readlink -f "$SITE_ROOT/current")")" "$GOOD" "good release becomes current"
assert_eq "$(basename "$(readlink -f "$SITE_ROOT/previous")")" "$OLD" "old release becomes previous"
grep -qx 'health=ok' "$TMP/good.out"
grep -qx 'backup_snapshot=deadbeef' <("$MANAGER" backup test)

export FAKE_FAILED_RELEASE="$BAD"
assert_fails "$MANAGER" deploy "$BAD"
assert_eq "$(basename "$(readlink -f "$SITE_ROOT/current")")" "$GOOD" "failed deployment restores current release"
grep -qx 'last_good_release=2222222222222222222222222222222222222222' "$SITE_ROOT/shared/release-state/state.env"

export FAKE_FAILED_RELEASE="$GOOD"
"$MANAGER" auto-recover >"$TMP/recover.out"
assert_eq "$(basename "$(readlink -f "$SITE_ROOT/current")")" "$OLD" "auto recovery rolls code back"
grep -qx 'recovery=rollback' "$TMP/recover.out"
export FAKE_FAILED_RELEASE="$OLD"
assert_fails "$MANAGER" auto-recover
assert_fails "$MANAGER" install-archive not-a-release-id
assert_fails "$ROOT/scripts/restore-production.sh" --snapshot deadbeef

printf 'PASS: release manager deploy, backup, rollback, cooldown and restore guard\n'
