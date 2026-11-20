"""Benchmark support: an extra review of a ticket's final diff, and a per-ticket report of cost and quality.

Both are for comparing versions of cw on the same prompts. An audit is kept apart from the ticket's runs, so it
never changes what `cw stats` reports.
"""

import logging
from collections import Counter, defaultdict
from pathlib import Path

from cw import git, prompts
from cw.claude_runner import run_agent
from cw.config import AgentRole, get_settings
from cw.errors import AppErrorCode, AppException
from cw.models import AuditResult, ReviewerResult, Severity, Ticket, TicketStatus, utc_now
from cw.reports import run_costs
from cw.tickets import find_ticket, list_tickets, project_lock, read_ticket, ticket_path, write_ticket

logger = logging.getLogger(__name__)

AUDIT_LABEL = "audit"
FIRST_PARENT = "^1"


def _final_diff_range(ticket: Ticket, checkout: Path) -> tuple[str, str]:
    """Return the commits whose difference is the ticket's final change: its merge, or its committed branch."""
    if ticket.merge_commit:
        return f"{ticket.merge_commit}{FIRST_PARENT}", ticket.merge_commit
    if git.resolve_commit(checkout, ticket.branch):
        return ticket.base_sha, ticket.branch
    raise AppException(AppErrorCode.INVALID_STATE, f"{ticket.id} has no committed change to audit.")


def audit(ticket_id: str) -> Ticket:
    """Review the ticket's final diff once more with the reviewer model and store the counts on the ticket."""
    settings = get_settings()
    ticket = find_ticket(ticket_id)
    project = settings.project(ticket.project)
    checkout = project.path
    old, new = _final_diff_range(ticket, checkout)
    full_diff = git.diff(checkout, old, new)
    diff = full_diff[: project.profile.limits.max_diff_chars]
    model = project.profile.models.reviewer
    with project_lock(project.name, ticket.id):
        result, usage = run_agent(
            settings,
            project,
            project.profile.reviewers[0],
            AgentRole.REVIEWER,
            prompts.reviewer_prompt(project, ticket, diff, len(full_diff) > len(diff), []),
            checkout,
            ReviewerResult,
            ticket.id,
            files=git.changed_files(checkout, old, new),
            label=AUDIT_LABEL,
            model=model,
        )
    severities = Counter(comment.severity for comment in result.comments)
    ticket.audit = AuditResult(
        at=utc_now(),
        model=model,
        blocking=severities[Severity.BLOCKING],
        minor=severities[Severity.MINOR],
        findings=result.comments,
        cost_usd=usage.cost_usd,
    )
    write_ticket(ticket)
    logger.info("Audit of %s: %s blocking, %s minor.", ticket.id, ticket.audit.blocking, ticket.audit.minor)
    return ticket


def benchmark_report(project_name: str) -> list[dict]:
    """Return per ticket of a project: cost per role, reviews per model, turns, and the quality signals."""
    rows = []
    for summary in sorted(list_tickets(), key=lambda item: item.id):
        if summary.project != project_name or summary.status == TicketStatus.ABANDONED:
            continue
        ticket = read_ticket(ticket_path(project_name, summary.id))
        cost: dict[str, float] = defaultdict(float)
        turns: dict[str, int] = defaultdict(int)
        for run, run_cost in run_costs(ticket.runs):
            cost[run.role] += run_cost
            turns[run.role] += run.num_turns
        reviews = Counter(run.model for run in ticket.runs if run.role == AgentRole.REVIEWER)
        rows.append(
            {
                "ticket": ticket.id,
                "title": ticket.title,
                "status": ticket.status,
                "cost_usd": round(sum(cost.values()), 4),
                "cost_by_role": {role: round(value, 4) for role, value in sorted(cost.items())},
                "turns_by_role": dict(sorted(turns.items())),
                "reviews_by_model": dict(sorted(reviews.items())),
                "failing_checks": ticket.failing_checks,
                "pull_request_fix_rounds": ticket.pull_request_fix_rounds,
                "open_findings": len(ticket.open_findings),
                "audit_blocking": ticket.audit.blocking if ticket.audit else None,
                "audit_minor": ticket.audit.minor if ticket.audit else None,
            }
        )
    return rows
