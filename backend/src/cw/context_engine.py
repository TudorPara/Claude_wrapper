"""The context engine at ticket time: refresh the index, build a ticket's seed once, keep finished tickets' records.

Everything here is best effort. A failure is returned as a warning for the ticket; it never stops the ticket.
"""

import logging
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

from cw import context_index, git, prompts
from cw.claude_runner import run_agent
from cw.config import AgentRole, Project, Settings, context_db_path
from cw.context_index import EmbeddingMode, RecordFinding, TicketRecord, UpdateResult, opened_index
from cw.context_search import ALL_CHANNELS, build_seed, layout_outline
from cw.errors import AppException
from cw.models import HydeSketch, RunUsage, Ticket, utc_now
from cw.storage import BYTES_PER_MB
from cw.tickets import events_path

logger = logging.getLogger(__name__)

CONTEXT_AGENT = "context-sketcher"
CONTEXT_LABEL = "context"
INDEX_ERRORS = (sqlite3.Error, OSError, AppException)
FINDING_SUMMARY_LINES = 1


def request_text(ticket: Ticket) -> str:
    """Return what the user asked for: the ticket's prompts that were not answers to questions."""
    return "\n\n".join(prompt.text for prompt in ticket.prompts if not prompt.is_answer)


def build_hint(project_name: str) -> str:
    """Return the warning that asks for one full build."""
    return f'semantic search is incomplete; run "cw context build {project_name}" once'


def size_warning(project_name: str, settings: Settings) -> str:
    """Return a warning when the index file has grown past `max_index_mb`, or an empty string."""
    path = context_db_path(project_name)
    size_mb = path.stat().st_size / BYTES_PER_MB if path.exists() else 0
    if size_mb > settings.storage.max_index_mb:
        return f"the index is {size_mb:.0f} MB (limit {settings.storage.max_index_mb}); run: cw gc {project_name}"
    return ""


def _embedding_warnings(project_name: str, result: UpdateResult) -> list[str]:
    """Return the warning an update that skipped embedding leaves: Ollama's reason, or the hint to build."""
    if result.embedding_error:
        return [f"semantic search uses older vectors ({result.embedding_error})"]
    return [build_hint(project_name)] if result.embedding_skipped else []


def refresh_at_start(settings: Settings, project: Project, start_point: str) -> list[str]:
    """Bring the index to the commit a ticket starts from; a first build here parses only, it embeds nothing."""
    first = not context_db_path(project.name).exists()
    commit = git.resolve_commit(project.path, start_point) or start_point
    try:
        with opened_index(project.name) as connection:
            mode = EmbeddingMode.NONE if first else EmbeddingMode.AUTOMATIC
            result = context_index.update_snapshot(connection, project, settings, commit, embedding=mode)
    except INDEX_ERRORS as error:
        logger.warning("The context index was not updated: %s", error)
        return [f"the index was not updated ({error})"]
    warnings = _embedding_warnings(project.name, result)
    return warnings + [warning for warning in [size_warning(project.name, settings)] if warning]


def ticket_record(settings: Settings, ticket: Ticket, finished_at: datetime | None = None) -> TicketRecord:
    """Return the record a finished ticket leaves, built from the ticket alone; `finished_at` defaults to now."""
    files = ticket.changed_files or list(dict.fromkeys(path for entry in ticket.work_log for path in entry.files))
    return TicketRecord(
        ticket_id=ticket.id,
        title=ticket.title,
        finished_at=finished_at or utc_now(),
        request=request_text(ticket),
        plan_excerpt=ticket.plan[: settings.context.record_plan_chars],
        changed_files=files,
        open_findings=[
            RecordFinding(
                path=finding.path,
                line=finding.line,
                severity=finding.severity,
                summary=" ".join(finding.body.strip().splitlines()[:FINDING_SUMMARY_LINES]),
            )
            for finding in ticket.open_findings
        ],
        notes=list(dict.fromkeys(note for entry in ticket.work_log for note in entry.project_notes)),
        merge_commit=ticket.merge_commit,
    )


