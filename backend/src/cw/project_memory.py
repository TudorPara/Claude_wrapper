"""The "Notes kept by cw" section that earlier versions of cw wrote into a project's CLAUDE.md.

cw no longer writes it: workers' notes live in ticket records now. This module only reads the old section, so
`cw context migrate-notes` can carry its lines into the index. The file itself is never changed.
"""

from pathlib import Path

MEMORY_FILE_NAME = "CLAUDE.md"
CLAUDE_FOLDER = ".claude"
NOTES_HEADING = "## Notes kept by cw"
NEXT_HEADING = "\n## "
BULLET = "- "


def legacy_notes(project_dir: Path) -> list[str]:
    """Return the notes in the project's "Notes kept by cw" section, or an empty list."""
    path = project_dir / MEMORY_FILE_NAME
    if not path.exists() and (project_dir / CLAUDE_FOLDER / MEMORY_FILE_NAME).exists():
        path = project_dir / CLAUDE_FOLDER / MEMORY_FILE_NAME
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if NOTES_HEADING not in text:
        return []
    start = text.index(NOTES_HEADING) + len(NOTES_HEADING)
    end = text.find(NEXT_HEADING, start)
    section = text[start : end if end != -1 else len(text)]
    return [
        line.strip().removeprefix(BULLET).strip() for line in section.splitlines() if line.strip().startswith(BULLET)
    ]
