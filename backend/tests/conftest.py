import json
import subprocess
from collections.abc import Callable
from importlib import resources
from pathlib import Path

import pytest

from cw import onboarding, pipeline
from cw.config import AgentRole, GitHostProvider, get_settings
from cw.git_hosts.base import PullRequest, PullRequestThread
from cw.models import (
    PlannerResult,
    ReviewerResult,
    RunUsage,
    Subtask,
    Verdict,
    WorkerResult,
    WorkerStatus,
    utc_now,
)

TOOLKIT = Path(__file__).resolve().parents[2] / "toolkit"
LINT_COMMAND = "test ! -f BROKEN"
TEST_COMMAND = "true"


def git(cwd: Path, *arguments: str) -> str:
    return subprocess.run(["git", *arguments], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def usage(role: AgentRole, session: str = "s") -> RunUsage:
    return RunUsage(
        at=utc_now(), role=role.value, agent=role.value, model="test", session_id=session, input_tokens=1,
        output_tokens=1, cache_read_tokens=0, cache_creation_tokens=0, cost_usd=0.0, duration_ms=1, num_turns=1,
    )  # fmt: skip


def answer(status: WorkerStatus, summary: str = "", **fields) -> WorkerResult:
    result = {
        "files": [],
        "handoff": "",
        "questions": [],
        "project_notes": [],
        "commit_message": "feat: work",
        "reason": "",
    } | fields
    return WorkerResult(status=status, summary=summary, **result)


class FakeAgents:
    """Stands in for `claude -p`: records every prompt and session, and edits files the way a worker would."""

    def __init__(self) -> None:
        self.prompts: list[tuple[AgentRole, str]] = []
        self.resumed: list[tuple[AgentRole, str | None]] = []
        self.review_files: list[list[str] | None] = []
        self.worker: Callable[[str, Path, int], WorkerResult] = self.default_worker
        self.reviewer: Callable[[str], ReviewerResult] = lambda prompt: ReviewerResult(
            verdict=Verdict.APPROVE, summary="fine", comments=[]
        )
        self.task_workers = ["W1", "W1"]
        self.planner_answers = ["use the settings value"]

    def default_worker(self, prompt: str, cwd: Path, call: int) -> WorkerResult:
        if "Resolve merge conflicts" in prompt:
            (cwd / "f.txt").write_text("ticket and main\n")
            return answer(WorkerStatus.DONE, "resolved the conflict", files=["f.txt"])
        (cwd / f"work_{call}.txt").write_text("done\n")
        handoff = f"work_{call}.txt holds the result"
        return answer(WorkerStatus.DONE, f"pass {call}", files=[f"work_{call}.txt"], handoff=handoff)

    def prompts_for(self, role: AgentRole) -> list[str]:
        return [prompt for seen, prompt in self.prompts if seen == role]

    def run(
        self, settings, project, agent_name, role, prompt, cwd, output_model, ticket_id, resume_session=None,
        files=None, new_project=False,
    ):  # fmt: skip
        self.prompts.append((role, prompt))
        self.resumed.append((role, resume_session))
        session = resume_session or f"{role.value}-{len(self.resumed)}"
        if role == AgentRole.WORKER:
            return self.worker(prompt, cwd, len(self.prompts_for(role))), usage(role, session)
        nothing = {"questions": [], "split": [], "plan": "", "options_considered": "", "subtasks": [], "files": []}
        if role == AgentRole.PLANNER and "## Questions from worker" in prompt:
            return PlannerResult(**nothing, answers=self.planner_answers), usage(role, session)
        if role == AgentRole.PLANNER:
            tasks = [
                Subtask(id=f"S{number}", title=f"task {number}", worker=worker, instructions="do it", files=[])
                for number, worker in enumerate(self.task_workers, start=1)
            ]
            plan = PlannerResult(**(nothing | {"plan": "the plan", "subtasks": tasks}), answers=[])
            return plan, usage(role, session)
        self.review_files.append(files)
        return self.reviewer(prompt), usage(role, session)


class FakeHost:
    def __init__(self) -> None:
        self.reviews = 0
        self.summaries: list[str] = []
        self.targets: list[str] = []
        self.threads: list[PullRequestThread] = []
        self.resolved: list[str] = []
        self.ci = True
        self.merged: list[tuple[int, str, str]] = []

    def create_pull_request(self, branch: str, base_branch: str, title: str, body: str) -> PullRequest:
        self.targets.append(base_branch)
        return PullRequest(number=1, url="https://example.test/pr/1")

    def post_review(self, number: int, summary: str, comments: list) -> None:
        self.reviews += 1
        self.summaries.append(summary)
        self.threads += [
            PullRequestThread(id=f"R{len(self.threads) + 1}", path=c.path, line=c.line, body=c.body) for c in comments
        ]

    def ci_passed(self, number: int, sha: str) -> bool:
        return self.ci

    def open_threads(self, number: int) -> list[PullRequestThread]:
        return [thread for thread in self.threads if thread.id not in self.resolved]

    def resolve_thread(self, number: int, thread_id: str) -> None:
        self.resolved.append(thread_id)

    def merge(self, number: int, branch: str, message: str) -> None:
        self.merged.append((number, branch, message))


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A cw home with a valid config and the repository's own toolkit."""
    for key in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(key, "tester")
    for key in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(key, "tester@example.test")
    home = tmp_path / "cw-home"
    home.mkdir()
    monkeypatch.setenv("CW_HOME", str(home))
    config = json.loads(resources.files("cw").joinpath("config.example.json").read_text())
    config["toolkit"]["path"] = str(TOOLKIT)
    config["stacks"]["python"]["commands"] = {"lint": LINT_COMMAND, "test": TEST_COMMAND}
    (home / "config.json").write_text(json.dumps(config))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


@pytest.fixture
def agents(monkeypatch: pytest.MonkeyPatch) -> FakeAgents:
    fake = FakeAgents()
    monkeypatch.setattr(pipeline, "run_agent", fake.run)
    return fake


@pytest.fixture
def host(monkeypatch: pytest.MonkeyPatch) -> FakeHost:
    fake = FakeHost()
    monkeypatch.setattr(pipeline, "setup_git_host", lambda settings, project, checkout: fake)
    return fake


def make_repository(root: Path, with_remote: bool) -> Path:
    """Create a registered project `demo` with one commit, optionally with a bare repository as its origin."""
    repository = root / "demo"
    repository.mkdir()
    git(repository, "init", "-b", "main")
    (repository / "f.txt").write_text("base\n")
    (repository / "pyproject.toml").write_text('[project]\nname = "demo"\nversion = "0"\n')
    git(repository, "add", "-A")
    git(repository, "commit", "-m", "base")
    if with_remote:
        git(root, "init", "--bare", "-b", "main", "origin.git")
        git(repository, "remote", "add", "origin", str(root / "origin.git"))
        git(repository, "push", "-u", "origin", "main")
    onboarding.add_project(repository, "demo", GitHostProvider.GITHUB, onboard=False)
    return repository


@pytest.fixture
def local_repository(workspace: Path) -> Path:
    return make_repository(workspace, with_remote=False)


@pytest.fixture
def remote_repository(workspace: Path) -> Path:
    return make_repository(workspace, with_remote=True)
