"""Fail when local agent state, secrets or generated artifacts enter the Git index."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL_DIRECTORIES = frozenset(
    {
        ".claude",
        ".cursor",
        ".kilo",
        ".kilocode",
        ".roo",
        ".agents",
        ".agent",
        ".codex",
        ".gemini",
        ".opencode",
        ".windsurf",
        ".continue",
        ".aider",
        ".trae",
        ".fleet",
        ".zed",
        ".vscode",
        ".idea",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
    }
)
LOCAL_FILES = frozenset(
    {
        "skills-lock.json",
        ".mcp.json",
        ".cursorrules",
        ".cursorignore",
        ".cursorindexingignore",
        ".rooignore",
        ".envrc",
    }
)
ENV_EXAMPLES = frozenset({".env.example", ".env.production.example"})


def forbidden(path: str) -> bool:
    parts = tuple(part.lower() for part in path.replace("\\", "/").split("/"))
    name = parts[-1]
    return bool(
        LOCAL_DIRECTORIES.intersection(parts[:-1])
        or name in LOCAL_FILES
        or name.startswith(".aider")
        or (name.startswith(".env") and name not in ENV_EXAMPLES)
        or name.endswith((".pem", ".key", ".p12", ".pfx", ".sqlite", ".sqlite3", ".dump"))
    )


def main() -> int:
    files = subprocess.check_output(["git", "ls-files", "--cached", "-z"], cwd=ROOT)
    tracked = (path.decode("utf-8", "surrogateescape") for path in files.split(b"\0") if path)
    unexpected = sorted(path for path in tracked if forbidden(path))
    if unexpected:
        print("Do not commit local agent state or secrets:", file=sys.stderr)
        for path in unexpected:
            print(f"  {path}", file=sys.stderr)
        return 1
    print("Git index contains no local agent state or secret files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
