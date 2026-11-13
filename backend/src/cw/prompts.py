"""The prompts cw sends to each agent.

Agents never talk to each other directly. They communicate through the ticket: cw gives an agent the part of the
ticket's markdown it needs plus one instruction, and writes the agent's structured answer back into the ticket.
A worker that already has a session only gets what changed since its last turn, so nothing is sent twice.
"""

from cw.config import Limits, Project, repo_map_path
from cw.models import QuestionEntry, ReviewComment, ReviewEntry, Subtask, Ticket
from cw.tickets import ticket_markdown, work_record


def _join(*sections: str) -> str:
    """Join the non-empty sections."""
    return "\n\n".join(section for section in sections if section)


def _request(ticket: Ticket) -> str:
    """Return every prompt the user gave, oldest first."""
    lines = [f"{index}. {entry.text}" for index, entry in enumerate(ticket.prompts, start=1)]
    return "## Request (the user's prompts, latest last)\n\n" + "\n".join(lines)


def _plan(ticket: Ticket) -> str:
    """Return the approved plan, if there is one."""
    return f"## Approved plan\n\n{ticket.plan}" if ticket.plan else ""


def _changes(diff_stat: str) -> str:
    """Return the files this ticket has changed so far."""
    return f"## Files changed so far on this branch\n\n```\n{diff_stat}\n```" if diff_stat else ""


def _checks(project: Project, ticket: Ticket) -> str:
    """Return the checks a worker may run and which of them are its responsibility."""
    lines = []
    for name, command in project.checks():
        if ticket.new_project:
            note = "new project: set it up and make it pass"
        elif ticket.baseline.get(name):
            note = "passes on the base branch: keep it passing"
        else:
            note = "already fails on the base branch: not yours to fix"
        lines.append(f"- {name}: `{command}` ({note})")
    if not lines:
        return ""
    return "## Checks\n\ncw runs these when the work is finished. Run them yourself first.\n\n" + "\n".join(lines)


def _check_results(project: Project, ticket: Ticket) -> str:
    """Return how the checks ended, for the reviewer."""
    lines = []
    for name, command in project.checks():
        if not ticket.baseline.get(name):
            state = "not enforced, it already failed on the base branch"
        elif command in ticket.failing_checks:
            state = "FAILS after this change"
        else:
            state = "passes"
        lines.append(f"- {name} `{command}`: {state}")
    return "## Checks cw ran on this change\n\n" + "\n".join(lines) if lines else ""


def _off_limits(project: Project) -> str:
    """Return the paths the agent may not read or edit."""
    if not project.protected_paths:
        return ""
    return "## Off limits\n\nNever read or edit: " + ", ".join(f"`{path}`" for path in project.protected_paths) + "."


def _limits(limits: Limits) -> str:
    """Return the limits the agent must respect."""
    return f"## Limits\n\nWrite at most {limits.summary_max_lines} lines in `summary`."


def _context_pack(ticket: Ticket) -> str:
    """Return the key locations identified by the planner for the worker to inspect."""
    if not ticket.context_pack:
        return ""
    lines = []
    for anchor in ticket.context_pack:
        line_info = f":{anchor.lines}" if anchor.lines else ""
        reason_info = f" ({anchor.reason})" if anchor.reason else ""
        lines.append(f"- `{anchor.path}{line_info}`{reason_info}")
    return "## Key code locations to inspect (from the planner)\n\n" + "\n".join(lines)


def fresh_worker_prompt(project: Project, ticket: Ticket, instruction: str, diff_stat: str) -> str:
    """Return the first prompt of a worker session: the ticket, the files changed so far, then what to do."""
    return _join(
        ticket_markdown(ticket, full=False).strip(),
        _changes(diff_stat),
        instruction,
        _context_pack(ticket),
        _checks(project, ticket),
        _off_limits(project),
        _limits(project.profile.limits),
    )


def resumed_worker_prompt(ticket: Ticket, worker: str, instruction: str) -> str:
    """Return the next prompt of a worker that already has a session: what others did since, then what to do."""
    seen = ticket.worker_seen.get(worker, 0)
    news = [line for entry in ticket.work_log[seen:] if entry.worker != worker for line in work_record(entry)]
    changed = "## What other workers did since your last turn\n\n" + "\n".join(news) if news else ""
    return _join(changed, instruction)


def task_instruction(task: Subtask) -> str:
    """Return the instruction for one task of the plan."""
    files = "Expected files:\n" + "\n".join(f"- {file}" for file in task.files) if task.files else ""
    return _join(
        f"## Your task: {task.id} · {task.title}\n\n{task.instructions}",
        files,
        "Do only this task. Earlier tasks' changes are already in the files: read them, do not redo them.",
    )


def direct_instruction(project: Project, ticket: Ticket) -> str:
    """Return the instruction for the user's latest request, taken straight from the prompt."""
    limits = project.profile.limits
    latest = next(entry.text for entry in reversed(ticket.prompts) if not entry.is_answer)
    unfinished = any(task.id not in ticket.tasks_done for task in ticket.subtasks)
    remaining = "The unchecked tasks in the task list run after you: do not do them." if unfinished else ""
    escalate = (
        f"If this needs changes in more than {limits.escalate_file_threshold} files, or a plan to be done correctly, "
        "return status needs_plan before editing."
        if not ticket.work_log and not ticket.plan
        else ""
    )
    return _join(f"## Do now\n\n{latest}", remaining, escalate)


def fix_checks_instruction(failures: list[tuple[str, str]]) -> str:
    """Return the instruction to fix checks this ticket broke."""
    outputs = "\n\n".join(f"```\n$ {command}\n{output}\n```" for command, output in failures)
    return (
        "## Fix the failing checks\n\nThese passed before this ticket's changes and fail now. Fix the cause in the "
        "code this ticket changed. Do not weaken, skip or delete a check or a test to make it pass. "
        "Decide yourself: questions are not possible here.\n\n" + outputs
    )


