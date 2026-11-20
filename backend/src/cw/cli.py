"""The `cw` command line. Every view has a --json form, which is what FRIDAY reads."""

import argparse
import json
import logging
import signal
import sys
import time
from collections.abc import Callable
from importlib import resources
from pathlib import Path

from cw import (
    benchmark,
    context_engine,
    context_eval,
    context_index,
    embeddings,
    git,
    onboarding,
    pipeline,
    repo_map,
    reports,
    storage,
)
from cw.config import (
    GitHostProvider,
    config_path,
    context_db_path,
    get_settings,
    repo_map_path,
    validate_config,
    write_settings,
)
from cw.context_index import EmbeddingMode, opened_index
from cw.context_search import ALL_CHANNELS, Channel, build_seed, layout_outline
from cw.errors import AppErrorCode, AppException
from cw.models import TicketStatus, utc_now
from cw.project_memory import legacy_notes
from cw.tickets import (
    CLOSED_STATUSES,
    find_ticket,
    list_tickets,
    looks_like_ticket_id,
    project_lock,
    read_events,
    read_ticket,
    ticket_markdown,
    ticket_path,
)

logger = logging.getLogger(__name__)

EXAMPLE_CONFIG = "config.example.json"
LOG_FORMAT = "%(asctime)s %(message)s"
LOG_DATE_FORMAT = "%H:%M:%S"
FAILURE_EXIT_CODE = 1
STOPPED_EXIT_CODE = 130
GITHUB_VISIBILITIES = ("private", "public")
DEFAULT_EVENTS_SHOWN = 300
BUILD_LOCK_LABEL = "build"
SEARCH_EVENTS_PREFIX = "search-"
NOTES_IMPORT_ID = "notes-import"
NOTES_IMPORT_TITLE = "Notes imported from CLAUDE.md"
CONFIRM_YES = ("y", "yes")
PROGRESS_EVERY_SECONDS = 10
HEAD = "HEAD"


def _print_json(payload: object) -> None:
    """Print a payload as JSON on stdout."""
    print(json.dumps(payload, indent=2, default=str))


def _init(arguments: argparse.Namespace) -> None:
    """Write a starter config."""
    if config_path().exists() and not arguments.force:
        logger.info("%s already exists; pass --force to overwrite it.", config_path())
        return
    starter_config = json.loads(resources.files("cw").joinpath(EXAMPLE_CONFIG).read_text())
    starter_config["toolkit"]["path"] = str(Path(arguments.toolkit).expanduser().resolve())
    starter_config["toolkit"]["github_repo"] = arguments.github_repo
    write_settings(validate_config(starter_config))
    logger.info("Wrote %s.", config_path())


def _project(arguments: argparse.Namespace) -> None:
    """Add, onboard, remove or list projects."""
    match arguments.project_command:
        case "add":
            host = GitHostProvider(arguments.host) if arguments.host else None
            project = onboarding.add_project(Path(arguments.path), arguments.name, host, arguments.onboard)
            logger.info("Project %s is ready.", project.name)
        case "onboard":
            onboarding.onboard_project(arguments.name)
        case "remove":
            _remove_project(arguments)
        case "list":
            _print_json(reports.project_overview(get_settings()))
        case "wire":
            settings = get_settings()
            path = onboarding.write_project_settings(settings, settings.project(arguments.name))
            logger.info("Wrote %s. Commit it so teammates' Claude Code installs the same toolkit.", path)
        case "map":
            settings = get_settings()
            project = settings.project(arguments.name)
            map_path = repo_map_path(project.name)
            content = repo_map.write_project_repo_map(project.path, map_path)
            if arguments.json:
                _print_json({"project": project.name, "path": str(map_path), "lines": len(content.splitlines())})
            else:
                print(content)


def _confirm(question: str) -> bool:
    """Ask a yes/no question on the terminal; anything but yes is no."""
    return input(f"{question} [y/N] ").strip().lower() in CONFIRM_YES


