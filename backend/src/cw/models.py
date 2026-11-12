"""Tickets and the structured results agents return."""

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from cw.config import ProjectCommands


def utc_now() -> datetime:
    """Return the current time in UTC."""
    return datetime.now(UTC)


class TicketStatus(StrEnum):
    """Where a ticket is in its lifecycle."""

    NEEDS_INPUT = "needs_input"
    AWAITING_APPROVAL = "awaiting_approval"
    IN_PROGRESS = "in_progress"
    READY = "ready"
    IN_REVIEW = "in_review"
    BLOCKED = "blocked"
    PAUSED = "paused"
    PR_OPEN = "pr_open"
    DONE = "done"
    ABANDONED = "abandoned"


class Route(StrEnum):
    """Whether the ticket went straight to a worker or through the planner."""

    DIRECT = "direct"
    PLANNED = "planned"


class Step(StrEnum):
    """A resumable pipeline step."""

    PLAN = "plan"
    WORK = "work"
    PR = "pr"


class WorkerStatus(StrEnum):
    """How a worker run ended."""

    DONE = "done"
    NEEDS_PLAN = "needs_plan"
    BLOCKED = "blocked"


class Verdict(StrEnum):
    """A reviewer's decision."""

    APPROVE = "approve"
    CHANGES = "changes"


class Severity(StrEnum):
    """How serious a review finding is."""

    BLOCKING = "blocking"
    MINOR = "minor"


class Subtask(BaseModel):
    """One step of a plan, done by one worker after the steps before it."""

    id: str = Field(description="Short id such as S1.")
    title: str
    instructions: str
    files: list[str] = Field(description="Files this task is expected to change.")


class ReviewComment(BaseModel):
    """One review finding anchored to a line."""

    path: str
    line: int = Field(description="Line number in the new version of the file.")
    severity: Severity
    body: str


class PlannerResult(BaseModel):
    """What the planner returns."""

    questions: list[str] = Field(description="Blocking clarifying questions; empty when the plan is ready.")
    split: list[str] = Field(
        description="When the request is several independent changes that each need their own branch or PR: "
        "one standalone prompt per change. Empty otherwise."
    )
    plan: str
    options_considered: str
    subtasks: list[Subtask]
    files: list[str]


class WorkerResult(BaseModel):
    """What a worker returns."""

    status: WorkerStatus
    summary: str
    commit_message: str
    reason: str


class ReviewerResult(BaseModel):
    """What a reviewer returns."""

    verdict: Verdict
    summary: str
    comments: list[ReviewComment]


class Difference(BaseModel):
    """A convention where the project differs from the team rules."""

    id: str
    topic: str
    theirs: str
    ours: str
    example: str


class OnboardingResult(BaseModel):
    """What the onboarder returns."""

    stack: str
    commands: ProjectCommands
    architecture: str
    test_inventory: str
    differences: list[Difference]
    warnings: list[str]


class PromptEntry(BaseModel):
    """A prompt the user gave the ticket."""

    at: datetime
    text: str


class WorkEntry(BaseModel):
    """A worker pass recorded in the ticket."""

    at: datetime
    subtask: str | None
    status: WorkerStatus
    summary: str


class ReviewEntry(BaseModel):
    """A reviewer pass recorded in the ticket."""

    at: datetime
    round: int
    reviewer: str
    verdict: Verdict
    summary: str
    comments: list[ReviewComment]


class RunUsage(BaseModel):
    """Token usage of one agent run."""

    at: datetime
    role: str
    agent: str
    model: str
    session_id: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    cost_usd: float
    duration_ms: int
    num_turns: int


class Ticket(BaseModel):
    """A unit of work: one branch in the project checkout, at most one pull request."""

    id: str
    project: str
    title: str
    status: TicketStatus
    route: Route
    branch: str
    base_branch: str
    previous_branch: str
    base_sha: str
    owner: str
    toolkit_version: str
    created_at: datetime
    updated_at: datetime
    prompts: list[PromptEntry] = []
    questions: list[str] = []
    split: list[str] = []
    question_rounds: int = 0
    planner_session: str | None = None
    plan: str = ""
    options_considered: str = ""
    subtasks: list[Subtask] = []
    tasks_done: list[str] = []
    files: list[str] = []
    work_log: list[WorkEntry] = []
    reviews: list[ReviewEntry] = []
    last_reviewed_tree: str | None = None
    baseline: dict[str, bool] = {}
    warnings: list[str] = []
    commit_message: str = ""
    pushed_at: datetime | None = None
    pr_url: str | None = None
    pr_number: int | None = None
    paused_step: Step | None = None
    error: str | None = None
    runs: list[RunUsage] = []


class TicketSummary(BaseModel):
    """The short form of a ticket used by `cw status`."""

    id: str
    project: str
    title: str
    status: TicketStatus
    route: Route
    branch: str
    pr_url: str | None
    updated_at: datetime
    running: bool
    archived: bool
    error: str | None
