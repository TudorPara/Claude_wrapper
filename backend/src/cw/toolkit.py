"""Read agents and skills from the local clone of the toolkit marketplace."""

import fnmatch
import json
import logging
from pathlib import Path, PurePosixPath

import yaml
from pydantic import BaseModel

from cw.config import Settings
from cw.errors import AppErrorCode, AppException

logger = logging.getLogger(__name__)

PLUGINS_DIR = "plugins"
AGENTS_DIR = "agents"
SKILLS_DIR = "skills"
SKILL_FILE = "SKILL.md"
TOPICS_DIR = "topics"
TOPICS_INDEX = "topics.json"
NEW_PROJECT_KEY = "new_project"
REVIEW_KEY = "review"
TOPICS_KEY = "topics"
FOLDER_MARK = "/"
TOPIC_SUFFIX = ".md"
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
    pin: bool
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
                name=path.parent.name,
                plugin=path.parent.parent.parent.name,
                description=front.get("description", ""),
                model_invocable=not front.get("disable-model-invocation", False),
                pin=bool(front.get("pin", False)),
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


def pinned_skills(settings: Settings, plugins: list[str]) -> list[str]:
    """Return, as plugin:skill, every skill of these plugins whose front matter says `pin: true`."""
    return [
        f"{skill.plugin}{PLUGIN_SKILL_SEPARATOR}{skill.name}"
        for skill in list_skills(settings)
        if skill.pin and skill.plugin in plugins
    ]


def _skill_folder(settings: Settings, reference: str) -> Path:
    """Return the folder of a skill referenced as plugin:skill."""
    plugin, _, name = reference.partition(PLUGIN_SKILL_SEPARATOR)
    return plugin_dir(settings, plugin) / SKILLS_DIR / name


def _covers(pattern: str, path: PurePosixPath) -> bool:
    """Return whether a topic pattern covers a path: `name/` is a folder on the path, anything else a file-name glob.

    The toolkit's rules_on_touch.py hook applies the same rule; keep the two alike.
    """
    if pattern.endswith(FOLDER_MARK):
        return pattern.rstrip(FOLDER_MARK) in path.parts[:-1]
    return fnmatch.fnmatch(path.name, pattern)


def pinned_topics(
    settings: Settings, reference: str, new_project: bool, review_files: list[str] | None
) -> dict[str, str]:
    """Return, by name, the topic texts of a rules skill that go into the system prompt for this run.

    While the project has no code yet: the topics listed under `new_project`. For a review: the topics
    listed under `review` and every topic that covers a changed file, so the reviewer has the exact rules.
    """
    folder = _skill_folder(settings, reference) / TOPICS_DIR
    index_file = folder / TOPICS_INDEX
    if not index_file.exists():
        return {}
    index = json.loads(index_file.read_text())
    names = list(index.get(NEW_PROJECT_KEY, [])) if new_project else []
    if review_files is not None:
        names += index.get(REVIEW_KEY, [])
        paths = [PurePosixPath(file) for file in review_files]
        names += [
            topic
            for topic, patterns in index.get(TOPICS_KEY, {}).items()
            if any(_covers(pattern, path) for pattern in patterns for path in paths)
        ]
    return {name: (folder / f"{name}{TOPIC_SUFFIX}").read_text() for name in dict.fromkeys(names)}


def skill_body(settings: Settings, reference: str) -> str:
    """Return the body of a skill referenced as plugin:skill."""
    path = _skill_folder(settings, reference) / SKILL_FILE
    if not path.exists():
        logger.error("Pinned skill %s not found at %s.", reference, path)
        raise AppException(AppErrorCode.INVALID_CONFIGURATION, f"Unknown skill {reference}.")
    return _split_front_matter(path.read_text())[1]