def _remove_project(arguments: argparse.Namespace) -> None:
    """Unregister a project and delete its rebuildable caches; --purge also deletes its decisions and tickets."""
    get_settings().project(arguments.name)
    caches = storage.cache_files(arguments.name)
    purged = storage.purge_targets(arguments.name) if arguments.purge else []
    if purged and not arguments.yes:
        print("This deletes:\n" + "\n".join(f"  {path}" for path in purged))
        if not _confirm("Delete them?"):
            logger.info("Nothing was removed.")
            return
    onboarding.remove_project(arguments.name)
    storage.delete_paths(caches + purged)
    logger.info("Unregistered %s; deleted %s.", arguments.name, ", ".join(map(str, caches + purged)) or "nothing")


def _progress_logger(started: float) -> Callable[[int, int], None]:
    """Return a callback that logs embedding progress about every ten seconds."""
    last = [started]

    def report(done: int, total: int) -> None:
        now = time.monotonic()
        if now - last[0] < PROGRESS_EVERY_SECONDS and done < total:
            return
        last[0] = now
        rate = done / max(now - started, 1e-9)
        left = (total - done) / rate if rate else 0
        logger.info("Embedded %s of %s chunks, %.1f a second, about %.0f s left.", done, total, rate, left)

    return report


def _context_build(project_name: str, prune: bool) -> None:
    """Build a project's whole index at HEAD: every chunk embedded, every finished ticket recorded."""
    settings = get_settings()
    project = settings.project(project_name)
    started = time.monotonic()
    with project_lock(project.name, BUILD_LOCK_LABEL):
        embeddings.check_embeddings(settings)
        if prune:
            kept = context_index.reset_index(project.name)
            logger.info("Deleted the old index of %s, keeping its %s ticket record(s).", project.name, kept)
        with opened_index(project.name) as connection:
            head = git.resolve_commit(project.path, HEAD)
            result = context_index.update_snapshot(
                connection, project, settings, head, embedding=EmbeddingMode.ALL, on_progress=_progress_logger(started)
            )
            records = 0
            for summary in list_tickets():
                if summary.project == project.name and summary.status == TicketStatus.DONE:
                    ticket = read_ticket(ticket_path(project.name, summary.id))
                    record = context_engine.ticket_record(settings, ticket, ticket.updated_at)
                    context_engine.save_record(connection, settings, record)
                    records += 1
    report = storage.collect_garbage(settings, project, events=False)
    logger.info(
        "Built the index of %s at %s: %s files, %s blobs parsed, %s chunks embedded, %s ticket records, %.0f s; "
        "%.1f MB after cleanup.",
        project.name,
        head[:12],
        result.files,
        result.blobs_parsed,
        result.chunks_embedded,
        records,
        time.monotonic() - started,
        report.size_after_bytes / storage.BYTES_PER_MB,
    )


def _context_status(project_name: str, as_json: bool) -> None:
    """Print what the index holds and whether it is current."""
    settings = get_settings()
    project = settings.project(project_name)
    key = context_index.model_key(settings)
    with opened_index(project.name) as connection:
        indexed = context_index.meta_value(connection, context_index.META_COMMIT)
        status = {
            "project": project.name,
            "indexed_commit": indexed,
            "is_head": bool(indexed) and indexed == git.resolve_commit(project.path, HEAD),
            **context_index.counts(connection),
            "model_key": key,
            "embedded_chunks": context_index.embedded_snapshot_chunks(connection, key),
        }
    path = context_db_path(project.name)
    status["size_bytes"] = path.stat().st_size if path.exists() else 0
    dry_run = storage.collect_garbage(settings, project, dry_run=True, events=False)
    status["gc_would_delete"] = {"blobs": dry_run.blobs_deleted, "vectors": dry_run.vectors_deleted}
    status["size_warning"] = context_engine.size_warning(project.name, settings)
    if as_json:
        _print_json(status)
        return
    for name, value in status.items():
        print(f"{name:<18} {value}")


