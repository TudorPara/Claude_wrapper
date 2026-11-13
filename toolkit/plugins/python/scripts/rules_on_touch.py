"""Show the team's rules for an area the first time Claude reads or edits a file in it.

Each topic of the python-rules skill is shown once per session, on standard error with exit code 2:
before an edit that stops the edit so the rules are read first, after a read it only adds them.
Topics cw already put in the agent's system prompt (named in CW_SHOWN_TOPICS) are not shown again.
"""

import fnmatch
import json
import os
import sys
import tempfile
from pathlib import Path

FEEDBACK_EXIT_CODE = 2
TOPICS_FOLDER = Path(__file__).resolve().parent.parent / "skills" / "python-rules" / "topics"
INDEX_FILE = "topics.json"
TOPIC_SUFFIX = ".md"
SEEN_FOLDER_PREFIX = "cw-rules-"
FOLDER_MARK = "/"
BEFORE_EDIT_EVENT = "PreToolUse"
SHOWN_TOPICS_ENV = "CW_SHOWN_TOPICS"


def _matches(relative: Path, pattern: str) -> bool:
    """Return whether a path matches: `name/` means a folder on the path, anything else is a file-name glob."""
    if pattern.endswith(FOLDER_MARK):
        return pattern.rstrip(FOLDER_MARK) in relative.parts[:-1]
    return fnmatch.fnmatch(relative.name, pattern)


def _unseen_topics(relative: Path, seen: Path) -> list[str]:
    """Return the topics that cover this file and were not shown yet in this session."""
    index = json.loads((TOPICS_FOLDER / INDEX_FILE).read_text())["topics"]
    shown = os.environ.get(SHOWN_TOPICS_ENV, "").split(",")
    return [
        topic
        for topic, patterns in index.items()
        if any(_matches(relative, pattern) for pattern in patterns)
        and topic not in shown
        and not (seen / topic).exists()
    ]


def main() -> int:
    """Print the unseen topics for the file in the hook payload."""
    payload = json.load(sys.stdin)
    file_path = payload.get("tool_input", {}).get("file_path", "")
    working_folder = payload.get("cwd", "")
    if not file_path or not working_folder:
        return 0
    try:
        relative = Path(file_path).resolve().relative_to(Path(working_folder).resolve())
    except ValueError:
        return 0
    seen = Path(tempfile.gettempdir()) / f"{SEEN_FOLDER_PREFIX}{payload.get('session_id', 'session')}"
    topics = _unseen_topics(relative, seen)
    if not topics:
        return 0
    seen.mkdir(parents=True, exist_ok=True)
    for topic in topics:
        (seen / topic).touch()
    rules = "\n\n".join((TOPICS_FOLDER / f"{topic}{TOPIC_SUFFIX}").read_text() for topic in topics)
    retry = " Read them, then repeat your edit." if payload.get("hook_event_name") == BEFORE_EDIT_EVENT else ""
    print(f"Team rules for files like {relative} (shown once per session).{retry}\n\n{rules}", file=sys.stderr)
    return FEEDBACK_EXIT_CODE


if __name__ == "__main__":
    sys.exit(main())
