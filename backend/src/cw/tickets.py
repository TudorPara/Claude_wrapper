"""Tickets live in cw's own folder (~/.cw/tickets/<project>/<id>.json), never inside a project repository."""

import json
import logging
import os
import re
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from cw.config import cw_home
from cw.errors import AppErrorCode, AppException
from cw.models import Ticket, TicketStatus, TicketSummary, WorkEntry, WorkerStatus, utc_now

logger = logging.getLogger(__name__)

TICKETS_DIR = "tickets"
TICKET_SUFFIX = ".json"
TEMPORARY_SUFFIX = ".tmp"
MARKDOWN_SUFFIX = ".md"
EVENTS_SUFFIX = ".events.jsonl"
LOCK_FILE_NAME = ".lock"
LOCK_SEPARATOR = " "
ID_DATE_FORMAT = "%y%m%d"
ID_SLUG_WORDS = 5
ID_SLUG_MAX_CHARS = 32
ID_RANDOM_BYTES = 2
TITLE_MAX_CHARS = 72
TICKET_ID_PATTERN = re.compile(r"^\d{6}-[a-z0-9-]+-[0-9a-f]{4}$")
SLUG_SEPARATORS = re.compile(r"[^a-z0-9]+")
CLOSED_STATUSES = {TicketStatus.DONE, TicketStatus.ABANDONED}


def make_title(prompt: str) -> str:
    """Return a one-line title from the first line of a prompt."""
    first_line = prompt.strip().splitlines()[0] if prompt.strip() else "untitled"
    return first_line[:TITLE_MAX_CHARS]


def new_ticket_id(title: str) -> str:
    """Return a unique, readable ticket id such as 261111-fix-login-timeout-3fa2."""
    words = SLUG_SEPARATORS.sub(" ", title.lower()).split()[:ID_SLUG_WORDS]
    slug = "-".join(words)[:ID_SLUG_MAX_CHARS].strip("-") or "task"
    return f"{utc_now():{ID_DATE_FORMAT}}-{slug}-{secrets.token_hex(ID_RANDOM_BYTES)}"


def looks_like_ticket_id(value: str) -> bool:
    """Return whether a string has the shape of a ticket id."""
    return bool(TICKET_ID_PATTERN.match(value))


def tickets_root() -> Path:
    """Return the folder that holds every ticket."""
    return cw_home() / TICKETS_DIR


def ticket_path(project: str, ticket_id: str) -> Path:
    """Return the ticket file."""
    return tickets_root() / project / f"{ticket_id}{TICKET_SUFFIX}"


def events_path(project: str, ticket_id: str) -> Path:
    """Return the file of the ticket's agent events, one JSON object per line, which FRIDAY follows live."""
    return tickets_root() / project / f"{ticket_id}{EVENTS_SUFFIX}"


def append_event(project: str, ticket_id: str, event: dict) -> None:
    """Add one timestamped event to the ticket's event file."""
    path = events_path(project, ticket_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as events:
        events.write(json.dumps({"at": utc_now().isoformat(), **event}) + "\n")


def read_events(project: str, ticket_id: str, last: int) -> list[dict]:
    """Return the ticket's last events, oldest first; a ticket without runs has none."""
    path = events_path(project, ticket_id)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()[-last:] if line.strip()]


def read_ticket(path: Path) -> Ticket:
    """Parse a ticket file."""
    return Ticket.model_validate_json(path.read_text())


