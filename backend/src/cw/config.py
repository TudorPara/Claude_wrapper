"""cw settings: one JSON file (edited by hand, by `cw config set` or by FRIDAY) plus secrets from the environment."""

import json
import logging
import os
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveInt,
    SecretStr,
    ValidationError,
)
from pydantic_settings import BaseSettings, JsonConfigSettingsSource, PydanticBaseSettingsSource, SettingsConfigDict

from cw.errors import AppErrorCode, AppException

logger = logging.getLogger(__name__)

HOME_ENV = "CW_HOME"
DEFAULT_HOME = Path.home() / ".cw"
CONFIG_FILE_NAME = "config.json"
PROJECTS_DIR = "projects"
CONVENTIONS_FILE_NAME = "conventions.md"
REPO_MAP_FILE_NAME = "repo_map.txt"
SECRET_FIELDS = {"bitbucket"}

ExpandedPath = Annotated[Path, AfterValidator(lambda path: path.expanduser())]


class AgentRole(StrEnum):
    """The four agent roles cw runs."""

    PLANNER = "planner"
    WORKER = "worker"
    REVIEWER = "reviewer"
    ONBOARDER = "onboarder"


class GitHostProvider(StrEnum):
    """Supported code hosts."""

    GITHUB = "github"
    BITBUCKET = "bitbucket"


class StrictModel(BaseModel):
    """Base for config groups: unknown keys are typos, so they are rejected."""

    model_config = ConfigDict(extra="forbid")


class RoleModels(StrictModel):
    """Claude model alias or id per agent role."""

    planner: str
    worker: str
    reviewer: str
    onboarder: str


class RoleTurns(StrictModel):
    """Maximum agentic turns per agent role."""

    planner: PositiveInt
    worker: PositiveInt
    reviewer: PositiveInt
    onboarder: PositiveInt


class Limits(StrictModel):
    """Caps that keep token usage and loops bounded."""

    max_review_rounds: NonNegativeInt = Field(description="Fix rounds after a reviewer asks for changes.")
    max_fix_rounds: NonNegativeInt = Field(description="Fix rounds after lint or tests fail.")
    max_conflict_rounds: NonNegativeInt = Field(description="Attempts to resolve merge conflicts with the base branch.")
    max_question_rounds: NonNegativeInt = Field(
        description="Times the planner may ask you, and times a worker may ask per instruction."
    )
    max_workers: PositiveInt = Field(default=4, description="Most workers the planner may spread a plan over.")
    escalate_file_threshold: PositiveInt = Field(description="A direct task touching more files goes to the planner.")
    summary_max_lines: PositiveInt = Field(description="Longest summary an agent may write into the ticket.")
    max_diff_chars: PositiveInt = Field(description="Longest diff sent to the reviewer.")
    max_check_output_chars: PositiveInt = Field(description="Longest lint/test output sent back to the worker.")
    agent_timeout_minutes: PositiveInt = Field(default=45, description="Longest one agent run may take.")
    check_timeout_minutes: PositiveInt = Field(default=15, description="Longest one lint or test command may take.")
    max_turns: RoleTurns


class Profile(StrictModel):
    """Models, limits and permissions a project runs with."""

    models: RoleModels
    limits: Limits
    worker_permission_mode: str
    reviewers: list[str] = Field(description="Reviewer agent names; every one must approve.")


class ProjectCommands(StrictModel):
    """Shell commands run in the project checkout; an empty string skips the step."""

    lint: str
    test: str


class Project(StrictModel):
    """A registered repository."""

    name: str
    path: ExpandedPath
    base_branch: str
    remote: str
    stack: str
    git_host: GitHostProvider
    commands: ProjectCommands
    protected_paths: list[str] = Field(description="Glob patterns agents may not read or edit.")
    extra_allowed_bash: list[str] = Field(description="Extra Bash prefixes the worker may run without asking.")
    env: dict[str, str] = Field(
        description="Environment for commands and agents; {ticket} is replaced by the ticket id."
    )
    profile: Profile

    def checks(self) -> list[tuple[str, str]]:
        """Return the configured checks as (name, command)."""
        checks = (("lint", self.commands.lint), ("test", self.commands.test))
        return [(name, command) for name, command in checks if command]


