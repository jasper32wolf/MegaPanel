#!/usr/bin/env bash
# Stop locally started Site Panel processes
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME="$ROOT/data/runtime"
DEPS=0
[[ "${1:-}" == "--deps" ]] && DEPS=1

for name in api worker panel; do
  pidfile="$RUNTIME/$name.pid"
  if [[ -f "$pidfile" ]]; then
    pid="$(cat "$pidfile")"
    if kill -0 "$pid" 2>/dev/null; then
      # kill process group if possible
      kill "$pid" 2>/dev/null || true
      sleep 0.5
      kill -9 "$pid" 2>/dev/null || true
      echo "Stopped $name (pid $pid)"
    else
      echo "$name not running (stale pid $pid)"
    fi
    rm -f "$pidfile"
  fi
done

# Also stop orphaned uvicorn/vite on default ports (best-effort)
if command -v pkill >/dev/null 2>&1; then
  pkill -f "uvicorn app.main:app" 2>/dev/null || true
  pkill -f "vite" 2>/dev/null || true
fi

if [[ "$DEPS" -eq 1 ]] && command -v docker >/dev/null 2>&1; then
  cd "$ROOT"
  docker compose -f infra/docker/docker-compose.deps.yml down
  echo "Stopped Docker deps (postgres/redis)"
fi

echo "Done."
