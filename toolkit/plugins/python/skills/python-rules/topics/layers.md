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

## Examples

Snippets carry no comments, as the rules require.

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
