"""Format and lint a Python file with ruff right after Claude Code edits it.

A file is held to the standard it met in the last commit: one that was unformatted, or had lint errors, before
the edit is left as it is, so Claude is never sent to fix problems it did not cause. New files are held to both.
Unused imports and variables are left alone: an import added in one edit is often used in the next, and the
project's lint check catches what is still unused at the end. When ruff changed the file, Claude is told to read it
again before its next edit.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

FEEDBACK_EXIT_CODE = 2
RUFF_FOUND_ERRORS = 1
PYTHON_SUFFIX = ".py"
PROJECT_MARKER = "pyproject.toml"
PROJECT_RUFF = Path(".venv") / "bin" / "ruff"
STDIN = "-"
UNFINISHED_EDIT_RULES = "F401,F841"


def _project_root(file_path: Path) -> Path:
    """Return the nearest folder above the file that holds a pyproject.toml."""
    for folder in file_path.parents:
        if (folder / PROJECT_MARKER).exists():
            return folder
    return file_path.parent


def _ruff_command(root: Path) -> list[str] | None:
    """Return the project's installed ruff, or uvx's when the project has none; never create a venv or lockfile."""
    project_ruff = root / PROJECT_RUFF
    if project_ruff.exists():
        return [str(project_ruff)]
    return ["uvx", "--quiet", "ruff"] if shutil.which("uvx") else None


def _committed_version(file_path: Path, root: Path) -> str | None:
    """Return the file as it is in the last commit, or None when it is new or there is no repository."""
    shown = subprocess.run(
        ["git", "show", f"HEAD:./{file_path.relative_to(root)}"], cwd=root, capture_output=True, text=True
    )
    return shown.stdout if shown.returncode == 0 else None


def _ruff(ruff: list[str], arguments: list[str], root: Path, source: str | None = None) -> bool:
    """Run ruff, optionally on source text given on standard input; return whether it found nothing to report."""
    return subprocess.run([*ruff, *arguments], cwd=root, input=source, capture_output=True, text=True).returncode == 0


def main() -> int:
    """Format the edited file and report lint errors back to Claude, where the file was clean before."""
    payload = json.load(sys.stdin)
    file_path = Path(payload.get("tool_input", {}).get("file_path", "")).resolve()
    if file_path.suffix != PYTHON_SUFFIX or not file_path.exists():
        return 0
    root = _project_root(file_path)
    ruff = _ruff_command(root)
    if ruff is None:
        return 0
    committed = _committed_version(file_path, root)
    from_stdin = ["--stdin-filename", str(file_path), STDIN]
    was_formatted = committed is None or _ruff(ruff, ["format", "--check", *from_stdin], root, committed)
    was_lint_clean = committed is None or _ruff(ruff, ["check", *from_stdin], root, committed)
    before = file_path.read_text(encoding="utf-8", errors="replace")
    check = None
    if was_lint_clean:
        check = subprocess.run(
            [*ruff, "check", "--fix", "--ignore", UNFINISHED_EDIT_RULES, str(file_path)],
            cwd=root,
            capture_output=True,
            text=True,
        )
    if was_formatted:
        _ruff(ruff, ["format", str(file_path)], root)
    if check is not None and check.returncode == RUFF_FOUND_ERRORS:
        print(f"ruff found problems in {file_path}:\n{check.stdout}{check.stderr}", file=sys.stderr)
        return FEEDBACK_EXIT_CODE
    if file_path.read_text(encoding="utf-8", errors="replace") != before:
        context = f"ruff reformatted or fixed {file_path}; read it again before you edit it."
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": context}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
