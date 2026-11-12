"""The ticket pipeline: a branch in the project checkout, tasks one at a time, checks, review, one commit, PR."""

import logging
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

from cw import git, prompts
from cw.claude_runner import TICKET_PLACEHOLDER, run_agent
from cw.config import AgentRole, Project, Settings, get_settings
from cw.errors import AppErrorCode, AppException
from cw.git_hosts.factory import setup_git_host
from cw.models import (
    PlannerResult,
    PromptEntry,
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
MERGE_COMMIT_MESSAGE = "chore: merge base branch"
PARK_COMMIT_PREFIX = "wip: "
CHECKS_WARNING = "Checks:"
REVIEW_WARNING = "Review:"
FETCH_WARNING = "Fetch:"
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


def _context(ticket: Ticket) -> tuple[Settings, Project, Path]:
    """Return the settings, the ticket's project and its checkout."""
    settings = get_settings()
    project = settings.project(ticket.project)
    return settings, project, project.path


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


def _guarded(project: Project, ticket: Ticket, step: Step, action: Callable[[], None]) -> None:
    """Run a step on the ticket's branch; on a real failure record it as paused (usage limit) or blocked."""
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
        ticket.status = TicketStatus.PAUSED if error.error_code == AppErrorCode.USAGE_LIMIT else TicketStatus.BLOCKED
        ticket.paused_step = step
        ticket.error = str(error)
        save_ticket(ticket)
        raise


def _run_shell(project: Project, ticket_id: str, command: str) -> subprocess.CompletedProcess[str]:
    """Run a project command in the checkout with the project's environment."""
    environment = {key: value.replace(TICKET_PLACEHOLDER, ticket_id) for key, value in project.env.items()}
    return subprocess.run(
        command, shell=True, cwd=project.path, capture_output=True, text=True, env={**os.environ, **environment}
    )


def _check_commands(project: Project) -> list[tuple[str, str]]:
    """Return the configured checks as (name, command)."""
    checks = (("lint", project.commands.lint), ("test", project.commands.test))
    return [(name, command) for name, command in checks if command]


def _record_baseline(project: Project, ticket: Ticket) -> None:
    """Run the checks once on the untouched base; only checks that pass here are enforced later."""
    for name, command in _check_commands(project):
        logger.info("Baseline $ %s", command)
        ticket.baseline[name] = _run_shell(project, ticket.id, command).returncode == 0
        if not ticket.baseline[name]:
            ticket.warnings.append(
                f"{CHECKS_WARNING} `{command}` already fails on {ticket.base_branch}; it is reported, not enforced."
            )


def _checks(project: Project, ticket: Ticket) -> tuple[bool, str]:
    """Run the checks that passed on the base; return whether they still pass and the failing output."""
    for name, command in _check_commands(project):
        if not ticket.baseline.get(name, False):
            continue
        logger.info("$ %s", command)
        result = _run_shell(project, ticket.id, command)
        if result.returncode != 0:
            output = f"$ {command}\n{result.stdout}{result.stderr}"
            return False, output[-project.profile.limits.max_check_output_chars :]
    return True, ""


def _record_work(ticket: Ticket, result: WorkerResult, task: str | None) -> None:
    """Append a worker pass to the ticket and keep the first commit message."""
    ticket.work_log.append(WorkEntry(at=utc_now(), subtask=task, status=result.status, summary=result.summary))
    if result.commit_message and not ticket.commit_message:
        ticket.commit_message = result.commit_message.splitlines()[0]


def _worker(settings: Settings, project: Project, ticket: Ticket, prompt: str, task: str | None) -> WorkerResult:
    """Run one fresh worker in the checkout and record it."""
    result, usage = run_agent(
        settings, project, WORKER_AGENT, AgentRole.WORKER, prompt, project.path, WorkerResult, ticket.id
    )
    ticket.runs.append(usage)
    _record_work(ticket, result, task)
    save_ticket(ticket)
    return result


def _changes_so_far(project: Project, ticket: Ticket) -> str:
    """Return the files changed on the branch since it started, committed or not."""
    return git.diff_stat(project.path, ticket.base_sha, git.snapshot(project.path))


def _checks_with_fixes(settings: Settings, project: Project, ticket: Ticket) -> None:
    """Run the enforced checks; let the worker fix what it broke, then report instead of blocking."""
    passed, output = _checks(project, ticket)
    rounds = 0
    while not passed and rounds < project.profile.limits.max_fix_rounds:
        rounds += 1
        logger.info("Checks broke; fix round %s of %s.", rounds, project.profile.limits.max_fix_rounds)
        _worker(settings, project, ticket, prompts.fix_checks_prompt(ticket, project.profile.limits, output), None)
        passed, output = _checks(project, ticket)
    _set_warning(ticket, CHECKS_WARNING + " now failing", None if passed else output.splitlines()[0])


def _plan(settings: Settings, project: Project, ticket: Ticket, feedback: str | None) -> None:
    """Run the planner and move the ticket to questions, a split, or approval."""
    ticket.status = TicketStatus.IN_PROGRESS
    limits = project.profile.limits
    result, usage = run_agent(
        settings,
        project,
        PLANNER_AGENT,
        AgentRole.PLANNER,
        prompts.planner_prompt(ticket, limits, feedback),
        project.path,
        PlannerResult,
        ticket.id,
        resume_session=ticket.planner_session,
    )
    ticket.runs.append(usage)
    ticket.planner_session = usage.session_id
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
    ticket.subtasks = result.subtasks or [
        Subtask(id="S1", title=ticket.title, instructions=result.plan, files=result.files)
    ]
    ticket.tasks_done = []
    ticket.status = TicketStatus.AWAITING_APPROVAL


def next_task(ticket: Ticket) -> Subtask | None:
    """Return the first planned task that is not done yet."""
    return next((task for task in ticket.subtasks if task.id not in ticket.tasks_done), None)


def _work_tasks(settings: Settings, project: Project, ticket: Ticket) -> None:
    """Run the plan's tasks one at a time, each with a fresh worker, then the checks."""
    ticket.status = TicketStatus.IN_PROGRESS
    save_ticket(ticket)
    while (task := next_task(ticket)) is not None:
        logger.info("Task %s · %s", task.id, task.title)
        prompt = prompts.task_prompt(ticket, project.profile.limits, task, _changes_so_far(project, ticket))
        result = _worker(settings, project, ticket, prompt, task.id)
        if result.status != WorkerStatus.DONE:
            raise AppException(AppErrorCode.AGENT_FAILED, f"Task {task.id} stopped: {result.reason}")
        ticket.tasks_done.append(task.id)
        save_ticket(ticket)
    _checks_with_fixes(settings, project, ticket)
    ticket.status = TicketStatus.READY


def _work_direct(settings: Settings, project: Project, ticket: Ticket) -> None:
    """Run one worker on the latest prompt; escalate to the planner when the worker asks for it."""
    ticket.status = TicketStatus.IN_PROGRESS
    save_ticket(ticket)
    stat = _changes_so_far(project, ticket) if ticket.work_log else ""
    prompt = prompts.direct_worker_prompt(ticket, project.profile.limits, stat)
    result = _worker(settings, project, ticket, prompt, None)
    if result.status == WorkerStatus.NEEDS_PLAN:
        logger.info("Worker escalated to the planner: %s", result.reason)
        ticket.route = Route.PLANNED
        _plan(settings, project, ticket, f"The worker escalated before editing: {result.reason}")
        return
    if result.status == WorkerStatus.BLOCKED:
        raise AppException(AppErrorCode.AGENT_FAILED, f"Worker blocked: {result.reason}")
    _checks_with_fixes(settings, project, ticket)
    ticket.status = TicketStatus.READY


def _work(settings: Settings, project: Project, ticket: Ticket) -> None:
    """Resume work: continue the task list if one is unfinished, otherwise a direct worker pass."""
    if ticket.route == Route.PLANNED and next_task(ticket) is not None:
        _work_tasks(settings, project, ticket)
        return
    _work_direct(settings, project, ticket)


def _open_ticket_on(project: Project) -> str | None:
    """Return the open ticket whose branch the checkout is on, if any."""
    branch = git.current_branch(project.path)
    for summary in list_tickets():
        if summary.project == project.name and not summary.archived and summary.branch == branch:
            return summary.id
    return None


def start_ticket(project_name: str, prompt: str, plan_first: bool) -> Ticket:
    """Create a ticket on a new branch from the base, in the project checkout, then plan or work on it."""
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
    start_point, fetch_warning = git.fetch_base(checkout, project.remote, project.base_branch)
    now = utc_now()
    ticket = Ticket(
        id=ticket_id,
        project=project.name,
        title=title,
        status=TicketStatus.IN_PROGRESS,
        route=Route.PLANNED if plan_first else Route.DIRECT,
        branch=f"{BRANCH_PREFIX}{ticket_id}",
        base_branch=start_point,
        previous_branch=previous_branch,
        base_sha=git.git(checkout, "rev-parse", start_point),
        owner=git.user_name(checkout),
        toolkit_version=toolkit_version(settings),
        created_at=now,
        updated_at=now,
        prompts=[PromptEntry(at=now, text=prompt)],
    )
    _set_warning(ticket, FETCH_WARNING, fetch_warning)
    with project_lock(project.name, ticket.id):
        git.switch_new(checkout, ticket.branch, start_point)
        logger.info("Ticket %s on branch %s in %s (was on %s)", ticket.id, ticket.branch, checkout, previous_branch)
        _record_baseline(project, ticket)
        save_ticket(ticket)
    if plan_first:
        _guarded(project, ticket, Step.PLAN, lambda: _plan(settings, project, ticket, None))
    else:
        _guarded(project, ticket, Step.WORK, lambda: _work_direct(settings, project, ticket))
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
            new_ticket = start_ticket(ticket.project, prompt, plan_first=False)
            park(new_ticket.id)
            started.append(new_ticket)
        except AppException as error:
            logger.error("Could not finish the ticket for %r: %s", prompt[:60], error)
    return started


def follow_up(ticket_id: str, prompt: str) -> Ticket:
    """Add a prompt to a ticket: answer the planner, revise the plan, or run another worker pass."""
    settings = get_settings()
    ticket = find_ticket(ticket_id)
    if ticket.status in CLOSED_STATUSES:
        raise AppException(AppErrorCode.INVALID_STATE, f"{ticket.id} is {ticket.status}.")
    _, project, _ = _context(ticket)
    ticket.prompts.append(PromptEntry(at=utc_now(), text=prompt))
    if ticket.status in PLAN_FEEDBACK_STATUSES:
        _guarded(project, ticket, Step.PLAN, lambda: _plan(settings, project, ticket, prompt))
    elif ticket.status in FOLLOW_UP_STATUSES:
        _guarded(project, ticket, Step.WORK, lambda: _work_direct(settings, project, ticket))
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
    _guarded(project, ticket, Step.WORK, lambda: _work_tasks(settings, project, ticket))
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


def _review(settings: Settings, project: Project, ticket: Ticket) -> bool:
    """Review the change until approved or out of rounds; return whether it was approved."""
    limits = project.profile.limits
    for round_index in range(limits.max_review_rounds + 1):
        tree = git.snapshot(project.path)
        full_diff = git.diff(project.path, ticket.last_reviewed_tree or ticket.base_sha, tree)
        if not full_diff.strip():
            return not _blocking(_last_round(ticket))
        diff = full_diff[: limits.max_diff_chars]
        previous = _last_round(ticket)
        next_round = (previous[0].round + 1) if previous else 1
        for reviewer in project.profile.reviewers:
            result, usage = run_agent(
                settings,
                project,
                reviewer,
                AgentRole.REVIEWER,
                prompts.reviewer_prompt(ticket, diff, len(full_diff) > len(diff), previous),
                project.path,
                ReviewerResult,
                ticket.id,
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
        if not blocking:
            return True
        if round_index == limits.max_review_rounds:
            return False
        logger.info("Reviewer asked for changes; fix round %s of %s.", round_index + 1, limits.max_review_rounds)
        _worker(settings, project, ticket, prompts.fix_review_prompt(ticket, limits, blocking), None)
        _checks_with_fixes(settings, project, ticket)
    return False


def _sync_base(settings: Settings, project: Project, ticket: Ticket) -> None:
    """Merge the latest base branch into the ticket branch, letting the worker resolve conflicts."""
    base_ref, fetch_warning = git.fetch_base(project.path, project.remote, project.base_branch)
    _set_warning(ticket, FETCH_WARNING, fetch_warning)
    if fetch_warning or git.merge(project.path, base_ref):
        return
    limits = project.profile.limits
    for attempt in range(1, limits.max_conflict_rounds + 1):
        files = git.conflicted_files(project.path)
        logger.info("Merge conflicts in %s; attempt %s of %s.", ", ".join(files), attempt, limits.max_conflict_rounds)
        _worker(settings, project, ticket, prompts.conflict_prompt(ticket, limits, files, base_ref), None)
        if not git.conflicted_files(project.path):
            git.commit_all(project.path, MERGE_COMMIT_MESSAGE)
            return
    git.abort_merge(project.path)
    raise AppException(AppErrorCode.MERGE_CONFLICT, f"Merging {base_ref}.")


def _open_pull_request(settings: Settings, project: Project, ticket: Ticket, skip_review: bool) -> None:
    """Check and review the change, commit it as one commit, push, and open or update the pull request."""
    ticket.status = TicketStatus.IN_REVIEW
    save_ticket(ticket)
    _checks_with_fixes(settings, project, ticket)
    if not skip_review and not _review(settings, project, ticket):
        count = len(_blocking(_last_round(ticket)))
        _set_warning(ticket, REVIEW_WARNING, f"{count} blocking findings left; follow up, or open the PR anyway.")
        ticket.status = TicketStatus.READY
        return
    _set_warning(ticket, REVIEW_WARNING, None)
    message = ticket.commit_message or f"feat: {ticket.title}"
    if ticket.pushed_at is None:
        git.squash_onto(project.path, ticket.base_sha, message)
    else:
        git.commit_all(project.path, message)
    logger.info("Committed: %s", message)
    _sync_base(settings, project, ticket)
    git.push(project.path, project.remote, ticket.branch)
    ticket.pushed_at = utc_now()
    logger.info("Pushed %s to %s.", ticket.branch, project.remote)
    host = setup_git_host(settings, project, project.path)
    final = _last_round(ticket)
    if ticket.pr_number is None:
        pull_request = host.create_pull_request(
            ticket.branch, project.base_branch, ticket.title, prompts.pull_request_body(ticket, final)
        )
        ticket.pr_number = pull_request.number
        ticket.pr_url = pull_request.url
        logger.info("Opened %s", pull_request.url)
    for entry in final:
        host.post_review(ticket.pr_number, f"**{entry.reviewer}** · {entry.verdict}\n\n{entry.summary}", entry.comments)
    ticket.status = TicketStatus.PR_OPEN


def open_pull_request(ticket_id: str, skip_review: bool) -> Ticket:
    """Finish the ticket's work into a reviewed pull request."""
    settings = get_settings()
    ticket = find_ticket(ticket_id)
    if ticket.status not in PR_STATUSES:
        raise AppException(AppErrorCode.INVALID_STATE, f"{ticket.id} is {ticket.status}.")
    _, project, _ = _context(ticket)
    _guarded(project, ticket, Step.PR, lambda: _open_pull_request(settings, project, ticket, skip_review))
    return ticket


def resume(ticket_id: str) -> Ticket:
    """Re-run the step that paused or blocked the ticket; a task list continues where it stopped."""
    settings = get_settings()
    ticket = find_ticket(ticket_id)
    if ticket.status not in RESUMABLE_STATUSES or ticket.paused_step is None:
        raise AppException(AppErrorCode.INVALID_STATE, f"{ticket.id} is {ticket.status}; nothing to resume.")
    _, project, _ = _context(ticket)
    step = ticket.paused_step
    match step:
        case Step.PLAN:
            _guarded(project, ticket, step, lambda: _plan(settings, project, ticket, None))
        case Step.WORK:
            _guarded(project, ticket, step, lambda: _work(settings, project, ticket))
        case Step.PR:
            _guarded(project, ticket, step, lambda: _open_pull_request(settings, project, ticket, False))
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


def finish(ticket_id: str, abandon: bool) -> None:
    """Close the ticket and switch back to the previous branch; abandoning discards its work and branch."""
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
    ticket.status = TicketStatus.ABANDONED if abandon else TicketStatus.DONE
    save_ticket(ticket)
    logger.info("%s is %s; back on %s.", ticket.id, ticket.status, ticket.previous_branch)
