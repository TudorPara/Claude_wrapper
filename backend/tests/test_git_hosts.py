import json
import subprocess
from pathlib import Path

import pytest
from pydantic import SecretStr

from cw.config import BitbucketCredentials
from cw.errors import AppException
from cw.git_hosts import github
from cw.git_hosts.base import finding_text
from cw.git_hosts.bitbucket import BitbucketHost
from cw.git_hosts.github import GitHubHost
from cw.models import ReviewComment, Severity

SHA = "a" * 40
NO_CHECKS = "no checks reported on the 'cw/x' branch"


def _github(monkeypatch: pytest.MonkeyPatch, head: str, checks_stdout: str, checks_stderr: str) -> list[list[str]]:
    calls = []

    def run(command, cwd=None, input=None, capture_output=True, text=True):
        calls.append(command)
        if command[:3] == ["gh", "pr", "view"]:
            return subprocess.CompletedProcess(command, 0, head, "")
        if command[:3] == ["gh", "pr", "checks"]:
            return subprocess.CompletedProcess(command, 1, checks_stdout, checks_stderr)
        return subprocess.CompletedProcess(command, 0, "owner/repo", "")

    monkeypatch.setattr(github.subprocess, "run", run)
    return calls


def test_github_counts_no_checks_as_passed_only_without_ci_config(monkeypatch: pytest.MonkeyPatch) -> None:
    _github(monkeypatch, SHA, "", NO_CHECKS)
    host = GitHubHost(Path("."))
    assert host.ci_passed(1, SHA, checks_expected=False)
    assert not host.ci_passed(1, SHA, checks_expected=True)


def test_github_refuses_ci_and_pins_the_merge_to_the_checked_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _github(monkeypatch, "b" * 40, '[{"bucket": "pass"}]', "")
    host = GitHubHost(Path("."))
    assert not host.ci_passed(1, SHA, checks_expected=True)
    host.merge(1, "cw/x", "feat: x", SHA)
    assert f"sha={SHA}" in next(call for call in calls if "PUT" in call)


def _bitbucket(monkeypatch: pytest.MonkeyPatch, statuses: list[dict], head: str) -> list[tuple[str, str]]:
    calls = []

    def request(self, method: str, path: str, payload: dict | None = None) -> dict:
        calls.append((method, path))
        if path.endswith("/statuses"):
            return {"values": statuses}
        return {"source": {"commit": {"hash": head}}}

    monkeypatch.setattr(BitbucketHost, "_request", request)
    return calls


def _bitbucket_host() -> BitbucketHost:
    credentials = BitbucketCredentials(email="me@example.test", api_token=SecretStr("token"))
    return BitbucketHost("git@bitbucket.org:team/repo.git", credentials)


def test_bitbucket_counts_no_builds_as_passed_only_without_ci_config(monkeypatch: pytest.MonkeyPatch) -> None:
    _bitbucket(monkeypatch, [], SHA[:12])
    host = _bitbucket_host()
    assert host.ci_passed(1, SHA, checks_expected=False)
    assert not host.ci_passed(1, SHA, checks_expected=True)


def test_bitbucket_refuses_to_merge_a_head_that_moved(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _bitbucket(monkeypatch, [], "b" * 12)
    with pytest.raises(AppException, match="not at the checked"):
        _bitbucket_host().merge(1, "cw/x", "feat: x", SHA)
    assert ("POST", "/pullrequests/1/merge") not in calls


def test_github_reads_each_threads_last_comment_and_replies_without_resolving(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    threads = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "nodes": [
                            {
                                "id": "T1",
                                "isResolved": False,
                                "path": "a.py",
                                "line": 3,
                                "originalLine": 3,
                                "comments": {"nodes": [{"body": "fix it", "author": {"login": "alice"}}]},
                                "lastComment": {"nodes": [{"body": "cw · declined: not needed"}]},
                            }
                        ]
                    }
                }
            }
        }
    }

    def gh(self, *arguments: str, stdin: str | None = None) -> str:
        calls.append(arguments)
        if any("reviewThreads" in argument for argument in arguments):
            return json.dumps(threads)
        return "owner/repo"

    monkeypatch.setattr(GitHubHost, "_gh", gh)
    host = GitHubHost(Path("."))
    [thread] = host.open_threads(1)
    assert thread.body == "alice: fix it" and thread.last_comment == "cw · declined: not needed"
    host.reply_to_thread(1, "T1", "cw · declined: why")
    reply = calls[-1]
    assert "id=T1" in reply and "body=cw · declined: why" in reply
    assert any("addPullRequestReviewThreadReply" in argument for argument in reply)


def test_bitbucket_replies_under_the_threads_first_comment(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = []
    monkeypatch.setattr(BitbucketHost, "_request", lambda self, method, path, payload=None: sent.append(payload) or {})
    _bitbucket_host().reply_to_thread(1, "42", "cw · declined: why")
    assert sent == [{"content": {"raw": "cw · declined: why"}, "parent": {"id": 42}}]


def test_a_posted_finding_carries_its_id() -> None:
    finding = ReviewComment(id="F3", path="a.py", line=1, severity=Severity.BLOCKING, body="wrong")
    assert finding_text(finding) == "**blocking** [F3] wrong"
