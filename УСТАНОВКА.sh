#!/usr/bin/env bash
# Site Panel — interactive installer (Linux / macOS / VPS)
# Usage: chmod +x УСТАНОВКА.sh && ./УСТАНОВКА.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

pick_python() {
  for c in python3.12 python3 python; do
    if command -v "$c" >/dev/null 2>&1; then
      if "$c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
        echo "$c"
        return 0
      fi
    fi
  done
  return 1
}

if ! PY="$(pick_python)"; then
  echo ""
  echo "[ERROR] Python 3.12+ not found."
  echo "Install Python 3.12+, then re-run: ./УСТАНОВКА.sh"
  echo ""
  exit 1
fi

chmod +x "$ROOT/scripts/"*.sh 2>/dev/null || true
exec "$PY" "$ROOT/scripts/setup_wizard.py" "$@"
