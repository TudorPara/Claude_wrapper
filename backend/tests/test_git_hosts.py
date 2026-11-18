import subprocess
from pathlib import Path

import pytest
from pydantic import SecretStr

from cw.config import BitbucketCredentials
from cw.errors import AppException
from cw.git_hosts import github
from cw.git_hosts.bitbucket import BitbucketHost
from cw.git_hosts.github import GitHubHost

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
