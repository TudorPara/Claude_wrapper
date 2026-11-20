"""cw settings: one JSON file (edited by hand, by `cw config set` or by FRIDAY) plus secrets from the environment."""

import fnmatch
import json
import logging
import os
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveInt,
    SecretStr,
    ValidationError,
    field_validator,
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
CONTEXT_DB_FILE_NAME = "context.sqlite3"
QUERY_PLACEHOLDERS = ("{instruction}", "{query}")
SECRET_FIELDS = {"bitbucket"}
DEFAULT_FOLLOWUP_REVIEWER_MODEL = "sonnet"
DEFAULT_REVIEW_SKIP_MAX_CHANGED_LINES = 20
ANY_FOLDER_PREFIX = "**/"

ExpandedPath = Annotated[Path, AfterValidator(lambda path: path.expanduser())]


class AgentRole(StrEnum):
    """The agent roles cw runs; `context` writes the HyDE sketch for the context engine."""

    PLANNER = "planner"
    WORKER = "worker"
    REVIEWER = "reviewer"
    ONBOARDER = "onboarder"
    CONTEXT = "context"


class EmbeddingProvider(StrEnum):
    """Where embeddings are computed."""

    OLLAMA = "ollama"


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
    reviewer_followup: str = Field(
        default=DEFAULT_FOLLOWUP_REVIEWER_MODEL,
        description="Model for every review after a ticket's first one: re-reviews, cw fix reviews, conflict reviews.",
    )
    context: str = Field(default="haiku", description="Model for the context engine's HyDE sketch.")


class RoleTurns(StrictModel):
    """Maximum agentic turns per agent role."""

    planner: PositiveInt
    worker: PositiveInt
    reviewer: PositiveInt
    onboarder: PositiveInt
    context: PositiveInt = Field(default=1, description="Turns for the HyDE sketch, which answers in one tool call.")


class Limits(StrictModel):
    """Caps that keep token usage and loops bounded."""

    max_review_rounds: NonNegativeInt = Field(description="Fix rounds after a reviewer asks for changes.")
    max_fix_rounds: NonNegativeInt = Field(description="Fix rounds after lint or tests fail.")
    max_backoff_rounds: NonNegativeInt = Field(
        default=1,
        description="Fresh worker sessions, started from the last attempt's reflection, for a task whose checks "
        "still fail after its fix rounds.",
    )
    max_conflict_rounds: NonNegativeInt = Field(description="Attempts to resolve merge conflicts with the base branch.")
    review_skip_max_changed_lines: NonNegativeInt = Field(
        default=DEFAULT_REVIEW_SKIP_MAX_CHANGED_LINES,
        description="A cw fix round that only addressed comments, passes the checks and changed at most this many "
        "lines skips its review; 0 never skips.",
    )
    max_pull_request_fix_rounds: PositiveInt = Field(
        default=3,
        description="Passes `cw fix` makes while blocking findings stay open and the worker still changes code.",
    )
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
    compose_file: str = Field(
        default="", description="Docker Compose file cw starts before every worker run and check; empty for none."
    )

    def is_protected(self, path: str) -> bool:
        """Return whether a path matches a protected pattern, the way the toolkit's guard hook matches it."""
        name = Path(path).name
        for pattern in self.protected_paths:
            anywhere = pattern.removeprefix(ANY_FOLDER_PREFIX)
            if any(fnmatch.fnmatch(*pair) for pair in ((path, pattern), (path, anywhere), (name, anywhere))):
                return True
        return False

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


