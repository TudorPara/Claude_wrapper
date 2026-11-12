---
name: python-rules
description: "The team's rulebook for Python backend and agent projects: tech stack with verified versions, project layout, code style and naming, enums, Pydantic DTOs and settings, layers and lifetimes, errors, logging, security, data model, Alembic, provider factories, LangGraph/deepagents agents, API conventions, git discipline and a review checklist. Use it for any Python code, design or review work."
---

# General rules

The team's self-contained rulebook for Python backend and agent projects, consolidated from past code reviews and a reference service. Where sources disagreed, the stricter reviewer ruling is the one written here.

A project's own decisions (its brief, its reviewers' rulings, its `CLAUDE.md`) win over these defaults. When unsure, ask instead of guessing.

## 1. How to work

The two rules above all others:

1. **Generic configuration.** Anything a coworker might configure differently (a provider, a model, a type, a limit, a path) comes from settings, never from code.
2. **No over-engineering.** The simplest code that is clean and correct, logically and syntactically. No abstraction, helper, layer or option the task does not need.

Working rules:

- For complex or uncertain work, reason step by step before writing code: restate the task, list the rules and files involved, name what is unknown, then choose the simplest correct option.
- Do not hallucinate. If a file, package version or requirement is unknown, read it or check installed versions (`uv pip show <packages>`) before relying on library internals.
- Verify by running: lint, tests, and where possible the app itself. Say plainly what was not verified.
- Never read, print or commit `.env`; `.env.example` is enough.
- Code must already satisfy every rule here; reviewers have flagged AI-written code applied as-is.

Order of work for any change:

1. Settle the contract: what goes in, what comes out, what can fail.
2. Write the narrowest thing that works. No abstraction yet.
3. Run it. "It should work" is not evidence.
4. Generalise only where a second caller already exists.

## 2. Tech stack

Versions are the ones installed and working together in the reference service on 2026-10-05. Treat them as "verified with", not as minimums; re-check with `uv pip show` in the new project.

**Core backend**

| Concern | Choice | Verified with |
|---|---|---|
| Language | Python, pinned in `.python-version` | 3.13 |
| Packaging and venv | uv, `uv.lock` committed, `uv_build` backend | uv 0.12.8 |
| HTTP | FastAPI on Starlette, served by Uvicorn | 0.141.1 / 1.6.0 / 0.52.4 |
| ORM | SQLAlchemy 2.0 async, `Mapped` / `mapped_column` | 2.0.52 |
| Driver | asyncpg (with greenlet) | 0.31.0 |
| Migrations | Alembic on an async engine | 1.19.2 |
| DTOs and settings | Pydantic v2, pydantic-settings | 2.13.5 / 2.15.0 |
| Tokens | PyJWT | 2.14.0 |
| Password hashing | argon2-cffi (Argon2id) | 25.1.0 |
| Identifiers | uuid6 (`uuid7`) | 2025.0.1 |
| Lint and format | Ruff | 0.16.7 |
| Uploads, emails | python-multipart, email-validator | 0.0.32 / 2.3.0 |
| Alembic env only | python-dotenv | 1.2.3 |
| Database | PostgreSQL via Docker Compose | `postgres:15-alpine` |

**AI and agent stack** (only when the project needs it)

| Concern | Choice | Verified with |
|---|---|---|
| Chains and abstractions | LangChain, langchain-core | 1.4.3 / 1.6.6 |
| Graphs | LangGraph | 1.2.11 |
| Tool-calling agents | deepagents | 0.7.20 |
| Local chat and embedding models | Ollama through langchain-ollama | 1.1.0 |
| Hosted chat models | langchain-anthropic | 1.7.5 |
| Local embeddings alternative | FastEmbed through langchain-community | 0.8.0 / 0.4.2 |
| Vector store | Qdrant through langchain-qdrant and qdrant-client | 1.1.0 / 1.19.1, image `qdrant/qdrant:v1.19.1` |
| PDF parsing | Docling | 2.127.0 |
| Splitting | langchain-text-splitters | 1.1.2 |
| Tracing | LangSmith, enabled through `LANGCHAIN_*` env keys | 0.14.1 |

