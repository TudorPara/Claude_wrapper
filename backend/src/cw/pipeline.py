"""The ticket pipeline: a branch in the project checkout, workers taking turns on the plan's tasks, checks,
a mandatory review, one commit and the pull request."""

import logging
import os
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel

from cw import context_engine, git, prompts, repo_map, storage
from cw.claude_runner import TICKET_PLACEHOLDER, run_agent
from cw.config import AgentRole, Project, Settings, get_settings, repo_map_path
from cw.errors import AppErrorCode, AppException
from cw.git_hosts.base import PullRequestThread, finding_tag, finding_text
from cw.git_hosts.factory import setup_git_host
from cw.models import (
    FIRST_WORKER,
    CommentOutcomeKind,
    PlannerResult,
    PromptEntry,
    QuestionEntry,
    ReviewComment,
    ReviewEntry,
    ReviewerResult,
    Route,
    Severity,
    Step,
    Subtask,
    Ticket,
    TicketStatus,
    Verdict,
    WorkEntry,
    WorkerResult,
    WorkerStatus,
    utc_now,
)
from cw.process import run_process
from cw.tickets import (
    CLOSED_STATUSES,
    append_event,
    find_ticket,
    is_running,
    list_tickets,
    make_title,
    new_ticket_id,
    project_lock,
    write_ticket,
)
from cw.toolkit import toolkit_version

logger = logging.getLogger(__name__)

BRANCH_PREFIX = "cw/"
WORKER_AGENT = "worker"
PLANNER_AGENT = "planner"
WORKER_PREFIX = "W"
MERGE_COMMIT_MESSAGE = "chore: merge base branch"
PARK_COMMIT_PREFIX = "wip: "
CHECKS_WARNING = "Checks:"
FAILING_WARNING = "Checks: now failing"
SCOPE_WARNING = "Scope:"
EMPTY_WARNING = "No changes:"
CONTEXT_WARNING = "Context:"
TICKET_MAX_NOTES = 5
FINDING_PREFIX = "F"
CHECK_EVENT = "check"
OTHER_WORK = "follow-up"
FIX_WORK = "fix"
GUARDED_DOC_FILES = {"README.md", "CLAUDE.md"}
DECLINE_REPLY_PREFIX = "cw · declined:"
THREADS_SHOWN = 3
ARTIFACTS_SHOWN = 5
REVIEW_WARNING = "Review:"
FETCH_WARNING = "Fetch:"
LOCAL_WARNING = "Local:"
CI_CONFIG_PATHS = (".github/workflows", "bitbucket-pipelines.yml")
DOCKER_COMPOSE = ("docker", "compose", "--file")
COMPOSE_UP = ("up", "--detach", "--wait")
USER = "user"
PLANNER = "planner"
WORKER_SESSION_TOKEN_CEILING = 45000
FOLLOW_UP_STATUSES = {
    TicketStatus.READY,
    TicketStatus.BLOCKED,
    TicketStatus.PAUSED,
    TicketStatus.PR_OPEN,
    TicketStatus.IN_PROGRESS,
    TicketStatus.IN_REVIEW,
}
PLAN_FEEDBACK_STATUSES = {TicketStatus.NEEDS_INPUT, TicketStatus.AWAITING_APPROVAL}
PR_STATUSES = {TicketStatus.READY, TicketStatus.BLOCKED, TicketStatus.PAUSED, TicketStatus.PR_OPEN}
RESUMABLE_STATUSES = {TicketStatus.PAUSED, TicketStatus.BLOCKED}
STALE_STATUSES = {TicketStatus.IN_PROGRESS, TicketStatus.IN_REVIEW}


def _add_context_warnings(ticket: Ticket, messages: list[str]) -> None:
    """Add the context engine's warnings to the ticket, each once."""
    for message in messages:
        warning = f"{CONTEXT_WARNING} {message}"
        if warning not in ticket.warnings:
            ticket.warnings.append(warning)


def _seed(settings: Settings, project: Project, ticket: Ticket) -> str:
    """Return the ticket's context seed, building it once on first use."""
    _add_context_warnings(ticket, context_engine.seed_for(settings, project, ticket))
    return ticket.context_seed


def _direct_seed(settings: Settings, project: Project, ticket: Ticket) -> str:
    """Return the seed for a direct ticket's first worker prompt, and nothing for any later prompt."""
    if ticket.route != Route.DIRECT or ticket.work_log:
        return ""
    return _seed(settings, project, ticket)


def _own_changes(project: Project, ticket: Ticket) -> list[str]:
    """Return the files the ticket's branch changed itself, without what it merged in from the base.

    A branch the base already contains (merged by fast-forward) is measured from the commit the ticket started at.
    """
    checkout = project.path
    if not git.resolve_commit(checkout, ticket.branch):
        return []
    target = _target(project, ticket)
    remote_base = f"{project.remote}/{target}"
    base_ref = remote_base if git.resolve_commit(checkout, remote_base) else target
    tip = git.resolve_commit(checkout, ticket.branch)
    base = git.merge_base(checkout, base_ref, ticket.branch) if git.resolve_commit(checkout, base_ref) else ""
    if not base or base == tip:
        base = ticket.base_sha
    return git.changed_files(checkout, base, ticket.branch)


def _context(ticket: Ticket) -> tuple[Settings, Project, Path]:
    """Return the settings, the ticket's project and its checkout."""
    settings = get_settings()
    project = settings.project(ticket.project)
    return settings, project, project.path


def _target(project: Project, ticket: Ticket) -> str:
    """Return the branch the ticket started from and goes back into."""
    return ticket.target_branch or project.base_branch


def _set_warning(ticket: Ticket, prefix: str, message: str | None) -> None:
    """Replace the ticket's warning with this prefix; None clears it."""
    ticket.warnings = [warning for warning in ticket.warnings if not warning.startswith(prefix)]
    if message:
        ticket.warnings.append(f"{prefix} {message}")


def _enter(project: Project, ticket: Ticket) -> None:
    """Put the checkout on the ticket's branch; refuse if someone else's changes are in the way."""
    checkout = project.path
    current = git.current_branch(checkout)
    if current == ticket.branch:
        return
    if not git.is_clean(checkout):
        raise AppException(
            AppErrorCode.INVALID_STATE,
            f"{project.name} has uncommitted changes on {current}; commit, stash or park them first.",
        )
    git.switch(checkout, ticket.branch)


def _stop(ticket: Ticket, step: Step, status: TicketStatus, reason: str) -> None:
    """Record why a step stopped, so `cw resume` can run it again."""
    ticket.status = status
    ticket.paused_step = step
    ticket.error = reason
    write_ticket(ticket)


def _guarded(project: Project, ticket: Ticket, step: Step, action: Callable[[], None]) -> None:
    """Run a step on the ticket's branch; whatever stops it, leave the ticket paused or blocked and resumable.

    The step is recorded before it runs, so a process killed without a chance to clean up resumes the same step.
    """
    try:
        with project_lock(project.name, ticket.id):
            _enter(project, ticket)
            ticket.paused_step = step
            write_ticket(ticket)
            action()
            ticket.error = None
            ticket.paused_step = None
            write_ticket(ticket)
    except AppException as error:
        if error.error_code in (AppErrorCode.TICKET_BUSY, AppErrorCode.INVALID_STATE):
            raise
        if error.usage:
            ticket.runs.append(error.usage)
        paused = error.error_code == AppErrorCode.USAGE_LIMIT
        _stop(ticket, step, TicketStatus.PAUSED if paused else TicketStatus.BLOCKED, str(error))
        raise
    except (KeyboardInterrupt, Exception) as error:
        _stop(ticket, step, TicketStatus.BLOCKED, f"Stopped before it finished ({type(error).__name__}).")
        raise


