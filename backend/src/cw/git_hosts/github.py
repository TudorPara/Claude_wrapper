"""GitHub through the `gh` CLI, which uses the developer's own login."""

import json
import logging
import subprocess
from pathlib import Path

from cw.errors import AppErrorCode, AppException
from cw.git_hosts.base import PullRequest
from cw.models import ReviewComment

logger = logging.getLogger(__name__)

REVIEW_EVENT = "COMMENT"
NEW_SIDE = "RIGHT"
UNPROCESSABLE_HINT = "422"


class GitHubHost:
    """Pull requests, reviews and checks on GitHub."""

    def __init__(self, checkout: Path) -> None:
        self._checkout = checkout

    def _gh(self, *arguments: str, stdin: str | None = None) -> str:
        """Run gh in the checkout and return stdout."""
        result = subprocess.run(["gh", *arguments], cwd=self._checkout, input=stdin, capture_output=True, text=True)
        if result.returncode != 0:
            logger.error("gh %s failed: %s", arguments[0], result.stderr.strip())
            raise AppException(AppErrorCode.GIT_HOST_FAILED, result.stderr.strip())
        return result.stdout.strip()

    def _repository(self) -> str:
        """Return owner/name of the repository."""
        return self._gh("repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner")

    def create_pull_request(self, branch: str, base_branch: str, title: str, body: str) -> PullRequest:
        """Open a pull request with gh."""
        url = self._gh(
            "pr", "create", "--head", branch, "--base", base_branch, "--title", title, "--body-file", "-", stdin=body
        )
        number = int(self._gh("pr", "view", url, "--json", "number", "--jq", ".number"))
        return PullRequest(number=number, url=url)

    def post_review(self, number: int, summary: str, comments: list[ReviewComment]) -> None:
        """Post one review with inline comments, falling back to a body-only review when GitHub rejects a line."""
        endpoint = f"repos/{self._repository()}/pulls/{number}/reviews"
        inline = [
            {"path": c.path, "line": c.line, "side": NEW_SIDE, "body": f"**{c.severity}** {c.body}"} for c in comments
        ]
        review = {"body": summary, "event": REVIEW_EVENT, "comments": inline}
        try:
            self._gh("api", "--method", "POST", endpoint, "--input", "-", stdin=json.dumps(review))
        except AppException as error:
            if UNPROCESSABLE_HINT not in error.detail:
                raise
            listed = "\n".join(f"- `{c.path}:{c.line}` **{c.severity}** {c.body}" for c in comments)
            fallback = {"body": f"{summary}\n\n{listed}", "event": REVIEW_EVENT}
            self._gh("api", "--method", "POST", endpoint, "--input", "-", stdin=json.dumps(fallback))

    def ci_status(self, number: int, sha: str) -> str:
        """Return the checks table gh prints for the pull request."""
        result = subprocess.run(["gh", "pr", "checks", str(number)], cwd=self._checkout, capture_output=True, text=True)
        return (result.stdout or result.stderr).strip() or "No checks reported."
