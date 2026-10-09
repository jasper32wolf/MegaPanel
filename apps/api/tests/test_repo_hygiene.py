from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_repo_hygiene.py"
spec = importlib.util.spec_from_file_location("check_repo_hygiene", SCRIPT)
assert spec and spec.loader
hygiene = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hygiene)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (".claude/settings.json", True),
        (".cursor/rules/site.mdc", True),
        (".kilo/worktrees/agent/.env.example", True),
        (".roo/mcp.json", True),
        (".agents/skills/design/SKILL.md", True),
        ("apps/api/.codex/config.toml", True),
        ("skills-lock.json", True),
        (".env", True),
        ("apps/api/.env.local", True),
        ("secrets/token.pem", True),
        (".env.example", False),
        (".env.production.example", False),
        (".github/workflows/ci.yml", False),
        ("data/geo/cities_sample.csv", False),
        ("apps/api/app/services/ai_secrets.py", False),
    ],
)
def test_git_hygiene_classifies_local_files(path: str, expected: bool):
    assert hygiene.forbidden(path) is expected


def test_git_index_contains_no_agent_state():
    assert hygiene.main() == 0


@pytest.mark.parametrize(
    ("path", "ignored"),
    [
        (".claude/settings.json", True),
        (".cursor/rules/site.mdc", True),
        (".kilo/agent-manager.json", True),
        (".roo/mcp.json", True),
        (".agents/skills/design/SKILL.md", True),
        ("skills-lock.json", True),
        ("apps/api/.env.local", True),
        (".env.example", False),
        (".env.production.example", False),
        (".github/workflows/ci.yml", False),
    ],
)
def test_git_ignore_keeps_local_agent_state_private(path: str, ignored: bool):
    result = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", "--", path],
        cwd=SCRIPT.parents[1],
        check=False,
    )
    assert (result.returncode == 0) is ignored


def test_docker_and_release_archives_exclude_agent_directories():
    root = SCRIPT.parents[1]
    dockerignore = (root / ".dockerignore").read_text(encoding="utf-8")
    workflow = (root / ".github/workflows/deploy-production.yml").read_text(encoding="utf-8")
    for directory in (".claude", ".cursor", ".kilo", ".roo", ".agents"):
        assert directory in dockerignore.splitlines()
        assert f"--exclude={directory} " in workflow
    assert "skills-lock.json" in dockerignore.splitlines()
    assert "--exclude=skills-lock.json " in workflow