def _run_check(project: Project, ticket_id: str, command: str) -> tuple[bool, str]:
    """Run a project command in the checkout; return whether it passed and its output. A timeout is a failure."""
    environment = {key: value.replace(TICKET_PLACEHOLDER, ticket_id) for key, value in project.env.items()}
    try:
        code, stdout, stderr = run_process(
            command, project.path, {**os.environ, **environment}, project.profile.limits.check_timeout_minutes
        )
    except AppException as error:
        append_event(project.name, ticket_id, {"kind": CHECK_EVENT, "command": command, "passed": False})
        return False, str(error)
    append_event(project.name, ticket_id, {"kind": CHECK_EVENT, "command": command, "passed": code == 0})
    return code == 0, f"{stdout}{stderr}"


def _start_services(project: Project) -> None:
    """Start the project's containers before a worker or the checks need them; a failure stops the ticket.

    Workers may run only the lint and test commands, so cw starts the services itself. `up --wait` returns at once
    when they already run, and waits for their healthchecks otherwise.
    """
    if not project.compose_file or not (project.path / project.compose_file).exists():
        return
    command = [*DOCKER_COMPOSE, project.compose_file, *COMPOSE_UP]
    code, stdout, stderr = run_process(
        command, project.path, {**os.environ, **project.env}, project.profile.limits.check_timeout_minutes
    )
    if code != 0:
        output = f"{stdout}{stderr}"[-project.profile.limits.max_check_output_chars :]
        logger.error("Could not start the containers in %s.", project.compose_file)
        raise AppException(
            AppErrorCode.COMMAND_FAILED, f"Could not start the containers in {project.compose_file}:\n{output}"
        )


def _record_baseline(project: Project, ticket: Ticket) -> None:
    """Run the checks once on the untouched base; only checks that pass here are enforced later.

    A new project has no base to compare with, so every check is the ticket's own to make pass. The containers start
    first, so tests that need them are not mistaken for tests that already fail.
    """
    if not ticket.new_project:
        _start_services(project)
    for name, command in project.checks():
        if ticket.new_project:
            ticket.baseline[name] = True
            continue
        logger.info("Baseline $ %s", command)
        ticket.baseline[name], _ = _run_check(project, ticket.id, command)
        if not ticket.baseline[name]:
            ticket.warnings.append(
                f"{CHECKS_WARNING} `{command}` already fails on {ticket.base_branch}; it is reported, not enforced."
            )
    left_behind = git.changed_paths(project.path)
    if left_behind:
        ticket.warnings.append(
            f"{CHECKS_WARNING} running the checks left files behind ({', '.join(left_behind[:ARTIFACTS_SHOWN])}). "
            "Commit or gitignore them on the base branch, or they become part of this ticket's commit."
        )


def _failing_checks(project: Project, ticket: Ticket) -> list[tuple[str, str]]:
    """Run the checks that passed on the base; return (command, output) for each one that fails now."""
    _start_services(project)
    failures = []
    for name, command in project.checks():
        if not ticket.baseline.get(name, False):
            continue
        logger.info("$ %s", command)
        passed, output = _run_check(project, ticket.id, command)
        if not passed:
            failures.append((command, output[-project.profile.limits.max_check_output_chars :]))
    return failures


def _changes_so_far(project: Project, ticket: Ticket) -> str:
    """Return the files changed on the branch since it started, committed or not."""
    return git.diff_stat(project.path, ticket.base_sha, git.snapshot(project.path))


def _resume_or_start_agent[T: BaseModel](
    settings: Settings,
    project: Project,
    ticket: Ticket,
    role: AgentRole,
    output_model: type[T],
    session: str | None,
    prompt: Callable[[bool], str],
    label: str = "",
) -> tuple[T, str]:
    """Run an agent, in the session it already has when there is one, and return its result and session.

    `prompt(fresh)` builds the prompt: the whole picture for a new session, only what is new for a resumed one.
    A session that can no longer be resumed is replaced by a new one.
    """
    agent = WORKER_AGENT if role == AgentRole.WORKER else PLANNER_AGENT
    if session:
        try:
            agent_result, usage = run_agent(
                settings,
                project,
                agent,
                role,
                prompt(False),
                project.path,
                output_model,
                ticket.id,
                session,
                new_project=ticket.new_project,
                label=label,
            )
        except AppException as error:
            if error.error_code != AppErrorCode.SESSION_LOST:
                raise
            logger.info("The %s's session could not be continued; starting a new one.", agent)
            session = None
    if not session:
        agent_result, usage = run_agent(
            settings,
            project,
            agent,
            role,
            prompt(True),
            project.path,
            output_model,
            ticket.id,
            new_project=ticket.new_project,
            label=label,
        )
    ticket.runs.append(usage)
    return agent_result, usage.session_id


def _worker(
    settings: Settings,
    project: Project,
    ticket: Ticket,
    worker: str,
    instruction: str,
    task: str | None,
    fix: bool = False,
) -> WorkerResult:
    """Give a worker its next instruction and write what it did into the ticket.

    A fix pass keeps no project notes, and changing README.md or CLAUDE.md there without being asked is flagged.
    """
    _start_services(project)
    tree_before = git.snapshot(project.path)
    worker_result, session = _resume_or_start_agent(
        settings,
        project,
        ticket,
        AgentRole.WORKER,
        WorkerResult,
        ticket.worker_sessions.get(worker),
        lambda fresh: (
            prompts.fresh_worker_prompt(
                project,
                ticket,
                instruction,
                _changes_so_far(project, ticket),
                _direct_seed(settings, project, ticket) if not fix else "",
            )
            if fresh
            else prompts.resumed_worker_prompt(ticket, worker, instruction)
        ),
        f"{worker} · {task}" if task else worker,
    )
    actual_files = git.changed_files(project.path, tree_before, git.snapshot(project.path))
    size = ticket.runs[-1].context_per_turn()
    if size > WORKER_SESSION_TOKEN_CEILING:
        logger.info("Worker %s's session is at about %s tokens a turn; it starts fresh next time.", worker, size)
    if size > WORKER_SESSION_TOKEN_CEILING or not session:
        ticket.worker_sessions.pop(worker, None)
    else:
        ticket.worker_sessions[worker] = session
    ticket.last_worker = worker
    if worker_result.status != WorkerStatus.QUESTION:
        label = task or (FIX_WORK if fix else OTHER_WORK)
        empty = worker_result.status == WorkerStatus.DONE and not actual_files
        _set_warning(
            ticket,
            f"{EMPTY_WARNING} {label}:",
            f"worker {worker} reported done but changed no files." if empty else None,
        )
        subtask = next((subtask for subtask in ticket.subtasks if subtask.id == task), None) if task else None
        if fix:
            asked = instruction.replace(prompts.FIX_SCOPE, "")
            unexpected = sorted(
                path for path in actual_files if Path(path).name in GUARDED_DOC_FILES and Path(path).name not in asked
            )
        elif subtask and subtask.files:
            unexpected = sorted(
                path for path in actual_files if path not in subtask.files and path not in subtask.instructions
            )
        else:
            unexpected = []
        _set_warning(
            ticket,
            f"{SCOPE_WARNING} {label}:",
            f"worker {worker} changed {', '.join(unexpected)}, which no finding or comment named"
            if fix and unexpected
            else f"worker {worker} changed files outside its task: {', '.join(unexpected)}"
            if unexpected
            else None,
        )
        written = sum(len(entry.project_notes) for entry in ticket.work_log)
        notes_kept = [] if fix else worker_result.project_notes[: max(TICKET_MAX_NOTES - written, 0)]
        ticket.work_log.append(
            WorkEntry(
                at=utc_now(),
                subtask=task,
                status=worker_result.status,
                summary=worker_result.summary,
                worker=worker,
                files=actual_files,
                handoff=worker_result.handoff,
                project_notes=notes_kept,
                reflection=worker_result.reflection,
            )
        )
        if worker_result.commit_message and not ticket.commit_message:
            ticket.commit_message = worker_result.commit_message.splitlines()[0]
    ticket.worker_seen[worker] = len(ticket.work_log)
    write_ticket(ticket)
    return worker_result


