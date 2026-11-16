"""Run one toolkit agent through the headless Claude Code CLI (`claude -p`) and parse its structured result."""

import json
import logging
import os
import tempfile
from pathlib import Path

from pydantic import BaseModel, ValidationError

from cw.config import AgentRole, Project, Settings, conventions_path
from cw.errors import AppErrorCode, AppException
from cw.models import RunUsage, utc_now
from cw.process import run_process
from cw.toolkit import load_agent, pinned_skills, pinned_topics, plugin_dir, skill_body

logger = logging.getLogger(__name__)

STRUCTURED_OUTPUT_TOOL = "StructuredOutput"
READ_ONLY_PERMISSION_MODE = "default"
USAGE_LIMIT_STATUS = 429
USAGE_LIMIT_HINTS = ("usage limit", "rate limit", "limit reached", "limit will reset")
ORCHESTRATED_ENV = "CW_RUN"
ISOLATED_SETTING_SOURCES = "project,local"
PROTECTED_PATHS_ENV = "CW_PROTECTED_PATHS"
SHOWN_TOPICS_ENV = "CW_SHOWN_TOPICS"
SECRET_ENV_PREFIXES = ("BITBUCKET__",)
TICKET_PLACEHOLDER = "{ticket}"
SAFE_GIT_PREFIXES = ("git status", "git diff", "git log", "git show")
PIPE_PREFIXES = ("head", "tail")
PROTECTED_TOOLS = ("Read", "Edit", "Write")
ERROR_TAIL_CHARS = 2000
RESULT_EVENT = "result"
ASSISTANT_EVENT = "assistant"
TOOL_USE_BLOCK = "tool_use"
TOOL_DETAIL_KEYS = ("file_path", "command", "pattern", "path")
TOOL_DETAIL_MAX_CHARS = 100


def _model_for(project: Project, role: AgentRole) -> str:
    """Return the configured model for a role."""
    return getattr(project.profile.models, role.value)


def _max_turns_for(project: Project, role: AgentRole) -> int:
    """Return the configured turn cap for a role."""
    return getattr(project.profile.limits.max_turns, role.value)


def _toolkit_plugins(settings: Settings, project: Project) -> list[str]:
    """Return the toolkit plugins loaded for this project's stack."""
    stack = settings.stack(project.stack)
    return list(dict.fromkeys(settings.toolkit.always_plugins + (stack.toolkit_plugins if stack else [])))


def _rules_prompt(
    settings: Settings,
    project: Project,
    agent_skills: list[str],
    review_files: list[str] | None = None,
    new_project: bool = False,
) -> tuple[str, list[str]]:
    """Return the rules appended to the system prompt, and the names of the topics among them.

    The rules are the pinned skills, the topics this run needs (see `pinned_topics`) and the project conventions.
    """
    stack = settings.stack(project.stack)
    skills = agent_skills + (stack.pinned_skills if stack else [])
    skills += pinned_skills(settings, _toolkit_plugins(settings, project))
    skills = list(dict.fromkeys(skills))
    sections = ["# Team rules for this run"]
    for reference in skills:
        sections.append(f"## Skill {reference}\n\n{skill_body(settings, reference)}")
    topics: dict[str, str] = {}
    for reference in skills:
        topics |= pinned_topics(settings, reference, new_project, review_files)
    sections += topics.values()
    conventions = conventions_path(project.name)
    if conventions.exists():
        sections.append(f"## Project conventions (decided at onboarding)\n\n{conventions.read_text()}")
    return "\n\n".join(sections), list(topics)


def _without_folders(command: str, checkout: Path) -> str:
    """Return the command without trailing folders; never a file, which may be the script itself, not a target."""
    words = command.split()
    while len(words) > 1 and (checkout / words[-1]).is_dir():
        words.pop()
    return " ".join(words)


