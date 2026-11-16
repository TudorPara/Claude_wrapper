"""A deterministic map of a repository's code (folders, files, classes, functions), made without an LLM."""

import ast
import re
from collections import defaultdict
from pathlib import Path

from cw import git

MAX_MAP_LINES = 250
MAX_LINE_LENGTH = 120
SWIFT_TYPES_SHOWN = 6
TRUNCATED_NOTE = "... (cut to fit the context budget)"
IGNORED_DIRECTORIES = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".build",
    "build",
    "dist",
    "target",
    ".idea",
    ".vscode",
}
CODE_EXTENSIONS = {".py", ".swift", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java"}
SWIFT_TYPE = re.compile(r"\b(?:struct|class|enum|protocol)\s+([A-Za-z0-9_]+)")


def _code_files(root: Path) -> list[Path]:
    """Return the code files git knows about or would add (so .gitignore is respected), relative to the root."""
    listed = git.git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    paths = [Path(name) for name in listed.split("\0") if name]
    return sorted(
        path
        for path in paths
        if path.suffix in CODE_EXTENSIONS
        and not IGNORED_DIRECTORIES.intersection(path.parts)
        and (root / path).is_file()
    )


def _python_outline(path: Path) -> tuple[str, list[str]]:
    """Return a Python file's first docstring line and its classes and public functions."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return "", []
    docstring = (ast.get_docstring(tree) or "").strip()
    symbols = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            symbols.append(f"class {node.name}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            parameters = ", ".join(argument.arg for argument in node.args.args if argument.arg != "self")
            symbols.append(f"{node.name}({parameters})")
    return (docstring.splitlines()[0] if docstring else ""), symbols


def _swift_outline(path: Path) -> tuple[str, list[str]]:
    """Return a Swift file's types."""
    try:
        return "", SWIFT_TYPE.findall(path.read_text(encoding="utf-8"))[:SWIFT_TYPES_SHOWN]
    except (UnicodeDecodeError, OSError):
        return "", []


def _outline(path: Path) -> tuple[str, list[str]]:
    """Return a file's one-line description and its symbols, for the languages the map understands."""
    if path.suffix == ".py":
        return _python_outline(path)
    if path.suffix == ".swift":
        return _swift_outline(path)
    return "", []


def _fit(line: str) -> str:
    """Cut a line to the longest length the map allows."""
    return line[:MAX_LINE_LENGTH]


def _detailed(folders: dict[Path, list[tuple[str, str, list[str]]]]) -> list[str]:
    """One line per file and per symbol."""
    lines = []
    for folder, files in folders.items():
        lines.append(f"[{folder}]")
        for name, description, symbols in files:
            lines.append(_fit(f"  {name}: {description}" if description else f"  {name}"))
            lines += [_fit(f"    {symbol}") for symbol in symbols]
    return lines


def _per_file(folders: dict[Path, list[tuple[str, str, list[str]]]]) -> list[str]:
    """One line per file, its symbols on the same line."""
    lines = []
    for folder, files in folders.items():
        lines.append(f"[{folder}]")
        lines += [_fit(f"  {name}: {', '.join(symbols)}" if symbols else f"  {name}") for name, _, symbols in files]
    return lines


def _per_folder(folders: dict[Path, list[tuple[str, str, list[str]]]]) -> list[str]:
    """One line per folder, with its file names."""
    return [_fit(f"[{folder}] {', '.join(name for name, _, _ in files)}") for folder, files in folders.items()]


def generate_repo_map(root: Path) -> str:
    """Return the map in the most detail that fits MAX_MAP_LINES: per symbol, then per file, then per folder."""
    folders: dict[Path, list[tuple[str, str, list[str]]]] = defaultdict(list)
    for path in _code_files(root):
        description, symbols = _outline(root / path)
        folders[path.parent].append((path.name, description, symbols))
    for render in (_detailed, _per_file, _per_folder):
        lines = render(folders)
        if len(lines) <= MAX_MAP_LINES:
            return "\n".join(lines)
    return "\n".join([*lines[: MAX_MAP_LINES - 1], TRUNCATED_NOTE])


def write_project_repo_map(project_dir: Path, output_file: Path) -> str:
    """Generate the map and write it to the target path."""
    content = generate_repo_map(project_dir)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(content, encoding="utf-8")
    return content
