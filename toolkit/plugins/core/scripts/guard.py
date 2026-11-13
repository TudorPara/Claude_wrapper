"""Block dangerous shell commands before Claude Code runs them.

It reads the command the way a shell would split it, so `git -C . commit` is caught and a quoted
"git commit" inside a grep is not. It is a guard rail, not a wall: cw's permission rules are the wall.
"""

import fnmatch
import json
import os
import re
import shlex
import sys

BLOCK_EXIT_CODE = 2
ORCHESTRATED_ENV = "CW_RUN"
PROTECTED_PATHS_ENV = "CW_PROTECTED_PATHS"
GIT = "git"
SHELLS = {"sh", "bash", "zsh"}
SHELL_COMMAND_FLAG = "-c"
OPERATOR_CHARACTERS = set("();<>|&")
GIT_OPTIONS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
FORCE_FLAGS = {"--force", "-f", "--force-with-lease"}
FORCED_REFSPEC_PREFIX = "+"
SKIP_HOOKS_FLAG = "--no-verify"
HISTORY_COMMANDS = {
    "commit", "push", "merge", "rebase", "checkout", "switch", "reset", "stash", "worktree", "restore", "clean",
}  # fmt: skip
BRANCH_DELETE_FLAGS = {"-d", "-D", "--delete"}
DELETE_ROOT = re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f?[a-zA-Z]*\s+(/|~|\$HOME)(\s|$)")
PATH_LIKE = re.compile(r"[\w./~*?\[\]-]+")
ANY_FOLDER_PREFIX = "**/"
HERE_PREFIX = "./"
FORCE_PUSH = "force pushes are not allowed"
HARD_RESET = "resetting to the remote discards work"
SKIP_HOOKS = "skipping git hooks is not allowed"
DELETE_HOME = "deleting the root or home folder is not allowed"
LEAVE_HISTORY = "cw commits, merges and pushes for you; leave git history alone"


def _segments(command: str) -> list[list[str]]:
    """Split a command line into simple commands, each a list of words."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        words = list(lexer)
    except ValueError:
        words = command.split()
    segments: list[list[str]] = [[]]
    for word in words:
        if set(word) <= OPERATOR_CHARACTERS:
            segments.append([])
        else:
            segments[-1].append(word)
    return [segment for segment in segments if segment]


def _git_call(arguments: list[str]) -> tuple[str, list[str]]:
    """Return the git subcommand and what follows it, skipping git's own options."""
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument in GIT_OPTIONS_WITH_VALUE:
            index += 2
        elif argument.startswith("-"):
            index += 1
        else:
            return argument, arguments[index + 1 :]
    return "", []


def _git_problem(subcommand: str, rest: list[str], orchestrated: bool) -> str | None:
    """Return why this git call is blocked, or None."""
    if SKIP_HOOKS_FLAG in rest:
        return SKIP_HOOKS
    if subcommand == "push" and any(word in FORCE_FLAGS or word.startswith(FORCED_REFSPEC_PREFIX) for word in rest):
        return FORCE_PUSH
    if subcommand == "reset" and "--hard" in rest and any(word.startswith("origin") for word in rest):
        return HARD_RESET
    if not orchestrated:
        return None
    if subcommand in HISTORY_COMMANDS or (subcommand == "branch" and BRANCH_DELETE_FLAGS & set(rest)):
        return LEAVE_HISTORY
    return None


def _problem(command: str, orchestrated: bool) -> str | None:
    """Return why this command line is blocked, or None."""
    if DELETE_ROOT.search(command):
        return DELETE_HOME
    for words in _segments(command):
        for position, word in enumerate(words):
            rest = words[position + 1 :]
            if os.path.basename(word) == GIT:
                problem = _git_problem(*_git_call(rest), orchestrated)
            elif os.path.basename(word) in SHELLS and SHELL_COMMAND_FLAG in rest[:-1]:
                problem = _problem(rest[rest.index(SHELL_COMMAND_FLAG) + 1], orchestrated)
            else:
                continue
            if problem:
                return problem
    return None


def _protected_patterns() -> list[str]:
    """Return the protected path patterns cw passed in the environment."""
    raw = os.environ.get(PROTECTED_PATHS_ENV, "")
    if not raw:
        return []
    try:
        return [str(pattern) for pattern in json.loads(raw)]
    except json.JSONDecodeError:
        return []


def _protected_path(command: str, patterns: list[str]) -> str | None:
    """Return the first path-like word of the command that matches a protected pattern."""
    for word in PATH_LIKE.findall(command):
        path = word.removeprefix(HERE_PREFIX)
        name = os.path.basename(path)
        for pattern in patterns:
            anywhere = pattern.removeprefix(ANY_FOLDER_PREFIX)
            if any(fnmatch.fnmatch(*pair) for pair in ((path, pattern), (path, anywhere), (name, anywhere))):
                return word
    return None


def main() -> int:
    """Read the hook payload from stdin and exit 2 with a reason to block it."""
    payload = json.load(sys.stdin)
    command = payload.get("tool_input", {}).get("command", "")
    problem = _problem(command, bool(os.environ.get(ORCHESTRATED_ENV)))
    if problem:
        print(f"Blocked by the toolkit guard: {problem}.", file=sys.stderr)
        return BLOCK_EXIT_CODE
    protected = _protected_path(command, _protected_patterns())
    if protected:
        print(f"Blocked by the toolkit guard: '{protected}' is a protected path.", file=sys.stderr)
        return BLOCK_EXIT_CODE
    return 0


if __name__ == "__main__":
    sys.exit(main())
