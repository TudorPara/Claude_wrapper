"""Tickets live in cw's own folder (~/.cw/tickets/<project>/<id>.json), never inside a project repository."""

import logging
import os
import re
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from cw.config import cw_home
from cw.errors import AppErrorCode, AppException
from cw.models import Ticket, TicketStatus, TicketSummary, utc_now

logger = logging.getLogger(__name__)

TICKETS_DIR = "tickets"
TICKET_SUFFIX = ".json"
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


def read_ticket(path: Path) -> Ticket:
    """Parse a ticket file."""
    return Ticket.model_validate_json(path.read_text())


def save_ticket(ticket: Ticket) -> None:
    """Write the ticket."""
    ticket.updated_at = utc_now()
    path = ticket_path(ticket.project, ticket.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ticket.model_dump_json(indent=2))


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


def render_ticket(ticket: Ticket) -> str:
    """Return a readable text view of a ticket for the terminal."""
    lines = [
        f"# {ticket.title}",
        f"{ticket.id} · {ticket.project} · {ticket.status} · {ticket.route} · {ticket.branch}",
    ]
    if ticket.pr_url:
        lines.append(f"PR: {ticket.pr_url}")
    lines += [f"! {warning}" for warning in ticket.warnings]
    if ticket.error:
        lines.append(f"Error: {ticket.error}")
    lines += ["", "Prompts:"] + [f"- {entry.text}" for entry in ticket.prompts]
    if ticket.questions:
        lines += ["", "Questions:"] + [f"- {question}" for question in ticket.questions]
    if ticket.plan:
        lines += ["", "Plan:", ticket.plan]
    if ticket.work_log:
        lines += ["", "Work:"] + [f"- [{work.status}] {work.summary}" for work in ticket.work_log]
    for review in ticket.reviews:
        lines += ["", f"Review {review.round} ({review.reviewer}): {review.verdict}", review.summary]
        lines += [f"- {c.path}:{c.line} ({c.severity}) {c.body}" for c in review.comments]
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