def _ask(
    settings: Settings, project: Project, ticket: Ticket, worker: str, task: str | None, questions: list[str]
) -> bool:
    """Write a worker's questions into the ticket and let the planner answer; return False when the user must."""
    asked = [QuestionEntry(at=utc_now(), worker=worker, task=task, question=question) for question in questions]
    ticket.qa += asked
    if ticket.planner_session:
        logger.info("Worker %s asks the planner %s question(s).", worker, len(asked))
        answer, ticket.planner_session = _resume_or_start_agent(
            settings,
            project,
            ticket,
            AgentRole.PLANNER,
            PlannerResult,
            ticket.planner_session,
            lambda fresh: prompts.planner_answer_prompt(ticket, worker, task, questions, fresh),
        )
        for entry, text in zip(asked, answer.answers, strict=False):
            if text.strip():
                entry.answer, entry.answered_by = text.strip(), PLANNER
    unanswered = [entry.question for entry in asked if not entry.answer]
    if unanswered:
        ticket.questions = unanswered
        ticket.asking_worker = worker
        ticket.status = TicketStatus.NEEDS_INPUT
    write_ticket(ticket)
    return not unanswered


def _with_answers(ticket: Ticket, worker: str, instruction: str) -> str:
    """Put the answers this worker is still waiting for in front of its instruction, once."""
    answers = [entry for entry in ticket.qa if entry.worker == worker and entry.answer and not entry.delivered]
    if not answers:
        return instruction
    for entry in answers:
        entry.delivered = True
    return prompts.answers_instruction(answers, instruction)


def _work_on(
    settings: Settings, project: Project, ticket: Ticket, worker: str, instruction: str, task: str | None
) -> WorkerResult | None:
    """Have a worker carry out one instruction, getting its questions answered on the way.

    Questions go to the planner first and to the user only when the planner cannot answer.
    Returns None when the ticket now waits for the user.
    """
    worker_result = _worker(settings, project, ticket, worker, _with_answers(ticket, worker, instruction), task)
    for _ in range(project.profile.limits.max_question_rounds):
        if worker_result.status != WorkerStatus.QUESTION:
            return worker_result
        if not _ask(settings, project, ticket, worker, task, worker_result.questions):
            return None
        worker_result = _worker(settings, project, ticket, worker, _with_answers(ticket, worker, instruction), task)
    if worker_result.status == WorkerStatus.QUESTION:
        worker_result = _worker(
            settings, project, ticket, worker, prompts.no_more_questions_instruction(instruction), task
        )
    if worker_result.status == WorkerStatus.QUESTION:
        raise AppException(
            AppErrorCode.AGENT_FAILED, f"Worker {worker} still asks: {'; '.join(worker_result.questions)}"
        )
    return worker_result


def _fix(settings: Settings, project: Project, ticket: Ticket, instruction: str) -> WorkerResult:
    """Send a fix (failing checks, review findings, pull request comments, conflicts) to the worker that worked last."""
    return _worker(settings, project, ticket, ticket.last_worker or FIRST_WORKER, instruction, None, fix=True)


def _checks_with_fixes(settings: Settings, project: Project, ticket: Ticket) -> list[tuple[str, str]]:
    """Run the enforced checks; let the worker fix what it broke, then report and return what still fails."""
    failures = _failing_checks(project, ticket)
    rounds = 0
    while failures and rounds < project.profile.limits.max_fix_rounds:
        rounds += 1
        logger.info("Checks broke; fix round %s of %s.", rounds, project.profile.limits.max_fix_rounds)
        _fix(settings, project, ticket, prompts.fix_checks_instruction(failures))
        failures = _failing_checks(project, ticket)
    ticket.failing_checks = [command for command, _ in failures]
    _set_warning(ticket, FAILING_WARNING, ", ".join(f"`{command}`" for command in ticket.failing_checks) or None)
    return failures


def _back_off(
    settings: Settings, project: Project, ticket: Ticket, task: Subtask, failures: list[tuple[str, str]]
) -> list[tuple[str, str]]:
    """Hand a task whose checks still fail to a fresh session of its worker, with the last attempt's reflection."""
    last = ticket.work_log[-1] if ticket.work_log else None
    reflection = (last.reflection or last.summary) if last else ""
    logger.info("Task %s still fails; worker %s starts a fresh session from its reflection.", task.id, task.worker)
    ticket.worker_sessions.pop(task.worker, None)
    _worker(settings, project, ticket, task.worker, prompts.backoff_instruction(task, reflection, failures), task.id)
    return _checks_with_fixes(settings, project, ticket)


def _assign_workers(subtasks: list[Subtask], max_workers: int) -> None:
    """Name the planner's workers W1, W2, ... in order of appearance; workers past the limit share the last one."""
    names = list(dict.fromkeys(task.worker for task in subtasks))
    assigned = {name: f"{WORKER_PREFIX}{min(number, max_workers)}" for number, name in enumerate(names, start=1)}
    for task in subtasks:
        task.worker = assigned[task.worker]


def _run_planner(settings: Settings, project: Project, ticket: Ticket, feedback: str | None) -> PlannerResult:
    """Run the planner in its session with the user's feedback, if any, and return its result."""
    plan_result, ticket.planner_session = _resume_or_start_agent(
        settings,
        project,
        ticket,
        AgentRole.PLANNER,
        PlannerResult,
        ticket.planner_session,
        lambda fresh: prompts.planner_prompt(
            project,
            ticket,
            feedback,
            fresh,
            _seed(settings, project, ticket) if fresh else "",
            settings.context.include_repo_map,
        ),
    )
    return plan_result


