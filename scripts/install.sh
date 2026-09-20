#!/usr/bin/env bash
# One-shot installer for Site Panel (Linux / macOS / VPS)
#
# Usage:
#   ./scripts/install.sh                 # local hybrid (deps in Docker)
#   ./scripts/install.sh --mode docker   # full compose
#   sudo ./scripts/install-production-vps.sh --source-dir "$PWD"  # Linux VPS production
#   ./scripts/install.sh --skip-tests --skip-start
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

MODE="local"
SKIP_TESTS=0
SKIP_SEED=0
SKIP_START=0
START=0
NO_DOCKER=0
DEMO_SEED=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode) MODE="$2"; shift 2 ;;
    --skip-tests) SKIP_TESTS=1; shift ;;
    --skip-seed) SKIP_SEED=1; DEMO_SEED=0; shift ;;
    --skip-start) SKIP_START=1; shift ;;
    --start) START=1; shift ;;
    --no-docker) NO_DOCKER=1; shift ;;
    --demo) DEMO_SEED=1; shift ;;
    -h|--help)
      sed -n '2,12p' "$0"
      exit 0
      ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

if [[ "$SKIP_START" -eq 0 && "$MODE" == "local" && "$START" -eq 0 ]]; then
  START=1
fi
if [[ "$SKIP_START" -eq 1 ]]; then
  START=0
fi

step() { printf '\n\033[36m==> %s\033[0m\n' "$*"; }
die() { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "Missing '$1'. $2"; }

wait_tcp() {
  local host="$1" port="$2" timeout="${3:-90}"
  local i=0
  while (( i < timeout )); do
    if (echo >/dev/tcp/"$host"/"$port") >/dev/null 2>&1; then
      return 0
    fi
    # fallback: nc
    if command -v nc >/dev/null 2>&1 && nc -z "$host" "$port" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
    ((i++)) || true
  done
  die "Timeout waiting for ${host}:${port}"
}

printf '\033[32mSite Panel installer — mode=%s\033[0m\n' "$MODE"

need python3 "Install Python 3.12+"
python3 - <<'PY' || die "Python 3.12+ required"
import sys
raise SystemExit(0 if sys.version_info >= (3, 12) else 1)
PY

DOCKER_OK=0
if [[ "$NO_DOCKER" -eq 0 ]] && command -v docker >/dev/null 2>&1; then
  if docker info >/dev/null 2>&1; then
    DOCKER_OK=1
  fi
fi

case "$MODE" in
  local|deps)
    need npm "Install Node.js 20+"
    ;;
  docker)
    [[ "$DOCKER_OK" -eq 1 ]] || die "Docker required for mode=$MODE"
    ;;
  vps)
    die "VPS production uses: sudo ./scripts/install-production-vps.sh --source-dir \"$ROOT\""
    ;;
  *) die "Unknown mode: $MODE (local|docker|deps)" ;;
esac

step "Preparing .env"
ENV_MODE="$MODE"
[[ "$MODE" == "deps" ]] && ENV_MODE="local"
python3 scripts/prepare_env.py --mode "$ENV_MODE"

COMPOSE_DEPS="infra/docker/docker-compose.deps.yml"
COMPOSE_FULL="infra/docker/docker-compose.yml"

if [[ "$MODE" == "docker" ]]; then
  step "Starting full Docker Compose stack"
  docker compose --env-file .env -f "$COMPOSE_FULL" up -d --build
  step "Waiting for API :8000"
  wait_tcp 127.0.0.1 8000 180
  echo
  printf '\033[32mDONE (%s mode)\033[0m\n' "$MODE"
  echo "  API:   http://127.0.0.1:8000/docs"
  echo "  Panel: http://127.0.0.1:5173"
  echo "  Stop:  docker compose -f infra/docker/docker-compose.yml down"
  exit 0
fi

if [[ "$MODE" == "local" && "$DOCKER_OK" -eq 1 ]]; then
  step "Starting Postgres + Redis (Docker deps)"
  docker compose -f "$COMPOSE_DEPS" up -d
  step "Waiting for Postgres/Redis"
  wait_tcp 127.0.0.1 5432 90
  wait_tcp 127.0.0.1 6379 90
elif [[ "$MODE" == "local" ]]; then
  echo "WARN: Docker unavailable — expecting Postgres :5432 and Redis :6379"
fi

step "Python venv + packages"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
export HTTP_PROXY= HTTPS_PROXY= ALL_PROXY= NO_PROXY="*"
pip install --upgrade pip
pip install hatchling editables
pip install --no-build-isolation \
  -e packages/shared \
  -e packages/security \
  -e packages/ssg \
  -e packages/block-library \
  -e apps/api \
  pytest pytest-asyncio ruff

export PYTHONPATH="$ROOT/apps/api:$ROOT/packages/shared/src:$ROOT/packages/security/src:$ROOT/packages/ssg/src"

if [[ "$SKIP_TESTS" -eq 0 ]]; then
  step "Running unit tests"
  pytest apps/api/tests -q
fi

if [[ "$MODE" != "deps" ]]; then
  step "Alembic migrations"
  alembic -c apps/api/alembic.ini upgrade head

  if [[ "$SKIP_SEED" -eq 0 && "$DEMO_SEED" -eq 1 ]]; then
    step "Seeding demo tenant"
    python scripts/seed_demo.py
  fi
fi

step "Panel npm install"
if [[ ! -d apps/panel/node_modules ]]; then
  (cd apps/panel && npm install)
else
  echo "node_modules present — skip"
fi

mkdir -p data/runtime

if [[ "$START" -eq 1 && "$MODE" == "local" ]]; then
  step "Starting API, worker, panel"
  bash scripts/start.sh
else
  echo
  echo "Install complete. Start with: ./scripts/start.sh"
fi

echo
printf '\033[32mDONE\033[0m\n'
echo "  Bootstrap operator: python3 scripts/bootstrap_operator.py --email you@example.com"
echo "  API:    http://127.0.0.1:8000/docs"
echo "  Panel:  http://127.0.0.1:5173"
echo "  Stop:   ./scripts/stop.sh"
echo "  Stop+DB:./scripts/stop.sh --deps"
echo "  Wizard: ./УСТАНОВКА.sh  (or python3 scripts/setup_wizard.py)"
