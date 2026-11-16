"""The project's CLAUDE.md as shared memory: cw adds the lasting facts workers report to one section of it.

Claude Code loads CLAUDE.md into every session in the folder, cw's agents included, so a note written here reaches
every later agent and every teammate's interactive session. The change is part of the ticket's diff, so it is
reviewed and committed with the code.
"""

from pathlib import Path

MEMORY_FILE_NAME = "CLAUDE.md"
CLAUDE_FOLDER = ".claude"
NOTES_HEADING = "## Notes kept by cw"
NEXT_HEADING = "\n## "
BULLET = "- "


def _normalized(line: str) -> str:
    """Return a note as compared for duplicates: without its bullet, spacing or case."""
    return line.strip().removeprefix(BULLET).strip().lower()


def add_notes(project_dir: Path, notes: list[str]) -> int:
    """Add the notes not already in the project's CLAUDE.md to its cw section; return how many notes it holds."""
    path = project_dir / MEMORY_FILE_NAME
    if not path.exists() and (project_dir / CLAUDE_FOLDER / MEMORY_FILE_NAME).exists():
        path = project_dir / CLAUDE_FOLDER / MEMORY_FILE_NAME
    memory_text = path.read_text(encoding="utf-8") if path.exists() else ""
    known = {_normalized(line) for line in memory_text.splitlines()}
    new = [note.strip() for note in dict.fromkeys(notes) if note.strip() and _normalized(note) not in known]
    if NOTES_HEADING not in memory_text and not new:
        return 0
    if NOTES_HEADING not in memory_text:
        memory_text = (memory_text.rstrip() + "\n\n" if memory_text.strip() else "") + NOTES_HEADING + "\n"
    start = memory_text.index(NOTES_HEADING)
    end = memory_text.find(NEXT_HEADING, start + len(NOTES_HEADING))
    end = len(memory_text) if end == -1 else end + 1
    section = memory_text[start:end].rstrip("\n") + "\n" + "".join(f"{BULLET}{note}\n" for note in new)
    rest = memory_text[end:]
    if new:
        path.write_text(memory_text[:start] + section + ("\n" + rest if rest else ""), encoding="utf-8")
    return sum(1 for line in section.splitlines() if line.startswith(BULLET))