def _plan(settings: Settings, project: Project, ticket: Ticket, feedback: str | None) -> None:
    """Run the planner and move the ticket to questions, a split, or approval."""
    ticket.status = TicketStatus.IN_PROGRESS
    limits = project.profile.limits
    plan_result = _run_planner(settings, project, ticket, feedback)
    if len(plan_result.split) > 1:
        ticket.split = plan_result.split
        ticket.questions = [
            f"This is {len(plan_result.split)} separate changes. Start each as its own ticket "
            "(own branch and PR), or reply to keep them together."
        ]
        ticket.status = TicketStatus.NEEDS_INPUT
        return
    ticket.split = []
    if plan_result.questions and ticket.question_rounds < limits.max_question_rounds:
        ticket.questions = plan_result.questions
        ticket.question_rounds += 1
        ticket.status = TicketStatus.NEEDS_INPUT
        return
    if not plan_result.plan.strip():
        logger.info("The planner returned no plan after its last question round; asking it to plan now.")
        plan_result = _run_planner(settings, project, ticket, prompts.PLAN_NOW)
    if not plan_result.plan.strip():
        ticket.questions = plan_result.questions or [prompts.NO_PLAN]
        ticket.status = TicketStatus.NEEDS_INPUT
        return
    ticket.questions = []
    ticket.plan = plan_result.plan
    ticket.options_considered = plan_result.options_considered
    ticket.files = plan_result.files
    ticket.context_pack = [anchor for anchor in plan_result.context_pack if (project.path / anchor.path).is_file()]
    ticket.subtasks = plan_result.subtasks or [
        Subtask(id="S1", title=ticket.title, instructions=plan_result.plan, files=plan_result.files)
    ]
    _assign_workers(ticket.subtasks, limits.max_workers)
    ticket.tasks_done = []
    ticket.status = TicketStatus.AWAITING_APPROVAL


def next_task(ticket: Ticket) -> Subtask | None:
    """Return the first planned task that is not done yet."""
    return next((task for task in ticket.subtasks if task.id not in ticket.tasks_done), None)


def _has_unfinished_tasks(ticket: Ticket) -> bool:
    """Return whether a planned ticket still has tasks to run."""
    return ticket.route == Route.PLANNED and next_task(ticket) is not None


def _direct_pass(settings: Settings, project: Project, ticket: Ticket) -> bool:
    """Have a worker do the user's latest request; return False when the ticket now waits for a plan or an answer."""
    worker = ticket.last_worker or FIRST_WORKER
    worker_result = _work_on(settings, project, ticket, worker, prompts.direct_instruction(project, ticket), None)
    if worker_result is None:
        return False
    if worker_result.status == WorkerStatus.NEEDS_PLAN:
        logger.info("Worker escalated to the planner: %s", worker_result.reason)
        ticket.route = Route.PLANNED
        _plan(settings, project, ticket, f"The worker escalated before editing: {worker_result.reason}")
        return False
    if worker_result.status == WorkerStatus.BLOCKED:
        raise AppException(AppErrorCode.AGENT_FAILED, f"Worker blocked: {worker_result.reason}")
    return True


def _task_passes(settings: Settings, project: Project, ticket: Ticket) -> bool:
    """Run the plan's unfinished tasks in order, each by its worker and followed by the checks.

    Checking after every task keeps a broken step from becoming the base of the next one.
    Returns False when a worker waits for the user.
    """
    while (task := next_task(ticket)) is not None:
        logger.info("Task %s · %s (worker %s)", task.id, task.title, task.worker)
        worker_result = _work_on(settings, project, ticket, task.worker, prompts.task_instruction(task), task.id)
        if worker_result is None:
            return False
        if worker_result.status != WorkerStatus.DONE:
            raise AppException(AppErrorCode.AGENT_FAILED, f"Task {task.id} stopped: {worker_result.reason}")
        failures = _checks_with_fixes(settings, project, ticket)
        backoffs = 0
        while failures and backoffs < project.profile.limits.max_backoff_rounds:
            backoffs += 1
            failures = _back_off(settings, project, ticket, task, failures)
        if ticket.failing_checks:
            failing = ", ".join(f"`{command}`" for command in ticket.failing_checks)
            raise AppException(
                AppErrorCode.CHECKS_FAILED,
                f"Task {task.id}: {failing} still fails after the fix round, so the next task would build on "
                "untested code. The worker's summary in the ticket says why; fix the cause, then "
                f"cw resume {ticket.id}.",
            )
        ticket.tasks_done.append(task.id)
        write_ticket(ticket)
    return True


def _work(settings: Settings, project: Project, ticket: Ticket, latest_prompt: bool) -> None:
    """Do the user's latest request when asked, then any unfinished planned tasks; the checks run after each."""
    ticket.status = TicketStatus.IN_PROGRESS
    write_ticket(ticket)
    if latest_prompt and not _direct_pass(settings, project, ticket):
        return
    if _has_unfinished_tasks(ticket):
        if not _task_passes(settings, project, ticket):
            return
    else:
        _checks_with_fixes(settings, project, ticket)
    ticket.status = TicketStatus.READY


def _open_ticket_on(project: Project) -> str | None:
    """Return the open ticket whose branch the checkout is on, if any."""
    branch = git.current_branch(project.path)
    for summary in list_tickets():
        if summary.project == project.name and not summary.archived and summary.branch == branch:
            return summary.id
    return None


def _create_ticket(
    settings: Settings,
    project: Project,
    prompt: str,
    plan_first: bool,
    new_project: bool,
    base: str | None,
    previous_branch: str | None = None,
) -> Ticket:
    """Create a ticket on a new branch from the base (or the branch you name) and record the checks' baseline.

    When the containers do not start, the new branch is removed again and the checkout goes back where it was.
    """
    checkout = project.path
    if not git.is_clean(checkout):
        open_ticket = _open_ticket_on(project)
        hint = f"open its PR or park it: cw park {open_ticket}" if open_ticket else "commit or stash them first"
        raise AppException(AppErrorCode.INVALID_STATE, f"{project.name} has uncommitted changes; {hint}.")
    title = make_title(prompt)
    ticket_id = new_ticket_id(title)
    current_branch = git.current_branch(checkout)
    target = base or project.base_branch
    start_point, fetch_warning = git.fetch_base(checkout, project.remote, target)
    now = utc_now()
    ticket = Ticket(
        id=ticket_id,
        project=project.name,
        title=title,
        status=TicketStatus.IN_PROGRESS,
        route=Route.PLANNED if plan_first else Route.DIRECT,
        branch=f"{BRANCH_PREFIX}{ticket_id}",
        base_branch=start_point,
        target_branch=target,
        previous_branch=previous_branch or current_branch,
        base_sha=git.git(checkout, "rev-parse", start_point),
        owner=git.user_name(checkout),
        toolkit_version=toolkit_version(settings),
        created_at=now,
        updated_at=now,
        prompts=[PromptEntry(at=now, text=prompt)],
        new_project=new_project,
    )
    _set_warning(ticket, FETCH_WARNING, fetch_warning)
    with project_lock(project.name, ticket.id):
        git.switch_new(checkout, ticket.branch, start_point)
        logger.info("Ticket %s on branch %s in %s (was on %s)", ticket.id, ticket.branch, checkout, current_branch)
        try:
            _record_baseline(project, ticket)
        except AppException:
            git.delete_uncommitted_changes(checkout)
            git.switch(checkout, current_branch)
            git.delete_branch(checkout, ticket.branch)
            raise
        if not new_project:
            repo_map.write_project_repo_map(checkout, repo_map_path(project.name))
            _add_context_warnings(ticket, context_engine.refresh_at_start(settings, project, start_point))
        write_ticket(ticket)
    return ticket