## 3. Project layout and commands

```
<project>/
├── alembic/            env.py (async runner) and versions/
├── alembic.ini
├── docker-compose.yml
├── pyproject.toml, uv.lock, .python-version, .env.example
└── src/<package>/
    ├── api/
    │   ├── entry_point/app.py   create_app(): logging, middleware, routers, handlers
    │   ├── lifespan.py          builds and disposes every long-lived resource
    │   ├── dependencies.py      providers reading app.state, current-user resolution
    │   ├── middleware.py        request logging, authentication gate
    │   ├── errors.py            the only module that renders error responses
    │   ├── health.py, cookies.py
    │   └── routes/
    ├── config.py                nested pydantic-settings, cached get_settings()
    ├── logging_config.py
    ├── core/                    security primitives, shared mappers
    ├── database/                base.py (DeclarativeBase), session.py (Database)
    ├── schemas/                 SQLAlchemy ORM models
    ├── models/                  Pydantic DTOs
    ├── repositories/
    ├── services/                business rules; exceptions.py holds the error enum
    ├── llm/, embeddings/        provider factories
    └── graphs/                  one package per graph or agent, chains/, consts.py
```

`schemas/` is ORM and `models/` is DTOs; state this in the README because the names suggest the reverse.

```bash
uv sync
docker compose up -d
uv run alembic upgrade head
uv run uvicorn src.<package>.api.entry_point.app:app --reload
uv run ruff check src alembic
uv run ruff format src alembic
uv run alembic revision --autogenerate -m "<message>"
uv run alembic check
uv run alembic heads
```

Run every command from the directory that contains `src/`, because imports are rooted there.

## 4. Code style and naming

- No magic numbers or magic strings: named module constants or settings. This includes path fragments, URL schemes, metadata keys, prefixes, router prefixes and tags.
- No comments in code, and none in `.env` or `.env.example`. One-line docstrings; add a `Raises:` section only where it helps. A docstring earns its place by explaining a decision the code cannot show; it never restates the next line. Longer reasoning goes in the pull request.
- Full type hints and return types on every function. Use enums, `Annotated` types and `Field` / `Query` descriptions wherever they fit.
- One logger per module, lowercase, defined once: `logger = logging.getLogger(__name__)`.
- One import root for internal imports (`src.<package>`). Never mix it with the bare package name: in an editable install both resolve and the module loads twice. Ruff's import sorting does not catch the mix; a grep does.
- `ruff check` and `ruff format` clean; imports sorted, no duplicates, no extra blank lines, no typos in names or docstrings.
- No dead code: no unused constants, helpers, imports or methods.
- Code must read as written by a person: no filler names, no docstrings that restate the code.

Naming:

- **Methods** are verb plus object in the vocabulary of their layer: `get_active_by_id`, `revoke_refresh_session`, `verify_password`. Never `handle`, `process`, `do_work`, `execute`, `run`, `manage`. A reviewer rejected `warm_up` in favour of `preload_models`.
- **Builders and factories** are `setup_<thing>`, with one `setup_<provider>_<thing>` per provider.
- **Repository verbs**: `create`, `get_by_...`, `list_...`, `count`, `update_...`, `replace`, `delete_...`.
- **DTOs** are `*RequestModel` / `*ResponseModel`. Table names are plural.
- **Booleans and predicates** read as questions: `is_active`, `has_expired`, `_is_public`.
- **Constants name a decision**, not a value: `MAX_PASSWORD_LENGTH`, `REFRESH_COOKIE_PATH`. A constant whose name is its value in capitals should be inlined.
- **Units and scale live in the name**: `pool_timeout_seconds`, `access_token_expire_minutes`, `balance_minor`, `amount_minor`.
- **Banned as standalone names**: `data`, `info`, `obj`, `temp`, `result`, `value`, `item`, `manager`, `helper`, `util`.
- **One word per concept** across the codebase: pick `get` or `fetch` or `find` and never alternate.
- If a name needs a comment to be understood, fix the name.

