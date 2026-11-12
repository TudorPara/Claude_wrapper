"""Read agents and skills from the local clone of the toolkit marketplace."""

import json
import logging
from pathlib import Path

import yaml
from pydantic import BaseModel

from cw.config import Settings
from cw.errors import AppErrorCode, AppException

logger = logging.getLogger(__name__)

PLUGINS_DIR = "plugins"
AGENTS_DIR = "agents"
SKILLS_DIR = "skills"
SKILL_FILE = "SKILL.md"
MANIFEST = Path(".claude-plugin") / "marketplace.json"
FRONT_MATTER_FENCE = "---"
LIST_SEPARATOR = ","
PLUGIN_SKILL_SEPARATOR = ":"


class AgentDefinition(BaseModel):
    """An agent file from the toolkit."""

    name: str
    plugin: str
    description: str
    tools: list[str]
    model: str
    skills: list[str]
    prompt: str
    path: str


class SkillDefinition(BaseModel):
    """A skill folder from the toolkit."""

    name: str
    plugin: str
    description: str
    model_invocable: bool
    path: str


def _split_front_matter(text: str) -> tuple[dict, str]:
    """Return the YAML front matter and the body of a markdown file."""
    if not text.startswith(FRONT_MATTER_FENCE):
        return {}, text
    _, front, body = text.split(FRONT_MATTER_FENCE, 2)
    return yaml.safe_load(front) or {}, body.strip()


def _as_list(value: str | list | None) -> list[str]:
    """Normalise a comma-separated string or YAML list."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value]
    return [item.strip() for item in str(value).split(LIST_SEPARATOR) if item.strip()]


def plugin_dir(settings: Settings, plugin: str) -> Path:
    """Return the folder of a toolkit plugin."""
    return settings.toolkit.path / PLUGINS_DIR / plugin


def toolkit_version(settings: Settings) -> str:
    """Return the marketplace version recorded in every ticket."""
    manifest = settings.toolkit.path / MANIFEST
    if not manifest.exists():
        return "unknown"
    return json.loads(manifest.read_text()).get("metadata", {}).get("version", "unknown")


def list_agents(settings: Settings) -> list[AgentDefinition]:
    """Return every agent in the toolkit."""
    agents = []
    for path in sorted((settings.toolkit.path / PLUGINS_DIR).glob(f"*/{AGENTS_DIR}/*.md")):
        front, body = _split_front_matter(path.read_text())
        agents.append(
            AgentDefinition(
                name=front.get("name", path.stem),
                plugin=path.parent.parent.name,
                description=front.get("description", ""),
                tools=_as_list(front.get("tools")),
                model=front.get("model", ""),
                skills=_as_list(front.get("skills")),
                prompt=body,
                path=str(path),
            )
        )
    return agents


def list_skills(settings: Settings) -> list[SkillDefinition]:
    """Return every skill in the toolkit."""
    skills = []
    for path in sorted((settings.toolkit.path / PLUGINS_DIR).glob(f"*/{SKILLS_DIR}/*/{SKILL_FILE}")):
        front, _ = _split_front_matter(path.read_text())
        skills.append(
            SkillDefinition(
                name=front.get("name", path.parent.name),
                plugin=path.parent.parent.parent.name,
                description=front.get("description", ""),
                model_invocable=not front.get("disable-model-invocation", False),
                path=str(path),
            )
        )
    return skills


def load_agent(settings: Settings, name: str) -> AgentDefinition:
    """Return one agent by name."""
    for agent in list_agents(settings):
        if agent.name == name:
            return agent
    logger.error("Agent %s is not in the toolkit at %s.", name, settings.toolkit.path)
    raise AppException(AppErrorCode.INVALID_CONFIGURATION, f"Unknown agent {name}.")


def skill_body(settings: Settings, reference: str) -> str:
    """Return the body of a skill referenced as plugin:skill."""
    plugin, _, name = reference.partition(PLUGIN_SKILL_SEPARATOR)
    path = plugin_dir(settings, plugin) / SKILLS_DIR / name / SKILL_FILE
    if not path.exists():
        logger.error("Pinned skill %s not found at %s.", reference, path)
        raise AppException(AppErrorCode.INVALID_CONFIGURATION, f"Unknown skill {reference}.")
    return _split_front_matter(path.read_text())[1]