class StackSettings(StrictModel):
    """What a stack loads from the toolkit and from external marketplaces."""

    markers: list[str] = Field(description="Files whose presence identifies the stack.")
    toolkit_plugins: list[str]
    pinned_skills: list[str] = Field(description="Skills injected in full into every agent run, as plugin:skill.")
    external_plugins: list[str] = Field(description="plugin@marketplace ids enabled for this stack.")
    commands: ProjectCommands = Field(description="Lint and test commands a new project of this stack starts with.")


class ToolkitSettings(StrictModel):
    """Where the shared toolkit lives."""

    path: ExpandedPath
    marketplace: str
    github_repo: str
    always_plugins: list[str]
    always_external_plugins: list[str]


class BitbucketCredentials(BaseModel):
    """Bitbucket Cloud API token, read from BITBUCKET__EMAIL and BITBUCKET__API_TOKEN."""

    email: str
    api_token: SecretStr


class Settings(BaseSettings):
    """All cw settings."""

    model_config = SettingsConfigDict(env_nested_delimiter="__", extra="forbid")

    claude_binary: str
    available_models: list[str]
    permission_modes: list[str]
    default_protected_paths: list[str]
    toolkit: ToolkitSettings
    external_marketplaces: dict[str, str] = Field(description="Marketplace name to GitHub owner/repo.")
    stacks: dict[str, StackSettings]
    defaults: Profile
    projects: list[Project]
    bitbucket: BitbucketCredentials | None = None

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Read init values, then the environment, then the JSON config file."""
        return init_settings, env_settings, JsonConfigSettingsSource(settings_cls, json_file=config_path())

    def project(self, name: str) -> Project:
        """Return the project with this name."""
        for project in self.projects:
            if project.name == name:
                return project
        raise AppException(AppErrorCode.PROJECT_NOT_FOUND, name)

    def stack(self, name: str) -> StackSettings | None:
        """Return the stack settings, or None for a stack the toolkit does not cover yet."""
        return self.stacks.get(name)


def cw_home() -> Path:
    """Return the folder that holds the config file."""
    return Path(os.environ.get(HOME_ENV, DEFAULT_HOME)).expanduser()


def conventions_path(project_name: str) -> Path:
    """Return the onboarding decisions of a project, kept outside its repository."""
    return cw_home() / PROJECTS_DIR / project_name / CONVENTIONS_FILE_NAME


def repo_map_path(project_name: str) -> Path:
    """Return the architecture and symbol map path of a project."""
    return cw_home() / PROJECTS_DIR / project_name / REPO_MAP_FILE_NAME


def config_path() -> Path:
    """Return the config file path."""
    return cw_home() / CONFIG_FILE_NAME


@lru_cache
def get_settings() -> Settings:
    """Load and validate the settings once per process."""
    if not config_path().exists():
        logger.error("No config at %s; run `cw init` first.", config_path())
        raise AppException(AppErrorCode.INVALID_CONFIGURATION, f"Missing {config_path()}.")
    try:
        return Settings()
    except ValidationError as error:
        logger.error("Invalid config at %s:\n%s", config_path(), error)
        raise AppException(AppErrorCode.INVALID_CONFIGURATION, str(error)) from error


def validate_config(raw_config: dict) -> Settings:
    """Validate config data without reading the file or the environment."""
    try:
        return Settings.model_validate(raw_config)
    except ValidationError as error:
        raise AppException(AppErrorCode.INVALID_CONFIGURATION, str(error)) from error


def write_settings(settings: Settings) -> None:
    """Write the settings back to the config file, never the secrets."""
    config_path().parent.mkdir(parents=True, exist_ok=True)
    data = settings.model_dump(mode="json", exclude=SECRET_FIELDS)
    config_path().write_text(json.dumps(data, indent=2) + "\n")
    get_settings.cache_clear()
