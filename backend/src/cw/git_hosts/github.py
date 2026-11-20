"""GitHub through the `gh` CLI, which uses the developer's own login."""

import json
import logging
import subprocess
from pathlib import Path

from cw.errors import AppErrorCode, AppException
from cw.git_hosts.base import PullRequest, PullRequestThread, finding_text
from cw.models import ReviewComment

logger = logging.getLogger(__name__)

REVIEW_EVENT = "COMMENT"
NEW_SIDE = "RIGHT"
UNPROCESSABLE_HINT = "422"
NO_CHECKS_HINT = "no checks reported"
PASSED_BUCKETS = {"pass", "skipping"}
SQUASH = "squash"
GHOST_AUTHOR = "ghost"
MAX_THREADS = 100
MAX_THREAD_COMMENTS = 20
THREADS_QUERY = (
    "query($owner: String!, $name: String!, $number: Int!) { repository(owner: $owner, name: $name) { "
    f"pullRequest(number: $number) {{ reviewThreads(first: {MAX_THREADS}) {{ nodes {{ id isResolved path line "
    f"originalLine comments(first: {MAX_THREAD_COMMENTS}) {{ nodes {{ body author {{ login }} }} }} "
    "lastComment: comments(last: 1) { nodes { body } } } } } } }"
)
RESOLVE_MUTATION = "mutation($id: ID!) { resolveReviewThread(input: {threadId: $id}) { thread { id } } }"
REPLY_MUTATION = (
    "mutation($id: ID!, $body: String!) { addPullRequestReviewThreadReply("
    "input: {pullRequestReviewThreadId: $id, body: $body}) { comment { id } } }"
)


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
            {
                "path": comment.path,
                "line": comment.line,
                "side": NEW_SIDE,
                "body": finding_text(comment),
            }
            for comment in comments
        ]
        review = {"body": summary, "event": REVIEW_EVENT, "comments": inline}
        try:
            self._gh("api", "--method", "POST", endpoint, "--input", "-", stdin=json.dumps(review))
        except AppException as error:
            if UNPROCESSABLE_HINT not in error.detail:
                raise
            listed = "\n".join(f"- `{comment.path}:{comment.line}` {finding_text(comment)}" for comment in comments)
            fallback = {"body": f"{summary}\n\n{listed}", "event": REVIEW_EVENT}
            self._gh("api", "--method", "POST", endpoint, "--input", "-", stdin=json.dumps(fallback))

    def ci_status(self, number: int, sha: str) -> str:
        """Return the checks table gh prints for the pull request."""
        result = subprocess.run(["gh", "pr", "checks", str(number)], cwd=self._checkout, capture_output=True, text=True)
        return (result.stdout or result.stderr).strip() or "No checks reported."

    def ci_passed(self, number: int, sha: str, checks_expected: bool) -> bool:
        """Return whether every check on the head passed or was skipped, and the head is this commit.

        gh's exit code also flags pending checks, so read JSON.
        """
        head = self._gh("pr", "view", str(number), "--json", "headRefOid", "--jq", ".headRefOid")
        if head != sha:
            logger.warning("Pull request %s is at %s, not at the local %s.", number, head, sha)
            return False
        command = ["gh", "pr", "checks", str(number), "--json", "bucket"]
        result = subprocess.run(command, cwd=self._checkout, capture_output=True, text=True)
        if NO_CHECKS_HINT in result.stderr:
            return not checks_expected
        try:
            checks = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            logger.error("gh pr checks failed: %s", result.stderr.strip())
            raise AppException(AppErrorCode.GIT_HOST_FAILED, result.stderr.strip()) from error
        return all(check["bucket"] in PASSED_BUCKETS for check in checks)

    def open_threads(self, number: int) -> list[PullRequestThread]:
        """Return the unresolved review threads, each comment prefixed with its author."""
        owner, name = self._repository().split("/")
        data = json.loads(
            self._gh(
                "api",
                "graphql",
                "-f",
                f"query={THREADS_QUERY}",
                "-f",
                f"owner={owner}",
                "-f",
                f"name={name}",
                "-F",
                f"number={number}",
            )
        )
        threads = data["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
        return [
            PullRequestThread(
                id=thread["id"],
                path=thread["path"],
                line=thread["line"] or thread["originalLine"] or 0,
                body="\n".join(
                    f"{(comment['author'] or {}).get('login', GHOST_AUTHOR)}: {comment['body']}"
                    for comment in thread["comments"]["nodes"]
                ),
                last_comment=next((comment["body"] for comment in thread["lastComment"]["nodes"]), ""),
            )
            for thread in threads
            if not thread["isResolved"]
        ]

    def resolve_thread(self, number: int, thread_id: str) -> None:
        """Resolve a review thread."""
        self._gh("api", "graphql", "-f", f"query={RESOLVE_MUTATION}", "-f", f"id={thread_id}")

    def reply_to_thread(self, number: int, thread_id: str, body: str) -> None:
        """Reply in a review thread without resolving it."""
        self._gh("api", "graphql", "-f", f"query={REPLY_MUTATION}", "-f", f"id={thread_id}", "-f", f"body={body}")

    def merge(self, number: int, branch: str, message: str, sha: str) -> str:
        """Squash-merge through the API, pinned to the checked head, then delete the branch on GitHub.

        GitHub refuses the merge when the head is no longer `sha`. The local checkout is left to cw. Returns the squash
        commit's SHA.
        """
        repository = self._repository()
        merged = self._gh(
            "api",
            "--method",
            "PUT",
            f"repos/{repository}/pulls/{number}/merge",
            "-f",
            f"merge_method={SQUASH}",
            "-f",
            f"commit_title={message}",
            "-f",
            f"sha={sha}",
            "--jq",
            ".sha",
        )
        self._gh("api", "--method", "DELETE", f"repos/{repository}/git/refs/heads/{branch}")
        return merged