Public and private:

- The underscore marks attributes, not parameters. `def __init__(self, session)` keeps the plain name; the attribute is `self._session`.
- Every collaborator a class receives to do its job is underscored unless callers are meant to use it. Module-level helpers and constants meant to stay internal take the underscore too; local variables never do.
- A private name imported by another module is a contradiction: make it public or move it.

After any IDE rename with "search in comments and strings", grep both the old and the new name in strings, prompts, env keys and docstrings; such a rename has rewritten an intent value, prompt text and a user-facing answer before.

## 5. Configuration

- One pydantic-settings `Settings` composed of nested `BaseModel` groups, read with the `__` env delimiter (`DATABASE__URL`, `LLM__PROVIDER`). A group's env prefix is its field name on `Settings`, uppercased.
- **No defaults** for configurable behaviour: every value comes from the environment, or the application refuses to start, with the failure naming the key. Never at first use.
- Constrain every field with `Annotated[..., Field(ge=...)]` against a named minimum constant.
- Provider settings are discriminated unions: one `BaseModel` per provider with `provider: Literal[<Enum>.<MEMBER>]`, combined as `Annotated[A | B, Field(discriminator=...)]`. One provider `StrEnum` per kind; secrets as `SecretStr`. Switching provider is a settings change only.
- Connection URLs arrive whole from the environment, never assembled in code. Env names stay vendor-neutral.
- `get_settings()` is `lru_cache`d. Call it inside the function or method that needs it, at the moment the object using it is created, lifespan included. Never at module level: that freezes values before any override, validates the environment as an import side effect and makes import order load-bearing. An object built from settings (a hasher, a client) is built lazily too.
- Settings validators raise the project's own error: `logger.error` the offending values, then `raise AppException(AppErrorCode.INVALID_CONFIGURATION)`. Never a bare `ValueError` there.
- `.env.example` mirrors every key of `.env` with placeholders for secrets; `.env` stays gitignored. One key per line, and the file ends with a newline. A new setting touches three places: `config.py`, `.env.example`, `.env`.

## 6. Layers

Dependencies point downward only. A layer knows the layer below it and nothing about its callers; no module imports a sibling to borrow a shared value.

- **Route**: HTTP only. Builds the service input from the request, calls one service method, returns what it returns. No business rules, no sessions, no queries; it never imports a graph.
- **Service**: owns the business rules and the transaction boundary through the injected `Database` (`async with self._database.database_session() as session:` commits on success, rolls back on any exception, always closes). Coordinates repositories, constructs entities, decides values such as timestamps and generated identifiers, raises domain errors and never HTTP exceptions, and **returns response models (`*ResponseModel`), never ORM entities**. Its inputs are plain arguments or HTTP-free dataclasses.
- **Repository**: data access only. Stores `self._session`, never commits, never decides (including the time: it takes a timestamp as an argument). Every method, sync staging methods included, is wrapped in `try/except SQLAlchemyError` that logs ids (never personal data) and raises `AppException(AppErrorCode.DATABASE_ERROR) from error`. `IntegrityError` passes through only where a service maps it to a business error.
- **One module** maps failures to responses. That mapping exists in exactly one place.

Transactions:

- The boundary lives in the service, in exactly one layer.
- Never hold a pooled connection across slow non-database work (model calls, graph runs, parsing, hashing). Read in one short session, close it, do the work, write in another short transaction.
- Move CPU-bound and blocking work off the event loop with `asyncio.to_thread`.
- The session never escapes the service layer.

Check: grep the service layer for `HTTPException`, `status_code`, `Response`. Any hit is a leak.

## 7. Dependency injection, lifetimes and startup

