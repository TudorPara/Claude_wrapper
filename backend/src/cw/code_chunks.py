"""One code file's text turned into searchable chunks, the names it defines and uses, and the modules it imports."""

import ast
import logging
import re
from enum import StrEnum
from pathlib import PurePosixPath

from pydantic import BaseModel

logger = logging.getLogger(__name__)

PYTHON_SUFFIX = ".py"
PYTHON_LANGUAGE = "python"
PACKAGE_FILE_STEM = "__init__"
MIN_TERM_LENGTH = 2
DUNDER = "__"
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
WORD = re.compile(r"[A-Za-z0-9_]+")
WORD_PART = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
STOP_WORDS = frozenset(
    "a an and are as at be but by can do does for from has have if in into is it its me my no not of on or our "
    "so than that the then them they this to was we were will with you your".split()
)
NAME_SEPARATOR = "."


class ChunkKind(StrEnum):
    """What a chunk covers."""

    FILE = "file"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"


class Chunk(BaseModel):
    """A piece of a file that is found and shown on its own."""

    kind: ChunkKind
    qualified_name: str
    signature: str
    start_line: int
    end_line: int
    docstring: str
    text: str


class ParsedFile(BaseModel):
    """What the index keeps of one file's content."""

    language: str
    docstring: str
    chunks: list[Chunk]
    definitions: list[str]
    references: list[str]
    imports: list[str]


def split_terms(text: str) -> list[str]:
    """Return the search terms of a text: each identifier whole and split at `_` and camelCase, lowercased."""
    terms: list[str] = []
    for word in WORD.findall(text):
        whole = word.strip("_").lower()
        parts = [part.lower() for piece in word.split("_") for part in WORD_PART.findall(piece)]
        terms += [
            term for term in dict.fromkeys([whole, *parts]) if len(term) >= MIN_TERM_LENGTH and term not in STOP_WORDS
        ]
    return terms


def _first_line(text: str | None) -> str:
    """Return the first line of a docstring, or an empty string."""
    return (text or "").strip().splitlines()[0] if (text or "").strip() else ""


def _is_kept_name(name: str) -> bool:
    """Return whether a name is worth indexing: not a dunder and not too short."""
    return len(name) >= MIN_TERM_LENGTH and not (name.startswith(DUNDER) and name.endswith(DUNDER))


def module_name(path: str) -> str:
    """Return the dotted module a Python file defines; a package's `__init__.py` defines the package."""
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts and parts[-1] == PACKAGE_FILE_STEM:
        parts = parts[:-1]
    return NAME_SEPARATOR.join(parts)


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> str:
    """Return a definition's first line rebuilt from the syntax tree, with every kind of parameter."""
    if isinstance(node, ast.ClassDef):
        bases = ", ".join(ast.unparse(base) for base in node.bases)
        return f"class {node.name}({bases})" if bases else f"class {node.name}"
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"{prefix} {node.name}({ast.unparse(node.args)}){returns}"


def _chunk(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    kind: ChunkKind,
    qualified_name: str,
    lines: list[str],
    max_chunk_chars: int,
) -> Chunk:
    """Return the chunk of one definition, decorators included."""
    start = min([node.lineno, *(decorator.lineno for decorator in node.decorator_list)])
    end = node.end_lineno or node.lineno
    return Chunk(
        kind=kind,
        qualified_name=qualified_name,
        signature=_signature(node),
        start_line=start,
        end_line=end,
        docstring=_first_line(ast.get_docstring(node)),
        text="\n".join(lines[start - 1 : end])[:max_chunk_chars],
    )


def _resolved_imports(tree: ast.Module, path: str) -> list[str]:
    """Return the modules a file imports, relative imports resolved; `from m import n` also yields `m.n`."""
    package = module_name(path).split(NAME_SEPARATOR)
    if PurePosixPath(path).stem != PACKAGE_FILE_STEM:
        package = package[:-1]
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = package[: len(package) - node.level + 1] if node.level else []
            module = NAME_SEPARATOR.join([*base, *([node.module] if node.module else [])])
            if module:
                imports.append(module)
            imports += [NAME_SEPARATOR.join(filter(None, [module, alias.name])) for alias in node.names]
    return list(dict.fromkeys(imports))


def _parse_python(path: str, text: str, max_chunk_chars: int) -> ParsedFile:
    """Return a Python file's chunks, definitions, references and imports from its syntax tree."""
    tree = ast.parse(text, filename=path)
    lines = text.splitlines()
    chunks: list[Chunk] = []
    definitions: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            chunks.append(_chunk(node, ChunkKind.FUNCTION, node.name, lines, max_chunk_chars))
            definitions.append(node.name)
        elif isinstance(node, ast.ClassDef):
            chunks.append(_chunk(node, ChunkKind.CLASS, node.name, lines, max_chunk_chars))
            definitions.append(node.name)
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    name = f"{node.name}{NAME_SEPARATOR}{member.name}"
                    chunks.append(_chunk(member, ChunkKind.METHOD, name, lines, max_chunk_chars))
                    definitions.append(member.name)
    references: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            references.append(node.id)
        elif isinstance(node, ast.Attribute):
            references.append(node.attr)
        elif isinstance(node, ast.ImportFrom):
            references += [alias.name for alias in node.names]
    docstring = _first_line(ast.get_docstring(tree))
    top_level = [chunk.qualified_name for chunk in chunks if chunk.kind != ChunkKind.METHOD]
    file_chunk = Chunk(
        kind=ChunkKind.FILE,
        qualified_name=path,
        signature="",
        start_line=1,
        end_line=max(len(lines), 1),
        docstring=docstring,
        text="\n".join(filter(None, [docstring, ", ".join(top_level)]))[:max_chunk_chars],
    )
    return ParsedFile(
        language=PYTHON_LANGUAGE,
        docstring=docstring,
        chunks=[file_chunk, *chunks],
        definitions=[name for name in dict.fromkeys(definitions) if _is_kept_name(name)],
        references=[name for name in dict.fromkeys(references) if _is_kept_name(name)],
        imports=_resolved_imports(tree, path),
    )


def _file_card(path: str, text: str, max_chunk_chars: int, language: str) -> ParsedFile:
    """Return a file known only by its card: the start of its text, and the identifiers it uses."""
    lines = text.splitlines()
    card = Chunk(
        kind=ChunkKind.FILE,
        qualified_name=path,
        signature="",
        start_line=1,
        end_line=max(len(lines), 1),
        docstring="",
        text=text[:max_chunk_chars],
    )
    references = [name for name in dict.fromkeys(IDENTIFIER.findall(text)) if _is_kept_name(name)]
    return ParsedFile(language=language, docstring="", chunks=[card], definitions=[], references=references, imports=[])


def parse_file(path: str, text: str, max_chunk_chars: int) -> ParsedFile:
    """Return what the index keeps of a file: Python through its syntax tree, other languages as a card."""
    suffix = PurePosixPath(path).suffix
    if suffix != PYTHON_SUFFIX:
        return _file_card(path, text, max_chunk_chars, suffix.lstrip(NAME_SEPARATOR))
    try:
        return _parse_python(path, text, max_chunk_chars)
    except (SyntaxError, ValueError, RecursionError) as error:
        logger.debug("Indexed %s as a card only: %s", path, error)
        return _file_card(path, text, max_chunk_chars, PYTHON_LANGUAGE)
