import hashlib
import json
import re
import subprocess
from collections.abc import Callable
from importlib import resources
from pathlib import Path

import pytest

from cw import benchmark, context_engine, embeddings, onboarding, pipeline
from cw.config import AgentRole, GitHostProvider, get_settings
from cw.git_hosts.base import PullRequest, PullRequestThread, finding_text
from cw.models import (
    CommentOutcome,
    CommentOutcomeKind,
    HydeSketch,
    PlannerResult,
    ReviewerResult,
    RunUsage,
    Subtask,
    WorkerResult,
    WorkerStatus,
    utc_now,
)

TOOLKIT = Path(__file__).resolve().parents[2] / "toolkit"
LINT_COMMAND = "test ! -f BROKEN"
NO_CLAUDE = "no-real-claude-in-tests"
THREAD_LABEL = re.compile(r"\[thread ([^\]]+)\]")
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
        self.labels: list[str] = []
        self.models: list[str | None] = []
        self.worker: Callable[[str, Path, int], WorkerResult] = self.default_worker
        self.reviewer: Callable[[str], ReviewerResult] = lambda prompt: ReviewerResult(summary="fine", comments=[])
        self.planner: Callable[[str], PlannerResult] | None = None
        self.sketcher: Callable[[str], HydeSketch] = lambda prompt: HydeSketch(
            paths=["app/services/statements.py"], symbols=["export_csv"], code="def export_csv(): ..."
        )
        self.cwds: list[Path] = []
        self.task_workers = ["W1", "W1"]
        self.planner_answers = ["use the settings value"]

    def default_worker(self, prompt: str, cwd: Path, call: int) -> WorkerResult:
        if "## Address these pull request comments" in prompt:
            (cwd / f"work_{call}.txt").write_text("done\n")
            outcomes = [
                CommentOutcome(thread_id=thread_id, outcome=CommentOutcomeKind.FIXED, reason="")
                for thread_id in THREAD_LABEL.findall(prompt)
            ]
            return answer(WorkerStatus.DONE, f"pass {call}", files=[f"work_{call}.txt"], comment_outcomes=outcomes)
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
        files=None, new_project=False, label="", model=None,
    ):  # fmt: skip
        self.prompts.append((role, prompt))
        self.models.append(model)
        self.cwds.append(cwd)
        self.labels.append(label or agent_name)
        self.resumed.append((role, resume_session))
        session = resume_session or f"{role.value}-{len(self.resumed)}"
        if role == AgentRole.WORKER:
            return self.worker(prompt, cwd, len(self.prompts_for(role))), usage(role, session)
        if role == AgentRole.CONTEXT:
            return self.sketcher(prompt), usage(role, session)
        nothing = {"questions": [], "split": [], "plan": "", "options_considered": "", "subtasks": [], "files": []}
        if role == AgentRole.PLANNER and self.planner:
            return self.planner(prompt), usage(role, session)
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
        self.replies: list[tuple[str, str]] = []
        self.ci = True
        self.checks_expected: list[bool] = []
        self.merged: list[tuple[int, str, str, str]] = []

    def create_pull_request(self, branch: str, base_branch: str, title: str, body: str) -> PullRequest:
        self.targets.append(base_branch)
        return PullRequest(number=1, url="https://example.test/pr/1")

    def post_review(self, number: int, summary: str, comments: list) -> None:
        self.reviews += 1
        self.summaries.append(summary)
        for comment in comments:
            text = finding_text(comment)
            thread_id = f"R{len(self.threads) + 1}"
            self.threads.append(PullRequestThread(id=thread_id, path=comment.path, line=comment.line, body=text))

    def ci_passed(self, number: int, sha: str, checks_expected: bool) -> bool:
        self.checks_expected.append(checks_expected)
        return self.ci

    def open_threads(self, number: int) -> list[PullRequestThread]:
        return [thread for thread in self.threads if thread.id not in self.resolved]

    def resolve_thread(self, number: int, thread_id: str) -> None:
        self.resolved.append(thread_id)

    def reply_to_thread(self, number: int, thread_id: str, body: str) -> None:
        self.replies.append((thread_id, body))
        for thread in self.threads:
            if thread.id == thread_id:
                thread.body = f"{thread.body}\nme: {body}"
                thread.last_comment = body

    def merge(self, number: int, branch: str, message: str, sha: str) -> str:
        self.merged.append((number, branch, message, sha))
        return sha


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
    config["claude_binary"] = str(home / NO_CLAUDE)
    config["stacks"]["python"]["commands"] = {"lint": LINT_COMMAND, "test": TEST_COMMAND}
    config["defaults"]["limits"] |= {"max_review_rounds": 1, "max_fix_rounds": 1, "review_skip_max_changed_lines": 0}
    (home / "config.json").write_text(json.dumps(config))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


@pytest.fixture
def agents(monkeypatch: pytest.MonkeyPatch) -> FakeAgents:
    fake = FakeAgents()
    monkeypatch.setattr(pipeline, "run_agent", fake.run)
    for module in (pipeline, context_engine, benchmark, onboarding):
        monkeypatch.setattr(module, "run_agent", fake.run)
    return fake


def fake_vector(text: str, length: int) -> list[float]:
    """Return a deterministic vector for a text, as a stand-in for Ollama."""
    digest = hashlib.sha256(text.encode()).digest()
    return [digest[index % len(digest)] / 255 - 0.5 for index in range(length)]


class FakeOllama:
    """Stands in for Ollama's /api/embed: records every batch; texts mapped in `same` share one vector."""

    def __init__(self) -> None:
        self.batches: list[list[str]] = []
        self.payloads: list[dict] = []
        self.same: dict[str, str] = {}
        self.down = False
        self.length = 4096

    def request(self, settings, path: str, payload: dict | None = None) -> dict:
        if self.down:
            raise embeddings._failed(settings, "not reachable (test)")
        if payload is None:
            return {"models": [{"name": settings.model}]}
        self.payloads.append(payload)
        self.batches.append(list(payload["input"]))
        vectors = [fake_vector(self.same.get(text, text), self.length) for text in payload["input"]]
        return {"embeddings": vectors}


@pytest.fixture(autouse=True)
def ollama(monkeypatch: pytest.MonkeyPatch) -> FakeOllama:
    """Every test gets a fake Ollama: no test ever reaches the network."""
    fake = FakeOllama()
    monkeypatch.setattr(embeddings, "_request", fake.request)
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
