"""Run one toolkit agent through the headless Claude Code CLI (`claude -p`) and parse its structured result."""

import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path

from pydantic import BaseModel, ValidationError

from cw.config import AgentRole, Project, Settings, conventions_path
from cw.errors import AppErrorCode, AppException
from cw.models import RunUsage, utc_now
from cw.toolkit import load_agent, plugin_dir, skill_body

logger = logging.getLogger(__name__)

STRUCTURED_OUTPUT_TOOL = "StructuredOutput"
READ_ONLY_PERMISSION_MODE = "default"
USAGE_LIMIT_STATUS = 429
USAGE_LIMIT_HINTS = ("usage limit", "rate limit", "limit reached", "limit will reset")
ORCHESTRATED_ENV = "CW_RUN"
ISOLATED_SETTING_SOURCES = "project,local"
PROTECTED_PATHS_ENV = "CW_PROTECTED_PATHS"
TICKET_PLACEHOLDER = "{ticket}"
SAFE_GIT_PREFIXES = ("git status", "git diff", "git log", "git show")
PROTECTED_TOOLS = ("Read", "Edit", "Write")
ERROR_TAIL_CHARS = 2000


def _model_for(project: Project, role: AgentRole) -> str:
    """Return the configured model for a role."""
    return getattr(project.profile.models, role.value)


def _max_turns_for(project: Project, role: AgentRole) -> int:
    """Return the configured turn cap for a role."""
    return getattr(project.profile.limits.max_turns, role.value)


def _rules_prompt(settings: Settings, project: Project, skills: list[str]) -> str:
    """Return the pinned skills and the project's conventions, appended to the system prompt."""
    sections = ["# Team rules for this run"]
    for reference in dict.fromkeys(skills):
        sections.append(f"## Skill {reference}\n\n{skill_body(settings, reference)}")
    conventions = conventions_path(project.name)
    if conventions.exists():
        sections.append(f"## Project conventions (decided at onboarding)\n\n{conventions.read_text()}")
    return "\n\n".join(sections)


def _run_settings(settings: Settings, project: Project, role: AgentRole) -> dict:
    """Return the per-run Claude Code settings: permissions and which plugins load."""
    allow = []
    if role == AgentRole.WORKER:
        commands = [project.commands.lint, project.commands.test]
        prefixes = [command for command in commands if command] + project.extra_allowed_bash + list(SAFE_GIT_PREFIXES)
        allow = [f"Bash({prefix}:*)" for prefix in prefixes]
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


def _plugin_dirs(settings: Settings, project: Project) -> list[str]:
    """Return the toolkit plugin folders loaded for this project's stack."""
    stack = settings.stack(project.stack)
    plugins = settings.toolkit.always_plugins + (stack.toolkit_plugins if stack else [])
    return [str(plugin_dir(settings, plugin)) for plugin in dict.fromkeys(plugins)]


def _environment(project: Project, ticket_id: str) -> dict[str, str]:
    """Return the environment for the agent and its hooks."""
    environment = dict(os.environ)
    environment |= {key: value.replace(TICKET_PLACEHOLDER, ticket_id) for key, value in project.env.items()}
    environment[ORCHESTRATED_ENV] = "1"
    environment[PROTECTED_PATHS_ENV] = json.dumps(project.protected_paths)
    return environment


def _last_json_line(stdout: str) -> dict | None:
    """Return the last JSON object printed by the CLI."""
    for line in reversed(stdout.splitlines()):
        if line.startswith("{"):
            return json.loads(line)
    return None


def _usage(data: dict, role: AgentRole, agent: str, model: str) -> RunUsage:
    """Return the token usage of a finished run."""
    usage = data.get("usage", {})
    models = list(data.get("modelUsage", {}))
    return RunUsage(
        at=utc_now(),
        role=role.value,
        agent=agent,
        model=models[0] if models else model,
        session_id=data.get("session_id", ""),
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        cache_read_tokens=usage.get("cache_read_input_tokens", 0),
        cache_creation_tokens=usage.get("cache_creation_input_tokens", 0),
        cost_usd=data.get("total_cost_usd", 0.0),
        duration_ms=data.get("duration_ms", 0),
        num_turns=data.get("num_turns", 0),
    )


def _raise_for_error(data: dict) -> None:
    """Translate a failed CLI result into the matching error."""
    message = str(data.get("result", ""))
    if data.get("api_error_status") == USAGE_LIMIT_STATUS or any(hint in message.lower() for hint in USAGE_LIMIT_HINTS):
        raise AppException(AppErrorCode.USAGE_LIMIT, message)
    raise AppException(AppErrorCode.AGENT_FAILED, message)


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
) -> tuple[T, RunUsage]:
    """Run a toolkit agent headlessly and return its validated structured result and usage."""
    agent = load_agent(settings, agent_name)
    model = _model_for(project, role)
    stack = settings.stack(project.stack)
    definition = {
        "description": agent.description,
        "prompt": agent.prompt,
        "model": model,
        "maxTurns": _max_turns_for(project, role),
    }
    if agent.tools:
        definition["tools"] = list(dict.fromkeys([*agent.tools, STRUCTURED_OUTPUT_TOOL]))
    skills = agent.skills + (stack.pinned_skills if stack else [])
    permission_mode = project.profile.worker_permission_mode if role == AgentRole.WORKER else READ_ONLY_PERMISSION_MODE
    with tempfile.TemporaryDirectory(prefix="cw-") as folder:
        agents_file = Path(folder) / "agents.json"
        agents_file.write_text(json.dumps({agent.name: definition}))
        settings_file = Path(folder) / "settings.json"
        settings_file.write_text(json.dumps(_run_settings(settings, project, role)))
        rules_file = Path(folder) / "rules.md"
        rules_file.write_text(_rules_prompt(settings, project, skills))
        command = [
            settings.claude_binary,
            "-p",
            "--output-format",
            "json",
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
            "--strict-mcp-config",
            "--setting-sources",
            ISOLATED_SETTING_SOURCES,
        ]
        for folder_path in _plugin_dirs(settings, project):
            command += ["--plugin-dir", folder_path]
        if resume_session:
            command += ["--resume", resume_session]
        logger.info("▶ %s (%s) on %s", agent.name, model, ticket_id)
        completed = subprocess.run(
            command, input=prompt, cwd=cwd, env=_environment(project, ticket_id), capture_output=True, text=True
        )
    data = _last_json_line(completed.stdout)
    if data is None:
        logger.error("%s printed no result; stderr: %s", agent.name, completed.stderr[-ERROR_TAIL_CHARS:])
        raise AppException(AppErrorCode.AGENT_FAILED, completed.stderr[-ERROR_TAIL_CHARS:])
    usage = _usage(data, role, agent.name, model)
    if data.get("is_error"):
        _raise_for_error(data)
    structured = data.get("structured_output")
    if structured is None:
        reason = f"{data.get('terminal_reason')} / {data.get('subtype')}"
        raise AppException(AppErrorCode.AGENT_FAILED, f"{agent.name} returned no structured result ({reason}).")
    try:
        result = output_model.model_validate(structured)
    except ValidationError as error:
        raise AppException(AppErrorCode.AGENT_FAILED, f"{agent.name} returned an invalid result: {error}") from error
    logger.info(
        "✓ %s done: %s turns, %s in / %s out tokens, %s cached",
        agent.name,
        usage.num_turns,
        usage.input_tokens,
        usage.output_tokens,
        usage.cache_read_tokens,
    )
    return result, usage
