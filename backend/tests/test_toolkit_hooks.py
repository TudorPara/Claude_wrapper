import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import TOOLKIT

GUARD = TOOLKIT / "plugins" / "core" / "scripts" / "guard.py"
RULES = TOOLKIT / "plugins" / "python" / "scripts" / "rules_on_touch.py"
RUFF_HOOK = TOOLKIT / "plugins" / "python" / "scripts" / "ruff_on_edit.py"
TOPICS = TOOLKIT / "plugins" / "python" / "skills" / "python-rules" / "topics"
PROTECTED = json.dumps([".env*", "**/*.key", "**/*.pem", "**/secrets/**"])


def guard(command: str, orchestrated: bool = True) -> int:
    environment = {"CW_PROTECTED_PATHS": PROTECTED, "PATH": "/usr/bin:/bin"} | ({"CW_RUN": "1"} if orchestrated else {})
    payload = json.dumps({"tool_input": {"command": command}})
    return subprocess.run(
        [sys.executable, str(GUARD)], input=payload, text=True, capture_output=True, env=environment
    ).returncode


@pytest.mark.parametrize(
    "command",
    [
        "git commit -m x", "git -C . commit -m x", "git -c user.name=a commit -m x", "cd src && git stash",
        'bash -c "git push origin main"', "git branch --delete main", "git restore .", "git clean -fd",
        "git push origin +main", "git commit --no-verify -m x", "rm -rf ~", "cat .env", "cat secrets/key.txt",
        "python3 -c \"print(open('.env').read())\"", "cat certs/server.pem",
    ],
)  # fmt: skip
def test_the_guard_blocks(command: str) -> None:
    assert guard(command) == 2


@pytest.mark.parametrize(
    "command",
    [
        "git status", "git diff src/reset.py", "git log --oneline -5", "git branch", 'grep -rn "git commit" docs/',
        "uv run pytest tests/test_environment.py", "uv run ruff check .", "cat README.md | head -5",
    ],
)  # fmt: skip
def test_the_guard_allows(command: str) -> None:
    assert guard(command) == 0


def test_outside_cw_the_guard_allows_commits_but_never_force_pushes() -> None:
    assert guard("git commit -m x", orchestrated=False) == 0
    assert guard("git push -f", orchestrated=False) == 2


def rules(session: str, project: Path, event: str, file_path: Path) -> subprocess.CompletedProcess[str]:
    payload = {
        "session_id": session,
        "cwd": str(project),
        "hook_event_name": event,
        "tool_input": {"file_path": str(file_path)},
    }
    environment = {"TMPDIR": str(project), "PATH": "/usr/bin:/bin"}
    return subprocess.run(
        [sys.executable, str(RULES)], input=json.dumps(payload), text=True, capture_output=True, env=environment
    )


def test_a_topic_is_shown_once_per_session_when_a_covered_file_is_touched(tmp_path: Path) -> None:
    session = "one-session"
    first = rules(session, tmp_path, "PostToolUse", tmp_path / "src" / "app" / "routes" / "items.py")
    assert first.returncode == 2 and "## 2. Layers" in first.stderr and "repeat your edit" not in first.stderr
    assert rules(session, tmp_path, "PostToolUse", tmp_path / "src" / "app" / "routes" / "users.py").returncode == 0
    edit = rules(session, tmp_path, "PreToolUse", tmp_path / "alembic" / "env.py")
    assert edit.returncode == 2 and "## 4. Migrations" in edit.stderr and "repeat your edit" in edit.stderr
    assert rules(session, tmp_path, "PreToolUse", tmp_path / "src" / "app" / "helpers.py").returncode == 0
    assert rules(session, tmp_path, "PreToolUse", Path("/etc/hosts")).returncode == 0


def test_every_topic_in_the_index_has_its_file() -> None:
    index = json.loads((TOPICS / "topics.json").read_text())
    for topic in [*index["topics"], *index["always"], *index["new_project"], *index["review"]]:
        assert (TOPICS / f"{topic}.md").exists(), topic


def test_the_ruff_hook_keeps_an_import_the_next_edit_will_use_and_says_when_it_changed_the_file(
    tmp_path: Path,
) -> None:
    def git(*arguments: str) -> None:
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *arguments], cwd=tmp_path, check=True)

    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / ".venv" / "bin" / "ruff").symlink_to(Path(sys.executable).parent / "ruff")
    (tmp_path / ".gitignore").write_text(".venv\n")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0"\n')
    app = tmp_path / "app.py"
    app.write_text("def f():\n    return 1\n")
    git("init", "-q")
    git("add", "-A")
    git("commit", "-qm", "base")
    app.write_text("import json\ndef f():\n  return 1\n")
    payload = json.dumps({"tool_input": {"file_path": str(app)}})
    hook = subprocess.run([sys.executable, str(RUFF_HOOK)], input=payload, text=True, capture_output=True)
    assert hook.returncode == 0 and app.read_text().startswith("import json\n")
    assert "read it again" in json.loads(hook.stdout)["hookSpecificOutput"]["additionalContext"]
