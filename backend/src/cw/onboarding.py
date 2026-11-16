"""Register a project, map it with the onboarder agent and wire it to the toolkit."""

import json
import logging
import subprocess
from pathlib import Path

from cw import git
from cw.claude_runner import PROTECTED_TOOLS, run_agent
from cw.config import (
    AgentRole,
    GitHostProvider,
    Project,
    ProjectCommands,
    Settings,
    conventions_path,
    get_settings,
    write_settings,
)
from cw.errors import AppErrorCode, AppException
from cw.models import OnboardingResult, utc_now
from cw.prompts import onboarder_prompt

logger = logging.getLogger(__name__)

DEFAULT_REMOTE = "origin"
NEW_PROJECT_BRANCH = "main"
FIRST_COMMIT_MESSAGE = "chore: start project"
NEW_PROJECT_GITIGNORE = "\n".join(
    [
        ".env",
        ".venv/",
        "__pycache__/",
        ".pytest_cache/",
        ".ruff_cache/",
        "node_modules/",
        "target/",
        ".DS_Store",
        ".idea/",
        ".vscode/",
        "",
    ]
)
ONBOARDER_AGENT = "onboarder"
PROJECT_SETTINGS_FILE = Path(".claude") / "settings.json"
HOST_MARKERS = {"github.com": GitHostProvider.GITHUB, "bitbucket.org": GitHostProvider.BITBUCKET}
GITHUB_SOURCE = "github"
CONVENTIONS_GUIDE = (
    "Agents read this file on every run. Set each Decision to one of:\n"
    "- **keep theirs**: keep the project's convention; the team rule does not apply here.\n"
    "- **migrate to ours**: new and touched code follows the team rule; do not mass-migrate untouched code.\n"
    "- **later** or **undecided**: follow the project's existing convention in code you touch."
)


def detect_stack(settings: Settings, path: Path) -> str:
    """Return the first configured stack whose marker files exist in the repository."""
    for name, stack in settings.stacks.items():
        if any((path / marker).exists() for marker in stack.markers):
            return name
    return ""


def _detect_host(url: str) -> GitHostProvider | None:
    """Return the code host from the remote URL."""
    for marker, provider in HOST_MARKERS.items():
        if marker in url:
            return provider
    return None


def add_project(
    path: Path, name: str | None, host: GitHostProvider | None, onboard: bool, stack: str | None = None
) -> Project:
    """Register a repository with the default profile; the stack's commands are its starting commands."""
    settings = get_settings()
    path = path.expanduser().resolve()
    if not git.is_repository(path):
        raise AppException(AppErrorCode.INVALID_CONFIGURATION, f"{path} is not a git repository.")
    detected_host = host or _detect_host(git.remote_url(path, DEFAULT_REMOTE))
    if detected_host is None:
        raise AppException(
            AppErrorCode.INVALID_CONFIGURATION, "Cannot tell the code host; pass --host github|bitbucket."
        )
    stack = stack or detect_stack(settings, path)
    stack_settings = settings.stack(stack)
    project = Project(
        name=name or path.name,
        path=path,
        base_branch=git.default_branch(path, DEFAULT_REMOTE),
        remote=DEFAULT_REMOTE,
        stack=stack,
        git_host=detected_host,
        commands=stack_settings.commands.model_copy() if stack_settings else ProjectCommands(lint="", test=""),
        protected_paths=list(settings.default_protected_paths),
        extra_allowed_bash=[],
        env={},
        profile=settings.defaults.model_copy(deep=True),
    )
    settings.projects = [existing for existing in settings.projects if existing.name != project.name] + [project]
    write_settings(settings)
    logger.info(
        "Registered %s (%s, %s, stack %s).", project.name, project.git_host, project.base_branch, project.stack or "?"
    )
    if onboard:
        return onboard_project(project.name)
    return project


def create_project(name: str, idea: str, parent: Path, stack: str | None, github: str | None) -> Project:
    """Create the repository of a new project (a README and a .gitignore), optionally on GitHub, and register it."""
    settings = get_settings()
    stack = stack or next(iter(settings.stacks), "")
    if settings.stack(stack) is None:
        raise AppException(AppErrorCode.INVALID_CONFIGURATION, f"No stack '{stack}' in the config.")
    path = (parent.expanduser() / name).resolve()
    if path.exists() and any(path.iterdir()):
        raise AppException(AppErrorCode.INVALID_CONFIGURATION, f"{path} already exists and is not empty.")
    path.mkdir(parents=True, exist_ok=True)
    git.init(path, NEW_PROJECT_BRANCH)
    (path / "README.md").write_text(f"# {name}\n\n{idea.strip()}\n")
    (path / ".gitignore").write_text(NEW_PROJECT_GITIGNORE)
    git.commit_all(path, FIRST_COMMIT_MESSAGE)
    if github:
        created = subprocess.run(
            ["gh", "repo", "create", name, f"--{github}", "--source", ".", "--remote", DEFAULT_REMOTE, "--push"],
            cwd=path,
            capture_output=True,
            text=True,
        )
        if created.returncode != 0:
            raise AppException(AppErrorCode.GIT_HOST_FAILED, created.stderr.strip())
    logger.info("Created %s.", path)
    return add_project(path, name, GitHostProvider.GITHUB, onboard=False, stack=stack)