def _context_search(arguments: argparse.Namespace) -> None:
    """Print the seed a planner would get for this text."""
    settings = get_settings()
    project = settings.project(arguments.project)
    channels = ALL_CHANNELS - {Channel.HYDE} if arguments.no_hyde else ALL_CHANNELS
    with opened_index(project.name) as connection:
        hyde = None
        if not arguments.no_hyde:
            layout = layout_outline(context_index.snapshot_paths(connection), settings.context.layout_max_folders)
            events_id = f"{SEARCH_EVENTS_PREFIX}{project.name}"
            hyde, _, warnings = context_engine.sketch_outside_ticket(
                settings, project, events_id, arguments.text, layout
            )
            for warning in warnings:
                logger.warning("%s", warning)
        seed = build_seed(connection, settings, arguments.text, channels, utc_now(), set(), hyde)
    if arguments.json:
        _print_json(seed.model_dump(mode="json"))
        return
    for warning in seed.warnings:
        logger.warning("%s", warning)
    print(seed.text or "(nothing found)")


def _context_eval(arguments: argparse.Namespace) -> None:
    """Score the seed against the files past changes really touched."""
    settings = get_settings()
    project = settings.project(arguments.project)
    methods = (
        [context_eval.Method(method) for method in arguments.method] if arguments.method else list(context_eval.Method)
    )

    def confirm(samples: int) -> bool:
        return arguments.yes or _confirm(
            f"The full method runs {samples} HyDE sketches ({project.profile.models.context}). Go on?"
        )

    report = context_eval.evaluate(settings, project, methods, arguments.from_commits, confirm)
    storage.collect_garbage(settings, project, events=False)
    if arguments.json:
        _print_json(report.model_dump(mode="json"))
        return
    print(
        f"{report.project}: {report.samples} {report.source} samples, {report.dropped_truth_files} created files left "
        f"out of the truth, {report.elapsed_seconds} s"
    )
    k_values = settings.context.eval_k_values
    print(
        f"{'method':<15}" + "".join(f"{'R@' + str(k):>8}" for k in k_values) + f"{'MRR':>8}{'samples':>9}{'approx':>8}"
    )
    for score in report.scores:
        recalls = "".join(f"{score.recall[k]:>8.2f}" for k in k_values)
        print(f"{score.method:<15}{recalls}{score.mrr:>8.2f}{score.samples:>9}{score.approximate:>8}")
    for method, reason in report.skipped_methods.items():
        print(f"skipped {method}: {reason}")
    if report.hyde_runs:
        print(f"HyDE: {report.hyde_runs} runs, ${report.hyde_cost_usd:.4f}")


def _migrate_notes(project_name: str) -> None:
    """Copy the old "Notes kept by cw" section of CLAUDE.md into a ticket record; CLAUDE.md is left as it is."""
    settings = get_settings()
    project = settings.project(project_name)
    notes = legacy_notes(project.path)
    with project_lock(project.name, NOTES_IMPORT_ID), opened_index(project.name) as connection:
        existing = next(
            (record for record in context_index.all_records(connection) if record.ticket_id == NOTES_IMPORT_ID), None
        )
        known = existing.notes if existing else []
        new = [note for note in notes if note not in known]
        if new:
            record = context_index.TicketRecord(
                ticket_id=NOTES_IMPORT_ID,
                title=NOTES_IMPORT_TITLE,
                finished_at=existing.finished_at if existing else utc_now(),
                request=NOTES_IMPORT_TITLE,
                plan_excerpt="",
                changed_files=[],
                open_findings=[],
                notes=known + new,
            )
            context_engine.save_record(connection, settings, record)
    logger.info("Imported %s new note(s) from CLAUDE.md into the index of %s.", len(new), project.name)


