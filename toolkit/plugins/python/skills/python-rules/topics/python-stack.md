---
name: nexttech-python-stack
description: The standard stack and tooling of a Nexttech Python service. Covers Python 3.14 with uv, FastAPI, async SQLAlchemy with asyncpg, Alembic, pydantic-settings, LangChain and LangGraph, Qdrant, Langfuse, ruff, the Makefile targets and run commands, dependency and pyproject hygiene, the import root, and what fails inside the Bash sandbox with its workaround. Use it when running a command, adding or removing a dependency, editing pyproject.toml, the Makefile or .gitignore, checking a library version, or when a uv, make, gh or git command fails.
---

# Nexttech Python stack

The stack is the same in every Nexttech service. `pyproject.toml` and `CLAUDE.md` say which parts a given project already uses. A source such as `#13` is a pull request of `nexttech-assistant-svc`.

## 1. Stack

| Concern | Choice |
|---|---|
| Language and environment | Python 3.14, `uv`, `uv.lock` committed, `uv_build` backend |
| API | FastAPI on uvicorn, Pydantic v2 on every request and response |
| Database | PostgreSQL through SQLAlchemy 2.x async and asyncpg; Alembic, async |
| Configuration | pydantic-settings, one `Settings`, the `__` delimiter |
| Vector store | Qdrant, through `langchain-qdrant`'s `QdrantVectorStore` |
| Embeddings | Behind LangChain's `Embeddings` (FastEmbed; an Azure OpenAI provider also exists) |
| Document extraction | Docling, behind an interface of ours |
| Agent framework | LangGraph; agents are built with `create_deep_agent` (#13) |
| Chat models | Behind LangChain's `BaseChatModel` (Anthropic through `langchain-anthropic`; Ollama for local runs) |
| Tracing | Langfuse |
| Streaming | Server-sent events |
| Scheduling | APScheduler, inside the `worker` |
| Outbound email | SMTP, behind an interface of ours |
| Lint and format | ruff, a dev dependency, on its default rule set |
| Infrastructure | Docker Compose, files under `dev/` (#1) |

Check an installed version with `uv pip show <package-name>` before relying on how a library behaves, and run the library on a real input before deciding how to handle its failures.

## 2. Commands

Run everything from the repository root. `CLAUDE.md` lists the project's exact commands; these are the standard ones.

```bash
make sync            # uv sync
make lint            # uv run ruff check .
make format          # uv run ruff format .
make format-check    # uv run ruff format --check .
make migrate         # PYTHONPATH=. uv run alembic upgrade head

docker compose -f dev/docker-compose.yml up -d
uv run uvicorn <package>.api.app:app --reload
uv run python -c "import <package>.api.app"

PYTHONPATH=. uv run alembic revision --autogenerate -m "<message>"
PYTHONPATH=. uv run alembic check
PYTHONPATH=. uv run alembic heads
```

- The smoke check is the import line: every module imports.
- The bar for lint is that a change adds no finding. Run `make format` and `make lint` before pushing (#10; #11, #13).
- Dependencies are declared as `Annotated[T, Depends(provider)]`. `Depends(...)` as a parameter default is flagged as B008.
- `CLAUDE.md` says whether the project has automated tests. Never report tests as passing unless you ran them.
- Startup needs a filled `.env` (every key is in `.env.example`) and the infrastructure running.

## 3. Imports and packaging

- The package lives in `src/<package>` and is imported by its bare name, never as `src.<package>` (#1 review). `pyproject.toml` sets `module-root = "src"`.
- Two import roots in one file is a finding.
- An enum is imported from the module that defines it, not through a module that only re-exports it (#1 review).

## 4. Dependencies and repository hygiene

- Dev tools are dev dependencies: "ruff is a dev tool, not a runtime dependency" (#1).
- No dependency nothing imports: "Why do we need OpenCV? docling already brings the headless build" (#13). No dependency listed twice (#13).
- Never add a dependency for what the standard library, the database or an installed package already does. uuid7 is `uuid.uuid7()` from the standard library; there is no `uuid6` dependency.
- No template text left in `pyproject.toml` (#1).
- `uv.lock` changes only together with `pyproject.toml`. A conflict in it is resolved by regenerating, never by editing.
- No `__pycache__` or other generated file is committed (#1).
- `.gitignore` stays generic for the whole team; personal entries go in `.git/info/exclude` (#1, #13, #18). `.claude/` is one of those personal entries.
- Every text file keeps its final newline (#13).
- The README matches the tree: a command in it that no longer works is a finding (#1).

## 5. Inside the Bash sandbox

Seen on a developer's Mac; check again when something fails.

- `uv run` and every `make` target fail with "Failed to initialize cache", because uv cannot write to `~/.cache/uv`. Prefix the command with `UV_CACHE_DIR="$TMPDIR/uv-cache"` (for example `UV_CACHE_DIR="$TMPDIR/uv-cache" make lint`), or call the tool directly: `.venv/bin/ruff`, `.venv/bin/python`, `.venv/bin/alembic`.
- A sandboxed Python run that imports the app can leave a small `:memory:.ses` file in the repository root (it appears with onnxruntime's "Failed to persist telemetry device ID" warning). Delete it; never commit it.
- The sandbox blocks connections to the local PostgreSQL and Qdrant, so a real boot has to run outside it.
- `gh` reads its token from the macOS keychain, which the sandbox also blocks: inside it `gh` fails with a TLS or invalid-token error. Rerun the command outside the sandbox; do not re-authenticate.
- `origin` is an SSH remote and the agent's shell has no key, so `git fetch` and `git ls-remote` fail with "Permission denied (publickey)". Read the remote through `gh` instead: `gh api "repos/{owner}/{repo}/branches"` and `gh pr list --state all`.
- The sandboxed and the unsandboxed shell have different `$TMPDIR` values. A log written by a server started outside the sandbox has to be read outside it too.
- The shell is zsh: an unquoted `$VARIABLE` holding several words is passed as one argument. Loop over a list in Python or with an array.