- Anything with a lifecycle (database, vector store, chat model, compiled graphs, agents) is created once in the lifespan, published on `app.state`, read by a dependency and passed into services through their constructor. Never a module-level global.
- Lifetimes: app-scoped (lifespan or a cached function), request-scoped (services from `Depends` providers, the current user), run-scoped (graph state, an agent's runtime context), call-scoped (sessions and the repositories built on them). A longer-lived object never keeps a shorter-lived one.
- **Shared objects hold configuration, not state.** Anything reused by concurrent requests keeps only its configuration and collaborators; counters, caches and the current user live in the request, the run's state or a local variable.
- Services used outside HTTP, such as by agent tools, are built once in the lifespan and passed through the agent's runtime context, because tools cannot use `Depends`. Safe while services keep no per-request data.
- Build into a local, verify, then publish on `app.state`. Whoever creates a resource disposes of it, in the same scope, on every exit path including a failed startup.
- **A hard dependency is verified at startup.** Pools are lazy, so force one real round-trip (`SELECT 1`) with a timeout and abort if it fails: one critical log, then `DATABASE_ERROR`. Crash-fast plus a restart policy is the retry mechanism; no retry loop in the startup hook. Third-party errors raised while building a resource propagate with their traceback.
- Soft dependencies do not fail startup; they belong to the health endpoint. The health endpoint exercises the same dependency the startup probe did.
- **Modules define; they do not act.** Importing a module must not configure logging, open a connection or read the environment. The composition root is the only exception.

## 8. Errors

- Business-rule and configuration failures use one exception, `AppException(AppErrorCode.X)`. `AppErrorCode` is an `Enum` whose members carry the stable wire code, a stdlib `HTTPStatus` and the client-facing message. One global handler renders it; no route builds error JSON.
- Add an error code only for a failure the client must tell apart; otherwise reuse one. A resource that is missing or owned by someone else is `RESOURCE_NOT_FOUND` (404, not 403).
- Configuration failures raise `INVALID_CONFIGURATION`: settings validators and every factory's `case _`.
- Framework errors keep their own handlers and stable codes (`validation_error`, `http_error`, `internal_error`); they are not enum members.
- Every error response has the same shape, `{"detail", "code"}` plus `errors` for validation, and carries the request id header.
- Never send `str(exc)` to a client. The handler owns the client-facing message; the raise site owns the log line.
- Never echo a rejected value in a validation error; report field and message only.
- Derive what is derivable: `WWW-Authenticate: Bearer` is emitted from the 401 status, not declared per error.
- Where a caller catches one failure deliberately, check the code and re-raise everything else.
- Cleanup helpers (delete a file, remove chunks, mark a row failed) log and swallow their own errors so they never hide the original one.
- Agent tool arguments are the exception to `AppException`: see section 13.

## 9. Logging

- Configure the root logger once in the composition root, with level and format from settings.
- Assign each request an id, or reuse an inbound `X-Request-ID`; publish it through a `ContextVar`, stamp it on every record with a logging filter, and echo it in the response header, error responses included.
- One line per request with method, path, status and duration.
- The log carries the specific reason (which check failed, which identifier, which exception type); the response carries one generic message per error code.
- Log ids, never personal data and never the credential itself. Identify a refresh session by its `jti`.
- Never ship debug callbacks such as `StdOutCallbackHandler`: they print every chain's inputs and outputs, including user data.

## 10. Security defaults

- Deny by default. Enforce authentication by position (middleware before routing), so a new endpoint is protected the moment it is mounted. Public paths are an explicit set, matched on whole path segments.
- One identical failure response for every authentication failure: unknown subject, wrong secret, expired, wrong token type, inactive user. Equalise timing where a lookup is skipped by verifying against a dummy hash.
- Signed is not encrypted. Restrict accepted algorithms to an explicit allowlist and require the claims relied on.
- Verify the token type where it is consumed; a refresh token must never be accepted as an access token.
- Access tokens are short-lived and stateless. Refresh sessions are persisted by `jti`, single-use and rotated on every refresh; the stored expiry is authoritative.
- Long-lived credentials get the narrowest transport scope that works: the refresh token travels only as an `HttpOnly` cookie scoped to the auth path and never appears in a response body.
- Password hashing is Argon2id with cost parameters from settings, run in a worker thread.
- Operations a client may retry are idempotent: logout with no session still succeeds.
- Response models omit sensitive fields by construction (no `password_hash` field to leak).

## 11. Data model and API conventions

- uuid7 for every generated id: primary keys and vector chunk ids; foreign keys follow. The default is applied at flush, so flush before another row needs the id.
- Every ORM model is exported from `schemas/__init__.py`, because Alembic registers models by importing that package.
- Timestamps are timezone-aware. An `Audit` mixin fills `created_at` / `updated_at` from the database's `now()`, the transaction start time, so rows inserted together share it: order by `(created_at, id)`.
- Enum columns store `StrEnum` values through `Enum(..., native_enum=False, create_constraint=True, length=..., values_callable=...)`, with named constants for the type name and length.
- Column lengths and constraint names are named constants next to the model, reused by the service that validates against them.
- Money is stored in integer minor units, and attribute and field names carry the unit; map to a brief's column name when it differs.
- Date filters take inclusive days counted in UTC; the service turns them into a start-inclusive, end-exclusive window.
- One table plus derivation beats two tables that must stay in sync.
- Every DTO inherits one base model with a camelCase alias generator, `populate_by_name=True` and `from_attributes=True`: camelCase on the wire, snake_case accepted on input.
- List endpoints return one generic page model (`items`, `page`, `page_size`, `total_count`, `total_pages`). Page size defaults and caps come from settings; an oversized request is capped, not rejected.
- The API version prefix comes from settings; each router declares its prefix and tag as constants.
- Request-shape validation stays in Pydantic constraints on bodies and query parameters; meaning that spans several values is a business rule and lives in the service.

## 12. Migrations (Alembic)

- `alembic/env.py` reads the database URL from the environment and targets `Base.metadata`.
- Model and migration match exactly: types, timezone, lengths, nullability, constraint names. `alembic check` prints "No new upgrade operations detected".
- Know whether the declarative base has a naming convention; without one, `index=True` gives `ix_<table>_<column>` and unique constraints are named explicitly.
- Exactly one head. Fill every generated migration body; never leave `pass`. Read every autogenerated revision before applying it.
- Renames use `op.rename_table` or explicit operations, never autogenerate (it drops and recreates). Later migrations use the new names.
- One local database shared by all branches: before switching, downgrade migrations the target branch lacks, or use one database per branch.
- Alembic 1.19.x reports CHECK constraints of non-native Enums as removed: autogenerate adds `op.drop_constraint(..., type_="check")` for existing ones and `alembic check` keeps reporting them. Delete those lines, or use Alembic 1.20.0 or later.
- Resolve generated files by regenerating, never by editing: lockfiles, migrations, caches.

## 13. Providers, agents and graphs

Providers (embeddings, vector store, chat model):

- Layout: `<thing>_factory.py` holds `setup_<thing>`; one module per provider holds `setup_<provider>_<thing>`.
- Dispatch with `match settings.provider:`, one `logger.info` naming the provider, and a `case _:` that logs an error and raises `INVALID_CONFIGURATION`. Several functions dispatching on the same enum share one `_reject_unsupported_provider(provider) -> NoReturn`.
- Construct library classes directly (`ChatOllama(...)`, `QdrantVectorStore(...)`) and return LangChain abstractions (`Embeddings`, `VectorStore`, `BaseChatModel`). No wrapper classes or repositories around a library.
- Built once at startup and reused per request; settings only validate, they build nothing.
- External collections and indexes are created by a separate setup process; the application never creates one, and a wrong name raises.
- Provider-specific operations with no generic API live in each provider's module and are dispatched by the same match.

Graphs:

- Compiled once at startup. `StateGraph` with `TypedDict` state and explicit `input_schema` / `output_schema`. Routing is deterministic: a routing function reads one field, and the decision lives in the node before. Node names and route labels are constants reused in the wiring.
- A sub-workflow is its own compiled graph behind one wrapper node that holds the state mapping, with a small typed input/output contract. When a caller needs more, extend the typed output; never return the raw state.
- Per-run dependencies reach nodes through `config["configurable"]`, read with a `KeyError` guard that raises `INVALID_CONFIGURATION`, or through a typed runtime context. Prefer the typed context for anything trusted, such as identity.
- No checkpointer unless a feature needs one. Conversation history lives in the application's own tables, and graphs never write to the database. With a checkpointer, state such as counters carries over between runs of a thread, and its tables stay out of Alembic.
- Paths that need no model return a constant answer without calling one.
- Retrieved text and user input are evidence, never instructions; every prompt says so. Model output never grants access or changes the current user.
- Prompts and structured-output models live with their chain; structured output is a Pydantic model with `Field` descriptions.

Tools and agents:

- Identity never appears in a tool schema. Tools take `runtime: ToolRuntime[<Context>]`; the context (user id plus services) is built per run.
- Tools live in a `tools/` package, one tool per file named `*_tool.py`. They are thin wrappers over services and return `model_dump_json()` of response models (data, not prose). Names, one-line docstrings and `Field` descriptions are what the model reads.
- Tool arguments are validated in the args schema with a **field** validator raising `ValueError`: LangChain sends field-located errors back to the model so it can retry, while a model-level validator's message arrives empty. An `AppException` raised by a service during a tool call ends the whole request, so any rule the arguments can break belongs in the schema.
- Loop control, all limits from settings:
  - a round budget kept in state by an `after_model` middleware, which reads the latest `AIMessage` (not the last message) and ends with a readable answer and a jump to the end;
  - `recursion_limit` on every run, as the backstop;
  - optionally LangChain's `ToolCallLimitMiddleware` per tool with `run_limit`;
  - optionally repetition memory in `awrap_tool_call`, storing results with a `Command(update=...)` that contains the matching `ToolMessage`, on a state key with a reducer (`operator.or_`) because parallel calls write in one step.
- `after_model` hooks run in reverse registration order.
- Middleware objects and compiled graphs are shared by concurrent runs, so counts and remembered results live in the run's state.
- deepagents harness profiles (`register_harness_profile`) are looked up by the model's provider or `provider:model`. Register under the provider value from settings, once at startup through a small function, never at module level. deepagents skips the `provider:model` key when the model name contains a colon, and a key that is not a provider is accepted silently and never applied. `FilesystemMiddleware` and `SubAgentMiddleware` cannot be excluded; `AnthropicPromptCachingMiddleware` is added for every provider and only acts on Anthropic models.

RAG:

- Ingestion order: validate metadata before reading bytes, read the content once with a size probe, checksum it, store the original file under a generated name, parse in a worker thread, split, index, then record the outcome on the database row.
- A new document is inserted as `processing` first; on failure its chunks are removed and the row is marked `failed`, so a retry of the same bytes is allowed. A replacement is indexed under a new id and swapped in one transaction before the old version is touched.
- Chunk metadata keys are named constants and are the contract between ingestion, retrieval and deletion; changing one means changing every side and re-indexing.
- Validate what the vector store returns before trusting it.
- Number the retrieved chunks, make the model report the positions it used, and build the cited sources from those positions only.
- Retrieval limits (`top_k`, minimum score) come from settings.

## 14. PR and git discipline

- Ship only the task. Development scripts stay local and gitignored; when a brief asks for something kept local, say so in the PR.
- Never commit `.env`, data directories, uploads, model caches or generated files.
- Explain anything non-obvious in the PR description: deviations from the brief, provider and resource lifecycle, model preloading, capability checks of a replacement model. Known gaps are named in the PR, not left for the reviewer to find.
- Reply to every review comment with the reason for the change, not only the fix.
- Small commits per folder with conventional messages (`fix:`, `refactor:`, `feat:`, `chore:`). One concern per branch; never apply cross-cutting edits on two branches independently.
- Merge the base branch in early and often. After any merge, boot the application.

Merging between stacked branches:

- Back up first: `git branch backup/<branch>`.
- Merge, not rebase, into a branch with an open PR: `git merge --no-commit --no-ff <source>`. An IDE may auto-commit, so check `git status` before any commit.
- Before committing, check for silent deletions, one-sided changes dropped without a conflict, empty files created by "keep" on deleted-by-them conflicts, leftover conflict markers and stale imports.
- Untrack a file but keep it locally with `git rm --cached`, then gitignore it.
- Never `stash pop` onto a different branch than the one stashed from.

## 15. Verify by running

The first test in any service boots the application and hits the health endpoint; it catches wiring, import and merge damage.

```python
with TestClient(app) as client:
    assert client.get("/health").status_code == 200
```

Injection is what makes the rest testable: `app.dependency_overrides[get_database]` swaps the whole persistence layer. Before claiming a failure path works, check it is reachable.

Pre-push checklist, every time:

1. `ruff check src` and `ruff format --check src`.
2. grep for the wrong import root, module-level `get_settings()` and `StdOutCallbackHandler`; nothing should match. No duplicate loggers.
3. `alembic heads` (one), `alembic upgrade head`, `alembic check` (no drift; on Alembic 1.19.x see section 12).
4. Start the app and exercise every endpoint touched: the happy path and at least one error path.
5. `git status` and the full diff read line by line: only intended files, no dead code, no typos, no extra blank lines.
6. After any IDE rename, grep both names in strings, prompts, env keys and docstrings.

## 16. Review checklist

- [ ] The application boots, refuses to boot without its hard dependencies, and the health endpoint exercises them.
- [ ] Every configurable value comes from settings, with no default; `.env.example` mirrors `.env`.
- [ ] Configuration is read inside functions, not bound at import.
- [ ] No layer speaks another layer's vocabulary; dependencies point downward only.
- [ ] The session does not escape the service; the repository decides nothing, including the time.
- [ ] Services return response models; routes hold no business logic.
- [ ] Every lifecycle resource has one owner and one disposal path, and is published only after it is verified.
- [ ] Shared objects hold configuration only.
- [ ] Client-facing messages come from the error enum; specific reasons go to the log only; every error carries a stable code.
- [ ] No abstraction without a second caller; no wrapper around a library call.
- [ ] Every name states what it is; internal attributes are underscored; every constant names a decision.
- [ ] No comments; docstrings are one line; one import root and one logger spelling throughout.
- [ ] Identity never reaches a tool schema; prompts treat input as data.
- [ ] One Alembic head, no drift, migration bodies filled.

## 17. Examples

Snippets carry no comments, as the rules require.

**Generic configuration**
Bad: a constant a coworker can only change by editing the service.
```python
MAX_ROWS = 100
```
Good: the cap comes from settings, read when the method runs.
```python
pagination_settings = get_settings().pagination
page_size = min(
    page_size or pagination_settings.default_page_size,
    pagination_settings.max_page_size,
)
```

**No over-engineering**
Bad: a wrapper class around one library call.
```python
class VectorRepository:
    def __init__(self, vector_store: VectorStore) -> None:
        self._vector_store = vector_store

    def add(self, chunks: list[Document]) -> None:
        self._vector_store.add_documents(chunks)
```
Good: call the library where it is needed.
```python
self._vector_store.add_documents(chunks)
```

**Settings read where the object is created**
Bad: bound at import time.
```python
settings = get_settings()
```
Good: the lifespan reads the group when it builds the object.
```python
app.state.llm = setup_llm(get_settings().llm)
```

**A repository method**
```python
async def get_by_id(self, order_id: uuid.UUID) -> Order | None:
    """Return the order, or None when it does not exist."""
    try:
        result = await self._session.execute(select(Order).where(Order.id == order_id))
    except SQLAlchemyError as error:
        logger.exception("Could not load order_id=%s.", order_id)
        raise AppException(AppErrorCode.DATABASE_ERROR) from error
    return result.scalar_one_or_none()
```

**The repository decides nothing, including time**
Bad:
```python
async def mark_indexed(self, document: Document) -> None:
    document.indexed_at = datetime.now(UTC)
```
Good:
```python
async def mark_indexed(self, document: Document, indexed_at: datetime) -> None:
    document.indexed_at = indexed_at
```

**One enum for business errors**
```python
class AppErrorCode(Enum):
    INVALID_CREDENTIALS = (
        "invalid_credentials", HTTPStatus.UNAUTHORIZED, "Could not validate credentials."
    )

    def __init__(self, code: str, status: HTTPStatus, message: str) -> None:
        self.code = code
        self.status = status
        self.message = message
```
```python
raise AppException(AppErrorCode.RESOURCE_NOT_FOUND)
```

**A provider factory**
```python
def setup_embeddings(settings: EmbeddingSettings) -> Embeddings:
    """Build the embedding model of the configured provider."""
    logger.info("Building embeddings with provider=%s.", settings.provider)
    match settings.provider:
        case EmbeddingProvider.OLLAMA:
            return setup_ollama_embeddings(settings)
        case _:
            logger.error("Embedding provider '%s' is not supported.", settings.provider)
            raise AppException(AppErrorCode.INVALID_CONFIGURATION)
```

**Identity in agent tools**
Bad: the model can choose whose data is read.
```python
@tool
async def list_open_orders(user_id: str) -> str:
    """Return the open orders of the given user."""
```
Good: the id comes from the run context and never appears in the schema.
```python
@tool
async def list_open_orders(runtime: ToolRuntime[AgentContext]) -> str:
    """Return the user's open orders, newest first."""
    context = runtime.context
    orders = await context.order_service.list_open(context.user_id)
    return orders.model_dump_json()
```

**Tool argument validation**
Bad: a model-level validator raising `AppException`; the model gets no usable reason and the request fails.
Good: a field validator raising `ValueError`.
```python
@field_validator("end_date")
@classmethod
def _within_allowed_range(cls, end_date: date, info: ValidationInfo) -> date:
    """Reject an end before the start or a range over the configured maximum."""
    start_date = info.data.get("start_date")
    if start_date is None:
        return end_date
    if end_date < start_date:
        raise ValueError(REVERSED_RANGE_MESSAGE)
    max_days = get_settings().agent.max_date_range_days
    if (end_date - start_date).days >= max_days:
        raise ValueError(RANGE_TOO_LONG_MESSAGE.format(max_days=max_days))
    return end_date
```

**Counters live in run state, not on the middleware**
Bad:
```python
self._rounds_used += 1
```
Good:
```python
rounds_used = state.get("tool_rounds", NO_TOOL_ROUNDS)
if rounds_used < self._budget:
    return {"tool_rounds": rounds_used + 1}
```

**Replying to a review comment**
Comment: "This should be put in a function and called in lifespan. register_harness_profile applies per LLM, not per deep agent."
Good reply, reason first: "Moved the registration into `register_deep_agent_harness_profile`, called from lifespan right after `setup_llm` and keyed by `LLM__PROVIDER`. deepagents looks profiles up by provider, so a key named after the agent was never applied."

**Reasoning step by step on an uncertain choice**
Request: "Should the agent get a checkpointer?"
1. What it would add: memory across runs, pausing for approvals.
2. What already exists: history in the application's own tables.
3. What it would break: counters and remembered tool results would carry over between questions.
4. Conclusion: not needed now; add one only when a feature such as approvals requires it.