def save_record(connection: sqlite3.Connection, settings: Settings, record: TicketRecord) -> list[str]:
    """Store a ticket record and its vector; a vector that cannot be made now is made by the next build."""
    context_index.write_record(connection, record)
    text = context_index.record_text(record)
    try:
        context_index.embed_texts(connection, settings, {context_index.text_hash(text): text})
    except AppException as error:
        logger.warning("The record of %s has no vector yet: %s", record.ticket_id, error)
        return [build_hint_for_record(record.ticket_id)]
    return []


def build_hint_for_record(ticket_id: str) -> str:
    """Return the warning for a record stored without its vector."""
    return f"the record of {ticket_id} has no vector yet; the next cw context build makes it"


def refresh_at_finish(settings: Settings, project: Project, ticket: Ticket, target: str) -> list[str]:
    """Bring the index to the tip of the branch the ticket went into, then store the ticket's record.

    The record is stored even when the update fails: it holds the workers' notes, which exist nowhere else.
    """
    commit = git.resolve_commit(project.path, target) or git.resolve_commit(project.path, f"{project.remote}/{target}")
    warnings: list[str] = []
    try:
        with opened_index(project.name) as connection:
            if commit:
                result = context_index.update_snapshot(connection, project, settings, commit)
                warnings += _embedding_warnings(project.name, result)
    except INDEX_ERRORS as error:
        logger.warning("The context index was not updated at finish: %s", error)
        warnings.append(f"the index was not updated at finish ({error})")
    try:
        with opened_index(project.name) as connection:
            warnings += save_record(connection, settings, ticket_record(settings, ticket))
    except INDEX_ERRORS as error:
        logger.warning("The record of %s was not stored: %s", ticket.id, error)
        warnings.append(f"the ticket's record was not stored ({error})")
    return warnings


def sketch(
    settings: Settings, project: Project, events_id: str, request: str, layout: str
) -> tuple[HydeSketch | None, list[RunUsage], list[str]]:
    """Run the context sketcher with no tools, rules or repository; return the sketch, its usage and any warning."""
    with tempfile.TemporaryDirectory(prefix="cw-context-") as folder:
        try:
            result, usage = run_agent(
                settings,
                project,
                CONTEXT_AGENT,
                AgentRole.CONTEXT,
                prompts.hyde_prompt(request, project.stack, layout),
                Path(folder),
                HydeSketch,
                events_id,
                label=CONTEXT_LABEL,
            )
        except AppException as error:
            logger.warning("The HyDE sketch failed: %s", error)
            return None, [error.usage] if error.usage else [], [f"the HyDE sketch failed ({error})"]
    limit = settings.context.hyde_max_chars
    return result.model_copy(update={"code": result.code[:limit]}), [usage], []


def sketch_outside_ticket(
    settings: Settings, project: Project, events_id: str, request: str, layout: str
) -> tuple[HydeSketch | None, list[RunUsage], list[str]]:
    """Run a sketch for `cw context search` or `eval` and remove the event lines it wrote, which no ticket owns."""
    try:
        return sketch(settings, project, events_id, request, layout)
    finally:
        events_path(project.name, events_id).unlink(missing_ok=True)


def seed_for(settings: Settings, project: Project, ticket: Ticket) -> list[str]:
    """Build the ticket's seed once and store it on the ticket; later fresh prompts reuse it. Returns warnings."""
    if ticket.context_seed_built:
        return []
    ticket.context_seed_built = True
    request = request_text(ticket)
    try:
        with opened_index(project.name) as connection:
            paths = context_index.snapshot_paths(connection)
            if not paths:
                return []
            layout = layout_outline(paths, settings.context.layout_max_folders)
            hyde, usages, warnings = sketch(settings, project, ticket.id, request, layout)
            ticket.runs += usages
            seed = build_seed(connection, settings, request, ALL_CHANNELS, ticket.created_at, {ticket.id}, hyde)
    except INDEX_ERRORS as error:
        logger.warning("No context seed for %s: %s", ticket.id, error)
        return [f"no seed for this ticket; the planner gets the repo map ({error})"]
    ticket.context_seed = seed.text
    ticket.seed_files = [file.path for file in seed.files]
    return warnings + seed.warnings
