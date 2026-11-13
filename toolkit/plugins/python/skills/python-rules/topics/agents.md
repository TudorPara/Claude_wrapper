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

## Examples

Snippets carry no comments, as the rules require.

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

**Reasoning step by step on an uncertain choice**
Request: "Should the agent get a checkpointer?"
1. What it would add: memory across runs, pausing for approvals.
2. What already exists: history in the application's own tables.
3. What it would break: counters and remembered tool results would carry over between questions.
4. Conclusion: not needed now; add one only when a feature such as approvals requires it.