def start_ticket(
    project_name: str, prompt: str, plan_first: bool, new_project: bool = False, base: str | None = None
) -> Ticket:
    """Create a ticket on a new branch from the base (or the branch you name), then plan or work on it."""
    settings = get_settings()
    project = settings.project(project_name)
    ticket = _create_ticket(settings, project, prompt, plan_first, new_project, base)
    if plan_first:
        _guarded(project, ticket, Step.PLAN, lambda: _plan(settings, project, ticket, None))
    else:
        _guarded(project, ticket, Step.WORK, lambda: _work(settings, project, ticket, latest_prompt=True))
    return ticket


def split_ticket(ticket_id: str) -> list[Ticket]:
    """Create one ticket per change the planner split out, then abandon the original.

    No agent runs here: each new ticket waits, paused before its plan, until you start it with `cw resume`.
    The original is abandoned only once every new ticket exists.
    """
    ticket = find_ticket(ticket_id)
    if not ticket.split:
        raise AppException(AppErrorCode.INVALID_STATE, f"{ticket.id} has nothing to split.")
    settings, project, checkout = _context(ticket)
    current_branch = git.current_branch(checkout)
    created = []
    for prompt in ticket.split:
        new_ticket = _create_ticket(
            settings, project, prompt, True, False, ticket.target_branch or None, ticket.previous_branch
        )
        git.switch(checkout, current_branch)
        _stop(new_ticket, Step.PLAN, TicketStatus.PAUSED, f"Split from {ticket.id}: cw resume {new_ticket.id}.")
        created.append(new_ticket)
    finish(ticket.id, abandon=True)
    return created


def _answer_from_user(ticket: Ticket, text: str) -> None:
    """Write the user's reply into the ticket as the answer to what the worker asked."""
    for entry in ticket.qa:
        if not entry.answer:
            entry.answer, entry.answered_by = text, USER
    ticket.prompts[-1].is_answer = True
    ticket.questions = []
    ticket.asking_worker = ""


def follow_up(ticket_id: str, prompt: str) -> Ticket:
    """Add a prompt to a ticket: answer a question, revise the plan, or ask for more work."""
    settings = get_settings()
    ticket = find_ticket(ticket_id)
    if ticket.status in CLOSED_STATUSES:
        raise AppException(AppErrorCode.INVALID_STATE, f"{ticket.id} is {ticket.status}.")
    _, project, _ = _context(ticket)
    ticket.prompts.append(PromptEntry(at=utc_now(), text=prompt))
    if ticket.status == TicketStatus.NEEDS_INPUT and ticket.asking_worker:
        asked_during_a_task = all(entry.task for entry in ticket.qa if not entry.answer)
        _answer_from_user(ticket, prompt)
        latest_prompt = not (asked_during_a_task and _has_unfinished_tasks(ticket))
        _guarded(project, ticket, Step.WORK, lambda: _work(settings, project, ticket, latest_prompt))
    elif ticket.status in PLAN_FEEDBACK_STATUSES:
        _guarded(project, ticket, Step.PLAN, lambda: _plan(settings, project, ticket, prompt))
    elif ticket.status in FOLLOW_UP_STATUSES:
        _guarded(project, ticket, Step.WORK, lambda: _work(settings, project, ticket, latest_prompt=True))
    else:
        raise AppException(AppErrorCode.INVALID_STATE, f"{ticket.id} is {ticket.status}.")
    return ticket


def approve(ticket_id: str) -> Ticket:
    """Approve the plan and work through its tasks."""
    settings = get_settings()
    ticket = find_ticket(ticket_id)
    if ticket.status != TicketStatus.AWAITING_APPROVAL:
        raise AppException(AppErrorCode.INVALID_STATE, f"{ticket.id} is {ticket.status}, not awaiting approval.")
    _, project, _ = _context(ticket)
    _guarded(project, ticket, Step.WORK, lambda: _work(settings, project, ticket, latest_prompt=False))
    return ticket


def _last_round(ticket: Ticket) -> list[ReviewEntry]:
    """Return the reviews of the latest round."""
    if not ticket.reviews:
        return []
    latest = ticket.reviews[-1].round
    return [entry for entry in ticket.reviews if entry.round == latest]


def _minor_findings(entries: list[ReviewEntry]) -> list[ReviewComment]:
    """Return the minor findings of these reviews."""
    return [comment for entry in entries for comment in entry.comments if comment.severity == Severity.MINOR]


def _anchored(project: Project, comments: list[ReviewComment], changed: list[str]) -> list[ReviewComment]:
    """Drop findings on files that neither exist nor changed, and move a line past the end onto the file's last line."""
    kept = []
    for comment in comments:
        path = project.path / comment.path
        if not path.is_file():
            if comment.path in changed:
                kept.append(comment)
            else:
                logger.warning("Dropped a finding on %s, which is not in the checkout.", comment.path)
            continue
        last_line = max(len(path.read_text(encoding="utf-8", errors="replace").splitlines()), 1)
        kept.append(comment.model_copy(update={"line": min(max(comment.line, 1), last_line)}))
    return kept


def _numbered(ticket: Ticket, comments: list[ReviewComment]) -> list[ReviewComment]:
    """Give each new finding a ticket-wide id (F1, F2, ...), by which a later review says it is fixed."""
    count = sum(len(entry.comments) for entry in ticket.reviews)
    return [
        comment.model_copy(update={"id": f"{FINDING_PREFIX}{number}"})
        for number, comment in enumerate(comments, start=count + 1)
    ]


