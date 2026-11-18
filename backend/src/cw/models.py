"""Tickets and the structured results agents return."""

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field
from pydantic.json_schema import SkipJsonSchema

from cw.config import ProjectCommands

FIRST_WORKER = "W1"


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
    REVIEW = "review"
    PR = "pr"


class WorkerStatus(StrEnum):
    """How a worker run ended."""

    DONE = "done"
    QUESTION = "question"
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
    worker: str = Field(
        default=FIRST_WORKER, description="The worker that does it: W1, W2, ... Related tasks share a worker."
    )
    instructions: str
    files: list[str] = Field(description="Files this task is expected to change.")


class ReviewComment(BaseModel):
    """One review finding anchored to a line."""

    path: str
    line: int = Field(description="Line number in the new version of the file.")
    severity: Severity
    body: str
    id: SkipJsonSchema[str] = ""


class ContextAnchor(BaseModel):
    """A precise code location the worker must inspect first."""

    path: str = Field(description="Relative file path.")
    lines: str = Field(default="", description="Line range, e.g. '45-60' or '120'.")
    reason: str = Field(default="", description="Why this code matters for the task.")


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
    answers: list[str] = Field(
        description="Only when a worker asked you questions: one answer per question, in the same order, with an "
        "empty string where only the user can answer. Otherwise empty."
    )
    context_pack: list[ContextAnchor] = Field(
        default_factory=list,
        description="5–15 key locations the worker must inspect to implement this plan.",
    )


class WorkerResult(BaseModel):
    """What a worker returns."""

    status: WorkerStatus
    summary: str = Field(description="How you solved it, in a few lines.")
    files: list[str] = Field(description="Every file you created or changed.")
    handoff: str = Field(
        description="What the next worker must know to build on this: names, signatures, decisions, "
        "anything left open. Empty when there is nothing."
    )
    questions: list[str] = Field(description="Only with status question: what you need answered. Otherwise empty.")
    project_notes: list[str] = Field(
        default_factory=list,
        description="Usually empty. Only a lasting fact a future session in this repository needs and cannot see "
        "quickly in the code: a command to use, where a kind of code lives, a decision and why, a trap. One line "
        "each, at most two. Not what you did (that is summary); cw adds them to the project's CLAUDE.md.",
    )
    commit_message: str
    reason: str


class ReviewerResult(BaseModel):
    """What a reviewer returns."""

    summary: str
    comments: list[ReviewComment]
    resolved: list[str] = Field(
        default_factory=list,
        description="Ids of the open findings in your prompt that the code now fixes. Empty when none were listed.",
    )


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
    is_answer: bool = False


class QuestionEntry(BaseModel):
    """A question a worker asked, and the answer the planner or the user gave."""

    at: datetime
    worker: str
    task: str | None
    question: str
    answer: str = ""
    answered_by: str = ""
    delivered: bool = False


class WorkEntry(BaseModel):
    """A worker pass recorded in the ticket: what it did, which files it touched, what the next worker must know."""

    at: datetime
    subtask: str | None
    status: WorkerStatus
    summary: str
    worker: str = ""
    files: list[str] = []
    handoff: str = ""
    project_notes: list[str] = []


class ReviewEntry(BaseModel):
    """A reviewer pass recorded in the ticket."""

    at: datetime
    round: int
    reviewer: str
    verdict: Verdict
    summary: str
    comments: list[ReviewComment]


class RunUsage(BaseModel):
    """Token usage of one agent run; `cost_usd` is Claude Code's total for the session so far, not for this run."""

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
    label: str = ""

    def context_per_turn(self) -> int:
        """Return the average context one turn sent, cached or not: the size of the session."""
        context = self.input_tokens + self.cache_read_tokens + self.cache_creation_tokens
        return context // max(self.num_turns, 1)


class Ticket(BaseModel):
    """A unit of work: one branch in the project checkout, at most one pull request."""

    id: str
    project: str
    title: str
    status: TicketStatus
    route: Route
    branch: str
    base_branch: str
    target_branch: str = ""
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
    qa: list[QuestionEntry] = []
    asking_worker: str = ""
    planner_session: str | None = None
    worker_sessions: dict[str, str] = {}
    worker_seen: dict[str, int] = {}
    last_worker: str = ""
    plan: str = ""
    options_considered: str = ""
    subtasks: list[Subtask] = []
    tasks_done: list[str] = []
    files: list[str] = []
    context_pack: list[ContextAnchor] = []
    work_log: list[WorkEntry] = []
    reviews: list[ReviewEntry] = []
    last_reviewed_tree: str | None = None
    open_findings: list[ReviewComment] = []
    baseline: dict[str, bool] = {}
    failing_checks: list[str] = []
    new_project: bool = False
    posted_review_round: int = 0
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
