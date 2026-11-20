"""Read-only views: usage stats, the toolkit inventory and the environment doctor."""

import json
import shutil
import sqlite3
import subprocess
from collections import defaultdict

from cw import context_index, embeddings, git, storage
from cw.config import GitHostProvider, Settings, context_db_path, conventions_path, cw_home
from cw.errors import AppException
from cw.models import RunUsage, TicketStatus
from cw.tickets import list_tickets, read_ticket, ticket_paths
from cw.toolkit import list_agents, list_skills, toolkit_version

OK = "ok"
MISSING = "missing"
WAITING_STATUSES = {
    TicketStatus.NEEDS_INPUT,
    TicketStatus.AWAITING_APPROVAL,
    TicketStatus.READY,
    TicketStatus.BLOCKED,
    TicketStatus.PAUSED,
}


def run_costs(runs: list[RunUsage]) -> list[tuple[RunUsage, float]]:
    """Return each run with its own cost; a resumed session reports a running total, so earlier runs are subtracted."""
    session_spent: dict[str, float] = {}
    costs = []
    for run in runs:
        costs.append((run, run.cost_usd - session_spent.get(run.session_id, 0.0)))
        if run.session_id:
            session_spent[run.session_id] = run.cost_usd
    return costs


def usage_stats(settings: Settings) -> list[dict]:
    """Return token usage summed per project and agent role, across all tickets."""
    totals: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"runs": 0, "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cost_usd": 0.0}
    )
    for path in ticket_paths():
        ticket = read_ticket(path)
        for run, cost in run_costs(ticket.runs):
            total = totals[(ticket.project, run.role)]
            total["cost_usd"] += cost
            total["runs"] += 1
            total["input_tokens"] += run.input_tokens + run.cache_creation_tokens
            total["output_tokens"] += run.output_tokens
            total["cache_read_tokens"] += run.cache_read_tokens
    return [{"project": project, "role": role, **values} for (project, role), values in sorted(totals.items())]


def project_overview(settings: Settings) -> list[dict]:
    """Return each project's codebase state: branch, uncommitted changes, last commit, remote, open tickets."""
    tickets = list_tickets()
    rows = []
    for project in settings.projects:
        exists = project.path.exists()
        open_tickets = [ticket for ticket in tickets if ticket.project == project.name and not ticket.archived]
        rows.append(
            {
                "name": project.name,
                "path": str(project.path),
                "exists": exists,
                "branch": git.current_branch(project.path) if exists else "",
                "dirty": git.is_dirty(project.path) if exists else False,
                "last_commit": git.last_commit_age(project.path) if exists else "",
                "remote_url": git.remote_url(project.path, project.remote) if exists else "",
                "conventions": conventions_path(project.name).exists(),
                "open_tickets": len(open_tickets),
                "running": sum(ticket.running for ticket in open_tickets),
                "waiting": sum(ticket.status in WAITING_STATUSES and not ticket.running for ticket in open_tickets),
            }
        )
    return rows


def toolkit_inventory(settings: Settings) -> dict:
    """Return the toolkit's agents, skills, stacks and the model each project uses per agent."""
    agents = list_agents(settings)
    return {
        "path": str(settings.toolkit.path),
        "version": toolkit_version(settings),
        "marketplace": settings.toolkit.marketplace,
        "always_plugins": settings.toolkit.always_plugins,
        "always_external_plugins": settings.toolkit.always_external_plugins,
        "agents": [agent.model_dump(exclude={"prompt"}) for agent in agents],
        "skills": [skill.model_dump() for skill in list_skills(settings)],
        "stacks": {name: stack.model_dump() for name, stack in settings.stacks.items()},
        "project_models": {
            project.name: project.profile.models.model_dump() | {"reviewers": project.profile.reviewers}
            for project in settings.projects
        },
    }


def _installed_plugins(settings: Settings) -> set[str]:
    """Return plugin@marketplace ids installed for this user."""
    result = subprocess.run([settings.claude_binary, "plugin", "list", "--json"], capture_output=True, text=True)
    if result.returncode != 0:
        return set()
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return set()
    entries = data if isinstance(data, list) else data.get("plugins", [])
    return {entry.get("id") or f"{entry.get('name')}@{entry.get('marketplace')}" for entry in entries}


def build_doctor_report(settings: Settings) -> list[tuple[str, str, str]]:
    """Return (check, status, hint) rows for everything cw relies on."""
    rows = []
    for binary in (settings.claude_binary, "git", "uv"):
        rows.append((f"binary {binary}", OK if shutil.which(binary) else MISSING, f"install {binary}"))
    if any(project.git_host == GitHostProvider.GITHUB for project in settings.projects):
        rows.append(("binary gh", OK if shutil.which("gh") else MISSING, "brew install gh && gh auth login"))
    if any(project.git_host == GitHostProvider.BITBUCKET for project in settings.projects):
        rows.append(
            (
                "bitbucket token",
                OK if settings.bitbucket else MISSING,
                "export BITBUCKET__EMAIL and BITBUCKET__API_TOKEN",
            )
        )
    rows.append(("toolkit", OK if settings.toolkit.path.exists() else MISSING, f"clone it to {settings.toolkit.path}"))
    for project in settings.projects:
        rows.append((f"project {project.name}", OK if project.path.exists() else MISSING, str(project.path)))
    installed = _installed_plugins(settings)
    wanted = set(settings.toolkit.always_external_plugins)
    for project in settings.projects:
        stack = settings.stack(project.stack)
        wanted |= set(stack.external_plugins if stack else [])
    for plugin in sorted(wanted):
        marketplace = plugin.split("@", 1)[1]
        repository = settings.external_marketplaces.get(marketplace, marketplace)
        hint = f"claude plugin marketplace add {repository} && claude plugin install {plugin}"
        rows.append((f"plugin {plugin}", OK if plugin in installed else MISSING, hint))
    return rows + _context_rows(settings)


def _context_rows(settings: Settings) -> list[tuple[str, str, str]]:
    """Return the context engine's checks: FTS5, Ollama and its model, each project's index and its size."""
    rows = []
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute(context_index.FTS5_PROBE)
        rows.append(("sqlite fts5", OK, ""))
    except sqlite3.OperationalError:
        rows.append(("sqlite fts5", MISSING, "use a Python whose SQLite has FTS5 (uv's Python does)"))
    finally:
        connection.close()
    embedding = settings.context.embeddings
    try:
        embeddings.check_embeddings(settings)
        rows.append((f"ollama {embedding.model}", OK, ""))
    except AppException as error:
        rows.append((f"ollama {embedding.model}", MISSING, error.detail))
    total = storage.folder_size(cw_home())
    rows.append((f"cw data {total / storage.BYTES_PER_MB:.1f} MB", OK, ""))
    for size in storage.storage_report(settings):
        project = settings.project(size["project"])
        index = context_db_path(project.name)
        current = ""
        if index.exists():
            with context_index.opened_index(project.name) as opened:
                current = context_index.meta_value(opened, context_index.META_COMMIT)
        base = git.resolve_commit(project.path, project.base_branch) if project.path.exists() else ""
        state = OK if index.exists() and current == base else MISSING
        hint = f"cw context build {project.name}" if not index.exists() else "updated at the next ticket start"
        mb = size["index_bytes"] / storage.BYTES_PER_MB
        rows.append((f"context index {project.name} ({mb:.1f} MB)", state, hint))
        if size["over_limit"]:
            rows.append((f"context index {project.name} size", MISSING, f"cw gc {project.name}"))
    return rows