def write_ticket(ticket: Ticket) -> None:
    """Write the ticket (through a temporary file, so a reader never sees half of it) and its readable .md twin."""
    ticket.updated_at = utc_now()
    path = ticket_path(ticket.project, ticket.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(TEMPORARY_SUFFIX)
    temporary.write_text(ticket.model_dump_json(indent=2))
    temporary.replace(path)
    path.with_suffix(MARKDOWN_SUFFIX).write_text(ticket_markdown(ticket))


def ticket_paths() -> list[Path]:
    """Return every ticket file."""
    return sorted(tickets_root().glob(f"*/*{TICKET_SUFFIX}"))


def find_ticket(ticket_id: str) -> Ticket:
    """Return the ticket with this id."""
    for path in tickets_root().glob(f"*/{ticket_id}{TICKET_SUFFIX}"):
        return read_ticket(path)
    raise AppException(AppErrorCode.TICKET_NOT_FOUND, ticket_id)


def summarize(ticket: Ticket) -> TicketSummary:
    """Return the short form of a ticket."""
    archived = ticket.status in CLOSED_STATUSES
    return TicketSummary(
        id=ticket.id,
        project=ticket.project,
        title=ticket.title,
        status=ticket.status,
        route=ticket.route,
        branch=ticket.branch,
        pr_url=ticket.pr_url,
        updated_at=ticket.updated_at,
        running=not archived and is_running(ticket),
        archived=archived,
        error=ticket.error,
    )


def list_tickets() -> list[TicketSummary]:
    """Return every ticket, newest first."""
    summaries = [summarize(read_ticket(path)) for path in ticket_paths()]
    return sorted(summaries, key=lambda summary: summary.updated_at, reverse=True)


def work_record(entry: WorkEntry, brief: bool = False) -> list[str]:
    """Return what a worker left behind: how it solved its job, the files it touched, what the next one must know.

    The brief form drops the summary but keeps the files and the handoff, which later tasks build on.
    """
    label = "Solved" if entry.status == WorkerStatus.DONE else str(entry.status)
    head = f"- {label} by {entry.worker or 'a worker'}"
    summary = entry.summary.strip().replace(chr(10), chr(10) + "  ")
    lines = [head if brief or not summary else f"{head}: {summary}"]
    if entry.files:
        lines.append(f"- Files: {', '.join(entry.files)}")
    if entry.handoff.strip():
        lines.append(f"- For the next worker: {entry.handoff.strip().replace(chr(10), chr(10) + '  ')}")
    return lines


def _tasks(ticket: Ticket, full: bool) -> list[str]:
    """Return the plan's tasks, each with its worker, whether it is done and what was left behind."""
    lines = []
    last_done_id = ticket.tasks_done[-1] if ticket.tasks_done else None
    for task in ticket.subtasks:
        mark = "x" if task.id in ticket.tasks_done else " "
        lines += ["", f"### [{mark}] {task.id} · {task.title} (worker {task.worker})"]
        if full:
            lines += ["", task.instructions, ""]
        records = [entry for entry in ticket.work_log if entry.subtask == task.id]
        if records:
            lines += work_record(records[-1], brief=not full and task.id != last_done_id)
    return lines


def _questions(ticket: Ticket) -> list[str]:
    """Return every question a worker asked, with its answer."""
    lines = []
    for entry in ticket.qa:
        where = f" on {entry.task}" if entry.task else ""
        lines.append(f"- Worker {entry.worker}{where} asked: {entry.question}")
        lines.append(f"  The {entry.answered_by} answered: {entry.answer}" if entry.answer else "  Not answered yet.")
    return lines


def ticket_markdown(ticket: Ticket, full: bool = True) -> str:
    """Return the ticket as markdown.

    The full form is the ticket's .md file and what `cw show` prints. The short form (no status, task
    instructions or reviews) is what a worker is given: the request, the answers, the plan and what was done.
    """
    lines = [f"# {ticket.title}"]
    if full:
        lines += ["", f"Ticket `{ticket.id}` · {ticket.project} · {ticket.status} · branch `{ticket.branch}`"]
        if ticket.pr_url:
            lines.append(f"PR: {ticket.pr_url}")
        lines += [f"- Warning: {warning}" for warning in ticket.warnings]
        if ticket.error:
            lines.append(f"- Error: {ticket.error}")
    lines += ["", "## Request (the user's prompts, latest last)", ""]
    lines += [
        f"{index}. {'(answer) ' if entry.is_answer else ''}{entry.text}"
        for index, entry in enumerate(ticket.prompts, start=1)
    ]
    if full and ticket.questions:
        lines += ["", "## Waiting for your answer", ""] + [f"- {question}" for question in ticket.questions]
    if ticket.qa:
        lines += ["", "## Questions and answers", ""] + _questions(ticket)
    if ticket.plan:
        lines += ["", "## Plan", "", ticket.plan]
    if ticket.subtasks:
        lines += ["", "## Tasks"] + _tasks(ticket, full)
    other = [entry for entry in ticket.work_log if entry.subtask is None]
    if other:
        lines += ["", "## Other work (follow-ups and fixes)", ""]
        for entry in other:
            lines += work_record(entry)
    if full:
        for review in ticket.reviews:
            lines += ["", f"## Review {review.round} by {review.reviewer}: {review.verdict}", "", review.summary]
            lines += [
                f"- `{comment.path}:{comment.line}` ({comment.severity}) {comment.body}" for comment in review.comments
            ]
    return "\n".join(lines) + "\n"


def _lock_path(project: str) -> Path:
    """Return the project's lock file: one cw process works in a checkout at a time."""
    return tickets_root() / project / LOCK_FILE_NAME


def _pid_alive(pid: int) -> bool:
    """Return whether a process with this id is running."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _lock_holder(project: str) -> str | None:
    """Return the ticket id a live cw process is working on in this project, if any."""
    lock = _lock_path(project)
    if not lock.exists():
        return None
    pid, _, ticket_id = lock.read_text().strip().partition(LOCK_SEPARATOR)
    try:
        return ticket_id if _pid_alive(int(pid)) else None
    except ValueError:
        return None


def is_running(ticket: Ticket) -> bool:
    """Return whether a live cw process is working on this ticket."""
    return _lock_holder(ticket.project) == ticket.id


@contextmanager
def project_lock(project: str, ticket_id: str) -> Iterator[None]:
    """Hold the project's checkout for one ticket; locks left by dead processes are replaced."""
    holder = _lock_holder(project)
    if holder:
        raise AppException(AppErrorCode.TICKET_BUSY, f"{project} is busy with {holder}.")
    lock = _lock_path(project)
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(f"{os.getpid()}{LOCK_SEPARATOR}{ticket_id}")
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)
