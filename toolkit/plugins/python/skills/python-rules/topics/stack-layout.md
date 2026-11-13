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