def _context(arguments: argparse.Namespace) -> None:
    """Build, inspect, search or evaluate a project's context index."""
    match arguments.context_command:
        case "build":
            _context_build(arguments.project, arguments.prune)
        case "status":
            _context_status(arguments.project, arguments.json)
        case "search":
            _context_search(arguments)
        case "eval":
            _context_eval(arguments)
        case "migrate-notes":
            _migrate_notes(arguments.project)


def _gc(arguments: argparse.Namespace) -> None:
    """Clean old index data and expired event logs, for one project or all."""
    settings = get_settings()
    projects = [settings.project(arguments.project)] if arguments.project else settings.projects
    reports_made = []
    for project in projects:
        try:
            reports_made.append(storage.collect_garbage(settings, project, arguments.dry_run))
        except AppException as error:
            if error.error_code != AppErrorCode.TICKET_BUSY:
                raise
            logger.warning("Skipped %s: %s", project.name, error)
    if arguments.json:
        _print_json([report.model_dump() for report in reports_made])
        return
    for report in reports_made:
        verb = "would delete" if report.dry_run else "deleted"
        print(
            f"{report.project}: {verb} {report.blobs_deleted} file versions, {report.vectors_deleted} vectors, "
            f"{report.events_deleted} event logs; index {report.size_before_bytes / storage.BYTES_PER_MB:.1f} MB → "
            f"{report.size_after_bytes / storage.BYTES_PER_MB:.1f} MB"
        )


def _audit(arguments: argparse.Namespace) -> None:
    """Review a ticket's final diff once more, for a benchmark."""
    ticket = benchmark.audit(arguments.ticket)
    if arguments.json:
        _print_json(ticket.audit.model_dump(mode="json"))
        return
    print(f"{ticket.id}: {ticket.audit.blocking} blocking, {ticket.audit.minor} minor (${ticket.audit.cost_usd:.4f})")
    for finding in ticket.audit.findings:
        print(f"  {finding.path}:{finding.line} {finding.severity} {finding.body}")


def _benchmark(arguments: argparse.Namespace) -> None:
    """Print cost and quality per ticket of a project."""
    rows = benchmark.benchmark_report(arguments.project)
    if arguments.json:
        _print_json(rows)
        return
    for row in rows:
        audit = "not audited" if row["audit_blocking"] is None else f"audit {row['audit_blocking']} blocking"
        print(
            f"{row['ticket']:<44} ${row['cost_usd']:<8} reviews {row['reviews_by_model']} "
            f"fix rounds {row['pull_request_fix_rounds']} failing {len(row['failing_checks'])} "
            f"open {row['open_findings']} {audit}"
        )
    print(f"total ${sum(row['cost_usd'] for row in rows):.4f} over {len(rows)} tickets")


def _new(arguments: argparse.Namespace) -> None:
    """Create a project from nothing and start its first ticket with the planner."""
    project = onboarding.create_project(
        arguments.name, arguments.idea, Path(arguments.path), arguments.stack, arguments.github
    )
    _report(pipeline.start_ticket(project.name, arguments.idea, plan_first=True, new_project=True).id)


def _run(arguments: argparse.Namespace) -> None:
    """Start a ticket in a project, or add a prompt to an existing ticket."""
    settings = get_settings()
    if any(project.name == arguments.target for project in settings.projects):
        ticket = pipeline.start_ticket(arguments.target, arguments.prompt, arguments.plan, base=arguments.base)
    elif looks_like_ticket_id(arguments.target):
        ticket = pipeline.follow_up(arguments.target, arguments.prompt)
    else:
        raise SystemExit(f"'{arguments.target}' is neither a project nor a ticket id.")
    _report(ticket.id)


