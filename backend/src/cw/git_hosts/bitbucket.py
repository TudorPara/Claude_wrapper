"""Bitbucket Cloud through its REST API 2.0, authenticated with an Atlassian email and API token."""

import base64
import json
import logging
import re
import urllib.error
import urllib.request

from cw.config import BitbucketCredentials
from cw.errors import AppErrorCode, AppException
from cw.git_hosts.base import PullRequest, PullRequestThread
from cw.models import ReviewComment

logger = logging.getLogger(__name__)

API_ROOT = "https://api.bitbucket.org/2.0/repositories"
REMOTE_PATTERN = re.compile(r"bitbucket\.org[:/](?P<workspace>[^/]+)/(?P<slug>[^/]+?)(\.git)?$")
JSON_CONTENT_TYPE = "application/json"
REQUEST_TIMEOUT_SECONDS = 30
SUCCESSFUL_STATE = "SUCCESSFUL"
SQUASH_STRATEGY = "squash"
COMMENTS_PAGE_SIZE = 100


class BitbucketHost:
    """Pull requests, comments and commit statuses on Bitbucket Cloud."""

    def __init__(self, remote_url: str, credentials: BitbucketCredentials) -> None:
        match = REMOTE_PATTERN.search(remote_url)
        if not match:
            raise AppException(AppErrorCode.INVALID_CONFIGURATION, f"Not a Bitbucket Cloud remote: {remote_url}")
        self._repository_url = f"{API_ROOT}/{match['workspace']}/{match['slug']}"
        token = f"{credentials.email}:{credentials.api_token.get_secret_value()}"
        self._authorization = f"Basic {base64.b64encode(token.encode()).decode()}"

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        """Call the API and return the decoded JSON response."""
        request = urllib.request.Request(
            f"{self._repository_url}{path}",
            method=method,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Authorization": self._authorization, "Content-Type": JSON_CONTENT_TYPE},
        )
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")
            logger.error("Bitbucket %s %s failed with %s.", method, path, error.code)
            raise AppException(AppErrorCode.GIT_HOST_FAILED, f"{error.code}: {detail}") from error

    def create_pull_request(self, branch: str, base_branch: str, title: str, body: str) -> PullRequest:
        """Open a pull request."""
        payload = {
            "title": title,
            "description": body,
            "source": {"branch": {"name": branch}},
            "destination": {"branch": {"name": base_branch}},
        }
        created = self._request("POST", "/pullrequests", payload)
        return PullRequest(number=created["id"], url=created["links"]["html"]["href"])

    def post_review(self, number: int, summary: str, comments: list[ReviewComment]) -> None:
        """Post the summary as a general comment and each finding as an inline comment."""
        self._request("POST", f"/pullrequests/{number}/comments", {"content": {"raw": summary}})
        for comment in comments:
            payload = {
                "content": {"raw": f"**{comment.severity}** {comment.body}"},
                "inline": {"path": comment.path, "to": comment.line},
            }
            self._request("POST", f"/pullrequests/{number}/comments", payload)

    def ci_status(self, number: int, sha: str) -> str:
        """Return the build statuses reported on the commit."""
        statuses = self._request("GET", f"/commit/{sha}/statuses").get("values", [])
        if not statuses:
            return "No builds reported."
        return "\n".join(f"{status.get('state')}\t{status.get('name')}" for status in statuses)

    def ci_passed(self, number: int, sha: str, checks_expected: bool) -> bool:
        """Return whether every build reported on the commit succeeded; none reported passes only without CI."""
        statuses = self._request("GET", f"/commit/{sha}/statuses").get("values", [])
        if not statuses:
            return not checks_expected
        return all(status.get("state") == SUCCESSFUL_STATE for status in statuses)

    def open_threads(self, number: int) -> list[PullRequestThread]:
        """Return the unresolved inline comments, each with its replies, every comment prefixed with its author."""
        comments = self._request("GET", f"/pullrequests/{number}/comments?pagelen={COMMENTS_PAGE_SIZE}")
        live = [comment for comment in comments.get("values", []) if not comment.get("deleted")]
        threads: dict[int, PullRequestThread] = {}
        for comment in live:
            inline = comment.get("inline")
            if inline and not comment.get("parent") and not comment.get("resolution"):
                line = inline.get("to") or inline.get("from") or 0
                thread = PullRequestThread(id=str(comment["id"]), path=inline["path"], line=line, body="")
                threads[comment["id"]] = thread
        for comment in live:
            thread = threads.get(comment["id"]) or threads.get((comment.get("parent") or {}).get("id"))
            if thread:
                author = comment.get("user", {}).get("display_name", "someone")
                thread.body = "\n".join(filter(None, [thread.body, f"{author}: {comment['content']['raw']}"]))
        return list(threads.values())

    def resolve_thread(self, number: int, thread_id: str) -> None:
        """Resolve an inline comment and its replies."""
        self._request("POST", f"/pullrequests/{number}/comments/{thread_id}/resolve")

    def merge(self, number: int, branch: str, message: str, sha: str) -> None:
        """Squash-merge the pull request and close its source branch, refusing when its head is no longer `sha`.

        Bitbucket's merge call takes no expected head, so the head is compared just before it.
        """
        head = self._request("GET", f"/pullrequests/{number}")["source"]["commit"]["hash"]
        if not sha.startswith(head):
            raise AppException(
                AppErrorCode.GIT_HOST_FAILED, f"Pull request {number} is at {head}, not at the checked {sha}."
            )
        payload = {"merge_strategy": SQUASH_STRATEGY, "close_source_branch": True, "message": message}
        self._request("POST", f"/pullrequests/{number}/merge", payload)
