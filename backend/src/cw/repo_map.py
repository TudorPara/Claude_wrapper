"""Deterministic AST-based architecture and repository map generator."""

import ast
import os
import re
from pathlib import Path

MAX_MAP_LINES = 250
MAX_LINE_LENGTH = 120
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


def _read_first_docstring_line(tree: ast.AST) -> str:
    """Return the first line of a module docstring."""
    docstring = ast.get_docstring(tree) or ""
    return docstring.strip().splitlines()[0] if docstring.strip() else ""


def _format_function_signature(function_node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Return a short function signature with its parameter names."""
    parameters = [arg.arg for arg in function_node.args.args if arg.arg != "self"]
    return f"{function_node.name}({', '.join(parameters)})"


def _summarize_python_file(path: Path) -> list[str]:
    """Return an outline of classes and functions for a Python file."""
    try:
        content = path.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(path))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return []

    lines = []
    doc = _read_first_docstring_line(tree)
    header = f"  {path.name}" + (f": {doc}" if doc else "")
    lines.append(header[:MAX_LINE_LENGTH])

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            lines.append(f"    class {node.name}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_") or node.name.startswith("__"):
                lines.append(f"    {_format_function_signature(node)}")
    return lines


def _summarize_swift_file(path: Path) -> list[str]:
    """Return an outline of types for a Swift file."""
    try:
        content = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []

    matches = re.findall(r"\b(struct|class|enum|protocol)\s+([A-Za-z0-9_]+)", content)
    if not matches:
        return [f"  {path.name}"]

    symbols = ", ".join(name for _, name in matches[:6])
    return [f"  {path.name}: {symbols}"[:MAX_LINE_LENGTH]]


def _is_ignored(path: Path) -> bool:
    """Return whether any path part is in the ignored directories list."""
    return any(part in IGNORED_DIRECTORIES for part in path.parts)


def generate_repo_map(root: Path) -> str:
    """Generate a compact, deterministic structural map of the repository."""
    collected_lines = []
    folders: dict[Path, list[Path]] = {}

    for directory_path, _, filenames in os.walk(root):
        current_dir = Path(directory_path)
        if _is_ignored(current_dir.relative_to(root)):
            continue
        matching_files = [
            current_dir / filename for filename in sorted(filenames) if Path(filename).suffix in CODE_EXTENSIONS
        ]
        if matching_files:
            folders[current_dir] = matching_files

    for folder, files in sorted(folders.items()):
        relative_folder = folder.relative_to(root)
        collected_lines.append(f"[{relative_folder}]")
        for file_path in files:
            if file_path.suffix == ".py":
                collected_lines.extend(_summarize_python_file(file_path))
            elif file_path.suffix == ".swift":
                collected_lines.extend(_summarize_swift_file(file_path))
            else:
                collected_lines.append(f"  {file_path.name}")
            if len(collected_lines) >= MAX_MAP_LINES:
                collected_lines.append("  ... (truncated to fit context budget)")
                return "\n".join(collected_lines[:MAX_MAP_LINES])

    return "\n".join(collected_lines)


def write_project_repo_map(project_dir: Path, output_file: Path) -> str:
    """Generate and write the repository map to the target path."""
    content = generate_repo_map(project_dir)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(content, encoding="utf-8")
    return content