class OllamaEmbeddingSettings(StrictModel):
    """Embeddings from a local Ollama server."""

    provider: Literal[EmbeddingProvider.OLLAMA] = EmbeddingProvider.OLLAMA
    base_url: str = Field(default="http://localhost:11434", description="Ollama server URL.")
    model: str = Field(default="qwen3-embedding:8b-q8_0", description="Ollama model tag, including the quantisation.")
    query_template: str = Field(
        default="Instruct: {instruction}\nQuery:{query}",
        description="Format of a query, from the Qwen3-Embedding model card; must contain {instruction} and {query}.",
    )
    query_instruction: str = Field(
        default="Given a request to change a code repository, retrieve the code that must be read or changed to do it",
        description="Task instruction placed in every query.",
    )
    dimensions: PositiveInt = Field(default=1024, description="Leading vector values kept, then renormalised.")
    context_length: PositiveInt = Field(default=8192, description="Tokens Ollama reads per text (options.num_ctx).")
    batch_size: PositiveInt = Field(default=32, description="Texts per /api/embed request.")
    request_timeout_seconds: PositiveInt = Field(default=300, description="Longest one /api/embed request may take.")
    keep_alive: str = Field(default="10m", description="How long Ollama keeps the model loaded after a request.")
    dense_top_k: PositiveInt = Field(default=60, description="Chunks taken from the embedding search.")
    max_incremental_files: PositiveInt = Field(
        default=20,
        description="Above this many files without vectors, an automatic update skips embedding and asks for a build.",
    )

    @field_validator("query_template")
    @classmethod
    def _has_placeholders(cls, template: str) -> str:
        """Refuse a query template without both placeholders."""
        missing = [placeholder for placeholder in QUERY_PLACEHOLDERS if placeholder not in template]
        if missing:
            logger.error("embeddings.query_template %r lacks %s.", template, ", ".join(missing))
            raise ValueError(f"query_template must contain {', '.join(QUERY_PLACEHOLDERS)}")
        return template


class ContextSettings(StrictModel):
    """How cw builds and uses the per-project context index."""

    include_repo_map: bool = Field(default=False, description="Also send the repo map when a seed exists.")
    seed_max_tokens: PositiveInt = Field(default=2500, description="Largest context seed, in estimated tokens.")
    seed_ticket_tokens: PositiveInt = Field(
        default=500,
        description="Part of the seed kept for similar tickets, so file blocks cannot squeeze out their notes.",
    )
    max_seed_files: PositiveInt = Field(default=15, description="Most files the seed lists.")
    max_symbols_per_file: PositiveInt = Field(default=6, description="Most symbols the seed shows under one file.")
    layout_max_folders: PositiveInt = Field(default=60, description="Most folders in the layout outline of the seed.")
    lexical_top_k: PositiveInt = Field(default=60, description="Chunks taken from the keyword search.")
    max_query_terms: PositiveInt = Field(
        default=60, description="Most search terms taken from a request and its sketch."
    )
    fusion_k: PositiveInt = Field(default=60, description="Reciprocal Rank Fusion constant.")
    graph_weight: float = Field(default=1.0, gt=0, description="Weight of the PageRank channel in the fusion.")
    history_weight: float = Field(default=1.0, gt=0, description="Weight of the history channel in the fusion.")
    pagerank_damping: float = Field(default=0.85, gt=0, lt=1, description="Personalized PageRank damping factor.")
    pagerank_iterations: PositiveInt = Field(default=30, description="Power iterations for PageRank.")
    import_edge_weight: float = Field(default=2.0, gt=0, description="An import edge counts like this many names.")
    reverse_edge_weight: float = Field(
        default=0.5, ge=0, description="Share of each edge also added backwards, so callers of found code rise too."
    )
    max_file_bytes: PositiveInt = Field(default=400_000, description="Larger files are left out of the index.")
    max_chunk_chars: PositiveInt = Field(default=6000, description="Longest chunk text kept for search and embedding.")
    max_similar_tickets: PositiveInt = Field(default=3, description="Most finished tickets shown in a seed.")
    record_plan_chars: PositiveInt = Field(default=1200, description="Plan text kept in a ticket record.")
    hyde_max_chars: PositiveInt = Field(default=2000, description="Longest HyDE sketch kept.")
    eval_k_values: list[PositiveInt] = Field(default=[5, 10, 20], description="Cut-offs reported by cw context eval.")
    eval_max_truth_files: PositiveInt = Field(
        default=20, description="Commits changing more files are left out of cw context eval --from-commits."
    )
    embeddings: OllamaEmbeddingSettings = Field(default_factory=OllamaEmbeddingSettings)


class StorageSettings(StrictModel):
    """How much local data cw keeps."""

    max_index_mb: PositiveInt = Field(default=1024, description="Above this size, cw warns and suggests cw gc.")
    event_retention_days: PositiveInt = Field(
        default=30, description="Event logs of tickets closed longer than this are deleted."
    )


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
    context: ContextSettings = Field(default_factory=ContextSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
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


def context_db_path(project_name: str) -> Path:
    """Return the context index of a project, kept outside its repository."""
    return cw_home() / PROJECTS_DIR / project_name / CONTEXT_DB_FILE_NAME


def project_data_dir(project_name: str) -> Path:
    """Return the folder cw keeps a project's own files in."""
    return cw_home() / PROJECTS_DIR / project_name


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
