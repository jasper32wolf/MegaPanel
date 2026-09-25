#!/usr/bin/env bash
# Start Site Panel (API + worker + Vite) on Linux/macOS
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
RUNTIME="$ROOT/data/runtime"
mkdir -p "$RUNTIME"

[[ -x .venv/bin/python ]] || { echo "Missing .venv — run ./scripts/install.sh first"; exit 1; }
# shellcheck disable=SC1091
source .venv/bin/activate

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  echo "Ensuring Postgres/Redis containers..."
  docker compose -f infra/docker/docker-compose.deps.yml up -d >/dev/null
fi

export PYTHONPATH="$ROOT/apps/api:$ROOT/packages/shared/src:$ROOT/packages/security/src:$ROOT/packages/ssg/src"
export HTTP_PROXY= HTTPS_PROXY= NO_PROXY="*"

start_one() {
  local name="$1"; shift
  local pidfile="$RUNTIME/$name.pid"
  if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    echo "$name already running (pid $(cat "$pidfile"))"
    return
  fi
  nohup "$@" >"$RUNTIME/$name.out.log" 2>"$RUNTIME/$name.err.log" &
  echo $! >"$pidfile"
  echo "Started $name (pid $!) — logs: data/runtime/$name.*.log"
}

start_one api uvicorn app.main:app --app-dir apps/api --host 127.0.0.1 --port 8000 --reload
start_one worker arq app.worker.WorkerSettings
start_one panel bash -lc "cd \"$ROOT/apps/panel\" && exec npm run dev -- --host 127.0.0.1 --port 5173"

ok=0
for _ in $(seq 1 45); do
  if curl -fsS http://127.0.0.1:8000/api/v1/health/ready >/dev/null 2>&1; then
    ok=1
    break
  fi
  sleep 1
done
if [[ "$ok" -eq 1 ]]; then
  echo "API health OK"
else
  echo "WARN: API not healthy yet — check data/runtime/api.err.log"
fi

echo
echo "Panel: http://127.0.0.1:5173"
echo "API:   http://127.0.0.1:8000/docs"
echo "Bootstrap operator: python3 scripts/bootstrap_operator.py --email you@example.com"
echo "Stop:  ./scripts/stop.sh"