def _review(
    settings: Settings,
    project: Project,
    ticket: Ticket,
    paths: tuple[str, ...] = (),
    fix_rounds: int | None = None,
) -> None:
    """Review the change, let the worker fix blocking findings, and review the fix; then report what is left.

    cw decides from the severities, not from the model's verdict. A blocking finding stays open until a later review
    names it as fixed, so reviewing only the new diff never drops it. `paths` limits the review to those files, and
    `fix_rounds` replaces `max_review_rounds`. Every review after the ticket's first runs on `reviewer_followup`.
    """
    limits = project.profile.limits
    rounds = limits.max_review_rounds if fix_rounds is None else fix_rounds
    for round_index in range(rounds + 1):
        tree = git.snapshot(project.path)
        start = ticket.last_reviewed_tree or ticket.base_sha
        full_diff = git.diff(project.path, start, tree, *paths)
        if not full_diff.strip():
            break
        diff = full_diff[: limits.max_diff_chars]
        previous = _last_round(ticket)
        next_round = (previous[0].round + 1) if previous else 1
        changed = git.changed_files(project.path, start, tree, *paths)
        still_open = list(ticket.open_findings)
        resolved: set[str] = set()
        new_blocking = []
        model = project.profile.models.reviewer_followup if ticket.reviews else None
        for reviewer in project.profile.reviewers:
            review_result, usage = run_agent(
                settings,
                project,
                reviewer,
                AgentRole.REVIEWER,
                prompts.reviewer_prompt(project, ticket, diff, len(full_diff) > len(diff), still_open),
                project.path,
                ReviewerResult,
                ticket.id,
                files=changed,
                new_project=ticket.new_project,
                model=model,
            )
            ticket.runs.append(usage)
            comments = _numbered(ticket, _anchored(project, review_result.comments, changed))
            blocking = [comment for comment in comments if comment.severity == Severity.BLOCKING]
            resolved.update(review_result.resolved)
            new_blocking += blocking
            ticket.reviews.append(
                ReviewEntry(
                    at=utc_now(),
                    round=next_round,
                    reviewer=reviewer,
                    verdict=Verdict.CHANGES if blocking else Verdict.APPROVE,
                    summary=review_result.summary,
                    comments=comments,
                )
            )
        ticket.open_findings = [finding for finding in still_open if finding.id not in resolved] + new_blocking
        ticket.last_reviewed_tree = tree
        write_ticket(ticket)
        if not ticket.open_findings or round_index == rounds:
            break
        logger.info("Blocking findings are open; fix round %s of %s.", round_index + 1, rounds)
        findings = ticket.open_findings + _minor_findings(_last_round(ticket))
        _fix(settings, project, ticket, prompts.fix_review_instruction(findings))
        _checks_with_fixes(settings, project, ticket)
    left = len(ticket.open_findings)
    _set_warning(ticket, REVIEW_WARNING, f"{left} blocking findings left after the fix round." if left else None)


def _resolve_conflicts(settings: Settings, project: Project, ticket: Ticket, base_ref: str) -> list[str]:
    """Let the worker resolve a stopped merge; cw stages the result, commits the merge and returns the files."""
    checkout = project.path
    files = git.conflicted_files(checkout)
    if not files:
        git.abort_merge(checkout)
        raise AppException(
            AppErrorCode.GIT_FAILED, f"Merging {base_ref} failed without conflicts; try `git merge {base_ref}` by hand."
        )
    limits = project.profile.limits
    conflicted = files
    for attempt in range(1, limits.max_conflict_rounds + 1):
        logger.info("Merge conflicts in %s; attempt %s of %s.", ", ".join(files), attempt, limits.max_conflict_rounds)
        _fix(settings, project, ticket, prompts.conflict_instruction(files, base_ref))
        files = git.files_with_conflict_markers(checkout, files)
        if not files:
            git.conclude_merge(checkout, MERGE_COMMIT_MESSAGE)
            return conflicted
    git.abort_merge(checkout)
    raise AppException(AppErrorCode.MERGE_CONFLICT, f"Merging {base_ref}.")


def _sync_base(settings: Settings, project: Project, ticket: Ticket) -> list[str]:
    """Merge the latest base branch into the ticket branch; return the files whose conflicts the worker resolved.

    A clean merge brings only the base's own, already reviewed work, so the review moves past it.
    """
    checkout = project.path
    base_ref, fetch_warning = git.fetch_base(checkout, project.remote, _target(project, ticket))
    _set_warning(ticket, FETCH_WARNING, fetch_warning)
    if fetch_warning and git.remote_url(checkout, project.remote):
        return []
    before = git.snapshot(checkout)
    if not git.merge(checkout, base_ref):
        return _resolve_conflicts(settings, project, ticket, base_ref)
    if ticket.last_reviewed_tree == before:
        ticket.last_reviewed_tree = git.snapshot(checkout)
    return []


def _require_changes(project: Project, ticket: Ticket) -> None:
    """Refuse to review or open a pull request for a ticket that changed nothing."""
    unchanged = not git.diff_stat(project.path, ticket.base_sha, git.snapshot(project.path))
    if ticket.pushed_at is None and unchanged:
        raise AppException(AppErrorCode.INVALID_STATE, f"{ticket.id} has no changes yet.")


def _review_only(settings: Settings, project: Project, ticket: Ticket) -> None:
    """Check and review the change without committing anything."""
    _require_changes(project, ticket)
    ticket.status = TicketStatus.IN_REVIEW
    write_ticket(ticket)
    _checks_with_fixes(settings, project, ticket)
    _review(settings, project, ticket)
    ticket.status = TicketStatus.READY


def _commit_message(ticket: Ticket) -> str:
    """Return the message of the ticket's one commit, and of its squash merge."""
    return ticket.commit_message or f"feat: {ticket.title}"


def _review_skip_reason(project: Project, since_tree: str, failures: list[tuple[str, str]]) -> str:
    """Return why a comment-only fix round may skip its review, or an empty string when it must be reviewed."""
    limit = project.profile.limits.review_skip_max_changed_lines
    if not limit or failures:
        return ""
    counts = git.changed_line_counts(project.path, since_tree, git.snapshot(project.path))
    if any(count is None or project.is_protected(path) for count, path in counts):
        return ""
    total = sum(count for count, _ in counts)
    if total > limit:
        return ""
    return f"the round changed {total} line(s) (limit {limit}), addressed no blocking finding, and the checks pass"


def _open_pull_request(
    settings: Settings,
    project: Project,
    ticket: Ticket,
    review_fix_rounds: int | None = None,
    small_fix_since: str | None = None,
) -> str:
    """Check and review the change, commit it as one commit, then push and open the pull request.

    `cw pr` always reviews, and its findings go on the pull request. A `cw fix` round passes `review_fix_rounds` 0,
    so it reviews once, and `small_fix_since` when it addressed only comments: then a small change that passes the
    checks skips the review, and the reason is returned. A project without a remote stops after the commit:
    `cw done` merges the branch into the base.
    """
    checkout = project.path
    target = _target(project, ticket)
    _require_changes(project, ticket)
    ticket.status = TicketStatus.IN_REVIEW
    write_ticket(ticket)
    failures = _checks_with_fixes(settings, project, ticket)
    skipped = _review_skip_reason(project, small_fix_since, failures) if small_fix_since else ""
    if skipped:
        logger.info("Review skipped: %s.", skipped)
    else:
        _review(settings, project, ticket, fix_rounds=review_fix_rounds)
    message = _commit_message(ticket)
    if ticket.pushed_at is None and not git.has_merge_commits(checkout, ticket.base_sha):
        git.squash_onto(checkout, ticket.base_sha, message)
    else:
        git.commit_all(checkout, message)
    logger.info("Committed: %s", message)
    resolved = _sync_base(settings, project, ticket)
    if resolved:
        logger.info("Checking and reviewing the conflict resolution in %s.", ", ".join(resolved))
        _checks_with_fixes(settings, project, ticket)
        _review(settings, project, ticket, tuple(resolved), review_fix_rounds)
        git.commit_all(checkout, message)
    ticket.status = TicketStatus.PR_OPEN
    if not git.remote_url(checkout, project.remote):
        merge_hint = f"`cw done {ticket.id}` merges it into {target}."
        _set_warning(ticket, LOCAL_WARNING, f"No remote: the change is committed on {ticket.branch}. {merge_hint}")
        return skipped
    git.push(checkout, project.remote, ticket.branch)
    ticket.pushed_at = utc_now()
    logger.info("Pushed %s to %s.", ticket.branch, project.remote)
    host = setup_git_host(settings, project, checkout)
    final = _last_round(ticket)
    first_post = ticket.pr_number is None
    if first_post:
        pull_request = host.create_pull_request(
            ticket.branch, target, ticket.title, prompts.pull_request_body(ticket, final)
        )
        ticket.pr_number = pull_request.number
        ticket.pr_url = pull_request.url
        logger.info("Opened %s", pull_request.url)
    if final and final[0].round > ticket.posted_review_round:
        shown = {comment.id for entry in final for comment in entry.comments}
        earlier = [finding for finding in ticket.open_findings if finding.id not in shown]
        for index, entry in enumerate(final):
            summary = f"**{entry.reviewer}** · {entry.verdict}\n\n{entry.summary}"
            comments = entry.comments + (earlier if index == 0 else [])
            if first_post:
                host.post_review(ticket.pr_number, summary, comments)
            else:
                host.post_review(ticket.pr_number, _join_findings(summary, comments), [])
        ticket.posted_review_round = final[0].round
    return skipped