def _report(ticket_id: str) -> None:
    """Log where the ticket stands and what to do next."""
    ticket = find_ticket(ticket_id)
    hints = {
        "needs_input": (
            f"start them as separate tickets: cw split {ticket.id}"
            if ticket.split
            else f'answer with: cw run {ticket.id} "<answers>"'
        ),
        "in_progress": f"it stopped before it finished; continue with: cw resume {ticket.id}",
        "awaiting_approval": f"review the plan, then: cw approve {ticket.id}",
        "ready": f'follow up with cw run {ticket.id} "...", or review and open the PR with: cw pr {ticket.id}',
        "pr_open": (
            f"address its comments with cw fix {ticket.id}, then merge it with: cw merge {ticket.id}"
            if ticket.pr_url
            else f"look at the commit on {ticket.branch}, then merge it with: cw done {ticket.id}"
        ),
        "paused": f"usage limit reached; later run: cw resume {ticket.id}",
        "blocked": f'fix the cause, then cw resume {ticket.id} or cw run {ticket.id} "..."',
    }
    logger.info("%s → %s. %s", ticket.id, ticket.status, hints.get(ticket.status, ""))
    for question in ticket.questions:
        logger.info("  ? %s", question)
    for item in ticket.split:
        logger.info("  → %s", item)
    for warning in ticket.warnings:
        logger.info("  ! %s", warning)


def _status(arguments: argparse.Namespace) -> None:
    """List tickets."""
    summaries = [summary for summary in list_tickets() if not arguments.project or summary.project == arguments.project]
    if not arguments.include_archived:
        summaries = [summary for summary in summaries if not summary.archived]
    if arguments.json:
        _print_json([summary.model_dump(mode="json") for summary in summaries])
        return
    for summary in summaries:
        running = " (running)" if summary.running else ""
        print(f"{summary.project:<20} {summary.id:<44} {summary.status:<18}{running} {summary.title}")


def _show(arguments: argparse.Namespace) -> None:
    """Show one ticket."""
    ticket = find_ticket(arguments.ticket)
    if arguments.json:
        _print_json(ticket.model_dump(mode="json") | {"archived": ticket.status in CLOSED_STATUSES})
        return
    print(ticket_markdown(ticket))


def _events(arguments: argparse.Namespace) -> None:
    """Show the ticket's latest agent events: starts, tool calls, ends, failures and checks."""
    ticket = find_ticket(arguments.ticket)
    events = read_events(ticket.project, ticket.id, arguments.last)
    if arguments.json:
        _print_json(events)
        return
    for event in events:
        print(" ".join(str(value) for key, value in event.items() if key != "role"))


def _config(arguments: argparse.Namespace) -> None:
    """Print the config, or replace it with validated JSON from stdin."""
    if arguments.config_command == "set":
        new = validate_config(json.load(sys.stdin))
        old = get_settings() if config_path().exists() else None
        write_settings(new)
        onboarding.sync_project_settings(old, new)
        logger.info("Saved %s.", config_path())
        return
    _print_json(get_settings().model_dump(mode="json", exclude={"bitbucket"}))


def _doctor(arguments: argparse.Namespace) -> None:
    """Check the environment."""
    report = reports.build_doctor_report(get_settings())
    if arguments.json:
        _print_json([{"check": check, "status": status, "hint": hint} for check, status, hint in report])
        return
    for check, status, hint in report:
        print(f"{status:<8} {check:<50} {'' if status == reports.OK else hint}")