def onboard_project(name: str) -> Project:
    """Optionally map the project with the onboarder agent: commands, architecture, differences from the rules."""
    settings = get_settings()
    project = settings.project(name)
    detected = detect_stack(settings, project.path) or project.stack
    project.stack = detected
    onboarding_result, usage = run_agent(
        settings,
        project,
        ONBOARDER_AGENT,
        AgentRole.ONBOARDER,
        onboarder_prompt(project, detected),
        project.path,
        OnboardingResult,
        f"onboarding-{project.name}",
    )
    logger.info("Onboarding used %s input and %s output tokens.", usage.input_tokens, usage.output_tokens)
    project.stack = detected or onboarding_result.stack
    if settings.stack(project.stack) is None:
        logger.warning("The toolkit has no stack plugin for '%s' yet; agents run with core rules only.", project.stack)
    project.commands = onboarding_result.commands
    _write_conventions(project, onboarding_result)
    write_settings(settings)
    return project


def _write_conventions(project: Project, onboarding_result: OnboardingResult) -> None:
    """Write the onboarding report with one decision per difference, keeping decisions already made."""
    path = conventions_path(project.name)
    lines = [
        f"# Conventions for {project.name}",
        "",
        f"Generated by cw onboarding on {utc_now():%Y-%m-%d}. {CONVENTIONS_GUIDE}",
        "",
        f"- Stack: {onboarding_result.stack}",
        f"- Architecture: {onboarding_result.architecture}",
        f"- Tests: {onboarding_result.test_inventory}",
        f"- Lint: `{project.commands.lint}`",
        f"- Test: `{project.commands.test}`",
    ]
    if onboarding_result.warnings:
        lines += ["", "## Warnings", ""] + [f"- {warning}" for warning in onboarding_result.warnings]
    lines += ["", "## Differences from the team rules"]
    for difference in onboarding_result.differences:
        lines += [
            "",
            f"### {difference.id} · {difference.topic}",
            "",
            f"- Theirs: {difference.theirs}",
            f"- Ours: {difference.ours}",
            f"- Example: `{difference.example}`",
            "- Decision: undecided",
        ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    logger.info("Wrote %s (outside the repository); review the decisions in FRIDAY.", path)


def _build_managed_entries(settings: Settings, project: Project) -> tuple[dict[str, dict], list[str], list[str]]:
    """Return the marketplaces, enabled plugins and deny rules cw manages for a project."""
    marketplaces = {
        settings.toolkit.marketplace: {"source": {"source": GITHUB_SOURCE, "repo": settings.toolkit.github_repo}}
    }
    for name, repository in settings.external_marketplaces.items():
        marketplaces[name] = {"source": {"source": GITHUB_SOURCE, "repo": repository}}
    stack = settings.stack(project.stack)
    own = settings.toolkit.always_plugins + (stack.toolkit_plugins if stack else [])
    external = settings.toolkit.always_external_plugins + (stack.external_plugins if stack else [])
    plugins = [f"{plugin}@{settings.toolkit.marketplace}" for plugin in own] + external
    deny = [f"{tool}({pattern})" for pattern in project.protected_paths for tool in PROTECTED_TOOLS]
    return marketplaces, plugins, deny


def write_project_settings(
    settings: Settings, project: Project, previous: tuple[Settings, Project] | None = None
) -> Path:
    """Write the toolkit marketplaces, plugins and protected paths into the project's .claude/settings.json.

    Entries cw added for the previous version of the project are removed first; everything else is kept.
    """
    path = project.path / PROJECT_SETTINGS_FILE
    project_settings = json.loads(path.read_text()) if path.exists() else {}
    enabled = project_settings.setdefault("enabledPlugins", {})
    deny = project_settings.setdefault("permissions", {}).setdefault("deny", [])
    if previous:
        _, old_plugins, old_deny = _build_managed_entries(*previous)
        for plugin in old_plugins:
            enabled.pop(plugin, None)
        deny[:] = [rule for rule in deny if rule not in old_deny]
    marketplaces, plugins, rules = _build_managed_entries(settings, project)
    project_settings.setdefault("extraKnownMarketplaces", {}).update(marketplaces)
    enabled.update(dict.fromkeys(plugins, True))
    deny.extend(rule for rule in rules if rule not in deny)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(project_settings, indent=2) + "\n")
    logger.info("Updated %s; commit it so the whole team gets the same toolkit.", path)
    return path


def _is_wired(settings: Settings, project: Project) -> bool:
    """Return whether the team opted this repository into the toolkit's .claude/settings.json."""
    path = project.path / PROJECT_SETTINGS_FILE
    return path.exists() and settings.toolkit.marketplace in json.loads(path.read_text()).get(
        "extraKnownMarketplaces", {}
    )


def sync_project_settings(old: Settings | None, new: Settings) -> None:
    """Rewrite .claude/settings.json of every wired project whose toolkit wiring changed."""
    old_projects = {project.name: project for project in old.projects} if old else {}
    for project in new.projects:
        if not project.path.exists() or not _is_wired(new, project):
            continue
        before = old_projects.get(project.name)
        previous = (old, before) if old and before else None
        if previous and _build_managed_entries(*previous) == _build_managed_entries(new, project):
            continue
        write_project_settings(new, project, previous)


def remove_project(name: str) -> None:
    """Unregister a project; its repository and tickets stay where they are."""
    settings = get_settings()
    settings.project(name)
    settings.projects = [project for project in settings.projects if project.name != name]
    write_settings(settings)
