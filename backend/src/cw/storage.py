"""Bounded local data: `cw gc` for the context index, event-log retention, and a removed project's files.

Nothing here touches a project's repository: everything it deletes lives in cw's own folder.
"""

import logging
import shutil
import sqlite3
from contextlib import nullcontext
from datetime import timedelta
from pathlib import Path

from pydantic import BaseModel

from cw import context_index, git
from cw.config import Project, Settings, context_db_path, project_data_dir, repo_map_path
from cw.context_index import opened_index
from cw.models import utc_now
from cw.tickets import CLOSED_STATUSES, events_path, project_lock, read_ticket, tickets_root

logger = logging.getLogger(__name__)

GC_LOCK_LABEL = "gc"
BYTES_PER_MB = 1024 * 1024
BLOB_TABLES = ("blobs", "chunks", "chunk_terms", "definitions", "refs", "imports")
TICKET_SUFFIX = ".json"


class GcReport(BaseModel):
    """What one `cw gc` did, or would do with --dry-run."""

    project: str
    dry_run: bool
    size_before_bytes: int
    size_after_bytes: int
    blobs_deleted: int
    vectors_deleted: int
    events_deleted: int


def prune_event_logs(settings: Settings, project_name: str, dry_run: bool = False) -> list[Path]:
    """Delete the event logs of the project's tickets closed longer than `event_retention_days`; return them."""
    cutoff = utc_now() - timedelta(days=settings.storage.event_retention_days)
    expired = []
    for path in sorted((tickets_root() / project_name).glob(f"*{TICKET_SUFFIX}")):
        try:
            ticket = read_ticket(path)
        except (OSError, ValueError) as error:
            logger.warning("Skipped %s while pruning event logs: %s", path.name, error)
            continue
        events = events_path(ticket.project, ticket.id)
        if ticket.status in CLOSED_STATUSES and ticket.updated_at < cutoff and events.exists():
            expired.append(events)
            if not dry_run:
                events.unlink()
    return expired


def retained_commits(project: Project) -> list[str]:
    """Return the commits whose files gc keeps: the base branch and every open ticket's target, local and remote."""
    branches = {project.base_branch} | {
        ticket.target_branch or project.base_branch
        for ticket in (read_ticket(path) for path in sorted((tickets_root() / project.name).glob(f"*{TICKET_SUFFIX}")))
        if ticket.status not in CLOSED_STATUSES
    }
    commits = []
    for branch in sorted(branches):
        for ref in (branch, f"{project.remote}/{branch}"):
            commit = git.resolve_commit(project.path, ref)
            if commit:
                commits.append(commit)
            else:
                logger.debug("gc skips %s: no such ref.", ref)
    return list(dict.fromkeys(commits))


def _retained_pairs(connection: sqlite3.Connection, project: Project, settings: Settings) -> set[tuple[str, str]]:
    """Return every (path, blob) gc keeps: the current snapshot and the trees of the retained commits."""
    pairs = set(connection.execute("SELECT path, blob FROM snapshot").fetchall())
    for commit in retained_commits(project):
        pairs |= set(context_index.indexed_tree(project, settings, commit).items())
    return pairs


def _delete_obsolete(
    connection: sqlite3.Connection, settings: Settings, pairs: set[tuple[str, str]], dry_run: bool
) -> tuple[int, int]:
    """Delete blobs, chunk keys and vectors no retained tree or record needs; return blobs and vectors deleted."""
    kept_blobs = {blob for _, blob in pairs}
    obsolete_blobs = [row[0] for row in connection.execute("SELECT blob FROM blobs") if row[0] not in kept_blobs]
    stale_keys = [
        (path, blob)
        for path, blob in connection.execute("SELECT DISTINCT path, blob FROM chunk_hashes")
        if (path, blob) not in pairs
    ]
    live = {
        row[0]
        for row in connection.execute("SELECT text_hash, path, blob FROM chunk_hashes")
        if (row[1], row[2]) in pairs
    }
    live |= {
        context_index.text_hash(context_index.record_text(record)) for record in context_index.all_records(connection)
    }
    key = context_index.model_key(settings)
    obsolete_vectors = [
        (model_key, digest)
        for model_key, digest in connection.execute("SELECT model_key, text_hash FROM embeddings")
        if model_key != key or digest not in live
    ]
    if not dry_run:
        for table in BLOB_TABLES:
            connection.executemany(f"DELETE FROM {table} WHERE blob = ?", [(blob,) for blob in obsolete_blobs])
        connection.executemany("DELETE FROM chunk_hashes WHERE path = ? AND blob = ?", stale_keys)
        connection.executemany("DELETE FROM embeddings WHERE model_key = ? AND text_hash = ?", obsolete_vectors)
        connection.commit()
    return len(obsolete_blobs), len(obsolete_vectors)


def collect_garbage(settings: Settings, project: Project, dry_run: bool = False, events: bool = True) -> GcReport:
    """Remove what the project's index and event logs no longer need, then shrink the index file.

    A dry run only reads, so it takes no lock and can run while a ticket works.
    """
    path = context_db_path(project.name)
    size_before = path.stat().st_size if path.exists() else 0
    blobs = vectors = 0
    with nullcontext() if dry_run else project_lock(project.name, GC_LOCK_LABEL):
        if path.exists():
            with opened_index(project.name) as connection:
                blobs, vectors = _delete_obsolete(
                    connection, settings, _retained_pairs(connection, project, settings), dry_run
                )
                if not dry_run:
                    connection.execute("VACUUM")
        expired = prune_event_logs(settings, project.name, dry_run) if events else []
    size_after = path.stat().st_size if path.exists() else 0
    return GcReport(
        project=project.name,
        dry_run=dry_run,
        size_before_bytes=size_before,
        size_after_bytes=size_before if dry_run else size_after,
        blobs_deleted=blobs,
        vectors_deleted=vectors,
        events_deleted=len(expired),
    )


def cache_files(project_name: str) -> list[Path]:
    """Return the project files cw can rebuild: the context index and the repo map."""
    return [path for path in (context_db_path(project_name), repo_map_path(project_name)) if path.exists()]


def purge_targets(project_name: str) -> list[Path]:
    """Return everything `--purge` deletes: the project's own folder (decisions included) and its tickets."""
    return [path for path in (project_data_dir(project_name), tickets_root() / project_name) if path.exists()]


def delete_paths(paths: list[Path]) -> None:
    """Delete files and folders."""
    for path in paths:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)


def folder_size(path: Path) -> int:
    """Return the bytes a file or folder takes."""
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file()) if path.exists() else 0


def storage_report(settings: Settings) -> list[dict]:
    """Return per project the size of its index, tickets and event logs, and whether the index is over its limit."""
    rows = []
    for project in settings.projects:
        index = context_db_path(project.name)
        tickets = tickets_root() / project.name
        events = sum(item.stat().st_size for item in tickets.glob("*.events.jsonl")) if tickets.exists() else 0
        index_bytes = folder_size(index)
        rows.append(
            {
                "project": project.name,
                "index_bytes": index_bytes,
                "tickets_bytes": folder_size(tickets) - events,
                "events_bytes": events,
                "over_limit": index_bytes > settings.storage.max_index_mb * BYTES_PER_MB,
            }
        )
    return rows
