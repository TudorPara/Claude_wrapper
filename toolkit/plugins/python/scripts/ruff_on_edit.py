"""Format and lint a Python file with ruff right after Claude Code edits it."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

FEEDBACK_EXIT_CODE = 2
PYTHON_SUFFIX = ".py"
PROJECT_MARKER = "pyproject.toml"
PROJECT_RUFF = Path(".venv") / "bin" / "ruff"


def _project_root(file_path: Path) -> Path:
    """Return the nearest folder above the file that holds a pyproject.toml."""
    for folder in file_path.parents:
        if (folder / PROJECT_MARKER).exists():
            return folder
    return file_path.parent


def _ruff(arguments: list[str], root: Path) -> subprocess.CompletedProcess[str]:
    """Run the project's installed ruff, or uvx's when the project has none; never create a venv or lockfile."""
    project_ruff = root / PROJECT_RUFF
    command = [str(project_ruff)] if project_ruff.exists() else ["uvx", "--quiet", "ruff"]
    return subprocess.run([*command, *arguments], cwd=root, capture_output=True, text=True)


def main() -> int:
    """Format the edited file, then report lint errors back to Claude."""
    payload = json.load(sys.stdin)
    file_path = Path(payload.get("tool_input", {}).get("file_path", ""))
    if file_path.suffix != PYTHON_SUFFIX or not file_path.exists() or not shutil.which("uv"):
        return 0
    root = _project_root(file_path)
    _ruff(["format", str(file_path)], root)
    check = _ruff(["check", "--fix", str(file_path)], root)
    if check.returncode == 1:
        print(f"ruff found problems in {file_path}:\n{check.stdout}{check.stderr}", file=sys.stderr)
        return FEEDBACK_EXIT_CODE
    return 0


if __name__ == "__main__":
    sys.exit(main())
