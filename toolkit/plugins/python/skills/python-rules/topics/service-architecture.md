---
name: nexttech-service-architecture
description: The shape of a Nexttech backend service. Covers the folder layout and where each kind of code goes, the route, service and repository layers and what each may do, how a route is declared, transactions and slow work, lifetimes and startup in the lifespan, dependencies, auth placement, provider factories, our own interfaces, and work that runs outside a request. Use it when adding or changing an endpoint, a service, a repository, a dependency provider, the lifespan, a provider factory, an interface or a worker handler, and when deciding where a piece of code belongs.
---

# Nexttech service architecture

Code in the wrong place is the second thing reviewers flag most (about 55 of the 650 review comments on `nexttech-assistant-svc`). A source such as `#13` is a pull request of that repository. Examples are real code from that repository.

## 1. Layout

```
src/<package>/
├── api/
│   ├── app.py              API_PREFIX once; routers declare only their own prefix (#6)
│   ├── lifespan.py         builds every long-lived object once, agents included (#22, #18)
│   ├── dependencies.py     thin providers that read app.state; no logic (#22)
│   ├── exception_handlers.py   the only place that builds an error body
│   ├── middleware/         request_id.py; auth.py resolves the caller into request.state (#6)
│   └── routes/             one module per resource group
├── agents/
│   ├── agent.py  agent_catalog.py
│   ├── orchestrator/       tools/ (one per agent), routing.py, invokers/
│   └── <name>_agent/       agent.py constants.py context.py prompts.py state.py tools/
├── services/               business rules and the transaction
├── repositories/           data access only
├── schemas/                SQLAlchemy ORM models (Base lives here, #5; #11)
├── models/                 Pydantic models and enums, one file per domain
├── mappers/                from_<source>_to_<target> functions
├── integrations/           an outside system behind an ABC (proposed on #17; check the project)
├── configuration/          config.py (Settings, get_settings), log_config.py
├── database/               database.py (the engine), session_factory.py (the transaction)
├── exceptions/             app_error.py, error_codes.py, status_codes.py
└── embedding/ llm/ vector_store/ langfuse_tracing/ permissions/ security/ worker/
```

`schemas/` holds the ORM models and `models/` the Pydantic models and the enums. The names suggest the reverse.

Where things go:

- Agent folders end in `_agent`: `knowledge_agent`, `ticket_agent` (#14). The orchestrator folder is `orchestrator/`.
- Enums go in `models/`, one file per business unit, never one `enums.py` (#4; #6). Provider enums too, one file per kind, not in `config.py` (#1).
- A mapper goes in `mappers/`, named `from_<x>_to_<y>` (#6, #13, #22; #22): "This is a mapper, why is this in a controller?"
- The agent catalog is `agents/agent_catalog.py`, not a helper (#7).
- A function that is not a helper does not live in `helpers.py`: "This is not a helper, this is auth related" (#7).
- Constants live in the module that uses them; there is no shared `constants.py` (#1, #6). An agent's tool names are the one case with their own `constants.py`, inside the agent's folder (`nexttech-agents`).
- Compose files go in `dev/` (#1).
- A module the docs mention may be missing from the working tree, and the same concept may exist twice, in two branches, under two names. Before building on something, find where it lives, and reuse what `main` or an open PR already defines.

## 2. Layers

Dependencies point downward only: `route → service → repository`. A layer knows the layer below it and nothing about its callers. No module imports a sibling to borrow a value.

**Route.** HTTP only.

- It builds the service's input from the request, calls one service method and returns what that returns. No `try`, no business rule, no log line, no session, no query (#6). It never reaches into an agent or a graph.
- A request body is passed to the service as the whole request model (#6).
- Every route decorator declares `response_model`, `status_code` and `responses`, the happy case included (#6, #9). `responses` lists each error status the endpoint can answer, with a description that says when it happens. An endpoint with no body sets `response_model=None`.
- Error entries in `responses` are built with `ErrorResponseModel.error_response(AppErrorCode.X, "...")`, not typed by hand (#18, #23).
- Every status is written with our own `AppStatusCode`, the enum the error codes use: the success code in `status_code=` and each error in `responses`. A status the enum lacks is added to it; routes never import `http.HTTPStatus` or `fastapi.status` (#9 review).
- An endpoint that takes an id also lists 422, or the generated docs show FastAPI's own validation body, which the service never sends.
- A query parameter never defaults to `None` when a real default exists: `page_size: Annotated[int, Query(ge=MIN_PAGE_SIZE, le=MAX_PAGE_SIZE, ...)] = DEFAULT_PAGE_SIZE` (#9 review).
- Request-shape validation stays in Pydantic constraints on bodies and query parameters. Meaning that spans several values is a business rule and lives in the service.

**Service.** Owns the business rules and the transaction.

- `async with self._session_factory.get_session() as session:` commits on success, rolls back on any exception and always closes. The session never leaves the service.
- It builds repositories on that session, creates the entity (the repository only persists it, #6), and decides values such as timestamps and generated ids.
- It raises `AppError` and never an HTTP type, and catches only our own error from the repository (#6).
- It returns response models, never ORM entities, and does not build them by hand: it calls a mapper (#22).
- Its inputs are free of HTTP types.
- The same access check is written once, as a private method (#22: `_load_authorized_conversation`).
- It is wired with abstractions only (`VectorStore`, `DocumentParser`), never with a provider's class or a provider setting.
- A path that comes from the client is checked before it reaches a file method: an absolute path, or one with a step up to a parent folder, is refused.

**Repository.** Data access only.

- It keeps `self._session`, never commits and never decides, the time included: it takes a timestamp as an argument.
- Every method is wrapped in `try/except SQLAlchemyError` that logs ids, never personal data, and raises `AppError(AppErrorCode.DATABASE_ERROR) from error` (#6; #8; #13). `IntegrityError` passes through only where a service maps it to a business error.
- A method that touches somebody's own data takes the user id and filters on it.
- No Pydantic models or records inside (#6).
- One repository per table: reuse the existing one (#13).

```python
async def create_document(self, document: DocumentMetadata) -> DocumentMetadata:
    """Insert the document as a new row and return it."""
    try:
        self._session.add(document)
        await self._session.flush()
        return document
    except SQLAlchemyError as error:
        logger.exception("Could not create document_id=%s.", document.id)
        raise AppError(AppErrorCode.DATABASE_ERROR) from error
```

**Dependencies.** A provider only reads `app.state` or builds a request-scoped service. Turn logic goes in a service class: "Let's make it a `ChatService` and keep `get_chat_stream` thin" (#22).

**Auth.** The middleware resolves the user into `request.state`; routes declare `CurrentUser` (#6). Admin routes check the role, not a document scope: "if HR is given ADMIN documents to read, HR also gets upload, delete and scan" (#23).

**One module** turns failures into responses (`nexttech-errors-and-logging`).

Check: `grep -rnE "HTTPException|status_code|JSONResponse|fastapi" src/<package>/services src/<package>/repositories` finds nothing.

## 3. API conventions

- The base path is `/api/v1`, held in one place. A router declares only its own prefix and tag.
- JSON in and out, except a file upload (multipart) and a stream (`text/event-stream`).
- Response models extend `ResponseModel` (`models/base.py`) and are camelCase on the wire. DTOs are `*RequestModel` and `*ResponseModel`.
- A list endpoint returns the one page model, `PageResponseModel[T]`, built with `PageResponseModel.create`: `items`, `page`, `page_size`, `total_count`, `total_pages`. The parameter is `page`, not `page_number`: it matches the query parameter clients send (#4). Where the default and the maximum page size come from is a project ruling.
- A resource that is missing or that belongs to someone else answers 404, never 403.

## 4. Transactions and slow work

- Never hold a session across slow work that is not database work: a model call, an agent run, parsing, embedding, a vector-store call, file I/O. Read in one short session, close it, do the work, write in another.
- Blocking and CPU-bound calls leave the event loop through `asyncio.to_thread`: file I/O, parsing, chunk splitting, hashing (password hashing included, #6), a synchronous client. Everything that waits on I/O is `async`; a function stays synchronous only when it is pure or is the thing run in the thread.
- When one store was written and the next write fails, undo the first and re-raise the original error. The undo logs its own failure and never raises.
- Rows that must not exist apart are written in one transaction.

From `DocumentService` on `nexttech-assistant-svc`: one short session marks the row `PENDING`, the slow work runs with no session open, then one short session writes the outcome. If anything fails, the chunks this run inserted are deleted and the original error is re-raised.

```python
    document.ingestion_status = IngestionStatus.SUCCESSFUL
    document = await self._update_document(document)
except Exception:
    await self._delete_chunks_and_mark_document_failed(
        document, chunks_to_delete_on_failure
    )
    raise
```

## 5. Lifetimes and startup

- Anything with a lifecycle (the engine, the vector store, the chat model, a parser, compiled agents, the tracing client, a service that is expensive to build such as `AuthService`, #18) is built once in `api/lifespan.py`, published on `app.state`, read by a provider in `api/dependencies.py` and passed into services through their constructor. Never a module-level instance. The `worker` gets its own lifespan, built the same way.
- Settings are read once in the lifespan and passed down (#1).
- Lifetimes: app-scoped (the lifespan, or a cached function such as `get_settings`), request-scoped (services built by `Depends` providers, the current user), turn-scoped (the context of one agent run), call-scoped (a session and the repositories built on it). A longer-lived object never keeps a shorter-lived one.
- Shared objects hold configuration and collaborators, not state. Counters, caches and the current user live in the request, the turn or a local variable.
- A service used outside HTTP, by an agent tool or a worker handler, cannot use `Depends`. Build it once in that process's lifespan and hand it over explicitly. That is safe only while services keep no per-request data.
- Build into a local, verify, then publish on `app.state`. Whoever creates a resource disposes of it in the same scope, on every exit path, a failed startup included.
- A hard dependency is verified at startup, and the process refuses to serve without it. Crash-fast plus a restart policy is the retry, so the lifespan has no retry loop.
- A soft dependency does not fail startup. Langfuse is one: `LangfuseClient.initialize()` returns `False` and the app runs without tracing.
- Modules define; they do not act. Importing one must not configure logging, open a connection or build a client.

## 6. Providers and interfaces

- Layout: `<kind>/<kind>_factory.py` holds the dispatcher, and one module per provider holds `setup_<provider>_<thing>(settings)`. Keep the form of the factory you touch.
- A factory receives its settings group as a parameter (#1).
- The dispatcher is a `match settings.provider:` that logs the provider on the branch it takes. Its `case _:` logs an error and raises the application error.
- Construct the library class directly (`ChatOllama(...)`, `QdrantVectorStore.from_existing_collection(...)`) and return the LangChain abstraction (`Embeddings`, `VectorStore`, `BaseChatModel`). No wrapper class or repository around a library.
- Built once at startup and reused by every request. Settings only validate; they build nothing.
- No service, route or repository imports a provider's package or is typed to a provider's class.
- An operation with no generic LangChain API lives in the provider's module, behind one dispatch function in the factory module. The service calls the dispatch with the abstraction and nothing else.
- Where LangChain has a generic call, use it and add no provider code: inserting is `aadd_documents`, and deleting known chunks is `adelete(ids)`.
- An interface of ours is an abstract base class (`ABC` with `@abstractmethod`), and its implementation inherits from it. No `typing.Protocol` (#9; #4).
- No interface with one implementation unless the project's docs ask for one (#8). On `nexttech-assistant-svc` the docs asked for three: document extraction, typed decisions and outbound email, and the abstract email sender was kept for that reason (#17).
- No middleware where a handler does the job (#4).
- Retrieval limits (`top_k`, a minimum score) come from settings.
- Open: whether the payload keys of a vector-store point are named constants imported everywhere, or literals. The first rule on `nexttech-assistant-svc` was constants; on #13 asked for `document.metadata.get("document_id")` with "no constants for this, we want to see it clearly". Ask the project lead, and record the answer in `nexttech-project-rulings`.

A service stays generic. Bad: `vector_store: QdrantVectorStore` on a service, with the Qdrant delete called from the service. Good: the service holds a `VectorStore` and calls one function that picks the provider's code from the object it is given.

```python
async def delete_points_by_metadata(
    vector_store: VectorStore, metadata_key: str, metadata_value: str
) -> None:
    """Delete every point whose metadata matches; LangChain has no filtered delete."""
    match vector_store:
        case QdrantVectorStore():
            await delete_qdrant_points_by_metadata(
                vector_store, metadata_key, metadata_value
            )
        case _:
            logger.error(
                "Failed to delete points for vector store=%s",
                type(vector_store).__name__,
            )
            raise AppError(AppErrorCode.INTERNAL_SERVER_ERROR)
```

No wrapper around a library. Bad: a `VectorRepository` class whose one method calls `add_documents`. Good: the call itself, where it is needed.

```python
await self._vector_store.aadd_documents(chunks)
```

## 7. Work that runs outside a request

Carried over from the design of `nexttech-assistant-svc`; the project's own docs win where they differ.

- `api` and `worker` are two processes from one codebase. Assume several workers run at once.
- Every handler will run twice at some point. Running it twice must leave the same end state as running it once, or a failure somebody can live with.
- A write to two stores is ordered so that a crash between them is repaired by the next run: what says "this is done" is written last, and on removal the derived data goes first and the record last.
- An error that can never succeed goes straight to failed; a connection error is retried.
- In the worker, every log line carries the job id and the work item id.

## 8. Simplicity

- Delete what nothing uses or what cannot be tested yet: "Here we are blind, nothing can be tested" (#4); "please remove these 2 if they cannot be tested" (#7); "keep this auth focused, the agent permission wiring will be implemented later" (#6).
- Two classes that are the same thing become one (#6).
- One table plus derivation beats two tables that must stay in sync.
