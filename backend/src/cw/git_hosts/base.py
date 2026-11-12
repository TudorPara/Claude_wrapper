"""What cw needs from a code host."""

from typing import Protocol

from pydantic import BaseModel

from cw.models import ReviewComment


class PullRequest(BaseModel):
    """An opened pull request."""

    number: int
    url: str


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
