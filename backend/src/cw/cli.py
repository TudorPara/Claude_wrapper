"""The `cw` command line. Every view has a --json form, which is what FRIDAY reads."""

import argparse
import json
import logging
import signal
import sys
from importlib import resources
from pathlib import Path

from cw import onboarding, pipeline, repo_map, reports
from cw.config import GitHostProvider, config_path, get_settings, repo_map_path, save_settings, validate_config
from cw.errors import AppException
from cw.tickets import CLOSED_STATUSES, find_ticket, list_tickets, looks_like_ticket_id, ticket_markdown

logger = logging.getLogger(__name__)

EXAMPLE_CONFIG = "config.example.json"
LOG_FORMAT = "%(asctime)s %(message)s"
LOG_DATE_FORMAT = "%H:%M:%S"
FAILURE_EXIT_CODE = 1
STOPPED_EXIT_CODE = 130
GITHUB_VISIBILITIES = ("private", "public")


def _print_json(data: object) -> None:
    """Print data as JSON on stdout."""
    print(json.dumps(data, indent=2, default=str))


def _init(arguments: argparse.Namespace) -> None:
    """Write a starter config."""
    if config_path().exists() and not arguments.force:
        logger.info("%s already exists; pass --force to overwrite it.", config_path())
        return
    data = json.loads(resources.files("cw").joinpath(EXAMPLE_CONFIG).read_text())
    data["toolkit"]["path"] = str(Path(arguments.toolkit).expanduser().resolve())
    data["toolkit"]["github_repo"] = arguments.github_repo
    save_settings(validate_config(data))
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
            onboarding.remove_project(arguments.name)
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
    summaries = [s for s in list_tickets() if not arguments.project or s.project == arguments.project]
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


def _config(arguments: argparse.Namespace) -> None:
    """Print the config, or replace it with validated JSON from stdin."""
    if arguments.config_command == "set":
        new = validate_config(json.load(sys.stdin))
        old = get_settings() if config_path().exists() else None
        save_settings(new)
        onboarding.sync_project_settings(old, new)
        logger.info("Saved %s.", config_path())
        return
    _print_json(get_settings().model_dump(mode="json", exclude={"bitbucket"}))


def _doctor(_: argparse.Namespace) -> None:
    """Check the environment."""
    for check, status, hint in reports.doctor(get_settings()):
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
    project_commands.add_parser("remove", help="unregister a project").add_argument("name")
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

    commands.add_parser("doctor", help="check binaries, tokens, toolkit and plugins").set_defaults(handler=_doctor)
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