def _run_settings(settings: Settings, project: Project, role: AgentRole) -> dict:
    """Return the per-run Claude Code settings: permissions and which plugins load."""
    allow = []
    if role == AgentRole.WORKER:
        commands = [command for command in (project.commands.lint, project.commands.test) if command]
        narrowed = [_without_folders(command, project.path) for command in commands]
        prefixes = commands + narrowed + project.extra_allowed_bash + list(SAFE_GIT_PREFIXES + PIPE_PREFIXES)
        allow = [f"Bash({prefix}:*)" for prefix in dict.fromkeys(prefixes)]
    deny = [f"{tool}({pattern})" for pattern in project.protected_paths for tool in PROTECTED_TOOLS]
    own_plugins = {
        f"{path.name}@{settings.toolkit.marketplace}": False for path in (settings.toolkit.path / "plugins").iterdir()
    }
    stack = settings.stack(project.stack)
    external = settings.toolkit.always_external_plugins + (stack.external_plugins if stack else [])
    return {
        "permissions": {"allow": allow, "deny": deny},
        "enabledPlugins": own_plugins | {plugin: True for plugin in external},
    }


def _environment(project: Project, ticket_id: str, shown_topics: list[str]) -> dict[str, str]:
    """Return the environment for the agent and its hooks, without the secrets only cw needs."""
    environment = {key: value for key, value in os.environ.items() if not key.startswith(SECRET_ENV_PREFIXES)}
    environment |= {key: value.replace(TICKET_PLACEHOLDER, ticket_id) for key, value in project.env.items()}
    environment[ORCHESTRATED_ENV] = "1"
    environment[PROTECTED_PATHS_ENV] = json.dumps(project.protected_paths)
    environment[SHOWN_TOPICS_ENV] = ",".join(shown_topics)
    return environment


def _event(line: str) -> dict | None:
    """Return the JSON object on a line of CLI output, or None when the line is something else."""
    if not line.startswith("{"):
        return None
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else None


def _log_tool_calls(agent_name: str, line: str) -> None:
    """Log every tool the agent calls, as it happens, so a run can be followed live."""
    event = _event(line)
    if not event or event.get("type") != ASSISTANT_EVENT:
        return
    for block in event.get("message", {}).get("content", []):
        if block.get("type") != TOOL_USE_BLOCK or block.get("name") == STRUCTURED_OUTPUT_TOOL:
            continue
        tool_input = block.get("input", {})
        detail = next((str(tool_input[key]) for key in TOOL_DETAIL_KEYS if key in tool_input), "")
        logger.info("  %s · %s %s", agent_name, block.get("name"), detail[:TOOL_DETAIL_MAX_CHARS].replace("\n", " "))


def _result_event(stdout: str) -> dict | None:
    """Return the final result event the CLI printed."""
    for line in reversed(stdout.splitlines()):
        event = _event(line)
        if event and event.get("type") == RESULT_EVENT:
            return event
    return None


def _usage(data: dict, role: AgentRole, agent: str, model: str) -> RunUsage:
    """Return the token usage of a finished run."""
    usage = data.get("usage", {})
    model_usage = data.get("modelUsage", {})
    busiest = max(model_usage, key=lambda name: model_usage[name].get("outputTokens", 0), default=model)
    return RunUsage(
        at=utc_now(),
        role=role.value,
        agent=agent,
        model=busiest,
        session_id=data.get("session_id", ""),
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        cache_read_tokens=usage.get("cache_read_input_tokens", 0),
        cache_creation_tokens=usage.get("cache_creation_input_tokens", 0),
        cost_usd=data.get("total_cost_usd", 0.0),
        duration_ms=data.get("duration_ms", 0),
        num_turns=data.get("num_turns", 0),
    )


def _raise_for_error(data: dict, usage: RunUsage) -> None:
    """Translate a failed CLI result into the matching error, keeping the tokens it used."""
    message = str(data.get("result") or data.get("subtype") or "")
    if data.get("api_error_status") == USAGE_LIMIT_STATUS or any(hint in message.lower() for hint in USAGE_LIMIT_HINTS):
        raise AppException(AppErrorCode.USAGE_LIMIT, message, usage)
    raise AppException(AppErrorCode.AGENT_FAILED, message, usage)