def _parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(prog="cw", description="Claude Code orchestrator: tickets, agents, branches, PRs.")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="write a starter config")
    init.add_argument("--toolkit", required=True, help="local clone of the toolkit repository")
    init.add_argument("--github-repo", required=True, help="owner/name of the toolkit on GitHub")
    init.add_argument("--force", action="store_true")
    init.set_defaults(handler=_init)

    project = commands.add_parser("project", help="manage projects")
    project_commands = project.add_subparsers(dest="project_command", required=True)
    add = project_commands.add_parser("add", help="register a repository (no agent runs)")
    add.add_argument("path")
    add.add_argument("--name")
    add.add_argument("--host", choices=[provider.value for provider in GitHostProvider])
    add.add_argument(
        "--onboard", action="store_true", help="also map the project with the onboarder agent (costs tokens)"
    )
    project_commands.add_parser("onboard", help="map the project with the onboarder agent").add_argument("name")
    remove = project_commands.add_parser("remove", help="unregister a project and delete its rebuildable caches")
    remove.add_argument("name")
    remove.add_argument("--purge", action="store_true", help="also delete its decisions and all its tickets")
    remove.add_argument("--yes", action="store_true", help="do not ask before --purge deletes")
    project_commands.add_parser("list", help="print each project's codebase state as JSON")
    project_commands.add_parser("wire", help="write the toolkit into the repo's .claude/settings.json").add_argument(
        "name"
    )
    map_command = project_commands.add_parser("map", help="generate or print the deterministic architecture map")
    map_command.add_argument("name")
    map_command.add_argument("--json", action="store_true")
    project.set_defaults(handler=_project)

    new = commands.add_parser("new", help="create a project from nothing and plan it from your idea")
    new.add_argument("name", help="folder and project name")
    new.add_argument("idea", help="what the project should do")
    new.add_argument("--path", default=".", help="folder to create it in (default: the current folder)")
    new.add_argument("--stack", help="stack from the config (default: the first one)")
    new.add_argument("--github", choices=GITHUB_VISIBILITIES, help="also create the repository on GitHub with gh")
    new.set_defaults(handler=_new)

    run = commands.add_parser("run", help="start a ticket in a project, or follow up on a ticket")
    run.add_argument("target", help="project name or ticket id")
    run.add_argument("prompt")
    run.add_argument("--plan", action="store_true", help="plan first instead of going straight to a worker")
    run.add_argument("--base", help="start the ticket from this branch instead of the project's base branch")
    run.set_defaults(handler=_run)

    for name, help_text, function in (
        ("approve", "approve the plan and run the workers", pipeline.approve),
        ("resume", "re-run the step that paused, blocked or was stopped", pipeline.resume),
        ("review", "run the checks and the reviewer on the change; nothing is committed", pipeline.review),
        ("pr", "review the change, commit it once, push and open the pull request", pipeline.open_pull_request),
        ("fix", "let the worker address the pull request's open comments, then push", pipeline.fix_pull_request),
        ("merge", "squash-merge the pull request once CI passed, then close the ticket", pipeline.merge_pull_request),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("ticket")
        command.set_defaults(handler=lambda arguments, function=function: _report(function(arguments.ticket).id))

    split = commands.add_parser("split", help="start one ticket per change the planner split out")
    split.add_argument("ticket")
    split.set_defaults(
        handler=lambda arguments: [_report(ticket.id) for ticket in pipeline.split_ticket(arguments.ticket)]
    )

    park = commands.add_parser("park", help="save the ticket's work as a WIP commit and switch back")
    park.add_argument("ticket")
    park.set_defaults(handler=lambda arguments: _report(pipeline.park(arguments.ticket).id))

    ci = commands.add_parser("ci", help="show the pull request's CI status")
    ci.add_argument("ticket")
    ci.set_defaults(handler=lambda arguments: print(pipeline.ci_status(arguments.ticket)))

    done = commands.add_parser("done", help="close the ticket and switch back to your previous branch")
    done.add_argument("ticket")
    done.add_argument("--abandon", action="store_true", help="discard its uncommitted changes and delete the branch")
    done.set_defaults(handler=lambda arguments: pipeline.finish(arguments.ticket, arguments.abandon))

    status = commands.add_parser("status", help="list tickets")
    status.add_argument("--project")
    status.add_argument("--include-archived", action="store_true")
    status.add_argument("--json", action="store_true")
    status.set_defaults(handler=_status)

    show = commands.add_parser("show", help="show a ticket")
    show.add_argument("ticket")
    show.add_argument("--json", action="store_true")
    show.set_defaults(handler=_show)

    events = commands.add_parser("events", help="a ticket's latest agent events, for following it live")
    events.add_argument("ticket")
    events.add_argument("--last", type=int, default=DEFAULT_EVENTS_SHOWN, help="how many of the latest events")
    events.add_argument("--json", action="store_true")
    events.set_defaults(handler=_events)

    stats = commands.add_parser("stats", help="token usage per project and role")
    stats.add_argument("--json", action="store_true")
    stats.set_defaults(
        handler=lambda arguments: (
            _print_json(reports.usage_stats(get_settings()))
            if arguments.json
            else [print(row) for row in reports.usage_stats(get_settings())]
        )
    )

    toolkit = commands.add_parser("toolkit", help="agents, skills and models")
    toolkit.add_argument("--json", action="store_true")
    toolkit.set_defaults(handler=lambda arguments: _print_json(reports.toolkit_inventory(get_settings())))

    config = commands.add_parser("config", help="print or replace the config")
    config.add_argument("config_command", nargs="?", choices=["show", "set"], default="show")
    config.add_argument("--json", action="store_true")
    config.set_defaults(handler=_config)

    context = commands.add_parser("context", help="the per-project context index the planner's seed comes from")
    context_commands = context.add_subparsers(dest="context_command", required=True)
    build = context_commands.add_parser("build", help="build the whole index at HEAD, embeddings included")
    build.add_argument("project")
    build.add_argument("--prune", action="store_true", help="delete the index first and build it from scratch")
    context_status = context_commands.add_parser("status", help="what the index holds and whether it is current")
    context_status.add_argument("project")
    context_status.add_argument("--json", action="store_true")
    search = context_commands.add_parser("search", help="print the seed a planner would get for a text")
    search.add_argument("project")
    search.add_argument("text")
    search.add_argument("--json", action="store_true")
    search.add_argument("--no-hyde", action="store_true", help="skip the HyDE sketch (no model call)")
    evaluation = context_commands.add_parser("eval", help="score the seed against the files changes really touched")
    evaluation.add_argument("project")
    evaluation.add_argument("--from-commits", type=int, help="replay the last N commits instead of finished tickets")
    evaluation.add_argument("--method", action="append", choices=[method.value for method in context_eval.Method])
    evaluation.add_argument("--json", action="store_true")
    evaluation.add_argument("--yes", action="store_true", help="do not ask before the HyDE runs of the full method")
    context_commands.add_parser("migrate-notes", help="copy CLAUDE.md's old cw notes into the index").add_argument(
        "project"
    )
    context.set_defaults(handler=_context)

    gc = commands.add_parser("gc", help="remove old index data and expired event logs")
    gc.add_argument("project", nargs="?")
    gc.add_argument("--dry-run", action="store_true")
    gc.add_argument("--json", action="store_true")
    gc.set_defaults(handler=_gc)

    audit = commands.add_parser("audit", help="review a ticket's final diff once more (for benchmarks)")
    audit.add_argument("ticket")
    audit.add_argument("--json", action="store_true")
    audit.set_defaults(handler=_audit)

    bench = commands.add_parser("benchmark", help="cost and quality per ticket of a project")
    bench.add_argument("project")
    bench.add_argument("--json", action="store_true")
    bench.set_defaults(handler=_benchmark)

    doctor = commands.add_parser("doctor", help="check binaries, tokens, toolkit and plugins")
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(handler=_doctor)
    return parser


def main() -> None:
    """Entry point."""
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt=LOG_DATE_FORMAT, stream=sys.stderr)
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    arguments = _parser().parse_args()
    try:
        arguments.handler(arguments)
    except AppException as error:
        logger.error("✗ %s", error)
        sys.exit(FAILURE_EXIT_CODE)
    except KeyboardInterrupt:
        logger.error("✗ Stopped. Continue a ticket with: cw resume <ticket>")
        sys.exit(STOPPED_EXIT_CODE)
