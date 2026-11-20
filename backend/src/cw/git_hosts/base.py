"""What cw needs from a code host."""

from typing import Protocol

from pydantic import BaseModel

from cw.models import ReviewComment


def finding_tag(finding_id: str) -> str:
    """Return the label that marks a review finding in the text cw posts, so a thread can be matched to it."""
    return f"[{finding_id}]"


def finding_text(comment: ReviewComment) -> str:
    """Return a review finding as cw posts it: severity, id label, body."""
    tag = f"{finding_tag(comment.id)} " if comment.id else ""
    return f"**{comment.severity}** {tag}{comment.body}"


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
    last_comment: str = ""


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

    def ci_passed(self, number: int, sha: str, checks_expected: bool) -> bool:
        """Return whether every check on this commit passed; none reported passes only when no CI is configured."""
        ...

    def open_threads(self, number: int) -> list[PullRequestThread]:
        """Return the unresolved comment threads on lines of the pull request."""
        ...

    def resolve_thread(self, number: int, thread_id: str) -> None:
        """Mark a comment thread resolved."""
        ...

    def reply_to_thread(self, number: int, thread_id: str, body: str) -> None:
        """Add a reply to a comment thread, leaving it open."""
        ...

    def merge(self, number: int, branch: str, message: str, sha: str) -> str:
        """Squash-merge only while the head is this commit, delete the branch; return the merge commit."""
        ...
