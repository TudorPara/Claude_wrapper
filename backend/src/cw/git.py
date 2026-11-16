"""Thin wrappers over the git commands cw needs."""

import logging
import subprocess
from pathlib import Path

from cw.errors import AppErrorCode, AppException

logger = logging.getLogger(__name__)

EXCLUDE_FILE = Path("info") / "exclude"
CONFLICT_FILTER = "--diff-filter=U"
CONFLICT_MARKER = "^<<<<<<< "
MERGES_ONLY = "--merges"
PORCELAIN_PATH_COLUMN = 3


def git(cwd: Path, *arguments: str, check: bool = True) -> str:
    """Run git in a folder and return its stripped stdout."""
    result = subprocess.run(["git", *arguments], cwd=cwd, capture_output=True, text=True)
    if check and result.returncode != 0:
        logger.error("git %s failed in %s: %s", " ".join(arguments), cwd, result.stderr.strip())
        raise AppException(AppErrorCode.GIT_FAILED, f"git {' '.join(arguments)}: {result.stderr.strip()}")
    return result.stdout.strip()


def is_repository(path: Path) -> bool:
    """Return whether the folder is inside a git repository."""
    return subprocess.run(["git", "rev-parse", "--git-dir"], cwd=path, capture_output=True).returncode == 0


def remote_url(path: Path, remote: str) -> str:
    """Return the URL of a remote, or an empty string when it does not exist."""
    return git(path, "remote", "get-url", remote, check=False)


def default_branch(path: Path, remote: str) -> str:
    """Return the remote's default branch, falling back to the current branch."""
    head = git(path, "symbolic-ref", "--short", f"refs/remotes/{remote}/HEAD", check=False)
    if head:
        return head.removeprefix(f"{remote}/")
    return git(path, "branch", "--show-current")


def fetch_base(path: Path, remote: str, base_branch: str) -> tuple[str, str]:
    """Fetch the base branch; return the ref to use and a warning when the fetch failed."""
    if not remote_url(path, remote):
        return base_branch, f"No remote '{remote}'; using the local {base_branch}."
    fetched = subprocess.run(["git", "fetch", remote, base_branch], cwd=path, capture_output=True, text=True)
    if fetched.returncode != 0:
        reason = fetched.stderr.strip().splitlines()[-1] if fetched.stderr.strip() else "unknown error"
        logger.warning("Could not fetch %s/%s (%s); using the local branch.", remote, base_branch, reason)
        return base_branch, f"Could not fetch {remote}/{base_branch} ({reason}); pushing will fail the same way."
    return f"{remote}/{base_branch}", ""


def fast_forward(cwd: Path, remote: str, branch: str) -> bool:
    """Move the local branch to the remote's copy if that needs no merge; return whether it is up to date now."""
    if current_branch(cwd) == branch:
        command = ["git", "pull", "--ff-only", remote, branch]
    else:
        command = ["git", "fetch", remote, f"{branch}:{branch}"]
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True).returncode == 0


def is_clean(cwd: Path) -> bool:
    """Return whether the checkout has no uncommitted or untracked changes."""
    return not git(cwd, "status", "--porcelain")


def changed_paths(cwd: Path) -> list[str]:
    """Return the paths git reports as changed or untracked in the checkout."""
    return [line[PORCELAIN_PATH_COLUMN:] for line in git(cwd, "status", "--porcelain").splitlines()]


def switch_new(cwd: Path, branch: str, start_point: str) -> None:
    """Create a branch from a start point and switch the checkout to it."""
    git(cwd, "switch", "-c", branch, start_point)


def switch(cwd: Path, branch: str) -> None:
    """Switch the checkout to an existing branch."""
    git(cwd, "switch", branch)


def discard_changes(cwd: Path) -> None:
    """Throw away every uncommitted change and untracked file in the checkout."""
    git(cwd, "reset", "--hard")
    git(cwd, "clean", "-fd")


def delete_branch(repository: Path, branch: str) -> None:
    """Delete a local branch even when it is not merged."""
    git(repository, "branch", "-D", branch)


def squash_onto(cwd: Path, base: str, message: str) -> None:
    """Fold every commit since base and the uncommitted changes into one commit."""
    git(cwd, "add", "-A")
    git(cwd, "reset", "--soft", base)
    git(cwd, "commit", "-m", message)


def head_sha(cwd: Path) -> str:
    """Return the current commit."""
    return git(cwd, "rev-parse", "HEAD")


def commit_all(cwd: Path, message: str) -> bool:
    """Stage everything and commit; return False when there was nothing to commit."""
    git(cwd, "add", "-A")
    if not git(cwd, "status", "--porcelain"):
        return False
    git(cwd, "commit", "-m", message)
    return True


def snapshot(cwd: Path) -> str:
    """Stage every change and return the tree id of the working state, without committing."""
    git(cwd, "add", "-A")
    return git(cwd, "write-tree")


def diff(cwd: Path, old: str, new: str) -> str:
    """Return the diff between two commits or trees."""
    return git(cwd, "diff", old, new)


def diff_stat(cwd: Path, old: str, new: str) -> str:
    """Return the diff summary between two commits or trees."""
    return git(cwd, "diff", "--stat", old, new)


def changed_files(cwd: Path, old: str, new: str) -> list[str]:
    """Return the paths under this folder that differ between two commits or trees, relative to it."""
    return git(cwd, "diff", "--name-only", "--relative", old, new).splitlines()


def merge(cwd: Path, ref: str) -> bool:
    """Merge a ref; return False when it stopped on conflicts."""
    result = subprocess.run(["git", "merge", "--no-edit", ref], cwd=cwd, capture_output=True, text=True)
    return result.returncode == 0


def conflicted_files(cwd: Path) -> list[str]:
    """Return the files a stopped merge left unmerged."""
    return sorted(set(git(cwd, "diff", "--name-only", CONFLICT_FILTER).splitlines()) - {""})


def files_with_conflict_markers(cwd: Path, files: list[str]) -> list[str]:
    """Return which of these files still contain conflict markers."""
    if not files:
        return []
    return git(cwd, "grep", "-l", "-E", CONFLICT_MARKER, "--", *files, check=False).splitlines()


def conclude_merge(cwd: Path, message: str) -> None:
    """Stage the resolved files and create the merge commit."""
    git(cwd, "add", "-A")
    git(cwd, "commit", "-m", message)


def has_merge_commits(cwd: Path, base: str) -> bool:
    """Return whether the branch already contains a merge since base."""
    return bool(git(cwd, "rev-list", MERGES_ONLY, f"{base}..HEAD"))


def init(path: Path, branch: str) -> None:
    """Create a new repository with this first branch."""
    git(path, "init", "-b", branch)


def abort_merge(cwd: Path) -> None:
    """Abort a merge in progress."""
    git(cwd, "merge", "--abort", check=False)


def push(cwd: Path, remote: str, branch: str) -> None:
    """Push the branch and set its upstream."""
    git(cwd, "push", "-u", remote, branch)


def current_branch(cwd: Path) -> str:
    """Return the checked-out branch, or an empty string when detached."""
    return git(cwd, "branch", "--show-current", check=False)


def is_dirty(cwd: Path) -> bool:
    """Return whether the checkout has uncommitted changes."""
    return bool(git(cwd, "status", "--porcelain", check=False))


def last_commit_age(cwd: Path) -> str:
    """Return how long ago the last commit was made, such as '3 days ago'."""
    return git(cwd, "log", "-1", "--format=%cr", check=False)


def user_name(cwd: Path) -> str:
    """Return the configured git author name."""
    return git(cwd, "config", "user.name", check=False) or "unknown"
