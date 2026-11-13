"""The ticket pipeline: a branch in the project checkout, workers taking turns on the plan's tasks, checks,
a mandatory review, one commit and the pull request."""

import logging
import os
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel

from cw import git, prompts, repo_map
from cw.claude_runner import TICKET_PLACEHOLDER, run_agent
from cw.config import AgentRole, Project, Settings, get_settings, repo_map_path
from cw.errors import AppErrorCode, AppException
from cw.git_hosts.factory import setup_git_host
from cw.models import (
    FIRST_WORKER,
    PlannerResult,
    PromptEntry,
    QuestionEntry,
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
    find_ticket,
    is_running,
    list_tickets,
    make_title,
    new_ticket_id,
    project_lock,
    save_ticket,
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
ARTIFACTS_SHOWN = 5
REVIEW_WARNING = "Review:"
FETCH_WARNING = "Fetch:"
LOCAL_WARNING = "Local:"
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
    save_ticket(ticket)


def _guarded(project: Project, ticket: Ticket, step: Step, action: Callable[[], None]) -> None:
    """Run a step on the ticket's branch; whatever stops it, leave the ticket paused or blocked and resumable."""
    try:
        with project_lock(project.name, ticket.id):
            _enter(project, ticket)
            action()
            ticket.error = None
            ticket.paused_step = None
            save_ticket(ticket)
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
        return False, str(error)
    return code == 0, f"{stdout}{stderr}"


def _record_baseline(project: Project, ticket: Ticket) -> None:
    """Run the checks once on the untouched base; only checks that pass here are enforced later.

    A new project has no base to compare with, so every check is the ticket's own to make pass.
    """
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


def _run[T: BaseModel](
    settings: Settings,
    project: Project,
    ticket: Ticket,
    role: AgentRole,
    output_model: type[T],
    session: str | None,
    prompt: Callable[[bool], str],
) -> tuple[T, str]:
    """Run an agent, in the session it already has when there is one, and return its result and session.

    `prompt(fresh)` builds the prompt: the whole picture for a new session, only what is new for a resumed one.
    A session that can no longer be resumed is replaced by a new one.
    """
    agent = WORKER_AGENT if role == AgentRole.WORKER else PLANNER_AGENT
    if session:
        try:
            result, usage = run_agent(
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
            )
        except AppException as error:
            if error.error_code != AppErrorCode.AGENT_FAILED or error.usage is not None:
                raise
            logger.info("The %s's session could not be continued; starting a new one.", agent)
            session = None
    if not session:
        result, usage = run_agent(
            settings,
            project,
            agent,
            role,
            prompt(True),
            project.path,
            output_model,
            ticket.id,
            new_project=ticket.new_project,
        )
    ticket.runs.append(usage)
    return result, usage.session_id


def _worker(
    settings: Settings, project: Project, ticket: Ticket, worker: str, instruction: str, task: str | None
) -> WorkerResult:
    """Give a worker its next instruction and write what it did into the ticket."""
    tree_before = git.snapshot(project.path)
    result, session = _run(
        settings,
        project,
        ticket,
        AgentRole.WORKER,
        WorkerResult,
        ticket.worker_sessions.get(worker),
        lambda fresh: (
            prompts.fresh_worker_prompt(project, ticket, instruction, _changes_so_far(project, ticket))
            if fresh
            else prompts.resumed_worker_prompt(ticket, worker, instruction)
        ),
    )
    tree_after = git.snapshot(project.path)
    actual_files = git.changed_files(project.path, tree_before, tree_after)
    if usage := (ticket.runs[-1] if ticket.runs else None):
        if usage.input_tokens > WORKER_SESSION_TOKEN_CEILING:
            logger.info("Worker %s session reached %s tokens; refreshing next turn.", worker, usage.input_tokens)
            session = ""
    if session:
        ticket.worker_sessions[worker] = session
    else:
        ticket.worker_sessions.pop(worker, None)
    ticket.last_worker = worker
    if result.status != WorkerStatus.QUESTION:
        recorded_files = actual_files or result.files
        if task and result.status == WorkerStatus.DONE:
            current_subtask = next((subtask for subtask in ticket.subtasks if subtask.id == task), None)
            if current_subtask and current_subtask.files:
                unexpected = sorted(set(actual_files) - set(current_subtask.files))
                if unexpected:
                    message = f"Worker {worker} touched undeclared files: {', '.join(unexpected)}"
                    _set_warning(ticket, SCOPE_WARNING, message)
        ticket.work_log.append(
            WorkEntry(
                at=utc_now(),
                subtask=task,
                status=result.status,
                summary=result.summary,
                worker=worker,
                files=recorded_files,
                handoff=result.handoff,
            )
        )
        if result.commit_message and not ticket.commit_message:
            ticket.commit_message = result.commit_message.splitlines()[0]
    ticket.worker_seen[worker] = len(ticket.work_log)
    save_ticket(ticket)
    return result


def _ask(
    settings: Settings, project: Project, ticket: Ticket, worker: str, task: str | None, questions: list[str]
) -> bool:
    """Write a worker's questions into the ticket and let the planner answer; return False when the user must."""
    asked = [QuestionEntry(at=utc_now(), worker=worker, task=task, question=question) for question in questions]
    ticket.qa += asked
    if ticket.planner_session:
        logger.info("Worker %s asks the planner %s question(s).", worker, len(asked))
        answer, ticket.planner_session = _run(
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
    save_ticket(ticket)
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
    result = _worker(settings, project, ticket, worker, _with_answers(ticket, worker, instruction), task)
    for _ in range(project.profile.limits.max_question_rounds):
        if result.status != WorkerStatus.QUESTION:
            return result
        if not _ask(settings, project, ticket, worker, task, result.questions):
            return None
        result = _worker(settings, project, ticket, worker, _with_answers(ticket, worker, instruction), task)
    if result.status == WorkerStatus.QUESTION:
        result = _worker(settings, project, ticket, worker, prompts.no_more_questions_instruction(instruction), task)
    if result.status == WorkerStatus.QUESTION:
        raise AppException(AppErrorCode.AGENT_FAILED, f"Worker {worker} still asks: {'; '.join(result.questions)}")
    return result


def _fix(settings: Settings, project: Project, ticket: Ticket, instruction: str) -> None:
    """Send a fix (failing checks, review findings, conflicts) to the worker that worked last."""
    _worker(settings, project, ticket, ticket.last_worker or FIRST_WORKER, instruction, None)


def _checks_with_fixes(settings: Settings, project: Project, ticket: Ticket) -> None:
    """Run the enforced checks; let the worker fix what it broke, then report instead of blocking."""
    failures = _failing_checks(project, ticket)
    rounds = 0
    while failures and rounds < project.profile.limits.max_fix_rounds:
        rounds += 1
        logger.info("Checks broke; fix round %s of %s.", rounds, project.profile.limits.max_fix_rounds)
        _fix(settings, project, ticket, prompts.fix_checks_instruction(failures))
        failures = _failing_checks(project, ticket)
    ticket.failing_checks = [command for command, _ in failures]
    _set_warning(ticket, FAILING_WARNING, ", ".join(f"`{command}`" for command in ticket.failing_checks) or None)


def _assign_workers(subtasks: list[Subtask], max_workers: int) -> None:
    """Name the planner's workers W1, W2, ... in order of appearance; workers past the limit share the last one."""
    names = list(dict.fromkeys(task.worker for task in subtasks))
    assigned = {name: f"{WORKER_PREFIX}{min(number, max_workers)}" for number, name in enumerate(names, start=1)}
    for task in subtasks:
        task.worker = assigned[task.worker]


def _plan(settings: Settings, project: Project, ticket: Ticket, feedback: str | None) -> None:
    """Run the planner and move the ticket to questions, a split, or approval."""
    ticket.status = TicketStatus.IN_PROGRESS
    limits = project.profile.limits
    result, ticket.planner_session = _run(
        settings,
        project,
        ticket,
        AgentRole.PLANNER,
        PlannerResult,
        ticket.planner_session,
        lambda fresh: prompts.planner_prompt(project, ticket, feedback),
    )
    if len(result.split) > 1:
        ticket.split = result.split
        ticket.questions = [
            f"This is {len(result.split)} separate changes. Start each as its own ticket "
            "(own branch and PR), or reply to keep them together."
        ]
        ticket.status = TicketStatus.NEEDS_INPUT
        return
    ticket.split = []
    if result.questions and ticket.question_rounds < limits.max_question_rounds:
        ticket.questions = result.questions
        ticket.question_rounds += 1
        ticket.status = TicketStatus.NEEDS_INPUT
        return
    ticket.questions = []
    ticket.plan = result.plan
    ticket.options_considered = result.options_considered
    ticket.files = result.files
    ticket.context_pack = result.context_pack
    ticket.subtasks = result.subtasks or [
        Subtask(id="S1", title=ticket.title, instructions=result.plan, files=result.files)
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
    result = _work_on(settings, project, ticket, worker, prompts.direct_instruction(project, ticket), None)
    if result is None:
        return False
    if result.status == WorkerStatus.NEEDS_PLAN:
        logger.info("Worker escalated to the planner: %s", result.reason)
        ticket.route = Route.PLANNED
        _plan(settings, project, ticket, f"The worker escalated before editing: {result.reason}")
        return False
    if result.status == WorkerStatus.BLOCKED:
        raise AppException(AppErrorCode.AGENT_FAILED, f"Worker blocked: {result.reason}")
    return True


def _task_passes(settings: Settings, project: Project, ticket: Ticket) -> bool:
    """Run the plan's unfinished tasks in order, each by its worker; return False when a worker waits for the user."""
    while (task := next_task(ticket)) is not None:
        logger.info("Task %s · %s (worker %s)", task.id, task.title, task.worker)
        result = _work_on(settings, project, ticket, task.worker, prompts.task_instruction(task), task.id)
        if result is None:
            return False
        if result.status != WorkerStatus.DONE:
            raise AppException(AppErrorCode.AGENT_FAILED, f"Task {task.id} stopped: {result.reason}")
        ticket.tasks_done.append(task.id)
        save_ticket(ticket)
    return True


def _work(settings: Settings, project: Project, ticket: Ticket, latest_prompt: bool) -> None:
    """Do the user's latest request when asked, then any unfinished planned tasks, then the checks."""
    ticket.status = TicketStatus.IN_PROGRESS
    save_ticket(ticket)
    if latest_prompt and not _direct_pass(settings, project, ticket):
        return
    if not _task_passes(settings, project, ticket):
        return
    _checks_with_fixes(settings, project, ticket)
    ticket.status = TicketStatus.READY


def _open_ticket_on(project: Project) -> str | None:
    """Return the open ticket whose branch the checkout is on, if any."""
    branch = git.current_branch(project.path)
    for summary in list_tickets():
        if summary.project == project.name and not summary.archived and summary.branch == branch:
            return summary.id
    return None


def start_ticket(
    project_name: str, prompt: str, plan_first: bool, new_project: bool = False, base: str | None = None
) -> Ticket:
    """Create a ticket on a new branch from the base (or the branch you name), then plan or work on it."""
    settings = get_settings()
    project = settings.project(project_name)
    checkout = project.path
    if not git.is_clean(checkout):
        open_ticket = _open_ticket_on(project)
        hint = f"open its PR or park it: cw park {open_ticket}" if open_ticket else "commit or stash them first"
        raise AppException(AppErrorCode.INVALID_STATE, f"{project.name} has uncommitted changes; {hint}.")
    title = make_title(prompt)
    ticket_id = new_ticket_id(title)
    previous_branch = git.current_branch(checkout)
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
        previous_branch=previous_branch,
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
        logger.info("Ticket %s on branch %s in %s (was on %s)", ticket.id, ticket.branch, checkout, previous_branch)
        _record_baseline(project, ticket)
        if not new_project:
            repo_map.write_project_repo_map(checkout, repo_map_path(project.name))
        save_ticket(ticket)
    if plan_first:
        _guarded(project, ticket, Step.PLAN, lambda: _plan(settings, project, ticket, None))
    else:
        _guarded(project, ticket, Step.WORK, lambda: _work(settings, project, ticket, latest_prompt=True))
    return ticket


def split_ticket(ticket_id: str) -> list[Ticket]:
    """Start one ticket per change the planner split out, one after another, then abandon the original."""
    ticket = find_ticket(ticket_id)
    if not ticket.split:
        raise AppException(AppErrorCode.INVALID_STATE, f"{ticket.id} has nothing to split.")
    prompts_to_start = list(ticket.split)
    finish(ticket.id, abandon=True)
    started = []
    for prompt in prompts_to_start:
        try:
            new_ticket = start_ticket(ticket.project, prompt, plan_first=False, base=ticket.target_branch or None)
            park(new_ticket.id)
            started.append(new_ticket)
        except AppException as error:
            logger.error("Could not finish the ticket for %r: %s", prompt[:60], error)
    return started


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


def _blocking(entries: list[ReviewEntry]) -> list:
    """Return the blocking findings of reviews that asked for changes."""
    return [
        comment
        for entry in entries
        if entry.verdict == Verdict.CHANGES
        for comment in entry.comments
        if comment.severity == Severity.BLOCKING
    ]


def _review(settings: Settings, project: Project, ticket: Ticket) -> None:
    """Review the change, let the worker fix blocking findings, and review the fix; then report what is left."""
    limits = project.profile.limits
    for round_index in range(limits.max_review_rounds + 1):
        tree = git.snapshot(project.path)
        full_diff = git.diff(project.path, ticket.last_reviewed_tree or ticket.base_sha, tree)
        if not full_diff.strip():
            break
        diff = full_diff[: limits.max_diff_chars]
        previous = _last_round(ticket)
        next_round = (previous[0].round + 1) if previous else 1
        changed = git.changed_files(project.path, ticket.last_reviewed_tree or ticket.base_sha, tree)
        for reviewer in project.profile.reviewers:
            result, usage = run_agent(
                settings,
                project,
                reviewer,
                AgentRole.REVIEWER,
                prompts.reviewer_prompt(project, ticket, diff, len(full_diff) > len(diff), previous),
                project.path,
                ReviewerResult,
                ticket.id,
                files=changed,
                new_project=ticket.new_project,
            )
            ticket.runs.append(usage)
            ticket.reviews.append(
                ReviewEntry(
                    at=utc_now(),
                    round=next_round,
                    reviewer=reviewer,
                    verdict=result.verdict,
                    summary=result.summary,
                    comments=result.comments,
                )
            )
        ticket.last_reviewed_tree = tree
        save_ticket(ticket)
        blocking = _blocking(_last_round(ticket))
        if not blocking or round_index == limits.max_review_rounds:
            break
        logger.info("Reviewer asked for changes; fix round %s of %s.", round_index + 1, limits.max_review_rounds)
        _fix(settings, project, ticket, prompts.fix_review_instruction(blocking))
        _checks_with_fixes(settings, project, ticket)
    left = len(_blocking(_last_round(ticket)))
    _set_warning(ticket, REVIEW_WARNING, f"{left} blocking findings left after the fix round." if left else None)


def _resolve_conflicts(settings: Settings, project: Project, ticket: Ticket, base_ref: str) -> None:
    """Let the worker resolve a stopped merge; cw stages the result and commits the merge."""
    checkout = project.path
    files = git.conflicted_files(checkout)
    if not files:
        git.abort_merge(checkout)
        raise AppException(
            AppErrorCode.GIT_FAILED, f"Merging {base_ref} failed without conflicts; try `git merge {base_ref}` by hand."
        )
    limits = project.profile.limits
    for attempt in range(1, limits.max_conflict_rounds + 1):
        logger.info("Merge conflicts in %s; attempt %s of %s.", ", ".join(files), attempt, limits.max_conflict_rounds)
        _fix(settings, project, ticket, prompts.conflict_instruction(files, base_ref))
        files = git.files_with_conflict_markers(checkout, files)
        if not files:
            git.conclude_merge(checkout, MERGE_COMMIT_MESSAGE)
            return
    git.abort_merge(checkout)
    raise AppException(AppErrorCode.MERGE_CONFLICT, f"Merging {base_ref}.")


def _sync_base(settings: Settings, project: Project, ticket: Ticket) -> None:
    """Merge the latest base branch into the ticket branch, letting the worker resolve conflicts."""
    checkout = project.path
    base_ref, fetch_warning = git.fetch_base(checkout, project.remote, _target(project, ticket))
    _set_warning(ticket, FETCH_WARNING, fetch_warning)
    if fetch_warning and git.remote_url(checkout, project.remote):
        return
    before = git.snapshot(checkout)
    if not git.merge(checkout, base_ref):
        _resolve_conflicts(settings, project, ticket, base_ref)
    if ticket.last_reviewed_tree == before:
        ticket.last_reviewed_tree = git.snapshot(checkout)


def _require_changes(project: Project, ticket: Ticket) -> None:
    """Refuse to review or open a pull request for a ticket that changed nothing."""
    unchanged = not git.diff_stat(project.path, ticket.base_sha, git.snapshot(project.path))
    if ticket.pushed_at is None and unchanged:
        raise AppException(AppErrorCode.INVALID_STATE, f"{ticket.id} has no changes yet.")


def _review_only(settings: Settings, project: Project, ticket: Ticket) -> None:
    """Check and review the change without committing anything."""
    _require_changes(project, ticket)
    ticket.status = TicketStatus.IN_REVIEW
    save_ticket(ticket)
    _checks_with_fixes(settings, project, ticket)
    _review(settings, project, ticket)
    ticket.status = TicketStatus.READY


def _open_pull_request(settings: Settings, project: Project, ticket: Ticket) -> None:
    """Check and review the change, commit it as one commit, then push and open the pull request.

    The review always runs first and its findings go on the pull request; nothing skips it.
    A project without a remote stops after the commit: `cw done` merges the branch into the base.
    """
    checkout = project.path
    target = _target(project, ticket)
    _require_changes(project, ticket)
    ticket.status = TicketStatus.IN_REVIEW
    save_ticket(ticket)
    _checks_with_fixes(settings, project, ticket)
    _review(settings, project, ticket)
    message = ticket.commit_message or f"feat: {ticket.title}"
    if ticket.pushed_at is None and not git.has_merge_commits(checkout, ticket.base_sha):
        git.squash_onto(checkout, ticket.base_sha, message)
    else:
        git.commit_all(checkout, message)
    logger.info("Committed: %s", message)
    _sync_base(settings, project, ticket)
    ticket.status = TicketStatus.PR_OPEN
    if not git.remote_url(checkout, project.remote):
        merge_hint = f"`cw done {ticket.id}` merges it into {target}."
        _set_warning(ticket, LOCAL_WARNING, f"No remote: the change is committed on {ticket.branch}. {merge_hint}")
        return
    git.push(checkout, project.remote, ticket.branch)
    ticket.pushed_at = utc_now()
    logger.info("Pushed %s to %s.", ticket.branch, project.remote)
    host = setup_git_host(settings, project, checkout)
    final = _last_round(ticket)
    if ticket.pr_number is None:
        pull_request = host.create_pull_request(
            ticket.branch, target, ticket.title, prompts.pull_request_body(ticket, final)
        )
        ticket.pr_number = pull_request.number
        ticket.pr_url = pull_request.url
        logger.info("Opened %s", pull_request.url)
    if final and final[0].round > ticket.posted_review_round:
        for entry in final:
            summary = f"**{entry.reviewer}** · {entry.verdict}\n\n{entry.summary}"
            host.post_review(ticket.pr_number, summary, entry.comments)
        ticket.posted_review_round = final[0].round


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


def _step_to_resume(ticket: Ticket) -> Step | None:
    """Return the step to run again: the recorded one, or the one a killed process left unfinished."""
    if ticket.status in RESUMABLE_STATUSES:
        return ticket.paused_step
    if ticket.status not in STALE_STATUSES or is_running(ticket):
        return None
    if ticket.status == TicketStatus.IN_REVIEW:
        return Step.PR
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
    _, project, checkout = _context(ticket)
    with project_lock(project.name, ticket.id):
        if git.current_branch(checkout) == ticket.branch:
            if abandon:
                git.discard_changes(checkout)
            elif not git.is_clean(checkout):
                raise AppException(AppErrorCode.INVALID_STATE, f"{ticket.branch} has uncommitted changes.")
            git.switch(checkout, ticket.previous_branch)
        if abandon:
            git.delete_branch(checkout, ticket.branch)
        elif ticket.status == TicketStatus.PR_OPEN and ticket.pr_number is None:
            _merge_locally(project, ticket)
        if not abandon:
            repo_map.write_project_repo_map(checkout, repo_map_path(project.name))
    ticket.status = TicketStatus.ABANDONED if abandon else TicketStatus.DONE
    save_ticket(ticket)
    logger.info("%s is %s; back on %s.", ticket.id, ticket.status, ticket.previous_branch)