def _join_findings(summary: str, comments: list[ReviewComment]) -> str:
    """Return a review summary with its findings listed in the text, for a review posted without new threads."""
    listed = "\n".join(f"- `{comment.path}:{comment.line}` {finding_text(comment)}" for comment in comments)
    return f"{summary}\n\n{listed}" if listed else summary


def _for_review(ticket_id: str) -> tuple[Settings, Project, Ticket]:
    """Return a ticket that can be reviewed, with its settings and project."""
    ticket = find_ticket(ticket_id)
    if ticket.status not in PR_STATUSES:
        raise AppException(AppErrorCode.INVALID_STATE, f"{ticket.id} is {ticket.status}.")
    settings, project, _ = _context(ticket)
    return settings, project, ticket


def review(ticket_id: str) -> Ticket:
    """Run the checks and the reviewer on the ticket's change; nothing is committed or pushed."""
    settings, project, ticket = _for_review(ticket_id)
    _guarded(project, ticket, Step.REVIEW, lambda: _review_only(settings, project, ticket))
    return ticket


def open_pull_request(ticket_id: str) -> Ticket:
    """Review the ticket's work, then turn it into a pull request."""
    settings, project, ticket = _for_review(ticket_id)
    _guarded(project, ticket, Step.PR, lambda: _open_pull_request(settings, project, ticket))
    return ticket


def _open_pull_request_of(ticket_id: str) -> tuple[Settings, Project, Ticket]:
    """Return a ticket that has an open pull request, with its settings and project."""
    ticket = find_ticket(ticket_id)
    if ticket.pr_number is None or ticket.status in CLOSED_STATUSES:
        raise AppException(AppErrorCode.INVALID_STATE, f"{ticket.id} has no open pull request.")
    settings, project, _ = _context(ticket)
    return settings, project, ticket


def _carries(thread: PullRequestThread, finding: ReviewComment) -> bool:
    """Return whether a pull request thread is the posted copy of this finding: by its id label, or its text."""
    return (bool(finding.id) and finding_tag(finding.id) in thread.body) or finding.body in thread.body


def _is_open_finding(ticket: Ticket, thread: PullRequestThread) -> bool:
    """Return whether a pull request thread carries one of the ticket's open blocking findings."""
    return any(_carries(thread, finding) for finding in ticket.open_findings)


def _fix_pull_request(settings: Settings, project: Project, ticket: Ticket) -> None:
    """Address the open comments and blocking findings, then check, review once, push and answer each thread; repeat.

    Each pass's review is posted as a comment, not as new threads, so cw does not feed its own reviews back as
    comments. A thread is resolved only when the worker fixed it; a declined one gets the reason as a reply and stays
    open for a person, and it is not sent again until someone replies after cw. A round that addressed only comments
    may skip its review (`review_skip_max_changed_lines`). The loop goes on while blocking findings stay open, up to
    `max_pull_request_fix_rounds`, and a pass in which the worker changed nothing ends it.
    """
    host = setup_git_host(settings, project, project.path)
    rounds = project.profile.limits.max_pull_request_fix_rounds
    for round_number in range(1, rounds + 1):
        unresolved = host.open_threads(ticket.pr_number)
        findings = [finding for finding in ticket.open_findings if not any(_carries(t, finding) for t in unresolved)]
        threads = [thread for thread in unresolved if not thread.last_comment.startswith(DECLINE_REPLY_PREFIX)]
        if not threads and not findings:
            logger.info("No open comments or blocking findings on %s.", ticket.pr_url)
            return
        logger.info(
            "Fix round %s of %s: %s comment(s) and %s finding(s) go to worker %s.",
            round_number,
            rounds,
            len(threads),
            len(findings),
            ticket.last_worker,
        )
        ticket.pull_request_fix_rounds += 1
        addressed_blocking = bool(findings) or any(_is_open_finding(ticket, thread) for thread in threads)
        tree_before = git.snapshot(project.path)
        worker_result = _fix(settings, project, ticket, prompts.pull_request_comments_instruction(threads, findings))
        changed = git.snapshot(project.path) != tree_before
        skipped = _open_pull_request(
            settings, project, ticket, review_fix_rounds=0, small_fix_since=None if addressed_blocking else tree_before
        )
        note = f"\n\nReview skipped: {skipped}." if skipped else ""
        host.post_review(
            ticket.pr_number,
            f"**{ticket.last_worker}** · {len(threads)} comment(s)\n\n{worker_result.summary}{note}",
            [],
        )
        outcomes = {outcome.thread_id: outcome for outcome in worker_result.comment_outcomes}
        for thread in threads:
            outcome = outcomes.get(thread.id)
            if outcome is None:
                continue
            if outcome.outcome == CommentOutcomeKind.FIXED:
                host.resolve_thread(ticket.pr_number, thread.id)
            else:
                host.reply_to_thread(ticket.pr_number, thread.id, f"{DECLINE_REPLY_PREFIX} {outcome.reason}")
        if not changed:
            logger.info("Worker %s changed nothing this round; stopping.", ticket.last_worker)
            return


def fix_pull_request(ticket_id: str) -> Ticket:
    """Address the pull request's open comments, from the reviewer agent and from people, and push the fix."""
    settings, project, ticket = _open_pull_request_of(ticket_id)
    _guarded(project, ticket, Step.PR, lambda: _fix_pull_request(settings, project, ticket))
    return ticket


