"""The task prompts cw sends to each agent. Agent roles and rules live in the toolkit's agent files."""

from cw.config import Limits, Project
from cw.models import ReviewComment, ReviewEntry, Subtask, Ticket


def _request(ticket: Ticket) -> str:
    """Return every prompt the user gave, oldest first."""
    lines = [f"{index}. {entry.text}" for index, entry in enumerate(ticket.prompts, start=1)]
    return "## Request (the user's prompts, latest last)\n\n" + "\n".join(lines)


def _plan(ticket: Ticket) -> str:
    """Return the approved plan, if there is one."""
    if not ticket.plan:
        return ""
    return f"## Approved plan\n\n{ticket.plan}"


def _work_so_far(ticket: Ticket) -> str:
    """Return the summaries of earlier worker passes."""
    if not ticket.work_log:
        return ""
    lines = [f"- [{entry.status}] {entry.summary}" for entry in ticket.work_log]
    return "## Work done so far\n\n" + "\n".join(lines)


def _limits(limits: Limits) -> str:
    """Return the limits the agent must respect."""
    return f"## Limits\n\nWrite at most {limits.summary_max_lines} lines in `summary`."


def _join(*sections: str) -> str:
    """Join the non-empty sections."""
    return "\n\n".join(section for section in sections if section)


def planner_prompt(ticket: Ticket, limits: Limits, feedback: str | None) -> str:
    """Return the planner's task."""
    questions_left = limits.max_question_rounds - ticket.question_rounds
    asking = (
        "You may ask clarifying questions if something blocks a correct plan."
        if questions_left > 0
        else "You may not ask more questions: make reasonable assumptions and list them at the top of the plan."
    )
    feedback_section = f"## User's answer or feedback on your last plan\n\n{feedback}" if feedback else ""
    return _join(
        _request(ticket),
        feedback_section,
        f"## Constraints\n\n- {asking}\n"
        "- Workers run your tasks one at a time, in order, each starting fresh from the files on disk. "
        "Most changes are one task; use more only when the work has clearly separate steps.\n"
        "- This ticket is exactly one branch and one pull request; nothing can create more. If the user wants "
        "several changes on separate branches or PRs, you must return them in `split` and not ask about it.",
        _limits(limits),
    )


def direct_worker_prompt(ticket: Ticket, limits: Limits, diff_stat: str) -> str:
    """Return a worker task that goes straight from the user's prompt."""
    escalate = (
        f"## Escalation\n\nIf this needs changes in more than {limits.escalate_file_threshold} files, "
        "or is too unclear to do correctly, return status needs_plan before editing."
        if not ticket.work_log and not ticket.plan
        else ""
    )
    changes = f"## Changes already on this branch\n\n```\n{diff_stat}\n```" if diff_stat else ""
    latest = f"## Do now\n\n{ticket.prompts[-1].text}"
    return _join(_request(ticket), _plan(ticket), _work_so_far(ticket), changes, latest, escalate, _limits(limits))


def task_prompt(ticket: Ticket, limits: Limits, task: Subtask, diff_stat: str) -> str:
    """Return a worker task for one step of the plan, with what earlier steps already did."""
    checklist = "\n".join(
        f"- [{'x' if item.id in ticket.tasks_done else ' '}] {item.id} · {item.title}" for item in ticket.subtasks
    )
    changes = f"## Files changed so far\n\n```\n{diff_stat}\n```" if diff_stat else ""
    files = "\n".join(f"- {file}" for file in task.files)
    return _join(
        _request(ticket),
        _plan(ticket),
        f"## Task list\n\n{checklist}",
        _work_so_far(ticket),
        changes,
        f"## Your task: {task.id} · {task.title}\n\n{task.instructions}\n\nExpected files:\n{files}\n\n"
        "Earlier tasks' changes are already in the files; read them instead of redoing them. Do only this task.",
        _limits(limits),
    )


def fix_checks_prompt(ticket: Ticket, limits: Limits, output: str) -> str:
    """Return a worker task to fix failing lint or tests."""
    return _join(
        _request(ticket),
        "## Fix the failing checks\n\nThe project's checks fail after the last change. Fix the cause.",
        f"```\n{output}\n```",
        _limits(limits),
    )


def fix_review_prompt(ticket: Ticket, limits: Limits, comments: list[ReviewComment]) -> str:
    """Return a worker task to address blocking review comments."""
    findings = "\n".join(f"- `{c.path}:{c.line}` {c.body}" for c in comments)
    return _join(
        _request(ticket),
        _plan(ticket),
        f"## Address these review findings, and only these\n\n{findings}",
        _limits(limits),
    )


def conflict_prompt(ticket: Ticket, limits: Limits, files: list[str], base_ref: str) -> str:
    """Return a worker task to resolve merge conflicts with the base branch."""
    listed = "\n".join(f"- {file}" for file in files)
    return _join(
        _request(ticket),
        f"## Resolve merge conflicts\n\nMerging `{base_ref}` into this branch stopped on conflicts in:\n\n{listed}\n\n"
        "Resolve every conflict so both the base branch's changes and this ticket's intent survive. "
        "Remove all conflict markers. Do not commit.",
        _limits(limits),
    )


def reviewer_prompt(ticket: Ticket, diff: str, truncated: bool, previous: list[ReviewEntry]) -> str:
    """Return a reviewer task for a diff."""
    note = "\n\nThe diff was cut to fit; read the changed files for the rest." if truncated else ""
    earlier = ""
    if previous:
        findings = "\n".join(f"- `{c.path}:{c.line}` {c.body}" for entry in previous for c in entry.comments)
        earlier = f"## Your previous findings (check each is fixed)\n\n{findings}"
    return _join(_request(ticket), _plan(ticket), earlier, f"## Diff to review{note}\n\n```diff\n{diff}\n```")


def onboarder_prompt(project: Project, detected_stack: str) -> str:
    """Return the onboarder's task."""
    stack_note = (
        f"The marker files suggest the stack is `{detected_stack}`; the team's rules for it are in your system prompt."
        if detected_stack
        else "No known stack was detected and the team has no rules for it yet, so leave differences empty."
    )
    return f"Onboard the repository `{project.name}` in the current directory.\n\n{stack_note}"


def pull_request_body(ticket: Ticket, final_reviews: list[ReviewEntry]) -> str:
    """Return the pull request description."""
    reviews = "\n\n".join(f"**{entry.reviewer}** ({entry.verdict}): {entry.summary}" for entry in final_reviews)
    work = "\n".join(
        f"- {entry.summary.splitlines()[0] if entry.summary else entry.status}" for entry in ticket.work_log
    )
    prompts = "\n".join(f"> {entry.text}" for entry in ticket.prompts)
    return _join(
        f"## Request\n\n{prompts}",
        f"## Plan\n\n{ticket.plan}" if ticket.plan else "",
        f"## Changes\n\n{work}",
        f"## Agent review\n\n{reviews}" if reviews else "",
        f"Ticket `{ticket.id}` · generated with cw",
    )
