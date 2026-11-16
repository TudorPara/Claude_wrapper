"""What cw needs from a code host."""

from typing import Protocol

from pydantic import BaseModel

from cw.models import ReviewComment


class PullRequest(BaseModel):
    """An opened pull request."""

    number: int
    url: str


class PullRequestThread(BaseModel):
    """An unresolved comment thread on a line of a pull request: its comments, each with its author."""

    id: str
    path: str
    line: int
    body: str


class GitHost(Protocol):
    """Pull request operations shared by GitHub and Bitbucket."""

    def create_pull_request(self, branch: str, base_branch: str, title: str, body: str) -> PullRequest:
        """Open a pull request and return it."""
        ...

    def post_review(self, number: int, summary: str, comments: list[ReviewComment]) -> None:
        """Post the reviewer's summary and line comments on the pull request."""
        ...

    def ci_status(self, number: int, sha: str) -> str:
        """Return a short, human-readable CI status."""
        ...

    def ci_passed(self, number: int, sha: str) -> bool:
        """Return whether every reported check passed; a pull request without checks passes."""
        ...

    def open_threads(self, number: int) -> list[PullRequestThread]:
        """Return the unresolved comment threads on lines of the pull request."""
        ...

    def resolve_thread(self, number: int, thread_id: str) -> None:
        """Mark a comment thread resolved."""
        ...

    def merge(self, number: int, branch: str, message: str) -> None:
        """Squash-merge the pull request and delete its branch on the host."""
        ...
