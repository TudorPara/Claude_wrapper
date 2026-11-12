"""Block dangerous shell commands before Claude Code runs them."""

import fnmatch
import json
import os
import re
import shlex
import sys

BLOCK_EXIT_CODE = 2
ORCHESTRATED_ENV = "CW_RUN"
PROTECTED_PATHS_ENV = "CW_PROTECTED_PATHS"
ALWAYS_BLOCKED = (
    (re.compile(r"\bgit\s+push\b.*(\s--force\b|\s-f\b|\s--force-with-lease\b)"), "force pushes are not allowed"),
    (re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f?[a-zA-Z]*\s+(/|~|\$HOME)(\s|$)"), "deleting the root or home folder is not allowed"),
    (re.compile(r"\bgit\s+reset\s+--hard\s+origin\b"), "resetting to the remote discards work"),
    (re.compile(r"--no-verify\b"), "skipping git hooks is not allowed"),
)
ORCHESTRATED_BLOCKED = (
    (re.compile(r"\bgit\s+(commit|push|merge|rebase|checkout|switch|reset|stash|worktree|branch\s+-[dD])\b"), "cw commits, merges and pushes for you; leave git history alone"),
)


def _protected_patterns() -> list[str]:
    """Return the protected path patterns cw passed in the environment."""
    raw = os.environ.get(PROTECTED_PATHS_ENV, "")
    if not raw:
        return []
    try:
        return [str(pattern) for pattern in json.loads(raw)]
    except json.JSONDecodeError:
        return []


def _touches_protected_path(command: str, patterns: list[str]) -> str | None:
    """Return the first command word that matches a protected pattern."""
    try:
        words = shlex.split(command)
    except ValueError:
        words = command.split()
    for word in words:
        name = os.path.basename(word)
        for pattern in patterns:
            short_pattern = pattern.removeprefix("**/")
            if fnmatch.fnmatch(word, pattern) or fnmatch.fnmatch(name, short_pattern):
                return word
    return None


def main() -> int:
    """Read the hook payload from stdin and exit 2 with a reason to block it."""
    payload = json.load(sys.stdin)
    command = payload.get("tool_input", {}).get("command", "")
    rules = ALWAYS_BLOCKED + (ORCHESTRATED_BLOCKED if os.environ.get(ORCHESTRATED_ENV) else ())
    for pattern, reason in rules:
        if pattern.search(command):
            print(f"Blocked by the toolkit guard: {reason}.", file=sys.stderr)
            return BLOCK_EXIT_CODE
    protected = _touches_protected_path(command, _protected_patterns())
    if protected:
        print(f"Blocked by the toolkit guard: '{protected}' is a protected path.", file=sys.stderr)
        return BLOCK_EXIT_CODE
    return 0


if __name__ == "__main__":
    sys.exit(main())