def run_agent[T: BaseModel](
    settings: Settings,
    project: Project,
    agent_name: str,
    role: AgentRole,
    prompt: str,
    cwd: Path,
    output_model: type[T],
    ticket_id: str,
    resume_session: str | None = None,
    files: list[str] | None = None,
    new_project: bool = False,
) -> tuple[T, RunUsage]:
    """Run a toolkit agent headlessly and return its validated structured result and usage.

    `files` are the changed files of a review; the rules that cover them are added to the system prompt.
    `new_project` adds the rules for starting a project. Both are the same for every run of a session,
    so the system prompt of a resumed session never changes and its cache stays valid.
    """
    agent = load_agent(settings, agent_name)
    model = _model_for(project, role)
    max_turns = _max_turns_for(project, role)
    definition = {"description": agent.description, "prompt": agent.prompt, "model": model, "maxTurns": max_turns}
    if agent.tools:
        definition["tools"] = list(dict.fromkeys([*agent.tools, STRUCTURED_OUTPUT_TOOL]))
    permission_mode = project.profile.worker_permission_mode if role == AgentRole.WORKER else READ_ONLY_PERMISSION_MODE
    with tempfile.TemporaryDirectory(prefix="cw-") as folder:
        agents_file = Path(folder) / "agents.json"
        agents_file.write_text(json.dumps({agent.name: definition}))
        settings_file = Path(folder) / "settings.json"
        settings_file.write_text(json.dumps(_run_settings(settings, project, role)))
        rules_file = Path(folder) / "rules.md"
        rules, shown_topics = _rules_prompt(settings, project, agent.skills, files, new_project)
        rules_file.write_text(rules)
        command = [
            settings.claude_binary,
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--agents",
            str(agents_file),
            "--agent",
            agent.name,
            "--settings",
            str(settings_file),
            "--append-system-prompt-file",
            str(rules_file),
            "--json-schema",
            json.dumps(output_model.model_json_schema()),
            "--permission-mode",
            permission_mode,
            "--max-turns",
            str(max_turns),
            "--strict-mcp-config",
            "--setting-sources",
            ISOLATED_SETTING_SOURCES,
        ]
        for plugin in _toolkit_plugins(settings, project):
            command += ["--plugin-dir", str(plugin_dir(settings, plugin))]
        if resume_session:
            command += ["--resume", resume_session]
        logger.info("▶ %s (%s) on %s", agent.name, model, ticket_id)
        _, stdout, stderr = run_process(
            command,
            cwd,
            _environment(project, ticket_id, shown_topics),
            project.profile.limits.agent_timeout_minutes,
            stdin_text=prompt,
            on_line=lambda line: _log_tool_calls(agent.name, line),
        )
    data = _result_event(stdout)
    if data is None:
        logger.error("%s printed no result; stderr: %s", agent.name, stderr[-ERROR_TAIL_CHARS:])
        raise AppException(AppErrorCode.AGENT_FAILED, stderr[-ERROR_TAIL_CHARS:])
    usage = _usage(data, role, agent.name, model)
    if data.get("is_error"):
        _raise_for_error(data, usage)
    structured = data.get("structured_output")
    if structured is None:
        reason = f"{data.get('terminal_reason')} / {data.get('subtype')}"
        raise AppException(AppErrorCode.AGENT_FAILED, f"{agent.name} returned no structured result ({reason}).", usage)
    try:
        result = output_model.model_validate(structured)
    except ValidationError as error:
        detail = f"{agent.name} returned an invalid result: {error}"
        raise AppException(AppErrorCode.AGENT_FAILED, detail, usage) from error
    logger.info(
        "✓ %s done: %s turns, %s in / %s out tokens, %s cached",
        agent.name,
        usage.num_turns,
        usage.input_tokens,
        usage.output_tokens,
        usage.cache_read_tokens,
    )
    return result, usage