def fix_review_instruction(comments: list[ReviewComment]) -> str:
    """Return the instruction to address blocking review comments."""
    findings = "\n".join(f"- `{c.path}:{c.line}` {c.body}" for c in comments)
    return (
        "## Address these review findings, and only these\n\n"
        f"{findings}\n\nDecide yourself: questions are not possible here."
    )


def conflict_instruction(files: list[str], base_ref: str) -> str:
    """Return the instruction to resolve merge conflicts with the base branch."""
    listed = "\n".join(f"- {file}" for file in files)
    return (
        f"## Resolve merge conflicts\n\nMerging `{base_ref}` into this branch stopped on conflicts in:\n\n{listed}\n\n"
        "Edit these files so both the base branch's changes and this ticket's intent survive, and remove every "
        "conflict marker. Do not run git: cw stages the files and commits the merge once no markers are left. "
        "Decide yourself: questions are not possible here."
    )


def answers_instruction(answers: list[QuestionEntry], instruction: str) -> str:
    """Return the answers a worker was waiting for, followed by the instruction it asked about."""
    lines = [f"- {entry.question}\n  The {entry.answered_by} answered: {entry.answer}" for entry in answers]
    return _join("## Answers to your questions\n\n" + "\n".join(lines), instruction)


def no_more_questions_instruction(instruction: str) -> str:
    """Return the instruction again, telling the worker to decide for itself."""
    return _join(
        "## No more questions\n\nNobody can answer further questions now. Choose the simplest reasonable option, "
        "do the work, and state what you assumed in `summary`.",
        instruction,
    )


def _repo_map(project: Project) -> str:
    """Return the repository's architecture and symbol map if available."""
    map_path = repo_map_path(project.name)
    if not map_path.exists():
        return ""
    content = map_path.read_text(encoding="utf-8").strip()
    return f"## Codebase architecture map\n\n```\n{content}\n```" if content else ""


def planner_prompt(project: Project, ticket: Ticket, feedback: str | None) -> str:
    """Return the planner's task."""
    limits = project.profile.limits
    questions_left = limits.max_question_rounds - ticket.question_rounds
    asking = (
        "You may ask clarifying questions if something blocks a correct plan."
        if questions_left > 0
        else "You may not ask more questions: make reasonable assumptions and list them at the top of the plan."
    )
    commands = ", ".join(f"`{command}` ({name})" for name, command in project.checks())
    checks = f" After the last task cw runs {commands}; the skeleton task must make them work." if commands else ""
    new_project = (
        "## New project\n\nThe repository holds only a README. Plan the whole project from the request, "
        f"following 'New project' in your instructions.{checks}"
        if ticket.new_project and not ticket.plan
        else ""
    )
    feedback_section = f"## User's answer or feedback on your last plan\n\n{feedback}" if feedback else ""
    return _join(
        _request(ticket),
        _repo_map(project),
        new_project,
        feedback_section,
        f"## Constraints\n\n- {asking}\n"
        f"- You have up to {limits.max_workers} workers (W1, W2, ...). They take turns, in the order of your task "
        "list, in one checkout. A worker keeps what it has read between its own tasks, so give related tasks to "
        "the same worker and use another worker only for an unrelated area.\n"
        "- This ticket is exactly one branch and one pull request; nothing can create more. If the user wants "
        "several changes on separate branches or PRs, return them in `split` and do not ask about it, "
        "unless the user has since said to keep them together.",
        _limits(limits),
    )


def planner_answer_prompt(ticket: Ticket, worker: str, task: str | None, questions: list[str], fresh: bool) -> str:
    """Return the planner's task when a worker asks it questions; a planner without a session also gets the ticket."""
    where = f" on task {task}" if task else ""
    listed = "\n".join(f"{index}. {question}" for index, question in enumerate(questions, start=1))
    return _join(
        ticket_markdown(ticket, full=False).strip() if fresh else "",
        f"## Questions from worker {worker}{where}\n\n{listed}\n\n"
        "Answer each from your plan and the code; read files if you need to. Be specific: the worker acts on your "
        "words alone. Put one answer per question, in the same order, in `answers`, with an empty string for a "
        "question only the user can answer (a product choice, a secret, something outside the repository). "
        "Leave every other field empty: this is not a request for a new plan.",
    )


def reviewer_prompt(project: Project, ticket: Ticket, diff: str, truncated: bool, previous: list[ReviewEntry]) -> str:
    """Return a reviewer task for a diff."""
    note = "\n\nThe diff was cut to fit; read the changed files for the rest." if truncated else ""
    notes = [line for entry in ticket.work_log for line in work_record(entry)]
    worker_notes = (
        "## Worker notes (the workers' own account: trust the diff, not the notes)\n\n" + "\n".join(notes)
        if notes
        else ""
    )
    earlier = ""
    if previous:
        findings = "\n".join(f"- `{c.path}:{c.line}` {c.body}" for entry in previous for c in entry.comments)
        earlier = f"## Your previous findings (check each is fixed)\n\n{findings}"
    return _join(
        _request(ticket),
        _plan(ticket),
        worker_notes,
        _check_results(project, ticket),
        earlier,
        f"## Diff to review{note}\n\n```diff\n{diff}\n```",
    )


def onboarder_prompt(project: Project, detected_stack: str) -> str:
    """Return the onboarder's task."""
    stack_note = (
        f"The marker files suggest the stack is `{detected_stack}`; the team's rules for it are in your system "
        "prompt under 'Team rules for this run'."
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