def merge_pull_request(ticket_id: str) -> Ticket:
    """Squash-merge the pull request once its CI passed and no blocking finding is left, then close the ticket."""
    settings, project, ticket = _open_pull_request_of(ticket_id)
    checkout = project.path
    if ticket.status != TicketStatus.PR_OPEN:
        raise AppException(AppErrorCode.INVALID_STATE, f"{ticket.id} is {ticket.status}; finish it first.")
    if is_running(ticket):
        raise AppException(AppErrorCode.TICKET_BUSY, ticket.id)
    if git.current_branch(checkout) == ticket.branch and not git.is_clean(checkout):
        raise AppException(AppErrorCode.INVALID_STATE, f"{ticket.branch} has uncommitted changes.")
    left = ticket.open_findings
    if left:
        raise AppException(
            AppErrorCode.INVALID_STATE,
            f"{len(left)} blocking review finding(s) left: cw fix {ticket.id}, or merge on the host yourself.",
        )
    host = setup_git_host(settings, project, checkout)
    unresolved = host.open_threads(ticket.pr_number)
    if unresolved:
        declined = [thread for thread in unresolved if thread.last_comment.startswith(DECLINE_REPLY_PREFIX)]
        waiting = [thread for thread in unresolved if thread not in declined]
        groups = [
            f"{label}: " + ", ".join(f"{thread.path}:{thread.line}" for thread in group[:THREADS_SHOWN])
            for label, group in (("declined by the worker, for you to decide", declined), ("not addressed", waiting))
            if group
        ]
        raise AppException(
            AppErrorCode.INVALID_STATE,
            f"{len(unresolved)} unresolved pull request thread(s) ({'; '.join(groups)}): cw fix {ticket.id}, "
            "resolve them on the host, or merge on the host yourself.",
        )
    sha = git.git(checkout, "rev-parse", ticket.branch)
    checks_expected = bool(git.git(checkout, "ls-tree", "--name-only", sha, "--", *CI_CONFIG_PATHS))
    if not host.ci_passed(ticket.pr_number, sha, checks_expected):
        raise AppException(
            AppErrorCode.INVALID_STATE,
            f"CI has not passed on {ticket.pr_url} at {sha} (failed, pending or not reported yet, or the pull "
            f"request is not at your local {ticket.branch}): cw ci {ticket.id}.",
        )
    target = _target(project, ticket)
    ticket.merge_commit = host.merge(ticket.pr_number, ticket.branch, _commit_message(ticket), sha)
    write_ticket(ticket)
    logger.info("Merged %s into %s.", ticket.pr_url, target)
    if not git.fast_forward(checkout, project.remote, target):
        logger.warning(
            "Your local %s could not be fast-forwarded to %s/%s; pull it yourself.", target, project.remote, target
        )
    finish(ticket.id, abandon=False)
    git.delete_branch(checkout, ticket.branch)
    return find_ticket(ticket.id)


def _step_to_resume(ticket: Ticket) -> Step | None:
    """Return the step to run again: the recorded one, or the one a killed process left unfinished."""
    if ticket.status in RESUMABLE_STATUSES:
        return ticket.paused_step
    if ticket.status not in STALE_STATUSES or is_running(ticket):
        return None
    if ticket.paused_step:
        return ticket.paused_step
    if ticket.status == TicketStatus.IN_REVIEW:
        return Step.REVIEW
    still_planning = ticket.route == Route.PLANNED and not ticket.subtasks
    return Step.PLAN if still_planning else Step.WORK


def resume(ticket_id: str) -> Ticket:
    """Re-run the step that paused, blocked or was killed; a task list continues where it stopped."""
    settings = get_settings()
    ticket = find_ticket(ticket_id)
    step = _step_to_resume(ticket)
    if step is None:
        raise AppException(AppErrorCode.INVALID_STATE, f"{ticket.id} is {ticket.status}; nothing to resume.")
    _, project, _ = _context(ticket)
    match step:
        case Step.PLAN:
            _guarded(project, ticket, step, lambda: _plan(settings, project, ticket, None))
        case Step.WORK:
            latest_prompt = not _has_unfinished_tasks(ticket)
            _guarded(project, ticket, step, lambda: _work(settings, project, ticket, latest_prompt))
        case Step.REVIEW:
            _guarded(project, ticket, step, lambda: _review_only(settings, project, ticket))
        case Step.PR:
            _guarded(project, ticket, step, lambda: _open_pull_request(settings, project, ticket))
    return ticket


def park(ticket_id: str) -> Ticket:
    """Save the ticket's uncommitted work as a WIP commit and switch back to the previous branch."""
    ticket = find_ticket(ticket_id)
    _, project, checkout = _context(ticket)
    with project_lock(project.name, ticket.id):
        if git.current_branch(checkout) != ticket.branch:
            raise AppException(AppErrorCode.INVALID_STATE, f"{project.name} is not on {ticket.branch}.")
        if git.commit_all(checkout, f"{PARK_COMMIT_PREFIX}{ticket.title}"):
            logger.info("Parked %s as a WIP commit; it is folded into the final commit at PR time.", ticket.id)
        git.switch(checkout, ticket.previous_branch)
    return ticket


def ci_status(ticket_id: str) -> str:
    """Return the CI status of the ticket's pull request."""
    settings = get_settings()
    ticket = find_ticket(ticket_id)
    if ticket.pr_number is None:
        raise AppException(AppErrorCode.INVALID_STATE, f"{ticket.id} has no pull request yet.")
    _, project, checkout = _context(ticket)
    host = setup_git_host(settings, project, checkout)
    return host.ci_status(ticket.pr_number, git.git(checkout, "rev-parse", ticket.branch))


def _merge_locally(project: Project, ticket: Ticket) -> None:
    """Merge a reviewed, committed ticket branch into its base in a project that has no remote."""
    checkout = project.path
    target = _target(project, ticket)
    git.switch(checkout, target)
    if not git.merge(checkout, ticket.branch):
        git.abort_merge(checkout)
        raise AppException(AppErrorCode.MERGE_CONFLICT, f"Merge {ticket.branch} into {target} by hand.")
    logger.info("Merged %s into %s.", ticket.branch, target)


def finish(ticket_id: str, abandon: bool) -> None:
    """Close the ticket and switch back to the previous branch; abandoning discards its work and branch.

    A ticket that `cw pr` committed in a project without a remote is merged into its base branch first.
    """
    ticket = find_ticket(ticket_id)
    if ticket.status in CLOSED_STATUSES:
        raise AppException(AppErrorCode.INVALID_STATE, f"{ticket.id} is already {ticket.status}.")
    if is_running(ticket):
        raise AppException(AppErrorCode.TICKET_BUSY, ticket.id)
    settings, project, checkout = _context(ticket)
    target = _target(project, ticket)
    with project_lock(project.name, ticket.id):
        if not abandon:
            ticket.changed_files = _own_changes(project, ticket) or ticket.changed_files
        if git.current_branch(checkout) == ticket.branch:
            if abandon:
                git.delete_uncommitted_changes(checkout)
            elif not git.is_clean(checkout):
                raise AppException(AppErrorCode.INVALID_STATE, f"{ticket.branch} has uncommitted changes.")
            git.switch(checkout, ticket.previous_branch)
        if abandon:
            git.delete_branch(checkout, ticket.branch)
        elif ticket.status == TicketStatus.PR_OPEN and ticket.pr_number is None:
            _merge_locally(project, ticket)
            ticket.merge_commit = git.resolve_commit(checkout, target)
        if not abandon:
            repo_map.write_project_repo_map(checkout, repo_map_path(project.name))
            _add_context_warnings(ticket, context_engine.refresh_at_finish(settings, project, ticket, target))
    ticket.status = TicketStatus.ABANDONED if abandon else TicketStatus.DONE
    write_ticket(ticket)
    storage.prune_event_logs(settings, project.name)
    logger.info("%s is %s; back on %s.", ticket.id, ticket.status, ticket.previous_branch)
